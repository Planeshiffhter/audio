import test from 'node:test';
import assert from 'node:assert/strict';
import { fetchHandler } from '../worker/index.mjs';

class MemoryBucket {
  data = new Map();
  version = 0;
  async put(key, value, options = {}) {
    const old = this.data.get(key);
    if (options.onlyIf?.etagMatches && old?.etag !== options.onlyIf.etagMatches) return null;
    const bytes = new Uint8Array(await new Response(value).arrayBuffer());
    const item = { bytes, etag: String(++this.version), size: bytes.length };
    this.data.set(key, item); return item;
  }
  async get(key) {
    const item = this.data.get(key);
    if (!item) return null;
    return { ...item, body: new Blob([item.bytes]).stream(),
      json: async () => JSON.parse(new TextDecoder().decode(item.bytes)),
      text: async () => new TextDecoder().decode(item.bytes) };
  }
  async head(key) { return this.data.get(key) || null; }
  async delete(keys) { for (const key of Array.isArray(keys) ? keys : [keys]) this.data.delete(key); }
  async list({ prefix = '' } = {}) {
    return { objects: [...this.data.keys()].filter(k => k.startsWith(prefix)).map(key => ({ key })), truncated: false };
  }
}
function env() { return { BUCKET: new MemoryBucket(), ENGINE_TOKEN: 'test-engine-key' }; }
async function request(environment, path, { method = 'GET', user = 'owner-a', engine = false, data, form, workerId } = {}) {
  const headers = {};
  if (user) headers['oai-authenticated-user-id'] = user;
  if (engine) headers['X-Engine-Key'] = environment.ENGINE_TOKEN;
  if (workerId) headers['X-Worker-Id'] = workerId;
  let body;
  if (data !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(data); }
  if (form) body = form;
  return fetchHandler(new Request('https://studio.example' + path, { method, headers, body }), environment);
}
async function create(environment, user = 'owner-a', script = 'Hello, this is my complete script.') {
  const form = new FormData();
  form.set('reference', new File(['fake-wav-for-api-test'], 'reference.wav', { type: 'audio/wav' }));
  form.set('script', script); form.set('preset', 'balanced'); form.set('title', 'First narration');
  const response = await request(environment, '/api/jobs', { method: 'POST', user, form });
  assert.equal(response.status, 201);
  return (await response.json()).job;
}

test('identity and cross-owner isolation protect references and output', async () => {
  const e = env();
  assert.equal((await request(e, '/api/jobs', { user: null })).status, 401);
  const job = await create(e);
  assert.equal((await request(e, `/api/jobs/${job.id}`, { user: 'owner-b' })).status, 404);
  assert.deepEqual((await (await request(e, '/api/jobs', { user: 'owner-b' })).json()).jobs, []);
  assert.equal((await request(e, '/api/engine/claim', { method: 'POST', data: { workerId: 'w1' } })).status, 403);
});

test('claim, lease, progress, cancellation and resume preserve the full script', async () => {
  const e = env();
  const script = 'A full script.\n\nWith another paragraph and special characters: <>&.';
  const job = await create(e, 'owner-a', script);
  const claim = await (await request(e, '/api/engine/claim', { method: 'POST', engine: true, data: { workerId: 'w1' } })).json();
  assert.equal(claim.job.id, job.id); assert.equal(claim.job.script, script);
  const second = await (await request(e, '/api/engine/claim', { method: 'POST', engine: true, data: { workerId: 'w2' } })).json();
  assert.equal(second.job, null);
  assert.equal((await request(e, `/api/engine/jobs/${job.id}/progress`, { method: 'POST', engine: true,
    data: { workerId: 'wrong', completedChunks: 1, totalChunks: 2, progress: .5 } })).status, 409);
  await request(e, `/api/engine/jobs/${job.id}/progress`, { method: 'POST', engine: true,
    data: { workerId: 'w1', completedChunks: 1, totalChunks: 2, progress: .5 } });
  const cancelled = await (await request(e, `/api/jobs/${job.id}/cancel`, { method: 'POST' })).json();
  assert.equal(cancelled.job.cancelRequested, true);
  const heartbeat = await (await request(e, '/api/engine/heartbeat', { method: 'POST', engine: true,
    data: { workerId: 'w1', activeJobId: job.id } })).json();
  assert.equal(heartbeat.cancelRequested, true);
  await request(e, `/api/engine/jobs/${job.id}/cancelled`, { method: 'POST', engine: true, data: { workerId: 'w1' } });
  const resumed = await (await request(e, `/api/jobs/${job.id}/resume`, { method: 'POST' })).json();
  assert.equal(resumed.job.status, 'queued'); assert.equal(resumed.job.completedChunks, 1);
});

test('completion requires both outputs and cannot expose incomplete audio', async () => {
  const e = env(), job = await create(e);
  await request(e, '/api/engine/claim', { method: 'POST', engine: true, data: { workerId: 'w1' } });
  assert.equal((await request(e, `/api/jobs/${job.id}/audio.mp3`)).status, 409);
  assert.equal((await request(e, `/api/engine/jobs/${job.id}/complete`, { method: 'POST', engine: true,
    data: { workerId: 'w1' } })).status, 409);
  await e.BUCKET.put(`jobs/${job.id}/audio.mp3`, 'mp3-audio');
  await e.BUCKET.put(`jobs/${job.id}/audio.wav`, 'wav-audio');
  await request(e, `/api/engine/jobs/${job.id}/complete`, { method: 'POST', engine: true,
    data: { workerId: 'w1', durationSeconds: 12, completedChunks: 1, totalChunks: 1 } });
  const repeated = await request(e, `/api/engine/jobs/${job.id}/complete`, { method: 'POST', engine: true,
    data: { workerId: 'w1', durationSeconds: 12, completedChunks: 1, totalChunks: 1 } });
  assert.equal(repeated.status, 200);
  assert.equal((await repeated.json()).status, 'completed');
  const response = await request(e, `/api/jobs/${job.id}/audio.mp3`);
  assert.equal(response.status, 200); assert.equal(await response.text(), 'mp3-audio');
});

test('queue limits, script limits and invalid formats are actionable', async () => {
  const e = env();
  await create(e); await create(e); await create(e);
  const form = new FormData();
  form.set('reference', new File(['abc'], 'reference.wav')); form.set('script', 'hello');
  assert.equal((await request(e, '/api/jobs', { method: 'POST', form })).status, 429);
  const large = new FormData(); large.set('reference', new File(['abc'], 'reference.wav'));
  large.set('script', 'word '.repeat(6001));
  assert.equal((await request(env(), '/api/jobs', { method: 'POST', form: large })).status, 400);
});

test('service probes stay separate from visitor data', async () => {
  const e = env();
  const form = new FormData(); form.set('reference', new File(['abc'], 'reference.wav')); form.set('script', 'Test narration.');
  const result = await request(e, '/api/jobs', { method: 'POST', user: null, engine: true, form });
  assert.equal(result.status, 201);
  assert.deepEqual((await (await request(e, '/api/jobs')).json()).jobs, []);
});

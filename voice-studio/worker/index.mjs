const HTML = /*INJECT_HTML*/ "";
const LIMITS = { maxUploadBytes: 20971520, maxScriptChars: 40000, maxWords: 6000 };
const LEASE_MS = 180000;
const ID_PATTERN = /^\d{13}-[a-f0-9-]{36}$/;
const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
});
class APIError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
function equalSecret(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  let result = 0;
  for (let i = 0; i < a.length; i++) result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return result === 0;
}
function owner(request, env = null) {
  const id = request.headers.get("oai-authenticated-user-id");
  // Authenticated engine probes have a separate namespace, never a visitor identity.
  if (!id && env && equalSecret(request.headers.get("X-Engine-Key"), env.ENGINE_TOKEN)) return "service:engine";
  if (!id) throw new APIError(401, "Sign in to open your voice studio.");
  return id;
}
function requireEngine(request, env) {
  if (!equalSecret(request.headers.get("X-Engine-Key"), env.ENGINE_TOKEN))
    throw new APIError(403, "Engine authentication failed.");
}
function sameOrigin(request) {
  const origin = request.headers.get("Origin");
  if (origin && origin !== new URL(request.url).origin)
    throw new APIError(403, "This request must come from your voice studio.");
}
function requireId(id) {
  if (!ID_PATTERN.test(id)) throw new APIError(404, "Narration not found.");
}
function publicJob(job) {
  const { id, title, preset, status, createdAt, updatedAt, progress, completedChunks,
    totalChunks, durationSeconds, error, stage, cancelRequested } = job;
  return { id, title, preset, status, createdAt, updatedAt, progress,
    completedChunks, totalChunks, durationSeconds, error, stage, cancelRequested };
}
async function getMeta(env, id) {
  requireId(id);
  const item = await env.BUCKET.get(`meta/${id}.json`);
  if (!item) throw new APIError(404, "Narration not found.");
  return { job: await item.json(), etag: item.etag };
}
async function updateMeta(env, id, transform) {
  for (let attempt = 0; attempt < 5; attempt++) {
    const { job, etag } = await getMeta(env, id);
    const next = transform(job);
    if (!next) return job;
    next.updatedAt = new Date().toISOString();
    const result = await env.BUCKET.put(`meta/${id}.json`, JSON.stringify(next), {
      onlyIf: { etagMatches: etag }, httpMetadata: { contentType: "application/json" },
    });
    if (result) return next;
  }
  throw new APIError(409, "This narration changed. Please try again.");
}
async function allJobs(env) {
  let cursor;
  const jobs = [];
  do {
    const page = await env.BUCKET.list({ prefix: "meta/", limit: 1000, ...(cursor ? { cursor } : {}) });
    for (let start = 0; start < page.objects.length; start += 10) {
      const group = await Promise.all(page.objects.slice(start, start + 10).map(async (item) => {
        const body = await env.BUCKET.get(item.key);
        return body ? body.json() : null;
      }));
      jobs.push(...group.filter(Boolean));
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  return jobs;
}
async function readJSON(request) {
  if (Number(request.headers.get("Content-Length") || 0) > 65536)
    throw new APIError(413, "Request is too large.");
  try { return await request.json(); } catch { throw new APIError(400, "Invalid request."); }
}
function leaseOwned(job, workerId) {
  if (job.status !== "processing" || !workerId || job.workerId !== workerId)
    throw new APIError(409, "This worker no longer owns the narration.");
}
async function handleEngine(request, env, path) {
  requireEngine(request, env);
  if (path === "/api/engine/heartbeat" && request.method === "POST") {
    const data = await readJSON(request);
    if (typeof data.workerId !== "string" || data.workerId.length > 120)
      throw new APIError(400, "Missing worker identity.");
    let cancelRequested = false;
    if (data.activeJobId) {
      const job = await updateMeta(env, data.activeJobId, (job) => {
        leaseOwned(job, data.workerId);
        job.leaseExpiresAt = Date.now() + LEASE_MS;
        return job;
      });
      cancelRequested = !!job.cancelRequested;
    }
    await env.BUCKET.put("engine/heartbeat.json", JSON.stringify({
      lastSeen: new Date().toISOString(), activeJobId: data.activeJobId || null,
      stage: String(data.stage || "Ready").slice(0, 120), workerId: data.workerId,
    }));
    return json({ ok: true, cancelRequested });
  }
  if (path === "/api/engine/claim" && request.method === "POST") {
    const data = await readJSON(request);
    if (typeof data.workerId !== "string" || !data.workerId || data.workerId.length > 120)
      throw new APIError(400, "Missing worker identity.");
    const candidates = (await allJobs(env)).filter((job) =>
      job.status === "queued" || (job.status === "processing" &&
        (job.workerId === data.workerId || (job.leaseExpiresAt || 0) < Date.now()))
    ).sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    for (const candidate of candidates) {
      try {
        const job = await updateMeta(env, candidate.id, (current) => {
          if (!(current.status === "queued" || (current.status === "processing" &&
            (current.workerId === data.workerId || (current.leaseExpiresAt || 0) < Date.now()))))
            throw new APIError(409, "Job already claimed.");
          return { ...current, status: "processing", workerId: data.workerId,
            leaseExpiresAt: Date.now() + LEASE_MS, error: null,
            stage: current.completedChunks ? "Resuming narration" : "Preparing your voice" };
        });
        const scriptObject = await env.BUCKET.get(`jobs/${job.id}/script.txt`);
        if (!scriptObject) throw new APIError(500, "The saved script is missing.");
        return json({ job: { ...job, script: await scriptObject.text() } });
      } catch (error) { if (error.status !== 409) throw error; }
    }
    return json({ job: null });
  }
  const match = path.match(/^\/api\/engine\/jobs\/([^/]+)\/(reference|progress|complete|fail|cancelled|files\/(mp3|wav))$/);
  if (!match) throw new APIError(404, "Endpoint not found.");
  const [, id, action, format] = match;
  const { job } = await getMeta(env, id);
  if (action === "reference" && request.method === "GET") {
    const item = await env.BUCKET.get(`jobs/${id}/reference`);
    if (!item) throw new APIError(404, "Voice sample not found.");
    return new Response(item.body, { headers: { "Content-Type": "application/octet-stream", "Cache-Control": "no-store" } });
  }
  if (action.startsWith("files/") && request.method === "POST") {
    leaseOwned(job, request.headers.get("X-Worker-Id"));
    const length = Number(request.headers.get("Content-Length") || 0);
    if (!length || length > 100 * 1024 * 1024) throw new APIError(413, "Audio exceeds the 100 MB upload limit.");
    await env.BUCKET.put(`jobs/${id}/audio.${format}`, request.body, {
      httpMetadata: { contentType: format === "mp3" ? "audio/mpeg" : "audio/wav" },
    });
    return json({ ok: true });
  }
  if (request.method !== "POST") throw new APIError(405, "Method not allowed.");
  const data = await readJSON(request);
  if (action === "complete") {
    if (!(await env.BUCKET.head(`jobs/${id}/audio.mp3`)) || !(await env.BUCKET.head(`jobs/${id}/audio.wav`)))
      throw new APIError(409, "Both audio formats must finish uploading first.");
  }
  const updated = await updateMeta(env, id, (current) => {
    const terminalForAction = { complete: ['completed', 'cancelled'], fail: ['failed'], cancelled: ['cancelled'] };
    if (terminalForAction[action]?.includes(current.status) && current.lastWorkerId === data.workerId) return null;
    leaseOwned(current, data.workerId);
    if (action === "progress") {
      const completedChunks = Math.max(0, Math.floor(Number(data.completedChunks) || 0));
      const totalChunks = Math.max(0, Math.floor(Number(data.totalChunks) || 0));
      if (totalChunks > 2000 || completedChunks > totalChunks) throw new APIError(400, "Invalid chunk counts.");
      return { ...current, completedChunks, totalChunks,
        progress: Math.max(0, Math.min(0.99, Number(data.progress) || 0)),
        stage: String(data.stage || "Generating narration").slice(0, 160),
        durationSeconds: Math.max(0, Number(data.durationSeconds) || 0), leaseExpiresAt: Date.now() + LEASE_MS };
    }
    if (action === "complete") {
      if (current.cancelRequested) return { ...current, status: "cancelled", stage: "Cancelled", lastWorkerId: current.workerId, workerId: null, leaseExpiresAt: null };
      return { ...current, status: "completed", progress: 1, stage: "Ready to listen",
        durationSeconds: Math.max(0, Number(data.durationSeconds) || 0),
        completedChunks: Number(data.completedChunks) || current.completedChunks,
        totalChunks: Number(data.totalChunks) || current.totalChunks, lastWorkerId: current.workerId, workerId: null, leaseExpiresAt: null };
    }
    if (action === "fail") return { ...current, status: "failed", stage: "Generation stopped",
      error: String(data.error || "The engine could not finish. You can try again.").slice(0, 500), lastWorkerId: current.workerId, workerId: null, leaseExpiresAt: null };
    if (action === "cancelled") return { ...current, status: "cancelled", stage: "Cancelled", lastWorkerId: current.workerId, workerId: null, leaseExpiresAt: null };
    throw new APIError(404, "Endpoint not found.");
  });
  return json({ ok: true, cancelRequested: !!updated.cancelRequested, status: updated.status });
}

export async function fetchHandler(request, env) {
  const url = new URL(request.url);
  const path = url.pathname;
  try {
    if (path === "/" && request.method === "GET") {
      owner(request);
      return new Response(HTML, { headers: {
        "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin",
        "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob: https://raw.githubusercontent.com; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'",
      } });
    }
    if (path === "/favicon.ico") return new Response(null, { status: 204 });
    if (!env.BUCKET) throw new APIError(503, "Studio storage is starting. Please try again shortly.");
    if (path.startsWith("/api/engine/")) return await handleEngine(request, env, path);
    const userId = owner(request, env);
    if (request.method !== "GET") sameOrigin(request);
    if (path === "/api/status" && request.method === "GET") {
      const body = await env.BUCKET.get("engine/heartbeat.json");
      const state = body ? await body.json() : {};
      return json({ engine: { online: !!state.lastSeen && Date.now() - Date.parse(state.lastSeen) < 60000,
        lastSeen: state.lastSeen || null, activeJobId: state.activeJobId || null }, limits: LIMITS });
    }
    if (path === "/api/jobs" && request.method === "GET") {
      const jobs = (await allJobs(env)).filter((job) => job.ownerId === userId)
        .sort((a, b) => b.createdAt.localeCompare(a.createdAt)).slice(0, 50).map(publicJob);
      return json({ jobs });
    }
    if (path === "/api/jobs" && request.method === "POST") {
      if (Number(request.headers.get("Content-Length") || 0) > LIMITS.maxUploadBytes + 100000)
        throw new APIError(413, "Choose a voice sample smaller than 20 MB.");
      let form;
      try { form = await request.formData(); } catch { throw new APIError(400, "Upload a voice sample and a script."); }
      const reference = form.get("reference");
      const script = String(form.get("script") || "").replace(/\r\n?/g, "\n").trim();
      const preset = String(form.get("preset") || "balanced");
      const title = String(form.get("title") || "Untitled narration").trim().slice(0, 100) || "Untitled narration";
      if (!reference || typeof reference.arrayBuffer !== "function" || !reference.size)
        throw new APIError(400, "Choose a voice sample first.");
      if (reference.size > LIMITS.maxUploadBytes) throw new APIError(413, "Choose a voice sample smaller than 20 MB.");
      if (!/\.(wav|mp3|m4a|mp4|aac|flac|ogg|webm)$/i.test(reference.name || ""))
        throw new APIError(400, "Use a WAV, MP3, M4A, MP4, AAC, FLAC, OGG, or WebM file.");
      if (!script) throw new APIError(400, "Add the script you want to hear.");
      if (script.length > LIMITS.maxScriptChars || script.split(/\s+/).length > LIMITS.maxWords)
        throw new APIError(400, "Split this script into parts of at most 6,000 words and 40,000 characters.");
      if (!["balanced", "expressive"].includes(preset)) throw new APIError(400, "Choose a delivery style.");
      const active = (await allJobs(env)).filter((j) => j.ownerId === userId && ["queued", "processing"].includes(j.status));
      if (active.length >= 3) throw new APIError(429, "You already have three active narrations. Wait for one to finish.");
      const id = `${Date.now()}-${crypto.randomUUID()}`;
      const now = new Date().toISOString();
      const job = { id, ownerId: userId, title, preset, status: "queued", createdAt: now, updatedAt: now,
        referenceName: String(reference.name).slice(0, 200), progress: 0, completedChunks: 0,
        totalChunks: 0, durationSeconds: 0, error: null, stage: "Waiting for the engine", cancelRequested: false };
      try {
        await env.BUCKET.put(`jobs/${id}/reference`, reference.stream());
        await env.BUCKET.put(`jobs/${id}/script.txt`, script, { httpMetadata: { contentType: "text/plain; charset=utf-8" } });
        await env.BUCKET.put(`meta/${id}.json`, JSON.stringify(job), { httpMetadata: { contentType: "application/json" } });
      } catch (error) {
        await env.BUCKET.delete([`jobs/${id}/reference`, `jobs/${id}/script.txt`, `meta/${id}.json`]);
        throw error;
      }
      return json({ job: publicJob(job) }, 201);
    }
    const match = path.match(/^\/api\/jobs\/([^/]+)(?:\/(cancel|resume|audio\.(mp3|wav)))?$/);
    if (match) {
      const [, id, action, format] = match;
      const { job } = await getMeta(env, id);
      if (job.ownerId !== userId) throw new APIError(404, "Narration not found.");
      if (!action && request.method === "GET") return json({ job: publicJob(job) });
      if (format && request.method === "GET") {
        if (job.status !== "completed") throw new APIError(409, "This narration is not ready yet.");
        const range = request.headers.get("Range");
        const item = await env.BUCKET.get(`jobs/${id}/audio.${format}`, range ? { range: request.headers } : {});
        if (!item) throw new APIError(404, "Audio file not found.");
        const headers = new Headers({ "Content-Type": format === "mp3" ? "audio/mpeg" : "audio/wav",
          "Cache-Control": "private, no-store", "Accept-Ranges": "bytes",
          "Content-Disposition": `inline; filename="${job.title.replace(/[^a-zA-Z0-9 _-]/g, "").slice(0, 70) || "narration"}.${format}"` });
        let status = 200;
        if (range && item.range) {
          const offset = item.range.offset || 0;
          const length = item.range.length || (item.size - offset);
          headers.set("Content-Range", `bytes ${offset}-${offset + length - 1}/${item.size}`);
          headers.set("Content-Length", String(length)); status = 206;
        } else headers.set("Content-Length", String(item.size));
        return new Response(item.body, { status, headers });
      }
      if (request.method === "POST" && action === "cancel") {
        const updated = await updateMeta(env, id, (current) => {
          if (current.ownerId !== userId) throw new APIError(404, "Narration not found.");
          if (!["queued", "processing"].includes(current.status)) return null;
          return { ...current, cancelRequested: true, status: current.status === "queued" ? "cancelled" : "processing",
            stage: current.status === "queued" ? "Cancelled" : "Stopping after the current section" };
        });
        return json({ job: publicJob(updated) });
      }
      if (request.method === "POST" && action === "resume") {
        const updated = await updateMeta(env, id, (current) => {
          if (current.ownerId !== userId) throw new APIError(404, "Narration not found.");
          if (!["failed", "cancelled"].includes(current.status)) throw new APIError(409, "Only stopped narrations can be resumed.");
          return { ...current, status: "queued", cancelRequested: false, workerId: null, leaseExpiresAt: null,
            error: null, stage: "Waiting to resume" };
        });
        return json({ job: publicJob(updated) });
      }
    }
    throw new APIError(404, "Page not found.");
  } catch (error) {
    if (!(error instanceof APIError)) console.error("Voice studio request failed", error?.name || "Error");
    return json({ error: error instanceof APIError ? error.message : "Something went wrong. Please try again." }, error.status || 500);
  }
}
export default { fetch: fetchHandler };

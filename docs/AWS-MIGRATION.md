# Your own voice website on AWS: future migration guide

Yes, this project can become an independent website on your own domain, using
AWS compute and storage. ChatGPT would then be a development tool, not the
runtime that must remain awake. This guide is a plan for a later migration.
No AWS resources were created and the current ChatGPT site remains in use.

## Who can do it?

An assistant in a future coding session with this repository and your authorized
AWS access can implement and deploy the migration. A Python/cloud developer
can do the same from this code. You provide an AWS account, preferred region,
budget, domain choice, and approval for the billable infrastructure. Do not send
account passwords or secret access keys in chat; use a supported connection,
short-lived role or the AWS console.

The first task is to preserve the current service, build a separate AWS copy,
and verify it before any later decision about moving traffic. There is no need
to rebuild the voice model, interface or generation logic from scratch.

## A sensible first setup for personal use

Use one Linux EC2 instance, persistent EBS storage, and your domain with HTTPS.
For CPU-only generation, start by evaluating an x86-64 instance with at least
4 vCPUs and 16 GiB RAM, with roughly 50–100 GiB disk space depending on retained
audio. This gives headroom beyond the 2-core/8-GiB workspace that produced the
approved examples. Benchmark the actual instance before committing to a size.

Run a web API and the generation process under a supervisor or container restart
policy so they start after a reboot. Store the downloaded model on persistent
storage. Keep one generation worker per memory-sized model allocation. For
personal use, a single account/login and a durable SQLite job database on EBS
can be enough; the audio files can live on EBS initially, with backups in S3.
S3 can also become the primary storage as the app grows.

Route your domain to the server, use a reverse proxy such as Caddy for HTTPS,
and expose only the web ports. Restrict administration to your chosen secure
access method. A small free-tier machine is unlikely to fit this model well.

## What needs adapting

The saved code is the current working application. It is **not** a completed
AWS port or a one-click EC2 image.

| Current component | Future AWS version | Reusable code |
| --- | --- | --- |
| Static interface embedded in a Cloudflare Worker | Serve the same interface from the web API or a static host | `voice-studio/frontend.html` |
| Cloudflare Worker API | Python FastAPI or Node API with the same routes | Route behavior and tests in `worker/index.mjs` and `tests/` |
| ChatGPT sign-in and identity headers | Personal login/session authentication, or Amazon Cognito | Ownership rules and UI flow; replace header trust |
| Private Sites service bearer | Internal worker authentication under your deployment | Engine request wrapper; replace Sites-only header/config |
| R2 metadata/voice/audio objects | EBS plus SQLite for a single server, or S3 plus a database/queue | Job schema, leases, cancellation and progress behavior |
| Workspace polling engine | EC2 process/container with persistent data and automatic restart | `voice-studio/engine/worker.py` |
| Model and approved voice presets | Same model and settings on EC2 | `voice-tools/` and `requirements.lock.txt` |

Keep the existing `/api/jobs`, `/api/status`, downloads and engine API contract
where practical. Then the frontend and most of the engine work without a
redesign. Never trust a browser-supplied `oai-authenticated-user-id` header on
an independent AWS server; that header is only trusted in the current Sites
deployment because the hosting platform supplies it.

## Migration acceptance checklist

1. Build the replacement API, authentication and durable storage adapters.
2. Configure model paths and remove the worker's dependency on Sites service
   credentials. Keep secrets in deployment configuration or AWS secret storage.
3. Package a reproducible server/container setup. Run the existing tests and
   equivalent integration tests against the new API.
4. Generate a short sample using the approved settings. Check complete text,
   playback, MP3/WAV downloads and ownership isolation.
5. Test a multi-section job, cancellation/resume, process restart and server
   reboot. Saved sections must survive and not be generated twice unnecessarily.
6. Test a full-length narration and its file sizes. The current upload API has
   a 100 MiB limit per audio file; an AWS implementation should stream files
   and use multipart S3 upload for larger outputs. The current 6,000-word limit
   does not guarantee a particular duration.
7. Set backups, storage retention, logs and spending alerts; document restore.
8. Connect the domain and HTTPS, verify independently, and hand over access.
   Change the current ChatGPT site's role only if the owner later requests it.

## Costs and speed

The open-source voice model does not need a paid speech API. AWS still charges
for running compute, disks, public IPv4 addresses where applicable, storage and
outbound data. As a rough planning allowance, an always-on small 16-GiB CPU
server can put the total in the low hundreds of US dollars per month; this is
not a current regional price quote. Get an estimate for the selected instance,
hours and region from the [AWS Pricing Calculator](https://calculator.aws/).

For personal use, running the instance only when needed reduces compute hours.
The website/engine on that instance will be unavailable while it is stopped;
stored disks and some associated resources can still incur charges. Always-on
operation means paying for an always-on server or designing a separate
on-demand processing system later.

The observed workspace took roughly three seconds of processing per second of
audio for the short approved takes. Thirty minutes could therefore take around
1–2 hours on comparable CPU hardware. Different hardware, scripts and system
load change this. A GPU may speed it up but needs compatible CUDA packages and
a tested inference configuration; that GPU deployment is not included here.

## Later, if this becomes a public product

Separate the web app and generation workers; use S3 for audio, a durable queue
such as SQS, a database for job ownership and status, and autoscaled workers
with capacity limits. Add accounts, quotas, retention/deletion controls and
billing only when needed. The initial recommended scope is a private personal
website so you do not pay for or maintain that complexity immediately.

Useful official references:

- https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/concepts.html
- https://docs.aws.amazon.com/ebs/latest/userguide/what-is-ebs.html
- https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html
- https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-identity-pools.html
- https://aws.amazon.com/ec2/pricing/on-demand/

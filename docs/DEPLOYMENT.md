# Deployment

One container serves the API and the frontend. It needs a writable directory
that survives a restart, and two environment variables for the reviewer login.

Verified locally against Docker; see [VALIDATION.md](VALIDATION.md) for what was
actually observed. Nothing here claims a hosted deployment happened.

---

## Run it locally with Docker

```bash
docker compose up --build
```

Then open <http://127.0.0.1:8000>. With no `DEMO_USERNAME` set, `compose.yaml`
runs in development mode and the app is open — convenient locally, wrong for
anything reachable from the internet.

To rehearse the hosted configuration, including the login:

```bash
docker build -t clearledger:latest .
docker run -d --name clearledger \
  -p 8000:8000 \
  -v clearledger-data:/data \
  -e APP_ENV=production \
  -e DEMO_USERNAME=reviewer \
  -e DEMO_PASSWORD='choose-a-long-password' \
  clearledger:latest
```

Check it came up:

```bash
curl -u reviewer:choose-a-long-password http://127.0.0.1:8000/api/readiness
curl -u reviewer:choose-a-long-password http://127.0.0.1:8000/api/capabilities
```

`readiness` reports on the database and the data directory. `capabilities`
reports which extraction provider is active and whether OCR is available — worth
checking after any deploy, because a missing Tesseract silently changes what the
system can read.

## Environment variables

| Variable | Required | Value for a hosted deployment |
| --- | --- | --- |
| `APP_ENV` | yes | `production`. The app refuses to start in production without reviewer credentials. |
| `DATA_DIR` | yes | `/data`, and this must be the disk mount path |
| `DEMO_USERNAME` | yes | any name; it is a shared reviewer login |
| `DEMO_PASSWORD` | yes | at least 12 characters. There is no default. |
| `STATIC_DIR` | set in image | `/app/frontend/dist` |
| `PORT` | platform-supplied | the image reads `$PORT` |
| `EXTRACTION_PROVIDER` | no | `rules` (default). No network, no key. |
| `RATE_LIMIT_PER_MINUTE` | no | `60` by default; raise it if you plan to run the scenario script against the deployment |
| `ALLOWED_ORIGINS` | no | only needed if a browser on another origin must perform mutations |
| `OLLAMA_MODEL` / `LLM_*` | no | see [Model providers](#model-providers) |

The full annotated list is in `.env.example`.

## Deploying to Render

Render is the example because it offers a container host with a persistent disk.
Any host that can run a container with a durable volume works the same way.

**Read this before starting.** Render's own documentation is the authority, and
the two constraints that matter here are not negotiable:

- **A persistent disk requires a paid compute plan.** Free web services cannot
  attach one, and their filesystem is wiped on every restart, redeploy, and
  spin-down. On a free instance this application would lose every processed run.
- **A free web service spins down after 15 minutes without traffic** and takes
  about a minute to come back. A reviewer opening the link cold waits through
  that.

So: a free instance can demonstrate the application, but it cannot keep
anything. If the demonstration link must still hold its runs tomorrow, it needs
a paid plan with a disk.

### Steps

Render deploys from a git repository, so the code has to be on GitHub, GitLab
or Bitbucket first. There is no way to deploy a local folder.

**Option A — the blueprint (fewer things to get wrong).** `render.yaml` at the
repository root declares the service, the disk, the health check and the single
instance. **New → Blueprint**, pick the repository, and Render prompts for
`DEMO_USERNAME` and `DEMO_PASSWORD`. Adjust `region` and `plan` in the file
first if you want somewhere other than Singapore.

**Option B — by hand**, if you would rather see each setting:

1. **New → Web Service**, pointed at the repository, with **Docker** as the
   runtime. The `Dockerfile` at the root is complete; there is no build or start
   command to supply.
2. **Instance type**: any paid plan if you need the disk (see above).
3. **Advanced → Add disk**:
   - Mount path: `/data`
   - Size: 1 GB is ample — the database and a few PDFs
   - `/data` is a legal mount path; note that Render disallows `/`, `/opt`,
     `/etc`, `/home` and a few specific ancestors.
4. **Environment variables**: set `APP_ENV=production`, `DATA_DIR=/data`,
   `DEMO_USERNAME`, `DEMO_PASSWORD`. Do not commit the password anywhere.
5. **Health check path**: `/api/readiness`.
6. **Scaling**: leave it at one instance. SQLite has a single writer, so a
   second instance is a correctness problem, not a cost one. Attaching a disk
   enforces this anyway.

Either way, deploy and then confirm:

- the browser prompts for the reviewer credentials at the root URL;
- `/api/capabilities` reports `"ocr": {"available": true}` — the image installs
  Tesseract, so a `false` here means the wrong image is running;
- process the happy-path sample, then use the dashboard's **Manual Deploy →
  Restart** and reload. The run must still be there. That single check verifies
  the disk is mounted at the path the app is writing to, which is the mistake
  worth catching.

### Hosts that will not work

Worth stating, because the fit is not obvious from the outside:

- **Vercel, Netlify functions, and anything else serverless.** The filesystem is
  read-only apart from an ephemeral `/tmp`, so the database, the uploaded PDFs
  and the reservation ledger do not survive between requests. There is no
  Tesseract binary, so the scanned invoice cannot be read. The queue worker and
  the startup recovery in `backend/app/main.py` both assume a process that lives
  between requests, and serverless has none.
- **Any container host without a durable volume**, including a free Render
  instance, for the same persistence reason.

What is needed is unremarkable: a container, a writable disk, and one instance.
Fly.io and Railway both qualify.

### What is still yours to do

- Choose and record the reviewer credentials, and deliver them to the reviewer
  separately from the link.
- Create the account and the paid plan if durability is needed.
- Confirm the live URL and paste it into `docs/SUBMISSION.md`.

## Persistence and the restart check

Everything durable lives under `DATA_DIR`:

```
/data/clearledger.db      SQLite database, WAL mode
/data/uploads/            original PDFs, keyed by SHA-256
/data/tmp/                page rasters during OCR, deleted after each page
```

Reference data is re-seeded on every startup from the CSVs in the image. It is
an idempotent upsert, so restarting does not duplicate vendors or purchase
orders, and it does not touch processed runs.

Startup recovery runs immediately after seeding: any run left `RUNNING` by a
killed process is marked `INTERRUPTED`, and anything still `QUEUED` is
re-enqueued. Both counts are logged, so the startup log tells you what the
restart cost.

**The restart check, in full:**

```bash
curl -u user:pass https://YOUR-URL/api/dashboard   # note cases_processed
# restart the service from the host's dashboard
curl -u user:pass https://YOUR-URL/api/dashboard   # must be identical
```

If the number resets to zero, the disk is not mounted where `DATA_DIR` points.

## Access protection

HTTP Basic over the platform's TLS, on one shared reviewer credential. That is
proportionate for a demonstration link that must not be publicly indexable, and
it is not an identity system — see
[ASSUMPTIONS.md](ASSUMPTIONS.md) and the production notes in
[ARCHITECTURE.md](ARCHITECTURE.md#what-would-have-to-change-for-production).

What it covers:

- Every API route and **the frontend shell itself**. An unauthenticated request
  to `/` gets a 401 with a `WWW-Authenticate` header, so the browser shows its
  own credential prompt rather than a page whose requests all fail.
- Two routes stay open so the platform can probe the service: `/api/health` and
  `/api/readiness`. Neither exposes invoice data.

Alongside it: an origin check on state-changing requests, a per-client mutation
rate limit, upload type/size/page limits, and security headers. In production
mode the app refuses to start without credentials, so it cannot be deployed
open by accident.

## Model providers

The default `rules` provider needs no network and no key, which is why it is the
default for a hosted demonstration.

**Ollama** is not reachable from a hosted container unless you run one and expose
it. Set `OLLAMA_BASE_URL` and `OLLAMA_MODEL`. Nothing is downloaded
automatically: if the model is absent, `/api/capabilities` reports the exact
`ollama pull` command rather than fetching gigabytes at startup.

**An OpenAI-compatible endpoint** needs `LLM_BASE_URL`, `LLM_MODEL` and
`LLM_API_KEY`, set as secret environment variables on the host. The key stays
server-side; it is never sent to the browser and never appears in an export.

If a configured provider is unreachable, the run **fails with that error**. It
does not silently fall back to the rule-based parser, because a decision made by
a different extractor than the one you configured is a decision you cannot
reason about. `ALLOW_RULES_FALLBACK=true` opts into the fallback, and when it is
used the original failure and the substitution are recorded on the run.

## Sizing

Modest. One worker, SQLite, and PDF parsing; OCR is the only heavy step, and it
is capped by page count and a per-page timeout. 512 MB works for the samples;
1 GB is comfortable if OCR is exercised. A persistent disk of 1 GB holds the
database and a few thousand invoice PDFs.

Note that a disk restricts the service to a single instance and disables
zero-downtime deploys — the host stops the old instance before starting the new
one. For this application that is correct rather than a limitation: two
instances sharing one SQLite file is exactly what must not happen.

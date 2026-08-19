# CLAUDE.md

Context for Claude Code sessions in this repo. Read this before changing anything.

## What this is

**StepWise** — a web app that takes your sex, age, weight and home address and suggests
walking routes, each scored for *your* body: real elevation from a DEM, an honest time
estimate, calories, steps, and how much of the walk is on a footpath rather than in the
roadway.

It is a take-home exercise for a job at **Valid.co** (contact: michael@valid.co). The brief
was one line — *"Overture Maps publishes free, open map data. Build a web app product on top
of this data. Anything you want."* — and the vagueness was explicitly the point. **Do not
submit anything**; David submits it himself when he's ready.

Assignment site: https://valid-takehome-demo-mauve.vercel.app (password `CUGVvq19XXf3IFS`).

## Hard constraints (from David — do not quietly relax these)

| Constraint | Status |
|---|---|
| Public GitHub repo `davidplappert/validco` | done |
| Dedicated AWS sub-account named `validco-dev` | done — account `294321867788` |
| **Everything free tier** | see *Cost* below |
| **API Gateway + Lambda** for the backend | REST API + Lambda (ARM64, Python 3.13) |
| **S3 + CloudFront** for the frontend, AWS-issued domains only | done — no Route 53, no ACM |
| **Python everywhere, including CDK** | CDK app is Python; Lambda is pure-stdlib Python |
| **CDK + GitHub Actions the only deploy path** | OIDC role trusts `main` on this repo only |
| One environment (`dev`), deployed from `main` | one stack, `stepwise-dev` |
| **Logging as DEBUG as possible** | see *Observability* |
| X-Ray and CloudTrail enabled | both on |
| Keep this CLAUDE.md current **every prompt** | ← standing instruction |

## Layout

```
api/stepwise/        Lambda source. Pure standard library — no runtime pip deps.
  handler.py         API Gateway router (accepts payload format 1.0 and 2.0)
  routing.py         Time-bounded Dijkstra, loop generation, ranking
  health.py          The physiology model. Most heavily cited file here.
  graph.py           Runtime dataset access: CSR graph, spatial grid, geocode index
  geocode.py         Address parsing + street normalisation
  container.py       The .spw binary format (read AND write live here)
  config.py          Wire-format constants shared with the builder
  data/*.spw         Baked datasets, committed (~25 MB). Regenerate, don't hand-edit.
data/pipeline/       Offline build: Overture -> .spw. Never runs in Lambda.
infra/               Python CDK app (one stack)
web/                 Next.js 15 static export + Tailwind v4 + MapLibre GL
bootstrap/           One-time GitHub OIDC CloudFormation (already applied)
tests/               87 tests, all offline
```

## Commands

```bash
uv sync --all-groups                      # local env (Python 3.13)

uv run pytest -q                          # 87 tests, ~1s, no network
uv run ruff check api data infra tests
uv run ruff format api data infra tests

# Rebuild the datasets from Overture (~60s cold, extracts cached under data/cache/)
uv run python -m data.pipeline build
uv run python -m data.pipeline build --region sf -v

cd web && npm run build                   # emits web/out/ for CDK to upload

# Infra. Deploys normally happen ONLY via GitHub Actions on push to main.
AWS_PROFILE=validco-dev CDK_DEFAULT_ACCOUNT=294321867788 CDK_DEFAULT_REGION=us-east-1 \
  npx cdk synth --quiet
```

## Architecture, and why

**The data is baked, not queried.** A build step pulls the Overture themes for a region with
DuckDB, splits transportation segments at their connectors into a routable graph, samples
elevation, and packs it into flat little-endian arrays (`container.py`). The Lambda reads
those with `array.frombytes` — a memcpy — and caches them at module scope. There is no
database, no runtime S3 fetch, and nothing to keep warm. Cold start decodes only the columns
a given request touches.

**Routing budgets in seconds, not metres.** A 40-minute request on Nob Hill used to come back
as a 60-minute walk because a kilometre uphill is not a kilometre. The Dijkstra accumulates
predicted walking time using the same physiology as the final scoring, so the budget the
search enforces and the number shown to the user cannot disagree.

**Loops fall out of penalising the outbound edges.** One bounded search from the start
produces every reachable node; anchors are picked from it (real Overture places where
possible, spread across compass bearings); the return leg re-runs the search with outbound
edges made 4× more expensive. No explicit cycle search anywhere.

**The health model is the differentiator — treat its citations as load-bearing.**
`health.py` uses Minetti et al. (2002) for gradient cost (defined for *descent*, unlike the
ACSM equation, which goes unboundedly negative downhill) and Mifflin-St Jeor for resting
metabolism (ACSM's flat 3.5 mL/kg/min scales resting metabolism linearly with total mass and
is documented to overestimate substantially at high BMI). Cross-checked against the Compendium
of Physical Activities: a 2.5 mph flat walk comes out at 3.1 MET against a published 3.0.

Two subtleties already resolved — don't "fix" them back:
- The Minetti polynomial regresses **average** cost across speeds (2.5 J/kg/m level), not the
  paper's **minimum Cw** series (1.64 level). Its own minimum sits near −0.15, while the
  minimum-Cw series bottoms at −0.10. Both are correct statements about different curves.
- `Profile.height_cm` defaults to a population mean when not supplied, and `height_assumed`
  must keep propagating into the API response and the user-facing caveats.

## Regions

Two, because David's own address is in Illinois and the reference demo is San Francisco:

| key | area | nodes | edges | addresses |
|---|---|---:|---:|---:|
| `sf` | San Francisco, CA | 82,889 | 123,055 | 394,704 |
| `pia` | Peoria & Chillicothe, IL | 62,159 | 83,935 | 114,045 |

Adding a city is one entry in `data/pipeline/config.py:REGIONS` plus a rebuild. Nothing in
the routing or health code is city-specific.

David's test address — `100 N Main St, Chillicothe, IL 61523` — is real, present in
Overture, and used in the smoke test and the test suite. Chillicothe has almost no mapped
sidewalks, so routes there come back ~100% `road`; that is accurate data, not a bug.

## Deployment

`main` → `.github/workflows/deploy.yml` → OIDC into `github-actions-deploy` → `cdk deploy` →
smoke test (`/v1/health`, `/v1/regions`, and a real `/v1/plan`) → URLs in the job summary.
PRs run `ci.yml`, which has **no** AWS credentials — `cdk synth` needs none.

Repo variables (already set): `AWS_DEPLOY_ROLE_ARN`, `AWS_REGION`, `AWS_ACCOUNT_ID`.

The deploy role can *only* assume the four CDK bootstrap roles, and its trust policy is
pinned to `repo:davidplappert/validco:ref:refs/heads/main`. There are no static AWS keys
anywhere. David retains break-glass admin via `OrganizationAccountAccessRole` from the
management account (`aws sts get-caller-identity --profile validco-dev`) — that is
deliberate, not a gap.

The frontend cannot know the API URL at build time, so CDK writes `/config.json` into the
site bucket from the real CloudFormation values and the app fetches it at runtime. That file
is served with caching disabled; everything else is cached normally.

## Observability

Turned up well past what a service this size would run in production, because it was asked
for explicitly:

- Lambda `LOG_LEVEL=DEBUG`, one JSON object per line, every record carrying `request_id`,
  `trace_id` and `route` so CloudWatch Logs Insights can pivot without regex.
- API Gateway: access logs as JSON, plus `loggingLevel=INFO` **and** `dataTraceEnabled` —
  full request and response bodies. Only acceptable because the API takes no credentials and
  persists nothing.
- X-Ray active on the Lambda and the stage. **This is why the stack uses a REST API**: HTTP
  APIs are cheaper per call but support neither gateway X-Ray nor data-trace logging. Don't
  "optimise" it to an HTTP API without re-reading that requirement.
- CloudTrail management events, 30-day S3 lifecycle. Data events deliberately **off** — they
  bill per event and are the one thing here that could cost real money.
- Log retention capped at 14 days everywhere.

## Cost

Always-free: Lambda (1M req + 400k GB-s/mo), CloudFront (1 TB + 10M req/mo), X-Ray (100k
traces/mo), one CloudTrail management trail, CloudWatch Logs (5 GB ingest/mo).
Twelve-month tier only: **API Gateway REST** (1M calls/mo) and S3 (5 GB). After that the
realistic bill is cents. If cost ever matters, the lever is turning `dataTraceEnabled` off,
not shrinking the datasets.

## Conventions

- Lambda code stays **pure standard library**. No numpy, no boto3 in the request path, no web
  framework. This is what keeps cold starts fast and the package portable.
- Comments explain *why*, especially where a value comes from a paper or where an obvious
  simpler approach was rejected. Don't strip them.
- `container.py` and `geocode.normalize_street` are imported *by* the build pipeline. The
  dependency runs runtime → builder, never the reverse, so a rebuild can't be read with stale
  semantics. **Changing `normalize_street` requires rebuilding the address containers.**
- Tests are offline and fast; the API tests run the real handler against the real datasets.
- Region data is committed. Rebuild with the pipeline rather than editing `.spw` files.

## Status

Working end to end and deployed. Not yet done / worth considering:

- The elevation profile chart uses node-level samples; per-shape-point would be smoother.
- `PlaceIndex.within` is a linear scan (fine at 4k places, would need an index at 100k).
- Green-space proximity uses polygon centroids and an equivalent radius, not true geometry.
- No caching layer in front of the API — every plan recomputes. Fine at this traffic.

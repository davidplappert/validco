# StepWise

> **StepWise turns your body and a spare half hour into a walk worth taking —
> using Overture's open map data to find routes whose hills, surfaces and length
> actually suit you, and telling you honestly what they will do for your health.**

Enter your sex, age, weight and home address. StepWise suggests walking loops from
your door — each with real elevation from a DEM, an honest time estimate, the
calories and steps it will actually cost *you*, and how much of it you will spend
on a footpath rather than in the roadway.

<!-- deploy:urls:start -->
| | |
|---|---|
| **Web app** | _(published by the deploy workflow — see [Deployment](#deployment))_ |
| **API** | _(published by the deploy workflow)_ |
| **API reference** | [`openapi.yaml`](openapi.yaml) — OpenAPI 3.1 |
<!-- deploy:urls:end -->

Built as a take-home for [Valid](https://www.valid.co). The brief was one line:
*"Overture Maps publishes free, open map data. Build a web app product on top of
this data. Anything you want."*

---

## Why this

Overture's transportation theme has something most map products throw away: it
distinguishes a **sidewalk** from a **park path** from a **crosswalk** from a
**road with no sidewalk mapped at all**. For most routing that is noise. For
someone deciding whether a walk is worth taking, it is the whole question.

Combine that with a DEM and you can answer something genuinely useful: *given
this body and forty minutes, where should I walk, and what will it do for me?*

That inverts the usual routing problem. The destination is an **output**, not an
input, and the route has to come back to where it started.

### Two regions, deliberately

| Key | Area | Nodes | Edges | Addresses | Character |
|---|---|---:|---:|---:|---|
| `sf` | San Francisco, CA | 82,889 | 123,055 | 394,704 | Near-complete sidewalks, brutal terrain |
| `pia` | Peoria & Morton, IL | 62,159 | 83,935 | 114,045 | Sparse sidewalks, gentle bluffs |

San Francisco because it is the city in Valid's own reference demo, and because
it is a genuinely hard case. Peoria because a pipeline that only works on the
most atypical city in America is not a pipeline. Routes in Morton come back
close to 100% `road` — that is accurate data about a town with few mapped
sidewalks, not a bug, and the app says so rather than pretending otherwise.

Adding a city is one entry in `data/pipeline/config.py` plus a rebuild.

---

## The stack

Everything is serverless and everything is inside the AWS always-free tier.
Python end to end, including the infrastructure.

### Backend — [`api/`](api/)

| | |
|---|---|
| **Runtime** | Python 3.13 on AWS Lambda, ARM64 (Graviton) |
| **HTTP** | Amazon API Gateway (REST), Lambda proxy integration |
| **Dependencies** | **None.** Pure standard library. |
| **Layout** | `physiology/` → `models/` → `datasets/` → `services/` → `http/` → `controllers/` |

No web framework. The API is five routes over data already in memory, so FastAPI
or Flask would add cold-start time and a dependency tree to replace about forty
lines of dispatch. No numpy either — it would mean an architecture-specific build
for a handful of array reads.

The architecture is layered and object-oriented, with the weight in the models:
a `Profile` derives its own BMI, resting metabolism, gait speed and step length;
a `WalkEffort` computes itself from a route's shape; a `HealthReport` carries its
own caveats; a `Route` serialises itself. Controllers parse, delegate and
serialise — each is between 10 and 40 lines.

### Frontend — [`web/`](web/)

| | |
|---|---|
| **Framework** | Next.js 15 (App Router), static export |
| **UI** | React 19, Tailwind CSS v4 |
| **Map** | MapLibre GL with OpenStreetMap raster tiles — no API key, nothing billable |
| **Hosting** | S3 (private) behind CloudFront with Origin Access Control |

Next.js + React + Tailwind + MapLibre is the same stack Valid's own take-home
demo runs on. Exported statically, so there is no Node server, no Lambda@Edge and
nothing to keep running.

The API's URL is an AWS-issued `execute-api` domain that does not exist until the
stack deploys, so the bundle cannot bake it in. CDK writes a `/config.json` into
the site bucket from the real CloudFormation outputs, and the app reads it at
runtime — served with caching disabled, so a redeploy that moves the API takes
effect immediately.

### Infrastructure — [`infra/`](infra/)

AWS CDK, written in Python. One stack, one environment.

```
Browser ──> CloudFront ──> S3 (private, OAC)          the static export
Browser ──> API Gateway (REST) ──> Lambda (ARM64)     the routing and health API
```

Two AWS-issued domains. No Route 53, no ACM, no custom domain, nothing to renew.

### Data pipeline — [`data/`](data/)

Offline, runs on a laptop, never in Lambda. DuckDB reads Overture's GeoParquet
straight off public S3 and pushes the bounding-box filter down into the row-group
statistics, so extracting a city takes seconds rather than a planet download.

```
Overture (public S3, GeoParquet)
  └─ DuckDB + spatial  ──>  parquet cache
      └─ split segments at connectors  ──>  routable graph
          └─ sample USGS 3DEP elevation (AWS Terrain Tiles)
              └─ keep the largest connected component
                  └─ pack into flat binary arrays  ──>  api/stepwise/data/*.spw
```

The `.spw` files are committed (~25 MB). They ship inside the Lambda package, so
there is no database, no runtime S3 fetch and no cold-start download.

---

## How it works

### The graph

Overture's transportation theme is not a routable graph — it is **segments** with
a list of **connectors** positioned as fractions along them. The build splits each
segment at its connectors, classifies every resulting edge as path / sidewalk /
crossing / road, flags stairs and bridges and unpaved ground, drops anything
pedestrians are not allowed on, and keeps only the largest connected component
(97% of nodes) so no route can start on an island.

### Elevation

Every node is sampled from [AWS Terrain Tiles](https://registry.opendata.aws/terrain-tiles/) —
Terrarium-encoded PNGs derived from USGS 3DEP, free and unauthenticated. Sampled
bilinearly at z=14, which is the honest native resolution of the ~10 m source;
sampling finer would invent detail the DEM does not have.

### Routing

One bounded search from the start produces every reachable node. Anchors are
picked from that ball — real Overture destinations where available, spread across
compass bearings so four suggestions are not one suggestion four times. Each
anchor gets a second search back to the start **with the outbound edges
penalised**, which produces a genuine loop without ever searching for cycles.

The search is bounded by **predicted walking time, not distance**. A kilometre
uphill is not a kilometre, and an earlier version returned hour-long walks for
forty-minute requests on Nob Hill. Accumulating seconds with the same physiology
that produces the final estimate means the budget the search enforces and the
number shown to the user cannot disagree.

Planning takes **4 ms in Peoria and ~90 ms in San Francisco**, in pure Python.

### The health model

This is the part most walking apps get wrong by 30% and then state to three
significant figures. Every coefficient in [`api/stepwise/physiology/`](api/stepwise/physiology/)
and [`api/stepwise/models/health.py`](api/stepwise/models/health.py) traces to a
published source, and the places where the literature says the model is weak are
called out rather than papered over.

**Energy** uses [Minetti et al. (2002)](https://doi.org/10.1152/japplphysiol.01177.2001),
whose fifth-order gradient polynomial is defined for **descent** as well as
ascent. The usual ACSM walking equation is validated uphill only; applied to a
negative grade its vertical term goes unboundedly negative, which in San
Francisco means predicting that the downhill half of a loop refunds the calories
of the uphill half. `AcsmCostModel` is kept in the codebase purely so a test can
demonstrate that failure.

**Resting metabolism** is estimated separately with Mifflin-St Jeor rather than
folded in via ACSM's flat 3.5 mL/kg/min. That constant scales resting metabolism
linearly with *total* body mass, and adipose tissue is far less metabolically
active than muscle — which is why ACSM equations are documented to overestimate
expenditure substantially in people with obesity, precisely the users this
product is most useful to.

**Speed** comes from Bohannon & Andrews (2011) norms by age and sex, scaled by a
body-mass term (reduced preferred speed in obesity is a documented adaptation
that lowers joint loading, not a lack of effort), then scaled by gradient using
the *shape* of Tobler's hiking function normalised so flat ground is 1.0.

**Outcomes** are framed as progress toward published guidelines, never as
invented claims: WHO 2020's 150–300 weekly moderate minutes, and the 7,000
steps/day inflection from the 2025 *Lancet Public Health* dose-response
meta-analysis.

> **Validation.** A 2.5 mph flat walk comes out at **3.1 MET** against the
> Compendium of Physical Activities' published **3.0**. That check runs in CI.

Every response carries its own caveats — including a specific one when height was
assumed rather than supplied. None of it is medical advice, and it says so.

---

## Testing

**419 tests**, wired into CI on every push and pull request.

| Suite | Count | What it covers |
|---|---:|---|
| `tests/test_physiology.py` | 33 | Cost curves, gait norms, body composition — against published values |
| `tests/test_models.py` | 35 | Domain models, suitability rules, geometry |
| `tests/test_health.py` | 33 | The health model, validated against the literature |
| `tests/test_services.py` | 38 | Search, planning, scoring, geocoding — on the real datasets |
| `tests/test_http.py` | 36 | Payload normalisation, validation, routing, error mapping |
| `tests/test_api.py` | 27 | The handler end to end |
| `tests/test_infra.py` | 23 | The synthesised CloudFormation template |
| `tests/test_container.py` | 7 | Binary format round-trips |
| `tests/test_geocode.py` | 20 | Address parsing and normalisation |
| `tests/test_openapi.py` | 8 | The spec matches the implementation |
| `tests/test_security.py` | 20 | CSP, CORS, log redaction, IAM, input bounds |
| `tests/test_performance.py` | 10 | Cold start, query latency, response size |
| `web/tests/**` (Vitest) | 52 | Components, hooks, formatting, the API client |
| `web/tests/e2e/**` (Playwright) | 48 | The real bundle, desktop and mobile, under the real CSP |

A few of these are load-bearing in ways worth calling out:

- **The physiology tests assert against papers, not against the code.** A
  refactor that keeps everything working but breaks agreement with Minetti or
  Bohannon fails — nothing else in the system would notice.
- **The infrastructure tests pin the requirements.** X-Ray active, CloudTrail
  enabled, `DEBUG` log level, every log group given a retention, the bucket
  private. Removing any of them fails CI rather than quietly shipping.
- **The OpenAPI tests detect drift.** Adding a route without documenting it, or
  changing a guideline constant without updating the spec, both fail.
- **The CSP is tested in a browser, not just in the template.** A policy
  asserted only against CloudFormation is not asserted against a browser: the
  tile host was once listed under `img-src` but not `connect-src`, and because
  MapLibre fetches raster tiles with `fetch()` rather than as `<img>`, the map
  shipped blank while every test passed. `csp.spec.ts` now applies the deployed
  policy to the real bundle and fails on any violation.
- **The Playwright suite runs twice.** Against a local static export with the API
  stubbed (fast, deterministic), and — in the deploy workflow, after
  `cdk deploy` — against the real CloudFront distribution with **nothing
  stubbed**, which is what proves the deployment actually works rather than
  merely that CloudFormation succeeded.

```bash
uv run pytest                     # backend, ~2s, fully offline
cd web && npm test                # components and hooks
cd web && npm run test:e2e        # end-to-end against a local export
cd web && npm run test:all        # typecheck, unit, build, e2e
```

---

## Security

- **Content-Security-Policy** on every response. `script-src` must permit
  `'unsafe-inline'` — a static export emits inline RSC scripts and there is no
  server to mint a nonce — but the protections that matter survive:
  `connect-src` is restricted to this API, so an injected script cannot
  exfiltrate a user's address; `frame-ancestors 'none'` blocks clickjacking;
  `object-src 'none'` and `base-uri 'self'` close two classic vectors. Plus
  HSTS (one year, preload), nosniff and a strict referrer policy.
- **CORS is not a wildcard** — preflight allows only this deployment's own
  CloudFront origin. The request body carries a home address and health inputs.
- **Bounded everywhere**: 64 KB request-body cap before parsing, every numeric
  input range-checked, API Gateway throttled to 20 rps, Lambda capped at 25
  reserved concurrent executions.
- **Personal data is redacted from application logs** — addresses removed,
  weight and age bucketed, coordinates coarsened to 0.1°, while derived values
  like BMI survive so the logs stay diagnosable.
  One honest caveat, documented rather than hidden: API Gateway's
  `dataTraceEnabled` (on by explicit request) writes raw request bodies to its
  own log group, which app-level redaction cannot reach. Deploy with
  `trace_request_bodies=False` and the redacted logs become the only record.
- **Errors disclose nothing** — a 500 returns a correlation id, never a trace.
- **No static AWS credentials exist.** The deploy role is assumed via OIDC,
  pinned to this repository and branch, and can do nothing but assume the four
  CDK bootstrap roles. Its CloudFormation read access is scoped to this
  project's stacks.
- **CI has no AWS identity at all** — `contents: read`, no `id-token` — so a
  pull request from a fork cannot reach the account.

`tests/test_security.py` pins all of the above.

## Performance

Measured and profiled, with regression guards in `tests/test_performance.py`.

| | Before | After |
|---|---:|---:|
| Cold start — decode SF arrays, build index, load addresses | — | **~15 ms** |
| Plan a 30-minute walk (Morton) | 5.3 ms | **4.1 ms** |
| Plan a 40-minute walk (San Francisco) | 157 ms | **90 ms** |
| Plan a 90-minute walk (San Francisco) | 437 ms | **224 ms** |
| Frontend first-load JavaScript | 382 kB | **110 kB** |

Both wins came from profiling rather than intuition:

**The gradient is baked, and the physics is tabulated.** Each edge stores its
gradient as an int16 (decipercent) computed at build time. `CostModel` then
tabulates cost factor and *inverse* speed against gradient once per request, so
the Dijkstra inner loop — several hundred thousand iterations on a long request
— does two array indexes and two multiplications instead of a fifth-order
polynomial, an exponential, a power and two divisions. Maximum duration error
against direct computation is **0.17%**, comfortably inside the resolution of
the DEM the gradients came from.

**Destination snapping is bounded.** Choosing anchors used to call
`nearest_node` for every Overture place in range — around 2,000 lookups on a
90-minute San Francisco request, which the profiler put at 25% of wall time.
Candidates are now filtered to the anchor band, ranked by Overture's own
confidence, and capped at 160.

On the frontend, MapLibre (266 kB gzipped) is dynamically imported so it never
enters the first load; the tile host gets a `preconnect`; and `config.json` is
prefetched during mount rather than serially on the first submit.

### Should this have a database?

**No.** A single plan request settles tens of thousands of graph nodes. Any
datastore would turn that into network round trips, and even a generous 0.1 ms
per lookup would push a San Francisco request into the *tens of seconds*.
Holding the graph as flat arrays in the Lambda's own memory is not a shortcut
around a database — it is several orders of magnitude faster than one could be
for this access pattern, and `tests/test_no_database_needed` asserts exactly
that comparison.

A database would earn its place if the data were mutable, per-user, or too large
for a deployment package. It is none of those: 25 MB, derived offline from
public data, identical for every user. The moment any of that changes — user
accounts, saved routes, nationwide coverage — the answer changes with it.

## Observability

Turned up well past what a service this size would run in production, because it
was an explicit requirement.

- **Lambda** logs at `DEBUG`, one JSON object per line, every record carrying
  `request_id`, `trace_id` and `route` — so CloudWatch Logs Insights can pivot
  without regex.
- **API Gateway** writes JSON access logs and runs with `loggingLevel=INFO` and
  `dataTraceEnabled`: full request and response bodies. Acceptable only because
  the API takes no credentials and persists nothing.
- **X-Ray** is active on both the function and the stage. This is why the stack
  uses a **REST** API rather than an HTTP API — HTTP APIs are cheaper per call but
  support neither gateway tracing nor data-trace logging.
- **CloudTrail** records management events, with a 30-day lifecycle on its
  bucket. Data events are deliberately off: they bill per event and are the one
  thing here that could cost real money.
- **Log retention** is capped at 14 days everywhere.

The `request_id` in any error response is the correlation key across all three.

---

## Deployment

Push to `main`. That is the entire deployment interface.

```
push to main
  └─ backend: lint, format, 252 tests
  └─ frontend: typecheck, 52 tests, build, 30 e2e tests
      └─ OIDC into AWS  ──>  cdk diff  ──>  cdk deploy
          └─ smoke test /v1/health, /v1/regions, a real /v1/plan
              └─ Playwright against the live deployment
                  └─ URLs in the job summary
```

There is **no way to deploy from a laptop**. The `github-actions-deploy` role
trusts this repository's GitHub OIDC identity and nothing else, its trust policy
is pinned to `main` and to the `dev` environment (itself restricted to `main`),
and it holds no permissions beyond assuming the four CDK bootstrap roles. There
are no static AWS credentials anywhere — which matters rather a lot for a public
repository.

### One-time bootstrap

Already applied; documented for reproducibility.

```bash
cdk bootstrap aws://<account>/us-east-1 --profile validco-dev

aws cloudformation deploy \
  --template-file bootstrap/github-oidc.yaml \
  --stack-name stepwise-github-oidc \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides GitHubOrg=davidplappert GitHubRepo=validco \
                        DeployBranch=main DeployEnvironment=dev
```

### Cost

| Service | Free allowance | Notes |
|---|---|---|
| Lambda | 1M requests + 400k GB-s/month | **Always free** |
| CloudFront | 1 TB egress + 10M requests/month | **Always free** |
| X-Ray | 100k traces/month | **Always free** |
| CloudTrail | One management-event trail | **Always free** |
| CloudWatch Logs | 5 GB ingest/month | **Always free** |
| API Gateway (REST) | 1M calls/month | 12 months |
| S3 | 5 GB | 12 months; a few MB used |

After the twelve-month tiers lapse the realistic bill is cents per month. If cost
ever mattered, the lever would be turning `dataTraceEnabled` off — not shrinking
the datasets.

---

## Local development

```bash
uv sync --all-groups                     # Python 3.13
uv run pytest                            # backend tests

cd web && npm ci && npm run dev          # frontend at :3000, API at 127.0.0.1:8000

# Rebuild the datasets from Overture (~60s cold; the parquet cache makes reruns fast)
uv run python -m data.pipeline build
uv run python -m data.pipeline build --region sf -v
```

`CLAUDE.md` carries the working context: architecture decisions, the constraints
this was built under, and the subtleties worth not re-deriving.

---

## Attribution

Places, roads and addresses © [Overture Maps Foundation](https://overturemaps.org)
and © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors.
Elevation from USGS 3DEP via [AWS Terrain Tiles](https://registry.opendata.aws/terrain-tiles/).
Basemap tiles © OpenStreetMap contributors.

Estimates are for a healthy adult and are **not medical advice**.

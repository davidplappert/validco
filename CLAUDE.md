# CLAUDE.md

Working context for Claude Code sessions in this repo. Read this before changing anything.

## What this is

**StepWise** — a web app that takes your sex, age, weight and home address and
suggests walking routes, each scored for *your* body: real elevation from a DEM,
an honest time estimate, calories, steps, and how much of the walk is on a
footpath rather than in the roadway.

It is a take-home for a job at **Valid.co** (contact: michael@valid.co, role:
Principal Engineer). The brief was one line — *"Overture Maps publishes free,
open map data. Build a web app product on top of this data. Anything you want."*
— and the vagueness was explicitly the point.

**Do not submit anything.** David submits it himself when he is ready.

Assignment site: https://valid-takehome-demo-mauve.vercel.app (password `CUGVvq19XXf3IFS`).

## Hard constraints (from David — do not quietly relax these)

| Constraint | Status |
|---|---|
| Public GitHub repo `davidplappert/validco` | done |
| Dedicated AWS sub-account `validco-dev` | done — account `294321867788` |
| **Everything free tier** | see *Cost* in README.md |
| **API Gateway + Lambda** for the backend | REST API + Lambda (ARM64, Python 3.13) |
| **S3 + CloudFront** for the frontend, AWS-issued domains only | done — no Route 53, no ACM |
| **100% serverless — no Docker, no k8s** | explicitly reaffirmed; do not add containers |
| **Python everywhere, including CDK** | CDK app is Python; Lambda is pure-stdlib Python |
| **CDK + GitHub Actions the only deploy path** | OIDC role trusts `main` on this repo only |
| **NEVER deploy manually — CI/CD only** | hard rule; `cdk deploy` from a laptop is not allowed even to hotfix |
| One environment (`dev`), deployed from `main` | one stack, `stepwise-dev` |
| **Logging as DEBUG as possible** | see *Observability* |
| X-Ray and CloudTrail enabled | both on; pinned by `tests/test_infra.py` |
| **Very OOP; heavy models, thin controllers** | see *Architecture* |
| **Component-heavy frontend** | ~30 components, none over ~120 lines |
| **Inline docs on every method** | maintain this — every method has a docstring |
| **Thorough tests both sides, wired to CI/CD** | 698 tests |
| **Secure: endpoints, data, IAM, pipelines** | see *Security* |
| **No personal data in this public repo** | scrubbed; `tests/test_privacy.py` fails the build if it returns |
| **Optimised page load and query times** | see *Performance* |
| README explaining the tech, with links | `README.md` |
| `openapi.yaml` with full API detail | validated + drift-tested in CI |
| Keep this CLAUDE.md current **every prompt** | ← standing instruction |

## Layout

```
api/stepwise/          Lambda source. Pure standard library — no runtime deps.
  physiology/          Swappable strategies: energy cost, gait speed, anthropometry
  models/              Profile, WalkEffort, HealthReport, Route, Suitability, Region
  datasets/            Read-only access to the baked containers, lazily loaded
  services/            search, planner, scoring, geocoder
  http/                Request, Response, Router, typed error hierarchy
  controllers/         parse, delegate, serialise — nothing else
  container.py         The .spw binary format (read AND write live here)
  config.py            Wire-format constants shared with the builder
  logging_config.py    Structured JSON DEBUG logging
  handler.py           Wiring only: builds the Router, dispatches
  data/*.spw           Baked datasets, committed (~25 MB). Regenerate, don't hand-edit.
data/pipeline/         Offline build: Overture -> .spw. Never runs in Lambda.
infra/                 Python CDK app (one stack)
web/                   Next.js 15 static export + React 19 + Tailwind v4 + MapLibre GL
  src/components/      layout/ form/ map/ results/ health/ chart/ feedback/ regions/
  src/hooks/           useRegions, usePlanner, useRegionBuilder
  src/lib/             api.ts, types.ts, format.ts
  tests/               Vitest (components, hooks, lib) + Playwright (e2e)
bootstrap/             One-time GitHub OIDC CloudFormation (already applied)
tests/                 431 backend tests, all offline
openapi.yaml           OpenAPI 3.1, drift-tested against the router
```

## Commands

```bash
uv sync --all-groups                      # local env (Python 3.13)

uv run pytest                             # 431 tests, ~9s, no network
uv run ruff check api data infra tests
uv run ruff format api data infra tests

cd web && npm ci
cd web && npm run lint                    # ESLint 9 flat config (next/core-web-vitals + TS)
cd web && npm run lint:fix
cd web && npm run format:check            # Prettier, 100 columns
cd web && npm run format                  # rewrites; currently touches 30 files
cd web && npm test                        # 128 Vitest tests
cd web && npm run test:e2e                # 139 Playwright tests (needs `npm run build` first)
cd web && npm run test:all                # typecheck + unit + build + e2e
cd web && npm run build                   # emits web/out/ for CDK to upload

# `build` passes --no-lint on purpose. Next 15 runs ESLint during `next build`
# once ESLint is installed, so configuring it turned a lint finding into a
# build failure. Linting is its own step (`npm run lint`) and its own CI step;
# the build still type-checks.

# Rebuild the datasets from Overture (~60s cold; parquet cache makes reruns fast)
uv run python -m data.pipeline build
uv run python -m data.pipeline build --region sf -v

# Infra. Deploys normally happen ONLY via GitHub Actions on push to main.
AWS_PROFILE=validco-dev CDK_DEFAULT_ACCOUNT=294321867788 CDK_DEFAULT_REGION=us-east-1 \
  npx cdk synth --quiet
```

## Architecture, and why

**Layered OOP with the weight in the models.** `Profile` derives its own BMI,
resting metabolism, gait speed and step length; `WalkEffort` computes itself from
a route's shape; `HealthReport` carries its own caveats; `Route` serialises
itself. Controllers are 10–40 lines: parse, delegate, serialise. If a controller
starts computing something, that computation belongs in a model.

**The data is baked, not queried.** A build step pulls Overture with DuckDB,
splits transportation segments at their connectors into a routable graph, samples
elevation, and packs it into flat little-endian arrays (`container.py`). The
Lambda reads those with `array.frombytes` — a memcpy — and caches them at module
scope. No database, no runtime S3 fetch, nothing to keep warm.

**Routing budgets in seconds, not metres.** A 40-minute request on Nob Hill used
to come back as a 60-minute walk, because a kilometre uphill is not a kilometre.
The Dijkstra accumulates predicted walking time using the same physiology as the
final scoring, so the budget the search enforces and the number shown to the user
cannot disagree.

**Loops fall out of penalising the outbound edges.** One bounded search from the
start produces every reachable node; anchors are picked from it (real Overture
places where possible, spread across compass bearings); the return leg re-runs
the search with outbound edges made 4× more expensive. No explicit cycle search.

**Suitability is a list of rule objects**, each stating its own user-facing
reason. Adding a consideration is an append, not surgery on a conditional chain.

**The health model is the differentiator — its citations are load-bearing.**
Minetti (2002) for gradient cost (defined for descent, unlike ACSM which goes
unboundedly negative downhill); Mifflin-St Jeor for resting metabolism (ACSM's
flat 3.5 mL/kg/min scales linearly with total mass and is documented to
overestimate at high BMI). Cross-checked against the Compendium of Physical
Activities: a 2.5 mph flat walk gives 3.1 MET against a published 3.0.

**The form has no visible submit button.** It plans itself once input settles,
and the waiting mechanism is deliberately three different numbers rather than
one — `TYPING_DELAY_MS` 700 for free text, `ADJUSTING_DELAY_MS` 400 for numbers
and sliders, `SUGGEST_DELAY_MS` 180 for autocomplete. The whole form is
debounced as one snapshot so two field changes are one plan, while the *delay*
still depends on which field moved. `useDebouncedValue.ts` argues the numbers.

### Subtleties already resolved — do not "fix" these back

- **MapLibre's stylesheet sets `.maplibregl-map { position: relative }`**, and
  unlayered CSS outranks Tailwind v4's layered utilities *regardless of source
  order*. That silently overrode `absolute` on the map container, `inset-0`
  stopped sizing it, and the map shipped as a zero-height strip while every
  network request succeeded. `globals.css` imports MapLibre into a `maplibre`
  layer declared before `utilities` — do not "simplify" that import.
- **Numbered streets** are spelled out in Peoria's Overture data ("North SECOND
  Street") and zero-padded in San Francisco's ("03RD ST"). `StreetNormalizer`
  folds every form to a canonical numeric ordinal. Changing it means rebuilding
  the address containers.
- **Weight projections use Hall et al. (2011), not 3,500 kcal/lb**, for
  anything past a month — the static rule roughly doubles real one-year loss.
  They also use *net* calories. Both are load-bearing; `tests/test_health.py`
  pins them.

- The Minetti polynomial regresses **average** cost across speeds (2.5 J/kg/m
  level), not the paper's **minimum Cw** series (1.64 level). Its own minimum
  sits near −0.15; the minimum-Cw series bottoms at −0.10. Both are correct
  statements about different curves. `tests/test_health.py` pins this.
- `AcsmCostModel` exists **only** so a test can demonstrate why it is not used.
  Do not wire it into the router.
- `Profile.height_cm` defaults to a population mean when absent, and
  `height_assumed` must keep propagating into the API response, the caveats and
  the frontend's `OriginSummary`.
- `StreetNormalizer` has one implementation, imported *by* the build pipeline
  from the runtime. **Changing it requires rebuilding the address containers**,
  or every lookup silently misses.
- The house-number regex requires the suffix letter to be *adjacent* to the
  digits. Allowing a space made "100 N Main St" search for "Main Street"
  without its directional prefix.
- `ErrorPanel` carries `aria-label="Planning error"` because Next.js injects its
  own `role="alert"` route announcer; without the name, "the alert" is ambiguous.
- The stubbed Playwright specs skip when `E2E_BASE_URL` is set. Running them
  against a deployment would intercept the very API they were meant to verify;
  `deployed.spec.ts` covers that case un-stubbed.
- **A submit button still exists, `sr-only` until focused.** Deleting it broke
  Enter everywhere: HTML performs implicit submission only when a form has a
  submit button or exactly one field, and this one has nine. It is also the
  only way an assistive-technology user can say "now" to a form that otherwise
  submits on a timer. Do not remove it again.
- **`usePlanner` gates every state write on a sequence counter.** Two plans are
  routinely in flight once the form submits itself, and they do not land in
  order — a 40-minute request against a dense city outlasts the 20-minute one
  typed after it. Without the counter the stale response wins by arriving last.
- **A failed plan deliberately does not clear the previous result.** Half-typed
  addresses fail constantly while typing; blanking the map on each one makes the
  app flicker between working and broken.
- **The region is chosen by the locality in the query, not by the first geocode
  hit.** Street names repeat between towns: probing in registry order planned
  "100 W Third St, Kewanee, IL" in Peoria and said nothing about it.
  `test_a_walk_is_planned_in_the_town_that_was_asked_for` uses a street that
  exists in *both* regions, because one that does not cannot detect the bug.
- **`userEvent` and Vitest fake timers deadlock.** Every interaction hangs until
  the runner kills it, including a bare `click` on a component with no timers.
  `PlanForm.test.tsx` therefore runs on the real clock and costs about a second
  per test; that is the deliberate trade, not an oversight.
- **MapLibre fetches raster tiles with `fetch()`, not `<img>`.** The tile host
  must therefore appear in **both** `img-src` and `connect-src`. Listing it only
  in `img-src` shipped a blank map to production with hundreds of console
  violations, while every test passed — because none of them ran a real CSP.
  `web/tests/e2e/csp.spec.ts` now applies the deployed policy to the real bundle
  and fails on any violation; it was verified to fail when the bug is
  reintroduced.

## Security

Audited across endpoints, data, IAM and pipelines. `tests/test_security.py`
pins the results (20 tests).

- **CSP** on every CloudFront response. `script-src` must allow `'unsafe-inline'`
  — a Next.js static export emits inline RSC payload scripts and there is no
  server to mint a nonce — but `connect-src` is restricted to `'self'` plus the
  region's execute-api hosts, so an injected script still cannot exfiltrate the
  user's address. Plus HSTS (1 year, preload), `frame-ancestors 'none'`,
  `object-src 'none'`, nosniff, strict referrer policy.
- **CORS is not a wildcard.** Preflight allows only this deployment's own
  CloudFront origin. The body carries a home address and health inputs.
- **Request body capped** at 64 KB before parsing (`MAX_BODY_BYTES`); API
  Gateway alone would accept 10 MB. Every numeric input is range-bounded.
- **PII redaction in application logs** (`logging_config.PII_FIELDS`): address
  redacted, weight/age bucketed, coordinates coarsened to 0.1°, derived values
  like BMI kept so logs stay useful. `LOG_PII=1` disables it for a specific
  investigation.
  **Caveat that matters:** API Gateway `dataTraceEnabled` is on by explicit
  request and writes *raw* request bodies to its own log group, which this
  redaction cannot reach. `StepWiseStack(trace_request_bodies=False)` turns that
  off, at which point the redacted app logs are the only record.
- **Errors never leak internals** — 500s return `{"error": "internal error",
  "request_id": ...}` and nothing else. Tested.
- **Lambda reserved concurrency 25** plus API Gateway throttling (20 rps / 40
  burst): bounds both a flood and the bill.
- **IAM**: the deploy role can only assume the four CDK bootstrap roles, and its
  CloudFormation read access is scoped to `stepwise-*` and `CDKToolkit` rather
  than `*`. No static AWS credentials exist anywhere.
- **Pipelines**: `ci.yml` has `contents: read` and no `id-token`, so a fork PR
  gets no AWS identity. `deploy.yml` requests only `id-token: write` and
  `contents: read`. No `pull_request_target` anywhere.
- **Buckets** are private (all four public-access blocks), encrypted, and deny
  non-TLS requests.

## Performance

Measured, not assumed. `tests/test_performance.py` guards the numbers.

| | Before | After |
|---|---:|---:|
| Cold start (all SF arrays + grid + addresses) | — | **~15 ms** |
| Plan, Morton 30 min | 5.3 ms | **4.1 ms** |
| Plan, SF 40 min | 157 ms | **90 ms** |
| Plan, SF 90 min | 437 ms | **224 ms** |
| Frontend first-load JS | 382 kB | **110 kB** |

Two optimisations did the work, both found by profiling rather than guessing:

1. **Baked per-edge gradient + per-request lookup tables.** `edge_grade_dpct`
   (int16 decipercent) is computed at build time; `CostModel` tabulates cost
   factor and *inverse* speed against it once per request. The inner loop went
   from evaluating a fifth-order polynomial, an exponential, a power and two
   divisions to two array indexes and two multiplications. Max duration error
   versus direct computation: **0.17%**, far inside the DEM's own resolution.
2. **Bounded place snapping.** `AnchorSelector._places_near` was calling
   `nearest_node` for every destination in range — ~2,000 lookups on a 90-minute
   SF request, 25% of wall time. Now filtered to the anchor band, ranked by
   Overture confidence, capped at `MAX_PLACE_ANCHORS = 160`.

Frontend: MapLibre (266 kB gzipped) is dynamically imported so it stays out of
the first load; `preconnect` to the tile host; `config.json` is prefetched
during mount rather than serially on first submit.

**Should this have a database? No — and `test_no_database_needed` argues it
executably.** One plan settles tens of thousands of graph nodes. Any datastore
would turn that into round trips; even 0.1 ms per lookup would put an SF request
into the tens of seconds. Flat arrays in Lambda memory are orders of magnitude
faster for this access pattern, not a shortcut around a database. A DB would
start to make sense if the data were mutable, per-user, or too large for the
package — it is none of those (25 MB, rebuilt offline from public data).

## Privacy

This repository is public, so it must contain **no personal data**. It was
seeded during development with a real home address, coordinates and body
weight; all of it has been replaced with public fixtures and
`tests/test_privacy.py` fails the build if any of it reappears.

Fixtures now used, all civic or commercial:

- `100 N Main St, Morton, IL 61550` — a commercial main street
- `1100 California St, San Francisco` — Grace Cathedral, on Nob Hill for terrain
- `908 N Second St, Chillicothe, IL 61523` — Chillicothe City Hall, the address
  `PlanForm` is pre-filled with so the app works on load. Written with the
  ordinal spelled out because Overture stores "North Second Street" and
  `StreetNormalizer` does not fold "2nd"; abbreviated, the geocode misses.
- Synthetic profile: male, 45, 320 lb, 6'0" — still obesity class III, so the
  same code paths are exercised without describing a real person

**What the guard forbids, and what it does not.** Only things that point at one
household: the residential street name, the coordinates on its roof, the real
body weight. The town name and the postcode were banned outright at first and
are not any more — they are shared by several thousand people and by the town's
own public buildings, so the ban described nobody while making a civic default
address impossible.

**The history was rewritten**, on David's explicit instruction, with
`git filter-repo` over every commit and every commit *message*. Verified by
cloning the published repository fresh and scanning every blob in its object
database — not `git log -S`, which only sees blobs reachable by a path.

Three things about that are worth keeping:

- **The `.spw` containers are deliberately untouched.** They hold every street
  and road vertex in the region because that is what Overture publishes; it is
  public map data, not a disclosure. They are also offset-addressed, so
  substituting a shorter street name would not redact anything — it would
  corrupt every array after the edit. The scrub skips any blob starting with
  the `STEPWISE` magic, which is the same line `tests/test_privacy.py` draws.
- **The first pass missed the guard file itself.** The scrub rule required a
  word boundary before the name. In the guard the name sits inside a regex
  literal, directly after a `\b` escape — and since a backslash is not a word
  character, the escape's letter and the name form a single run with no
  boundary between them. Word boundaries are the wrong tool for scrubbing
  source that contains regexes, and the same mistake later turned up in this
  guard's own tokenizer, which is why it now hashes every substring rather
  than whole tokens. **Do not write the term itself into this file to
  illustrate the point** — that has been done twice, and both times it was the
  leak, not the explanation.
- **Old clones and GitHub's unreferenced objects still hold the old commits**
  until they are garbage collected. A rewrite is not a recall.

**The guard stores its terms as SHA-256 digests**, because a scanner holding
the street name in plain text is the leak it exists to prevent. It hashes
candidate tokens per line and compares digests. That introduces a failure mode
regexes do not have — a wrong digest passes everything forever, silently — so
two tests exist purely to prove the detectors fire.

## If cost were not a constraint

The free-tier rule shaped several decisions. Worth separating the ones that were
compromises from the ones that were simply correct, because a reviewer will ask.

**Would not change.** S3 as the artifact store is the right answer at any budget —
it is a blob store holding blobs. And the routing graph would stay as flat arrays
in process memory: a Dijkstra settling tens of thousands of nodes cannot make
network calls, so no database is fast enough at any price. That is physics, not
frugality.

**Would change.**
- **PostGIS on Aurora/RDS for geocoding, places and region metadata.** This is the
  real compromise. Today addresses are baked per region into sorted arrays, POI
  lookup is a linear scan, and "did you mean" is `difflib`. PostGIS gives GiST
  spatial indexes, `pg_trgm` for proper fuzzy text, and removes the need to rebuild
  a container to add one address. At continental scale the current design does not
  hold; at city scale it is genuinely faster.
- **Long-lived compute instead of Lambda** — ECS/Fargate — so a whole country's
  graph stays resident rather than being decoded per container. Lambda's cold start
  is fine here only because the datasets are small.
- **ElastiCache/Valkey** for hot region metadata and rate limiting, replacing the
  S3 status documents and the per-container TTL cache.
- **A real routing engine's preprocessing** — contraction hierarchies or CRP, as
  OSRM and Valhalla use — if coverage went national. Bounded Dijkstra is the right
  algorithm for a 40-minute walk in one city and the wrong one for a continent.
- **DynamoDB for the region catalogue** if build concurrency ever outgrew S3's
  conditional writes.

## Regions

| key | area | nodes | edges | addresses |
|---|---|---:|---:|---:|
| `sf` | San Francisco, CA | 82,889 | 123,055 | 394,704 |
| `pia` | Peoria & Morton, IL | 62,159 | 83,935 | 114,045 |

Those two are **bundled** — shipped inside the deployment package. Every other
city is **on demand**: typing an address outside a known region offers to build
it, a builder Lambda extracts it from Overture into S3, and the client polls a
progress bar until it is ready and the plan re-submits itself.

- `datasets/catalog.py` is the registry *and* the artifact store, both in S3.
  Concurrent requests for the same city are deduplicated by a conditional
  `PutObject` (`IfNoneMatch="*"`), so two people asking for Austin at the same
  second produce one build. A build that stops reporting progress for 20 minutes
  is presumed dead and may be reclaimed — without that, one killed Lambda would
  poison a city permanently.
- Polling backs off (1s / 2s / 4s / 5s by elapsed time): ~40 requests over two
  minutes rather than 120. Three *consecutive* failures are tolerated, because a
  dropped poll should not discard a build the server is still running.
- Requested areas are clamped (`MAX_AREA_SQ_DEG = 1.2`): nobody gets to ask for
  a country and time out the builder.
- **Autocomplete only consults regions already on local disk.** Reading
  `.addresses` on an on-demand region downloads it from S3, and suggestions fire
  per keystroke — see `RegionDatasets.is_local`.

Adding a *bundled* city is one entry in `data/pipeline/config.py:REGIONS` plus a
rebuild. Nothing in the routing or health code is city-specific.

The primary test address — `100 N Main St, Morton, IL 61550` — is real,
present in Overture, and used in the smoke test and the test suite. Morton
has almost no mapped sidewalks, so routes there come back ~100% `road`; that is
accurate data, not a bug.

## Testing

698 tests. Backend `uv run pytest`; frontend `cd web && npm run test:all`.

- 431 backend (physiology, models, health, services, http, api, infra, security,
  performance, container, geocode, openapi)
- 128 Vitest (components, hooks, formatting, API client)
- 139 Playwright: 45 specs on each of `chromium` and `mobile` (40 run, the five
  `deployed.spec.ts` ones skipped locally), plus 49 across seven viewport-named
  projects (`phone-small` 320x568 through `desktop-wide` 2560x1440) which run
  `responsive.spec.ts` only. All against a local static export with the API
  stubbed. `--repeat-each=3` was run to confirm no flakes.

Load-bearing properties, worth preserving:

- Physiology tests assert against **published papers**, not against the code.
- `tests/test_infra.py` pins X-Ray, CloudTrail, DEBUG, log retention, private
  bucket, throttling, and the `/config.json` no-cache behaviour.
- `tests/test_openapi.py` fails if a route is added without documentation, or if
  a guideline constant changes without the spec following.
- The deploy workflow runs `deployed.spec.ts` against the **live** deployment
  with nothing stubbed.
- `responsive.spec.ts` asserts the map container's **height**, not just its
  presence, at every viewport. The zero-height map was a real shipped bug and a
  presence check passes straight through it; the assertion was verified to fail
  when the collapse is reintroduced.

## Deployment

**Never run `cdk deploy` by hand.** Not to hotfix, not to test, not "just this
once" — push to `main` and let the workflow do it. A manual deploy skips the
test gate, can deploy a dirty tree, and makes the deployed SHA a lie. (This was
violated once, to push a CSP fix quickly; the fix was correct and the shortcut
was not.)

**Two CI traps already paid for, do not reintroduce either.**

`playwright install --with-deps` runs apt, and apt on these runners has sat on
an unreachable Ubuntu mirror until the step timed out — three deploys lost that
way. Caching cannot help: apt installs into the runner, not into
`~/.cache/ms-playwright`, and every matrix job is a fresh machine. The system
libraries were never needed — `ubuntu-latest` already ships what Chromium wants
— so the install asks for the browser and nothing else.

`paths-ignore` silently skips **every** run after a force push. GitHub computes
the filter by diffing the event's before and after SHAs, and after a history
rewrite the before SHA no longer exists, so nothing appears to have changed.
The rewrite landed on `main` and deployed nothing, which looks exactly like a
green pipeline. Use `workflow_dispatch` after any force push.

Every job carries a `timeout-minutes`, and the deploy concurrency group uses
`cancel-in-progress: true`. Both exist because a hung end-to-end step once held
the group for 57 minutes and queued every later push behind it.

`main` → `.github/workflows/deploy.yml` → backend tests + frontend tests →
OIDC into `github-actions-deploy` → `cdk diff` → `cdk deploy` → curl smoke test →
Playwright against the live site → URLs in the job summary.

PRs run `ci.yml`, which has **no** AWS credentials — `cdk synth` needs none.

Repo variables (already set): `AWS_DEPLOY_ROLE_ARN`, `AWS_REGION`, `AWS_ACCOUNT_ID`.

**OIDC subject gotcha, already hit and fixed:** a job that declares
`environment: dev` gets an OIDC subject of `repo:...:environment:dev`, *not*
`repo:...:ref:refs/heads/main`. The trust policy lists both, and the GitHub
`dev` environment is restricted to the `main` branch so the environment form
cannot be claimed from elsewhere.

David retains break-glass admin via `OrganizationAccountAccessRole` from the
management account (`aws sts get-caller-identity --profile validco-dev`). That is
deliberate, not a gap.

The frontend cannot know the API URL at build time, so CDK writes `/config.json`
into the site bucket from the real CloudFormation values and the app fetches it
at runtime. That file is served with caching disabled; everything else is cached.

## Observability

Deliberately far beyond what a service this size would run in production:

- Lambda `LOG_LEVEL=DEBUG`, one JSON object per line, every record carrying
  `request_id`, `trace_id` and `route`.
- API Gateway: JSON access logs, plus `loggingLevel=INFO` **and**
  `dataTraceEnabled` — full request and response bodies. Only acceptable because
  the API takes no credentials and persists nothing.
- X-Ray active on the Lambda and the stage. **This is why the stack uses a REST
  API**: HTTP APIs are cheaper per call but support neither gateway X-Ray nor
  data-trace logging. Do not "optimise" to an HTTP API.
- CloudTrail management events, 30-day S3 lifecycle. Data events deliberately
  **off** — they bill per event.
- Log retention capped at 14 days everywhere.

## Working style

**Parallelise with subagents.** David wants three or four running at once, not a
serial queue. Partition by *file ownership* and say so explicitly in each prompt —
concurrent edits to one file is the only real failure mode. Keep whole-repo or
destructive operations (history rewrites, force pushes) for the main session, and
run them only when no agent is mid-edit, since they need a clean tree.

## Conventions

- Lambda code stays **pure standard library**. No numpy, no boto3 in the request
  path, no web framework. That is what keeps cold starts fast.
- **Every method gets a docstring**, and comments explain *why* — especially
  where a value comes from a paper or an obvious simpler approach was rejected.
  Don't strip them; this is an explicit requirement.
- `container.py` and `services/geocoder.py` are imported *by* the build pipeline.
  The dependency runs runtime → builder, never the reverse.
- Region data is committed. Rebuild with the pipeline rather than editing `.spw`.
- Frontend components stay small and single-purpose; page.tsx is composition only.
- **The layout is a bottom sheet below `sm` and a floating side panel above it.**
  On a phone `main` is a flex column: the map pane takes 38% of the height and
  the panel takes the rest, so the panel can never hide the map. From `sm` the
  map pane goes back to `absolute inset-0` and the panel floats over its right
  edge, capped at 420 px on a tablet, 430 px from `lg` and 480 px from `2xl`.
  The legend lives *inside* the map pane, so it is positioned against the map
  rather than the viewport and cannot drift under the panel; it is hidden below
  `sm`.
- **Inside the panel, use container queries (`@sm:`), not viewport breakpoints.**
  The panel is roughly the same width on a tablet as on a phone, so a `sm:` rule
  pairs cards up exactly where there is least room. `Panel` is the `@container`;
  `HealthPanel` and `RouteStats` key off it.

## Status

Working end to end and deployed. Known gaps, none blocking:

- Elevation profile uses node-level samples; per-shape-point would be smoother.
- `PlaceIndex.within` is a linear scan (fine at 4k places; would need an index at 100k).
- Green-space proximity uses polygon centroids and an equivalent radius, not true geometry.
- No caching layer in front of the API — every plan recomputes. Fine at this traffic.
- Route scores can exceed 100 once bonuses stack; it is a ranking score, not a percentage.

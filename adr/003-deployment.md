# ADR 003 — Deployment: one container on Railway, deployed from GitHub Actions

Status: Proposed (2026-10-13). Implemented in stage 3; Accepted with measurements in stage 6.
Amended 2026-10-14: the IaC file moved from `railway.json` to
`.railway/railway.ts` (see "Amendment" below).

## Context

The assignment requires a public URL for UI and API, container-level
visibility for the reviewers, infrastructure as code, secrets outside the
repository, reproducible builds and an idempotent deploy. Cost is not a
criterion; practices are.

## Decision

- **One Docker image** serves the API and the static UI (FastAPI +
  built Vite assets). No separate frontend service, no database.
- **Railway** hosts it: one project, one service, one environment.
  `.railway/railway.ts` (Railway Infrastructure as Code, TypeScript) is the
  desired state of the whole project: the service, its build (Dockerfile
  builder), deploy policy (`/healthz` health check with a 60 s timeout,
  restart on failure up to 3 times, 1 replica in `us-west2`) and its
  variables (`PORT=8000`, the port the generated domain targets;
  `ANTHROPIC_API_KEY` declared `preserve()`, value never in the file).
  A resource or variable missing from the file is deleted on apply.
- **One pipeline changes production.** The GitHub Actions `deploy.yml`
  workflow runs after `ci` succeeds on `main` (`workflow_run`) with a
  project-scoped `RAILWAY_TOKEN`: `railway config apply`, then
  `railway config plan --detailed-exit-code` (fails if anything is still
  pending), then sets `ANTHROPIC_API_KEY` from GitHub secrets with
  `--skip-deploys`, then `railway up` builds the checked-out commit. It
  waits for `/healthz` to report that commit and runs the smoke eval
  against the public URL. The service has no source attached, so Railway
  never deploys on its own.
- **Destructive changes need a person.** Apply runs with `--yes` but
  without `--confirm-destructive`, so a merge that deletes a resource or
  variable fails the deploy before anything changes. A maintainer reads
  the plan and re-runs `deploy` by hand with `allow_destructive`.
- **Plan on review.** The `infra` CI job type-checks `railway.ts` on every
  run and, on pull requests from this repository, prints the production
  plan to the job summary.
- **The build SHA is baked into the image.** CI writes the commit to
  `recipe_qa/BUILD_SHA` in the build context (`railway up` has no
  build-arg flag) and `/healthz` reads it. Railway injects service
  variables at runtime, so a `GIT_SHA` variable could make a restarted old
  container claim a newer commit; a file in the image cannot.
- **Secrets** (`ANTHROPIC_API_KEY`, `RAILWAY_TOKEN`) live only in GitHub
  Actions secrets and Railway variables.
- **Reviewer access**: Viewer role on the Railway project (read-only
  logs, deployments, container status; no variables).
- **Rollback**: `deploy.yml` accepts `workflow_dispatch` with a commit SHA;
  documented in the README, not automated. The infrastructure rolls back
  with the code, since the commit's own `railway.ts` is applied.

## Alternatives considered

| Option | Why not |
|---|---|
| Fly.io (`fly.toml` + `flyctl deploy`) | Equivalent fit; Railway chosen because the account and viewer-invite flow already exist for this project. Switching cost is one IaC file and one CI step. |
| `railway.json` (Railway Config as Code) | Deprecated by Railway; services created after 2026-08-28 never read it and existing files stop being read on 2026-12-01. It also covers one service's build/deploy settings only, not variables or the project, and has no plan/diff. |
| Terraform with the community Railway provider | No official provider; the community one lags the platform. Railway's own IaC is first-party and has plan/apply. |
| Railway's `railwayapp/config` workflow (plan on PR, apply on merge) | A second workflow that changes production on merge, racing `deploy.yml`. Applying inside `deploy.yml` keeps one serialized path. |
| `railway.ts` in Python or Go | Beta at the time of writing; TypeScript is generally available, and Node is already in the repository for `web/`. |
| Render (`render.yaml`) | Equivalent fit; free tier sleeps, which would hurt the latency measurement. |
| Railway's own GitHub integration (auto-deploy on push) | Convenient, but then the deploy runs outside our pipeline: no SHA check, no post-deploy eval, two systems that can both deploy. |
| Separate frontend (static hosting) + API | Two deploys, CORS, two URLs to keep in sync — nothing gained for a one-page UI. |
| Kubernetes / Terraform on a cloud provider | Right for a team service, wrong for a 7-hour assessment; recorded in README "production gaps". |

## Criteria

IaC in repo; deploy without UI steps; idempotent re-deploy; secrets outside
repo; reviewer visibility; time to first public deploy (< 1 hour).

## Measurements

_To be filled: deploy duration from push to healthy; cold start time;
image size._

## Consequences

- Deploy is one path driven by CI: apply, check the plan is empty, then
  `railway up`. Running it twice for the same SHA yields the same service.
- A commit without `.railway/railway.ts` cannot be deployed by the
  workflow (it fails fast): rollback reaches back only to this amendment.
- Railway's generated domain (`recipe-qa-production.up.railway.app`) is not
  expressible in `railway.ts`; it exists outside the file and the file does
  not delete it.
- The CI plan job runs pull-request code with the production token. Only
  branches of this repository get it (people who can already change the
  workflows); forks and Dependabot get no plan.
- Single instance: a redeploy causes a short restart; acceptable for the
  assessment, recorded as a gap.

## This decision becomes invalid when

- Traffic needs more than one instance or zero-downtime deploys → add a
  health-checked rolling deploy (Railway supports it) or move to Fly with
  multiple machines.
- The UI needs its own release cadence → split the static site out.
- Compliance requires a specific region or VPC → Railway's region options
  may not suffice; revisit the provider.

## Amendment 2026-10-14: `railway.json` → `.railway/railway.ts`

Stage 3 shipped `railway.json` as the IaC file. On 2026-10-14
`railway config migrate status` reported that no service in production
reads Config as Code, and `railway config pull` showed the live service
with no health check, no restart policy and the Railpack builder. That
matches Railway's rule that services created from 2026-08-28 cannot opt
into Config as Code (ours was created in stage 3): none of the file had
ever applied. Images were still built
from the Dockerfile only because Railway auto-detects one at the root.

The first plan of `.railway/railway.ts` against production was exactly
that gap (builder, health check, restart policy) plus deleting the
`GIT_SHA` variable, now replaced by the baked-in `BUILD_SHA`.

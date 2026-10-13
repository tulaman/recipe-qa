# ADR 003 — Deployment: one container on Railway, deployed from GitHub Actions

Status: Proposed (2026-10-13). Implemented in stage 3; Accepted with measurements in stage 6.

## Context

The assignment requires a public URL for UI and API, container-level
visibility for the reviewers, infrastructure as code, secrets outside the
repository, reproducible builds and an idempotent deploy. Cost is not a
criterion; practices are.

## Decision

- **One Docker image** serves the API and the static UI (FastAPI +
  built Vite assets). No separate frontend service, no database.
- **Railway** hosts it: one project, one service, one environment.
  `railway.json` in the repository defines the build (Dockerfile) and the
  deploy policy (`/healthz` health check, restart on failure, 1 replica).
  The GitHub Actions `deploy.yml` workflow runs after `ci` succeeds on
  `main` (`workflow_run`), uses the Railway CLI with a project-scoped
  `RAILWAY_TOKEN`, then waits for `/healthz` to report the deployed commit
  SHA and runs the smoke eval against the public URL.
- **Service variables are set by the pipeline**, not by hand:
  `ANTHROPIC_API_KEY` (copied from GitHub secrets), `PORT=8000` (the port
  the generated domain targets) and `GIT_SHA` (a Docker build arg that
  `/healthz` reports), all with `--skip-deploys` so only `railway up`
  creates a deployment. The service has no GitHub source attached, so
  Railway never deploys on its own.
- **Secrets** (`ANTHROPIC_API_KEY`, `RAILWAY_TOKEN`) live only in GitHub
  Actions secrets and Railway variables.
- **Reviewer access**: Viewer role on the Railway project (read-only
  logs, deployments, container status; no variables).
- **Rollback**: `deploy.yml` accepts `workflow_dispatch` with a commit SHA;
  documented in the README, not automated.

## Alternatives considered

| Option | Why not |
|---|---|
| Fly.io (`fly.toml` + `flyctl deploy`) | Equivalent fit; Railway chosen because the account and viewer-invite flow already exist for this project. Switching cost is one IaC file and one CI step. |
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

- Deploy is one command path (`railway up`) driven by CI; running it twice
  yields the same service.
- Single instance: a redeploy causes a short restart; acceptable for the
  assessment, recorded as a gap.

## This decision becomes invalid when

- Traffic needs more than one instance or zero-downtime deploys → add a
  health-checked rolling deploy (Railway supports it) or move to Fly with
  multiple machines.
- The UI needs its own release cadence → split the static site out.
- Compliance requires a specific region or VPC → Railway's region options
  may not suffice; revisit the provider.

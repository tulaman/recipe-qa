// Desired state of the Railway project (ADR 003). `railway config plan` diffs
// this file against the live environment; `railway config apply` converges it.
//
// This repository owns the whole project, so there is no `partial` export:
// a resource missing from this file is a resource the next apply deletes.
import { defineRailway, preserve, project, service } from "railway/iac";

export default defineRailway(() => {
  const app = service("recipe-qa", {
    // No source: the service never deploys on its own. deploy.yml uploads
    // the checked-out commit with `railway up` after CI passes.
    build: {
      // Railway would also pick the root Dockerfile by auto-detection; say it
      // explicitly so a moved Dockerfile fails loudly instead of falling back
      // to Railpack.
      builder: "DOCKERFILE",
      dockerfilePath: "Dockerfile",
    },
    deploy: {
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 3,
    },
    // Railway switches traffic only after /healthz answers 200. It loads the
    // corpus and index from disk, well inside the 10 s cold-start budget
    // (SPEC §8); 60 s leaves room for a slow container start.
    healthcheck: "/healthz",
    healthcheckTimeout: 60,
    replicas: { "us-west2": 1 },
    env: {
      // The generated domain targets this port.
      PORT: "8000",
      // Secret: the value lives in GitHub Actions secrets and Railway only.
      ANTHROPIC_API_KEY: preserve(),
      // Set by deploy.yml on every deploy; kept as-is here until the SHA
      // moves into the image build.
      GIT_SHA: preserve(),
    },
  });

  return project("recipe-qa", {
    // Variables not declared above are removed on apply.
    variables: { managed: true },
    resources: [app],
  });
});

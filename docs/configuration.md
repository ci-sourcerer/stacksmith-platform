# Configuration

Settings use the `STACKSMITH_PLATFORM_` environment prefix and optionally a local `.env`. Old `COMPOSER_` variables are not recognized. Restart API and workers after changing deployment settings.

## Authoring settings

| Setting suffix | Purpose | Default |
| --- | --- | --- |
| `STACKSMITH_COMMAND` | Separate CLI executable in local authoring mode | `stacksmith` |
| `CONFIG` | Bootstrap configuration path or GitHub `git+https` reference; hosted policies are managed under Settings > Administration > Platform defaults | `examples/platform.yaml` |
| `SAMPLE` | Local demo stack | `examples/stack.yaml` |
| `MODULE_SOURCE_PREFIX` | Trusted prefix stripped from displayed module paths | Unset |
| `COMMAND_TIMEOUT` | Isolated/local inspection timeout in seconds | `30` |
| `INSPECTION_WAIT_TIMEOUT` | Overall inspection wait limit in seconds, including queueing and startup | `COMMAND_TIMEOUT + 60` |
| `PUBLIC_URL` | Browser origin and GitHub OAuth callback base | `http://127.0.0.1:5173` |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | GitHub App user authorization | Unset |
| `GITHUB_APP_SLUG` | App identity for installation links when private-key credentials are unavailable | Unset |
| `SECRET_KEY` | Stable snapshot signing and database encryption secret | Random only in local demo |
| `DATABASE` | SQLite path when platform execution is disabled | `.data/platform.sqlite3` |
| `DRAFT_PULL_REQUESTS` | Open draft rather than regular PRs | `false` |

GitHub mode requires a stable `SECRET_KEY` of at least 32 characters. The callback remains `/api/auth/callback`. Local mode retains the Composer editor and uses a separately installed Stacksmith binary; the hosted execution mode never invokes that binary in the API process.

## Execution settings

| Setting suffix | Purpose | Default |
| --- | --- | --- |
| `EXECUTION_ENABLED` | Enable authenticated platform execution | `false` |
| `DATABASE_URL` | PostgreSQL SQLAlchemy URL using `postgresql+psycopg://` | Required for execution |
| `ADMIN_LOGINS` | JSON array of bootstrap GitHub administrators | Required for execution |
| `GITHUB_APP_ID` / `GITHUB_PRIVATE_KEY` | Background installation-token issuance; `GITHUB_PRIVATE_KEY_FILE` reads a mounted PEM instead | Required for execution |
| `GITHUB_WEBHOOK_SECRET` | HMAC authentication for GitHub events | Required for execution |
| `CA_BUNDLE` | Optional absolute host path to a PEM bundle of additional CAs for Compose services and Docker-launched runs | Unset |
| `EXECUTION_IMAGE` | Named `@sha256:` execution image; Docker also accepts local `sha256:` IDs | Required for execution |
| `EXECUTION_CALLBACK_URL` | API origin reachable from execution containers | Falls back to public URL |
| `RUNNER` | `docker` or `kubernetes` | `docker` |
| `SUPERVISION_MODE` | Persistent `worker` or temporary `kubernetes-jobs` supervision | `worker`; Helm selects `kubernetes-jobs` |
| `WORKER_ID` | Optional stable supervisor identifier | Container hostname for PID 1; otherwise random per worker start |
| `INSPECTION_CONCURRENCY` | Maximum concurrent isolated inspections per Compose worker; positive integer | `4` |
| `PHASE_TIMEOUT` | Execution deadline in seconds | `3600` |
| `APPROVAL_TTL` | Approval/private-handoff lifetime, at most 24 hours | `86400` |
| `ARTIFACT_RETENTION` | Review-artifact lifetime in seconds | `2592000` |
| `EXECUTION_CPU` / `EXECUTION_MEMORY` | Kubernetes execution limits | `1` / `2Gi` |
| `DOCKER_MEMORY` / `DOCKER_NETWORK` | Docker memory limit and network | `2g` / `stacksmith-execution` |
| `EXECUTION_NAMESPACE` | Kubernetes execution namespace | `stacksmith-runs` |
| `EXECUTION_SERVICE_ACCOUNT` | Restricted run Pod service account | `stacksmith-execution` |

## Artifacts and credential references

`ARTIFACT_ENDPOINT`, `ARTIFACT_REGION`, `ARTIFACT_BUCKET`, `ARTIFACT_ACCESS_KEY`, and `ARTIFACT_SECRET_KEY` configure private S3-compatible storage. The region defaults to `us-east-1`, and the bucket defaults to `stacksmith-platform`. A blank endpoint selects the AWS S3 service. Use a private bucket; presigned phase handoffs must be reachable from run containers.

`SECRET_REFERENCES` is an optional JSON object mapping administrator-defined reference names to secret values supplied by the deployment. Environment bindings select either one reference or one entered value and a set of phases. Entered values are encrypted and never returned by read APIs. Credential changes invalidate pending approvals through the environment revision. Binding names cannot override runner, source-locking, Git, or Python process controls.

For Compose deployments, `CA_BUNDLE` points to an absolute host path containing additional PEM certificates. The file is mounted read-only into the API and worker and into Docker-launched run containers. The platform combines it with the existing public CA bundle, so public roots remain trusted. Leave it unset when no additional CA certificates are needed. Helm/Kubernetes deployments do not use this Compose-only setting.

See [Deployment](deployment.md) for Compose files, Kubernetes Secrets, database roles, runtime image builds, and endpoint routing.

[Settings](settings.md#repository-configuration) manages repository discovery and approved configuration layers at runtime. **Effective configuration** shows authored layer metadata; Composer **Resolution** inspects merged values for the draft and selected context. Saving a discovery filter does not change the App's repository permissions or enable deployments.

## Compose configuration

The complete Compose template is [`.env.example`](https://github.com/ci-sourcerer/stacksmith-platform/blob/main/.env.example). `poe configure` merges it into a private `.env` and generates missing secrets. Compose maps a selected set of values into containers and supplies database URLs and internal endpoints itself. Build-only settings such as `STACKSMITH_PLATFORM_STACKSMITH_IMAGE`, published-port settings, and separate PostgreSQL role passwords remain on the host. See [Deployment](deployment.md) for the setup workflow.

Credential managers can search the configured deployment secret reference names in Environment credentials. The picker lists names only; secret values remain private. Use Refresh secrets to reload the list after deployment configuration changes. Existing bindings display their reference name or indicate a stored secret value.

Additional secret sources, artifact stores, and execution drivers are configured through installed integrations. See [Platform integrations](integrations.md) for AWS tag discovery, credential setup, and plugin contracts.

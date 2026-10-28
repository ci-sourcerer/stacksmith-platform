# Deployment

The first release targets one trusted team. Use the same API and worker codebase with either Docker Compose or Kubernetes. Configure a same-origin HTTPS public endpoint for hosted use. The frontend container serves Composer at `/` and the run dashboard at `/runs`, and proxies API requests to the private FastAPI container.

## GitHub App

Configure user authorization at `<public-url>/api/auth/callback` and the webhook URL at `<public-url>/api/v1/webhooks/github`. Subscribe to push and pull-request events. Set a distinct webhook secret. The App needs repository Contents read/write, Pull requests read/write, and Metadata access. Install it on repositories that administrators will register and on approved private configuration/module repositories.

Repository discovery defaults to matching custom property `stacksmith=stack` or topic `stacksmith-stack`. Personal-account installations use topics; custom properties are available for organization repositories. In **Settings > Account > GitHub connection**, property controls are disabled unless an organization installation is available to the signed-in user. Unavailable custom property metadata does not block repository discovery or topic matching.

Set the App client ID/secret, App ID, and PEM private key. Store the stable platform secret securely and back it up with the database; changing it prevents decryption of stored credentials. `STACKSMITH_PLATFORM_ADMIN_LOGINS` bootstraps administrators. GitHub sign-in alone grants no deployment capability.

The trusted reverse proxy must forward the browser Host and scheme. Uvicorn's `FORWARDED_ALLOW_IPS` controls which proxy addresses may supply forwarded headers; the supplied Compose example and private-Service Helm deployment trust their fronting proxy. Set the Helm `forwardedAllowIps` value or the environment variable to explicit proxy addresses when needed. Correct scheme forwarding prevents OAuth redirect loops behind TLS termination.

Open **Settings** to choose the active workspace, configure deployments, grant permissions, manage credentials, and inspect execution health. **Account > GitHub connection** contains App installation, repository access, discovery filters, and administrator refresh controls. **Workspace** selects the repository, Composer stack, and inspection environment used by Composer and repository settings. Newly discovered repositories inherit platform configuration and start with deployments disabled; administrators enable them under **Repository > Deployment settings**. Platform defaults, GitHub connection, audit log, and execution health do not require a selected repository.

Choose a repository and environment directly in **Runs**. Its selection starts from your workspace once, then is remembered separately for your account in this browser. Runs links use `repo` and `environment` query parameters without changing Composer's saved workspace. **All environments** shows repository history; choose a specific active environment to start a run. Changing repository selects its sole active environment automatically, or All environments when several exist. Inactive environments retain their history but cannot receive new runs.

The compact run form groups **Infrastructure** and **Operations** under **Include**. Enable **Apply after approval** to request changes after reviewing a plan, or **Plan to destroy** for removal plans. Changing target clears operation selections, apply/destruction flags, and administrator overrides. Commit and local-backend overrides remain under administrator-only **Advanced run options**.

Administrators can open **Settings > Administration > Audit log** to review run, configuration, and account events across the platform. Filter by repository, run ID, actor, or event; filters and pagination are retained in the URL. Run details link administrators to the filtered audit page. Both the platform audit API (`GET /api/v1/audit`) and the per-run audit API require an administrator browser session; ordinary users and API tokens cannot read audit events. Audit metadata excludes secret values.

Release the audit API and frontend together with database migration `0003`. Apply database migrations before starting the updated API and workers; the migration backfills repository scope on existing audit events. This change does not require a new Stacksmith CLI or execution image.

**API tokens** is an account-wide page listing your tokens across repositories. Choose a repository and environment explicitly inside the token creation form; changing the workspace repository does not change a token's scope.

The shared repository selector searches names and available descriptions. Arrow keys navigate results without changing the selection; Enter or a click chooses a repository. Escape dismisses the dropdown. The result list keeps a small rendered window for large accounts. Composer uses the same selector before listing the chosen repository's stack files.

The token table starts with active tokens and offers All, Expired, and Revoked filters. Rows show repository and environment scope, capabilities, expiry, and status. Only active tokens offer a Revoke action.

Use **Settings > Administration > Platform defaults** for shared defaults. Use **Repository configuration** to inherit those defaults, append repository layers, or replace them. Repository administrators can manage their repository policy. Saved references must end in a full commit SHA; later layers follow Stacksmith's configuration merge rules. Composer and Runs use the same effective configuration. `STACKSMITH_PLATFORM_CONFIG` supplies bootstrap defaults until a platform policy is saved. Previously registered repository configurations remain explicit replacements until changed.

Configuration layers use separate repository, file path, and commit fields. Add, remove, and reorder rows with their controls. The effective-layer cards update immediately and show platform or repository provenance; the Unsaved preview badge means those edits have not taken effect. Repeated references keep their first occurrence. Save configuration to activate the proposed order. Runs that reviewed different effective layers must be planned again.

Administrators can expand **Advanced run options** beneath the normal run controls to supply an exceptional commit override. A populated override remains visible in the collapsed summary. Blank uses the latest default-branch commit, and overrides retain their administrator approval requirement.

Open **Deployment settings** after choosing a repository. Platform inspects the immutable default-branch revision with Stacksmith and presents each valid deployment root, detected structure, and environment list. A root-level deployment is labeled **Repository root**, so no `.` path is required. If the repository contains multiple plausible roots, select one; the first release configures one deployment root per repository.

Choose **Automatic GitOps** to plan same-repository pull requests and apply pushes to the default branch, or choose **Manual runs only**. This choice is required before setup enables deployments. Platform requires source lockfiles automatically when complete coverage is detected. Missing or partial coverage leaves enforcement disabled and displays a reproducibility warning. Custom roots, explicit discovery modes, source-lock policy, and cross-repository concurrency groups remain available under **Advanced settings**.

Opening or refreshing Deployment settings rescans the default branch without changing saved configuration. Review additions, reactivations, and removals before applying the result. Removed environments become inactive: Platform excludes them from new runs and GitHub events while retaining credentials, roles, token scopes, and run history. Rediscovery reactivates the same environment record.

For a repository with one deployment and a root Stacksmith runfile, automatic discovery selects the single-deployment layout. No environment name or `environments/` directory is required. Platform and Stacksmith use `default` internally for permissions, credentials, and execution identity. API run requests may omit `environment` to select this target. Workspace automatically selects a sole target with Infrastructure planning access when no inspection choice has been made. Its environment selector also offers **Stack preview (no deployment)**, which supports Composer without configuring a deployment. An explicit preview preference is remembered.

## Docker Compose

Use the repository's single `.env` for both Compose interpolation and application configuration. There is no separate `.env.platform` or `.env.compose`.

```sh
uv sync --group dev
uv run poe configure
```

The configuration task expands `.env.example`, preserves existing values, migrates old Composer GitHub credentials/configuration, generates missing local secrets, and sets mode 0600. Running it again preserves generated secrets. Keep this file together with your database backups; replacing database passwords does not update roles in an existing PostgreSQL volume.

Fill the fields reported by configure.

For services or proxies that use an additional CA, set `STACKSMITH_PLATFORM_CA_BUNDLE` to the absolute path of a PEM bundle on the Docker host. Ensure the file is readable by the platform containers. Compose mounts it read-only into the API, worker, and Docker-launched run containers. The platform appends these certificates to its existing public CA roots; it does not replace them. Leave the setting blank when no custom CA is needed.

| Setting | Where to get it |
| --- | --- |
| `STACKSMITH_PLATFORM_GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` | GitHub App settings; existing Composer values are preserved |
| `STACKSMITH_PLATFORM_GITHUB_APP_ID` | Numeric App ID in GitHub App settings |
| `STACKSMITH_PLATFORM_GITHUB_PRIVATE_KEY_FILE` | Host path where you saved the downloaded App PEM, normally `./.secrets/github-app.pem` |
| `STACKSMITH_PLATFORM_CA_BUNDLE` | Optional absolute host path to a PEM bundle of additional trusted CAs |
| `STACKSMITH_PLATFORM_ADMIN_LOGINS` | JSON array of the GitHub users who administer this deployment |
| `STACKSMITH_PLATFORM_CONFIG` | GitHub `git+https` reference to the approved Composer config |
| `STACKSMITH_PLATFORM_STACKSMITH_IMAGE` | Defaults to `docker.io/cisourcerer/stacksmith:latest`; use an image with Python 3.15 and its toolchain |

Instead of a Stacksmith base image, you can set `STACKSMITH_PLATFORM_EXECUTION_IMAGE` to a prebuilt execution image's named digest or an immutable local Docker image ID. Leave the base-image setting blank in that case.

```sh
uv run poe stack-up
```

`stack-up` checks required settings and PEM readability before building anything, builds independent Python and frontend images, builds the execution wrapper when needed, and starts the complete stack in the foreground. Use `stack-up --detach` to wait for healthy services and leave them running in the background. Ctrl-C stops and removes foreground containers, preserving named data volumes. The wrapper is built from a fresh wheel context and its immutable local Docker ID is saved to `.env`. A registry push is not required for local execution.

To pass AWS CLI credentials from a profile into every Stacksmith run, prefix the exported values before starting Compose.

```sh
aws login --profile root
eval "$(aws configure export-credentials --profile root --format env)"
export STACKSMITH_RUN_AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID"
export STACKSMITH_RUN_AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY"
export STACKSMITH_RUN_AWS_SESSION_TOKEN="$AWS_SESSION_TOKEN"
uv run poe stack-up
```

The run prefix works for variables exported in the shell or stored in `.env`. Compose passes only the prefixed values to the worker; the worker removes the prefix when it builds phase environments. These credentials are available to runs across repositories and environments. Repository-specific environment credential bindings override them.

Open <http://localhost:8000> or <http://localhost:8000/runs>. For these defaults, the GitHub App callback is `http://localhost:8000/api/auth/callback`. Webhook delivery requires a reachable public URL or forwarding to `/api/v1/webhooks/github`; manual runs do not require webhook delivery.

```sh
uv run poe stack-logs
uv run poe stack-down
```

To check configuration without starting anything, use `uv run poe stack-check`. To rebuild just one image, use `uv run poe platform-image`, `uv run poe frontend-image`, or `uv run poe execution-image`. Once prepared, the ordinary `docker compose up -d --wait`, `docker compose logs -f frontend api worker`, and `docker compose down` commands work without extra env-file flags. Down preserves the data volumes.

Compose provisions PostgreSQL roles, runs schema initialization, grants separate API/worker database permissions, creates the private artifact bucket, and starts the API and worker. Only explicitly selected settings are injected into API and worker containers; their database URLs contain only their own role's password. The administrator's database password and unrelated `.env` values are not passed to them. Storage credentials come from the same `.env` values for every service.

Artifact storage uses [SeaweedFS mini](https://github.com/seaweedfs/seaweedfs/wiki/Quick-Start-with-weed-mini) behind an S3-only proxy. The storage backend has its own private network, so execution containers can reach the authenticated S3 endpoint without reaching the backend's management or filesystem APIs. No storage ports are published to the host.

Compose mounts the PostgreSQL role initialization SQL and storage proxy configuration as named, read-only `configs`. The SQL still runs only when PostgreSQL initializes an empty data volume. Storage HTTP health checks accept successful or authentication-required responses; connection failures and proxy errors fail the checks. Startup waits for healthy storage and then verifies authenticated bucket access before starting the API.

Python services use Compose's init process and a 90-second stop grace period. On SIGTERM or SIGINT, the worker finishes its current reconciliation step, removes its heartbeat, and exits without starting another step. Execution containers remain managed through their durable run records. Worker health checks require that container's own database heartbeat to be less than 120 seconds old; a healthy sibling cannot hide a stalled worker. PostgreSQL and SeaweedFS receive 60 seconds to stop; the frontend and storage proxy receive 30 seconds. The storage proxy uses SIGQUIT for graceful Nginx shutdown. An unhealthy status is reported by Docker and fails `up --wait`; it does not automatically restart a running container.

To test these Compose behaviors with local images, run `STACKSMITH_TEST_COMPOSE=1 uv run python -m pytest tests/test_compose.py tests/test_worker_health.py`. The runtime test creates a separate project with temporary data volumes, verifies config mounts and database roles, exercises health checks and worker shutdown, and removes its containers and volumes afterward.

Configure/check imports the host PEM into the private `.env`; Compose supplies it through the API/worker environment alongside the GitHub client secret. The API runs as a non-root user without depending on host PEM permissions. Restrict access to the Docker daemon and `.env`, which contain these credentials. Only the supervisor mounts Docker's socket. Execution containers join `stacksmith-execution`, which exposes the API claim endpoint and artifact storage without putting PostgreSQL on their network.

The frontend defaults to loopback port 8000; the API is reachable only on the private container networks. `STACKSMITH_PLATFORM_BIND_ADDRESS` and `STACKSMITH_PLATFORM_PORT` change that binding; `STACKSMITH_PLATFORM_PUBLIC_URL` must match the browser-visible origin and OAuth callback. Hosted deployments should terminate TLS at the trusted reverse proxy. Compose sets an execution-visible callback of `http://api:8000` and private artifact endpoint of `http://storage:9000`. Browsers download reports through the authenticated API.

For an existing PostgreSQL volume, provision the two login roles separately before starting migrations; initialization scripts only run for a new database. Back up PostgreSQL, artifact storage, and `.env` together. Alembic schema upgrades are explicit through `stacksmith-platform-init`; API and worker roles do not have schema-creation privileges.

Compose defaults to PostgreSQL 18. If an existing volume contains PostgreSQL 17 data at its root, set `STACKSMITH_PLATFORM_POSTGRES_IMAGE=postgres:17-alpine` and `STACKSMITH_PLATFORM_POSTGRES_PGDATA=/var/lib/postgresql` in `.env` to keep using it. That root-directory setting is only for an already initialized cluster. For a fresh PostgreSQL 17 volume, use `STACKSMITH_PLATFORM_POSTGRES_PGDATA=/var/lib/postgresql/17/docker`. The PostgreSQL 17 image creates a separate anonymous mount at `/var/lib/postgresql/data`, so the parent directory is nonempty even before initialization; using the versioned sibling directory keeps the database inside the named Compose volume. A PostgreSQL major-version upgrade requires a separate database migration; changing the image alone does not upgrade the data.

`STACKSMITH_PLATFORM_POSTGRES_DATABASE` selects the Compose database and defaults to `platform`. When using an existing PostgreSQL volume, create the selected database separately before starting Compose; changing this setting does not create another database in an initialized volume.

### Container development

Use Docker Compose 2.32 or newer and `uv run poe stack-up --debug` for the complete development stack on the same public origin. Vite serves the frontend with HMR, Uvicorn reloads Python source, and Compose restarts the worker after source synchronization. Dependency manifest and lockfile changes rebuild the affected development images. Execution-wrapper changes require restarting through `stack-up` to rebuild the execution image before new runs.

Attach a Python debugger to `localhost:5678` with the repository mapped to `/tmp/build`. The debug port binds to loopback and can be changed with `STACKSMITH_PLATFORM_DEBUG_PORT`; startup does not wait for a debugger. `--debug --detach` starts the development images without Compose watch or source synchronization. Both modes use the same `.env`, database, and artifact volumes. `--no-cache` disables image build caches.

`stack-logs` follows frontend, API, and worker logs. `db-shell` opens an interactive administrator psql shell in the running database. `run-db-migrations` builds the current Python image by default (`--no-build` uses a prepared image), starts PostgreSQL if needed, applies packaged Alembic revisions, and refreshes API/worker role grants; it preserves data volumes and leaves the database running. Frontend commands are available through `web-install`, `web-format`, `web-lint`, `web-test`, and `web-build`.

`STACKSMITH_PLATFORM_IMAGE` and `STACKSMITH_PLATFORM_FRONTEND_IMAGE` select the independent production image names. Their debug counterparts are `STACKSMITH_PLATFORM_DEBUG_IMAGE` and `STACKSMITH_PLATFORM_FRONTEND_DEBUG_IMAGE`. The frontend receives only API routing settings; it receives no GitHub, database, storage, or execution credentials.

## Kubernetes

### Local Minikube

Configure `.env` with `uv run poe configure`, select the `minikube` context, and sign in to the AWS profile before starting the local cluster deployment.

```sh
aws login --profile root
kubectl config use-context minikube
uv run poe kube-up
```

`kube-up` checks `.env`, builds the Python, frontend, and execution images, loads them into Minikube, and provisions local PostgreSQL and artifact storage with persistent volumes. It creates the shared configuration and three database Secrets, installs the Helm chart, creates the artifact bucket, and starts a background port forward to <http://localhost:8000>. It reads current AWS credentials from the shell or the `root` profile and passes them to API secret sources and every run. `STACKSMITH_RUN_` entries in `.env` or the shell are also passed to runs. Run `uv run poe kube-up` again after rebuilding or renewing credentials; it updates the Secrets and restarts the API and frontend. If only the port forward has stopped, run `uv run poe kube-forward`.

The local image allowance is enabled only by this task. Regular Helm deployments still require an immutable named execution image digest. The PostgreSQL and storage volumes persist across reruns; keep the generated database passwords in `.env` stable. Free local port 8000 before starting the forward, for example by stopping the Compose stack.


The Helm chart is `deploy/helm/stacksmith-platform`. It deploys frontend, API, and supervisor workloads, frontend and API Services, optional Ingress routed to the frontend, the schema hook, execution namespace, restricted supervisor RBAC, execution service account, and execution egress policy. PostgreSQL and S3-compatible storage are supplied externally.

Create three separate database credentials using the PostgreSQL roles `platform_api`, `platform_worker`, and a schema administrator. Revoke public schema creation. Create the roles before the chart's schema hook, which initializes tables and grants the runtime roles their required privileges. Each existing Kubernetes Secret contains just `STACKSMITH_PLATFORM_DATABASE_URL`; configure the three Secret names through chart values.

Create the existing `configSecret` with the shared encryption secret, GitHub settings, artifact endpoint/credentials, deployment-wide Composer config, and optional secret references. Set `executionImage` to an immutable digest, `adminLogins`, `publicUrl`, and the platform `image`. The image must be available to cluster nodes.

```sh
helm lint deploy/helm/stacksmith-platform
helm template stacksmith deploy/helm/stacksmith-platform -f your-values.yaml
helm upgrade --install stacksmith deploy/helm/stacksmith-platform \
    --namespace stacksmith-platform --create-namespace -f your-values.yaml
```

The API records a durable supervisor dispatch request in the same transaction that makes a run runnable, then attempts dispatch after the response. Temporary supervisor Jobs live in the control namespace and create one isolated execution Job per phase in the execution namespace. A supervisor exits at approval and authorization gates, when work finishes, or when it loses ownership. No supervisor remains alive waiting for a deployment lock. Run Pods have no Kubernetes API token, database credentials, or ability to create Jobs; they do not inherit supervisor identity.

The recovery CronJob runs every minute with `concurrencyPolicy: Forbid`, a 50-second starting deadline, and a 120-second active deadline. It retries missed launches and lock-blocked runs, expires approvals, and performs retention cleanup. A failed or interrupted supervisor is replaced after its ownership lease expires, adopting existing execution Jobs rather than replaying them. Supervisor generations and one-use phase claims prevent duplicate phase execution. Claimed mutations without conclusive results remain `uncertain` and retain their deployment locks.

Supervisor Jobs and execution Jobs use `backoffLimit: 0`. Supervisors have a deadline covering phases up to the next approval gate plus five minutes of startup allowance per Job. Completed supervisor Jobs are retained for 24 hours. Logs and results remain in the existing database and artifact storage paths. The Execution health admin page shows supervisor and execution observations, dispatch errors, and the last successful recovery pass instead of expecting worker heartbeats.

Configure `supervisorDatabaseSecret` for the existing `platform_worker` database role. `serviceAccounts.api` and `serviceAccounts.supervisor` support chart-managed accounts with annotations or existing account names. The API can create and inspect control-namespace supervisor Jobs. The supervisor account can dispatch recovery work and manage execution Jobs and Pod logs in the execution namespace. Set `supervisor.cpu` and `supervisor.memory` for supervisor limits, `recovery.resources` for maintenance, and `inspectionWaitTimeout` for Composer's total scheduling/wait budget, defaulting to 180 seconds. The Stacksmith command timeout remains separate.

Before upgrading from the persistent Kubernetes worker deployment or changing supervision mode, drain active runs and resolve uncertain runs. Remove old `workerReplicas` and `resources.worker` chart settings, rename `workerDatabaseSecret` to `supervisorDatabaseSecret`, and rename `serviceAccounts.worker` to `serviceAccounts.supervisor`. Helm removes the old worker Deployment. The schema hook adds nullable supervisor metadata to existing runs and provisions recovery health storage; existing Compose runs retain the persistent-worker path.

The execution network policy allows DNS, the platform claim endpoint, and public HTTPS. Configure `privateEgress` CIDRs and ports for private artifact storage, cloud APIs, or dependencies. Use a CNI that enforces NetworkPolicy. Do not share the execution namespace with unrelated workloads or reuse it across platform releases.

For local Compose deployments, prefix a host or `.env` variable with `STACKSMITH_RUN_` to pass it to every Stacksmith run phase. The prefix is removed inside the execution container. For example, `STACKSMITH_RUN_AWS_ACCESS_KEY_ID` becomes `AWS_ACCESS_KEY_ID`. These values apply across repositories and environments; repository environment credential bindings override matching names. Keep this broad access limited to credentials intended for all runs. Kubernetes deployments can set the equivalent JSON mapping in `STACKSMITH_PLATFORM_RUN_ENVIRONMENT` in the shared configuration Secret.

## Operations

Phase deadlines default to 60 minutes. Authoring inspection defaults to 30 seconds for the complete combined review, including document authoring, catalog inspection, and association resolution. Execution images must provide `stacksmith.authoring.author_stack` and the inspection functions in `stacksmith.api`; the API process does not import Stacksmith. Rebuild the execution wrapper, API, and frontend together when updating this review protocol. API logs record queue and total inspection durations plus review stage timings for runtime preparation, environment discovery, authoring, catalog inspection, and association resolution, without draft contents or credentials. Compose workers run up to four inspection containers concurrently, including authoring and operation discovery. Set `STACKSMITH_PLATFORM_INSPECTION_CONCURRENCY` to a positive integer to change this per-worker limit. Each container has its own configured memory limit, so choose concurrency to fit the host's resources. Additional inspections wait in the durable queue under the existing inspection wait deadline. Inspection scheduling and ordinary run reconciliation progress independently. Workers must have distinct identities when scaling; each receives its own inspection allowance. Restarted workers resume their existing inspections, even if the limit was reduced, and wait for capacity before launching more. Kubernetes uses a separate supervisor per run and does not use this limit. Mutating runs sharing a lock key remain serialized. Approvals/private handoffs expire within 24 hours; review artifacts default to 30 days. Worker maintenance removes expired objects even when the queue is idle.

`/api/health` reports process liveness; `/api/ready` checks the session schema/database. The administrator status endpoint and **Settings > Administration > Execution health** page show Compose workers with a heartbeat in the last 120 seconds, or Kubernetes supervisor/execution observations and recovery freshness. Both modes show queued and uncertain runs. Older Compose worker records are counted separately as stale. Container workers use a stable hostname identifier across process restarts. Logs identify run IDs and lifecycle outcomes. An uncertain run retains its lock until a deployer records reconciliation in the run view; resolution does not resume the interrupted command.

Cancel sends a termination request to the execution runtime. Cancellation of a claimed mutating phase can leave partial infrastructure changes and is marked uncertain. Review Stacksmith's reports and remote state before starting another deployment.

Never expose object-store credentials or schema-administrator database credentials to run Pods. Platform secrets must not be included in container builds, repository configuration, or browser recovery files. The Helm chart references existing Secrets instead of rendering secret values into release manifests.

## Test plans and list navigation

Run history and API tokens use server-side pagination with 10, 20, or 50 items per page. Token status counts cover the whole account.

Platform administrators can open Advanced run options and select Allow local backend for this test plan. This bypass is limited to plan and plan-operation. Workspace files pass between phases, but local state is not reused by separate runs; use this option for disposable testing.

## GitHub installation setup

Configure the App setup URL as `<public origin>/api/auth/setup` and enable **Redirect on update**. Keep the user authorization callback at `<public origin>/api/auth/callback` and the webhook at `<public origin>/api/v1/webhooks/github`. Subscribe to repository events for rename updates; installation and installation-repository events are delivered automatically by GitHub. Setup returns verify the installation against the signed-in user before opening Settings > GitHub connection.

Installation links use the configured App identity. For authoring deployments without App private-key credentials, set `STACKSMITH_PLATFORM_GITHUB_APP_SLUG` to the App slug so Connect GitHub is available before the first installation.

Run the existing schema initialization command before starting updated API and worker processes. The upgrade adds GitHub repository IDs and a deployment activation flag. Existing repository records keep deployments enabled; new discovery records start disabled. Settings and grants follow repository renames by GitHub ID. Revoked repositories retain their settings for restoration.

See [database migrations](database-migrations.md) for migration authoring, debug Adminer, drift checks, and the fresh-baseline transition.

# Stacksmith Platform

Stacksmith Platform combines visual stack authoring with a self-hosted execution system for [Stacksmith](https://github.com/ci-sourcerer/stacksmith). **Composer** is its visual editor. GitHub supplies identity, repositories, pull requests, and webhook events; the platform owns runs, approvals, permissions, logs, and reports.

The platform has a public frontend container, a private FastAPI service, and a separate worker supervisor. The frontend serves Composer and proxies API requests on the same browser origin. PostgreSQL holds durable control-plane state and the job queue. Each Stacksmith phase runs in a fresh Docker container or Kubernetes Job, with phase-scoped credentials and private artifact handoffs. Workers expose no separate inbound API.

## Authoring and execution

Composer retains component browsing, connections, association controls, source-preserving YAML/JSON editing, resolution history, browser recovery, source review, and GitHub pull requests. In execution mode, authoring and inspection also run in isolated containers; the API does not execute Stacksmith subprocesses.

Composer uses the environment applied in Settings > Workspace for resolved review and configuration resolution. It loads that environment's runfiles, variables, and prepare-phase credentials at the opened repository revision. Authored connections appear immediately and support nested object fields and list slots without replacing sibling values. Failed inspections retain expandable diagnostic details. Editing, source export, and recovery remain available without a selected environment; authoring-only deployments keep standalone managed-config review.

Connecting a collection or replacing an existing value opens a dialog with explicit destination choices and a preview before you commit. Empty scalar inputs connect immediately. Draft navigation, managed-rule changes, and saved-draft recovery use the same styled confirmation dialogs, with keyboard access and focus restoration.

The Runs page at `/runs` has its own repository and environment selectors, independent of Composer's workspace. It supports infrastructure plans, approved applies, previewed destroys, standalone operation previews and execution, live bounded phase logs, reports, and cancellation. Include and Execution controls choose the lifecycle, including whether to plan destruction. Applies use the saved binary plan from the reviewed workspace. After-apply operations follow Stacksmith's own lifecycle and can replan against current outputs. Administrators review platform activity under Settings > Administration > Audit log, with repository, run, actor, and event filters.

Same-repository PR events queue plans awaiting maintainer authorization for that exact revision before credentials are exposed. Fork PR execution is excluded. Default-branch pushes prepare deployments that wait for approval. Manual runs resolve the current default-branch tip on the server and save that immutable revision. An explicit commit override requires a platform administrator browser session, including administrator approval before deployment. Ordinary runs reject approval if the default branch advances.

GitHub repository access and platform permissions are both required. Administrators grant environment capabilities explicitly. Browser sign-in uses opaque sessions; scripts use hashed, scoped, expiring platform tokens. Deployment approval and uncertain-run reconciliation require an interactive browser session. API tokens expire no later than their issuing GitHub session, at most eight hours.

## Run with Docker Compose

Docker Compose is the primary way to run the whole platform. Use Python 3.15 for the project tasks and Docker Compose 2.32 or newer for development watch mode. The frontend Docker build supplies Node.js.

```sh
uv sync --group dev
uv run poe configure
```

`configure` creates or updates the single `.env`, preserves existing credentials, generates local database/storage/webhook secrets, and restricts the file to mode 0600. It reports the settings you still need to fill without printing their values. Set the GitHub App ID, client credentials, administrator logins, approved config, and host path to the App's PEM file. The default Stacksmith base image is `docker.io/cisourcerer/stacksmith:latest`; replace it if you use a different image, or set it blank to use an already-built execution image.

After filling those settings, start the platform.

```sh
uv run poe stack-up
```

This validates configuration, builds separate Python and frontend images, builds the execution wrapper when a Stacksmith base image is configured, and starts the full stack in the foreground. Open <http://localhost:8000> for Composer, <http://localhost:8000/runs> for runs, or <http://localhost:8000/settings> for workspace selection, GitHub access, and configuration. Configure the GitHub App callback as `http://localhost:8000/api/auth/callback` for these defaults. Use `stack-up --detach` to start in the background and wait for healthy services. For fast local restarts without a rebuild, use `stack-up --no-rebuild`; this reuses the current local images and keeps Compose watch actions responsible for source sync and restarts.

```sh
uv run poe stack-logs
uv run poe stack-down
```

Ctrl-C and `stack-down` stop and remove containers while preserving the named PostgreSQL and artifact volumes. `docker compose up -d --wait` also works directly after the production images and `.env` are prepared. See the [deployment guide](docs/deployment.md) for all settings and hosted/Kubernetes deployment.

For container development, use `uv run poe stack-up --debug`. Compose watches source changes, Uvicorn reloads the API, Vite provides frontend HMR, and Python source changes restart the worker. Attach a Python debugger to `localhost:5678`, mapping the local repository to `/tmp/build`; startup does not wait for attachment. Dependency changes rebuild the development images. `--debug --detach` runs the development images without watch or source synchronization. Use `--no-cache` to bypass image build caches. Repeat `--service NAME` to start selected services and their dependencies, for example `stack-up --debug --service adminer`; configuration checks still use the single `.env`. For ordinary restarts while iterating locally, prefer `stack-up --debug --no-rebuild` or `stack-up --no-rebuild` so the existing images are reused and changes continue to flow through the `develop.watch` actions. Changes to execution-wrapper code require running `stack-up` again to rebuild the execution image before subsequent runs.

`uv run poe db-shell` opens psql in the running database. `uv run poe run-db-migrations` builds the Python image, applies Alembic revisions, and refreshes database role grants. Use `--no-build` for a prepared image. Debug mode also provides automatic database administrator login through Adminer at <http://localhost:8081>. See [database migrations](docs/database-migrations.md) for revision authoring, drift checks, and database reset. Frontend dependencies are needed on the host only for frontend tests or direct frontend development; `uv run poe web-install` installs them from the lockfile.

## Container images

The API/worker image uses **common-python-tasks' managed Dockerfile**, including its wheel build, version metadata, dependency handling, and non-root runtime. The frontend uses a separate Node build and non-root Nginx runtime. Web orchestration is owned by this repository; it uses no shared FastAPI tasks or Compose templates.

```sh
uv run poe platform-image
uv run poe frontend-image
uv run poe execution-image
```

`platform-image` invokes the generic shared image builder and tags `stacksmith-platform:local`. `frontend-image` builds `stacksmith-platform-frontend:local` independently. Both accept `--debug` and `--no-cache`. Image names are configurable in `.env`. The Python image and wheel contain no frontend build output.

`execution-image` builds a fresh platform wheel in a temporary context and installs it into the Stacksmith image selected in `.env`. It records the resulting immutable Docker image ID in `.env`, so local Compose execution does not require a registry push. The Stacksmith base image must provide Python 3.15, its CLI, and its toolchain, including the version-two `ci prepare` / `ci execute` contract with saved-plan execution. Stacksmith remains a separately packaged tool; the platform does not import its internal models.

For Kubernetes, publish the execution image and use its named `repository@sha256:digest` reference. A local Docker image ID cannot be used by Kubernetes. Leave `STACKSMITH_PLATFORM_STACKSMITH_IMAGE` blank when using a prebuilt execution image that should not be rebuilt locally.

## Verification

Run formatting before linting and tests.

```sh
uv run poe format
uv run poe lint
uv run poe test
uv run poe build
uv run poe web-format
uv run poe web-lint
uv run poe web-test
uv run poe web-build
helm lint deploy/helm/stacksmith-platform
```

After building both production images and both debug images, run the isolated container checks with `STACKSMITH_TEST_WEB_IMAGES=1 uv run pytest tests/test_frontend_container.py`. They use a test API and verify proxy behavior, streaming, Vite HMR, and Python source reload without deployment credentials.

The control-plane tests use SQLite by default. To exercise PostgreSQL locking and schema behavior, point `STACKSMITH_PLATFORM_TEST_DATABASE_URL` at a **disposable dedicated database** before running `tests/test_control.py`. These tests clear that database's platform tables. The existing real CLI integration tests remain opt-in through `STACKSMITH_TEST_COMMAND`.

Real GitHub sign-in, webhook delivery, and infrastructure execution require a dedicated GitHub App, test repository, and disposable infrastructure credentials. Never use production infrastructure for transport smoke tests.

The package, console commands, and settings prefix have changed to `stacksmith-platform`, `stacksmith_platform`, and `STACKSMITH_PLATFORM_`. Runtime configuration does not recognize old environment names. `poe configure` migrates the old GitHub client credentials, stable encryption key, and Composer config when present; sessions are not migrated. Authored stack files, Composer diagram metadata, and browser recovery formats remain intact.

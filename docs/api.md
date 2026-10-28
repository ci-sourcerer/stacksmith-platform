# Platform API

The platform API is under `/api/v1`. Composer's authoring and GitHub sign-in routes remain under `/api`. Both are served by the same FastAPI process. Interactive documentation is available at `/docs` and the generated schema at `/openapi.json`.

## Authentication

Browser requests use the HTTP-only `platform_session` cookie and send `X-Composer-Request: 1` on mutations. Browser origins must match the configured public origin. Automation sends `Authorization: Bearer <platform-token>`. Create expiring tokens through Settings > Account > API tokens or `POST /api/v1/tokens` using a browser session. Token values are returned once; only hashes are stored.

API tokens are limited to their selected repository, environment, and actions, plus the owner's current GitHub access and platform grants. They expire within their issuing session's remaining lifetime, at most eight hours. Deployment approvals, PR execution authorization, and uncertain-run reconciliation require an interactive session.

`GET /api/v1/tokens` lists only the current account's tokens. It accepts `page` (default 1), `page_size` (default 20, maximum 100), and `status` (`active`, `all`, `expired`, or `revoked`; default `active`). The response contains `items`, `page`, `page_size`, `total`, and account-wide status `counts`. Each item contains `id`, `name`, `expires`, `revoked`, `repository`, `environment`, `permissions`, and `workloads`. This metadata never includes token values or the stored GitHub credential. Token status is derived from revocation state and expiry; revoked takes precedence over expired.

## Composer inspection

`POST /api/graph` accepts a signed `snapshot`, `components`, and optional `disabled_associations`. It returns authored `edges` and path-specific `diagnostics` without invoking Stacksmith or requiring an environment. Invalid expressions and cycles do not hide other extractable connections. `POST /api/source` renders the current authored document independently of resolved inspection.

Repository-backed `/api/review`, `/api/resolution`, and `/api/pull-requests` requests accept an optional `environment`. Omitting it selects stack preview using the authored stack and effective repository/platform configuration. Preview requires repository access and existing authoring permissions for edits, checks out the signed snapshot revision, preserves the original stack path, and excludes deployment runfiles, variables, and credentials. Execution-enabled previews use the isolated inspection runtime. Supplying an environment selects deployment inspection and additionally requires Infrastructure planning access to that environment. A failed deployment inspection never falls back to preview. `POST /api/catalog` accepts `{snapshot, environment}` and returns `{catalog, context}` for either context. `GET /api/catalog` retains managed-configuration-only metadata. Authoring-only deployments and the local demo retain standalone review without execution API calls.

Environment inspection checks out the signed snapshot revision, discovers the selected environment with its registered GitOps settings, and substitutes the draft for its original stack file. Runfile configuration, variable, and stack layers retain their original order and merge rules. The environment must consume the edited stack file. Runtime variables and credential bindings enabled for `prepare` are supplied inside the isolated runtime. Review and resolution responses include `context` with `mode` (`stack_preview` or `deployment`), repository, revision, path, configuration revision, and nullable environment. Deployment contexts additionally include the environment revision. Configuration changes during inspection invalidate the result. PR creation reinspects the current draft on the server and records the verified context in the PR description. PR retry identity includes the context so different configuration or deployment revisions produce separate proposals. Neither inspection mode runs an infrastructure plan.

`POST /api/review` also returns `catalog` for the inspected context. Execution-enabled reviews combine document authoring, catalog inspection, and association resolution in one isolated runtime using Stacksmith's Python API, with one repository checkout and shared remote caches. The editor uses this catalog instead of requesting a second inspection. Automatic reviews wait 400 ms after edits and allow one active request per mounted editor, replacing pending work with the latest draft. Source previews remain independent of inspection. A separate catalog request remains available when inspection fails.

Repeated repository-access checks share GitHub discovery within one request. Inspection completion always performs fresh discovery and configuration checks before accepting the result. The cache is discarded with the request and does not reuse permissions between users or requests.

`GET /api/inspection-targets?repository={owner}/{repo}` returns visible `targets` with `name` and `can_inspect`, an `availability` of `unconfigured`, `forbidden`, or `available`, and `can_configure`. This distinguishes missing setup from insufficient Infrastructure planning access without exposing inaccessible target names. Composer selects a sole eligible target when no target or explicit stack-preview preference has been chosen. `GET /api/v1/repositories/{owner}/{repo}/environments` also includes `can_inspect` on each visible configured target.

Inspection failures retain a readable `detail` and add `diagnostics` containing `stage`, `code`, `status`, `exit_code`, `message`, `stdout`, and `stderr`. Supplied credentials and handoff tokens are redacted, terminal control sequences are removed, and each displayed stream is limited to 32 KiB, including a truncation marker when needed. Detailed results remain encrypted and private; they are not emitted to ordinary application logs.

## Run requests

`POST /api/v1/runs` requires an `Idempotency-Key` header and a JSON body like this.

```json
{
  "repository": "acme/service",
  "environment": "production",
  "action": "apply",
  "include_infrastructure": true,
  "include_operations": true,
  "apply_after_plan": true,
  "destroy_plan": false,
  "operations": [],
  "tags": ["application", "production"]
}
```

The four phase flags select infrastructure planning, operation planning, applying after approval, and destroy planning. Set `destroy_plan` without `apply_after_plan` to create a destroy preview; set both to destroy after approval. `action` remains a derived lifecycle label: `plan`, `apply`, `destroy-plan`, `destroy`, `plan-operation`, or `apply-operation`. `operations` may select a subset when operation planning is enabled; an empty list selects all declared operations. Requests using only the older `action` field continue to map to the corresponding default phase set. Omit `revision` to resolve the latest default-branch commit on the server. The chosen SHA is stored with the run, and reusing the idempotency key returns that same run even if the branch has advanced. Supplying an explicit full SHA requires an interactive platform administrator session; deployment of an override also requires administrator approval. Ordinary approvals still require the default branch to remain at the captured revision. Different inputs for the same key produce `409`. The API does not accept arbitrary command strings, executable paths, or unrestricted Stacksmith flags.

Manual runs can provide `tags` to target infrastructure components matching every listed tag, including tags supplied by managed module mappings. An empty or omitted list targets all components. Tags are trimmed and deduplicated, and individual tags cannot contain commas. A nonempty selector requires infrastructure planning and does not filter operations. Stacksmith resolves the selection and includes it in the reviewed plan; automatic runs target all components.

`GET /runs?repository=acme/service&environment=production` filters by environment. Omit `environment` to list runs across all environments visible to the caller, narrowed by API-token scope. Counts and pagination include only authorized runs. The endpoint accepts `page` (default 1) and `page_size` (default 20, maximum 100). It returns an envelope containing `items`, `page`, `page_size`, and `total`, ordered newest first. Each item is a summary containing `id`, `repository`, `environment`, `revision`, `action`, `actor`, `status`, `phase`, `created`, `updated`, and `expires`; list queries do not load or return specifications, logs, or reports. `GET /runs/{id}` includes the immutable specification, current phase/status, review results, and approval expiry. Subresources provide logs, audit history, and authorized review artifacts. Artifact content is proxied through the API; private execution context is never exposed.

`POST /runs/{id}/authorize` permits credentialed execution of an exact PR revision. `POST /runs/{id}/approve` approves a successfully previewed deployment. `POST /runs/{id}/cancel` requests cancellation. `POST /runs/{id}/resolve` requires a reconciliation note and releases an uncertain run's deployment lock after operator inspection; it never resumes execution.

`DELETE /runs/{id}` requires an administrator browser session and a terminal status (`succeeded`, `failed`, `cancelled`, `expired`, or `uncertain`). It permanently deletes the run, associated artifacts, phase claims, credential sessions, and any retained lock, returning `{"deleted":true}`. Active runs return `409`; missing runs return `404`. Audit events are retained. Deleting an uncertain run releases its lock without recording reconciliation; inspect and reconcile infrastructure first.

`GET /repositories/{owner}/{repo}/environments/{name}/operations` requires view access to the environment. It inspects the latest default-branch commit in an isolated execution and returns its `revision` and an `operations` list containing stack-local `name`, `description`, and `stacks` fields. Discovery uses Stacksmith's environment and stack metadata APIs without planning infrastructure. An empty run request's `operations` list selects all declared operations; a nonempty list selects those names and their dependencies.

Interactive platform administrators can set `allow_local_backend: true` on non-mutating plans for disposable local-backend testing. Apply and destroy requests reject this option. Workspace files pass between phases within a run; local state is not reused by later runs.

## Administration

Repository discovery replaces registration. `GET /api/repository-discovery` lists repositories accessible to the signed-in user and App. `GET /api/github-connection` returns the installation URL and accessible installation management links. `GET /api/auth/setup` verifies installation returns. Administrator `POST /api/v1/repositories/refresh` reconciles the complete App catalog. Administrator `POST /api/v1/repositories/{owner}/{repo}/deployment` accepts `{"enabled":true}` or `{"enabled":false}` to control deployment processing. New records inherit platform configuration and start with deployments disabled. The former `POST /api/v1/repositories` registration endpoint has been removed. Per-repository environment, grant, and secret-binding endpoints configure execution. Secret read responses contain metadata only.

Discovery applies the administrator-controlled filter to user discovery and catalog refresh. `GET /api/v1/repository-filter` reads it; `PUT` replaces it with `{"properties":{"stacksmith":"stack"},"topics":["stacksmith-stack"]}`. Both require an administrator browser session. Each collection supports up to 32 entries, and matching any property or topic includes a repository. Saving does not refresh the catalog; call `POST /api/v1/repositories/refresh` afterward. Empty collections match no repositories.

Deployment setup uses `GET /repositories/{owner}/{repo}/deployment-configuration` to read settings and `POST /repositories/{owner}/{repo}/deployment-discovery` to inspect candidate roots at the default-branch revision. `PUT /repositories/{owner}/{repo}/deployment-configuration` applies reviewed settings with that full `revision`, `gitops_root`, `discovery_mode`, `locked`, `automatic`, optional `lock_key`, and `deactivate_missing`. A changed revision or unapproved removal of active environments returns `409`. Removed environments are deactivated without deleting history or credentials.

`GET /api/platform?repository={owner}/{repo}` provides authored layer summaries for the settings page, not merged runtime values. Each `layers` entry contains its `reference` and `summary`; credential-like source fields are redacted. Use `/api/resolution` for resolved draft inspection.

Platform administrators read and update shared configuration through `GET /configuration` and `POST /configuration`. Policies contain `mode` and an ordered `refs` list of GitHub configuration file references pinned to full commit SHAs. Repository administrators use `GET /repositories/{owner}/{repo}/configuration` and `POST` on the same endpoint. Repository modes are `inherit`, `append`, and `replace`; `inherit` requires an empty `refs` list, while platform defaults use `replace`. Runs capture the effective layers; changing those layers invalidates queued execution and pending approvals that captured the previous policy.


## Audit events

`GET /api/v1/audit` lists platform events with exact `repository`, `run_id`, `actor`, and `action` filters, plus `page` and `page_size` pagination. `GET /api/v1/runs/{id}/audit` lists a run's events. Both require an administrator browser session; API tokens cannot read them. Metadata excludes secret values. See [Settings](settings.md#account-and-administration) for the audit page.

## Service authentication

GitHub events use `/api/v1/webhooks/github` with a verified HMAC signature and deduplicated delivery ID. The one-use `/api/v1/executions/{id}/claim` endpoint accepts only the capability for that run's active phase. It cannot queue arbitrary work or access platform resources. Workers have no inbound service API; they consume jobs through their restricted database identity.

## Stack roles

Platform Admins remain configured through `STACKSMITH_PLATFORM_ADMIN_LOGINS`. Other users receive repository and environment role assignments. See [Roles and access](roles.md) for the role matrix and approval rules.

`GET /repositories/{owner}/{repo}/grants` and `POST` on that endpoint require an Admin browser session. Assignment bodies contain `login`, `environment` (`*` for all environments), `role` (`viewer`, `runner`, or `provisioner`), and `workloads` (`infrastructure`, `operations`, or both). Viewer assignments require an empty workload list; Runner and Provisioner assignments require a nonempty list. `DELETE /repositories/{owner}/{repo}/grants/{grant_id}` removes an assignment.

`GET /repositories/{owner}/{repo}/access?environment=dev` returns the signed-in user's assignments, administrator status, and effective capabilities. Assignments add access, and capabilities are computed independently for each assignment before merging. An Operations Provisioner combined with an Infrastructure Runner therefore cannot approve infrastructure changes.

Token creation retains the narrower `view`, `plan`, and `deploy` action permissions and adds `workloads`. Every token includes `view`; `plan` and `deploy` require at least one workload. Live role access limits token requests. Tokens cannot approve runs, change settings, author Composer changes, or manage assignments. Legacy token scopes are rejected and must be recreated.

## Deployment secret metadata

`GET /api/v1/repositories/{owner}/{repo}/environments/{name}/execution-credential-providers` lists deployment-approved provider names and types. The adjacent `execution-credential-bindings` endpoint lists bindings and accepts `POST` bodies containing `provider` and nonempty `phases`. `POST` to `execution-credential-bindings/{provider}/remove` removes a binding. These endpoints require `manage_credentials`. Each phase can have at most one provider. Binding changes invalidate pending approvals, and identity-conflicting environment variables are rejected.

`POST /api/v1/executions/{run_id}/credentials` is an internal execution endpoint accepting a distinct phase-scoped bearer capability after the execution's one-use claim. It returns `{values, expires_at}` with caching disabled. Refresh rechecks the active run, phase fence, deadline, binding, provider configuration, environment revision, and actor permissions. Responses are never available through browser sessions or API tokens alone. See [Execution credential providers](integrations.md#execution-credential-providers) for runtime delivery and deployment requirements.

`GET /api/v1/repositories/{owner}/{repo}/environments/{name}/secret-references` requires the existing `manage_credentials` permission on a registered environment. It returns public source metadata without retrieving secret values.

```json
{
  "references": [
    {
      "reference": "aws-prod:production/token",
      "name": "production/token",
      "source": "aws-prod",
      "supports_json_key": true
    }
  ]
}
```

A source outage returns 503 instead of a partial catalog. The existing binding POST accepts optional `json_key` alongside `reference`. Unknown or disabled references and unsupported selectors return 422; source outages return 503. Stored binding metadata includes the reference and nullable selector without ciphertext or plaintext. See [Platform integrations](integrations.md) for deployment and provider configuration.


## Execution health

`GET /api/v1/status` remains administrator-only and now includes a `mode` discriminator. `worker` responses contain the current worker heartbeat list and stale-record count. `kubernetes-jobs` responses contain recent supervisor metadata, execution observations, scheduling errors, and recovery completion/freshness; they do not contain a worker list. Both modes return observation time and pending runs. Kubernetes Job observations are snapshots updated during supervision and recovery, rather than a separate live Kubernetes query on each page refresh. Supervisor generations and Job identifiers are metadata only and convey no execution capabilities.

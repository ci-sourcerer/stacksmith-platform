# Architecture and trust

Stacksmith Platform is one public API with separate execution workers. Composer is its visual editor. PostgreSQL is the source of truth for sessions, capabilities, approvals, runs, worker leases, phase claims, deployment locks, and audit events. S3-compatible storage holds private execution handoffs and authorized review artifacts.

## Execution boundary

The API enqueues supported semantic actions. A worker supervisor claims durable work and starts a fresh Docker container or Kubernetes Job for each phase. The supervisor never executes repository hooks itself. In execution mode, Composer inspection and authoring commands use the same isolated runtime boundary.

Only supervisors receive Docker access or namespace-scoped Kubernetes Job-management permissions. Execution containers receive no Docker socket, host mounts, platform database credentials, signing keys, or supervisor Kubernetes token. They use a non-root identity, a read-only root filesystem, bounded writable workspace, resource limits, and phase-specific credentials. Kubernetes execution uses a dedicated service account with token automount disabled and an egress policy that must be extended for any private provider or artifact endpoints.

The execution host remains trusted. Docker socket access gives its supervisor substantial authority over that host. Containers are the isolation boundary for a trusted-team deployment, not a promise of hostile multi-tenant isolation.

## Lifecycle and approvals

Stacksmith's version-two CI manifest owns environment discovery, remote-state validation, planning, saved-plan application, operation previews, after-apply behavior, and destroy ordering. The platform schedules those phases and inserts its approval boundary. There is no arbitrary shell-command or unrestricted CLI-argument API.

A run captures its commit, approved managed configuration, environment settings revision, image digest, manifest digest, generated workspace digest, and plan digest. Every execution downloads and verifies the preceding context. Prepared manifests and saved binary plans must retain their digests across phases. Infrastructure apply/destroy consumes the saved plan from the approved workspace; operation phases retain Stacksmith's behavior of replanning against current outputs.

PR code can execute during preparation, inspection, and planning. Same-repository PR runs therefore wait for maintainer authorization before credentialed execution. Approval is bound to the captured run and exact revision. Mutating runs wait for a separate deployment approval after previews complete. Configuration/credential changes invalidate approval. A newer default-branch commit also invalidates ordinary approvals. Explicit commit overrides require an administrator browser session at submission and deployment approval; these runs retain their selected commit even if the default branch advances.

Mutating workflows hold a deployment lock across preparation, previews, approval, and execution. The default lock key is the repository; administrators can share a key across repositories that affect the same state. Remote state locking is still required. Approvals and private handoffs expire within 24 hours of run creation.

## Recovery and cancellation

Compose workers and temporary Kubernetes supervisors share a run reconciler that persists execution identity before launch and reconciles existing executions after lease expiry. Kubernetes supervisor dispatch requests and generations are durable; a one-minute recovery CronJob replaces interrupted supervisors and retries missed launches. Kubernetes supervisors do not register worker heartbeats or wait at approval gates. A one-use phase capability must be claimed transactionally through the API before any repository code executes. Duplicate execution attempts cannot both claim the phase. Failed Kubernetes Jobs do not automatically retry.

This does not make infrastructure side effects transactional. If a claimed mutating execution disappears, times out, or is cancelled without a complete result, the run becomes `uncertain` and retains its deployment lock. An authorized operator must inspect remote infrastructure and state, record reconciliation, and release the lock. The platform never automatically replays that mutation. Explicit Stacksmith failure reports can include confirmed partial changes.

## Access and credentials

GitHub App user authorization provides browser identity. Repository access is checked with the user's credential and scoped stack roles. See [Roles and access](roles.md) for workload permissions and approval authority. Administrators register each repository in platform settings; only registered repositories are available to platform users. Background checkout uses a separate short-lived installation token scoped by the approved App installation. Install the App on registered repositories and all required private configuration/module repositories.

Session cookies are opaque; their database keys are hashed and GitHub credentials are encrypted. API tokens are hashed, scoped to repositories/environments/actions, revocable, and limited by the issuing session's remaining lifetime. They cannot approve deployments or reconcile uncertain runs. Browser mutations enforce the configured public origin; API bearer requests, signed GitHub webhooks, and one-use execution claims use their own authentication.

Secret bindings are write-only and phase-scoped. Entered values are encrypted using the deployment secret; reference bindings resolve administrator-configured values. Changing bindings increments the environment revision. Source-locking, Python/Git process controls, and runner configuration cannot be overridden through secret bindings.

Review reports are separate from encrypted workspace artifacts. Private handoffs contain sensitive configuration and plans and are never downloadable through user artifact endpoints. Report downloads recheck access through the API. Logs are bounded and known injected credentials are redacted, but arbitrary values resolved by configuration hooks can still be sensitive. Give log/report access only to appropriate platform viewers.

## Composer data

Signed source snapshots still bind source, repository, destination path, principal, base revision, and managed configuration revision. PR creation repeats inspection and checks current GitHub push access and the captured base commit. The platform never writes directly to the default branch through Composer.

Composer browser recovery retains authored values after sign-out and is not a server backup. Diagram metadata and recovery formats remain compatible with existing authored documents. Local authoring mode without execution enabled still uses local Stacksmith subprocesses and should be used only with trusted configuration.

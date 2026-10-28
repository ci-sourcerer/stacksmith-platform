# Settings

Open `/settings` to manage workspace, account, repository, and administration settings. Available sections depend on your [roles and access](roles.md); the API checks permissions independently of the UI.

## Workspace

Choose a repository, Composer stack, and inspection environment, then apply the selection. **Stack preview (no deployment)** supports authoring without deployment setup or credentials. A sole environment with Infrastructure planning access is selected automatically when no inspection preference has been chosen. Changing the environment preserves the Composer draft and undo history while refreshing inspection.

The selection is remembered per account in this browser. **Refresh choices** reloads repositories, stack files, and environments. [Runs](runs.md) starts from the workspace selection but remembers its own target independently.

## GitHub connection

**Account > GitHub connection** contains App installation and repository access links. Administrators also manage **Repository discovery**, which includes repositories matching any configured custom property or topic. Defaults are custom property `stacksmith=stack` or topic `stacksmith-stack`. Custom properties require an organization installation; personal repositories use topics. Missing property metadata does not prevent topic matching.

Use the add and remove controls to edit individual rules. **Save filter** stores the rules; **Refresh repositories** reconciles the App catalog using saved rules. Neither action enables deployments. See [GitHub workflow](github-workflow.md) for installation and synchronization.

## Repository configuration

**Repository configuration** selects how approved layers combine with **Administration > Platform defaults**. Choose **Inherit** to use platform defaults, **Append** to add repository layers, or **Replace** to use only repository layers. Each reference must identify a GitHub file at a full commit SHA.

Add, remove, and reorder layers with the row controls. The effective-layer preview shows their order and provenance; repeated references keep their first occurrence. An **Unsaved preview** has no runtime effect. Save to activate changes; runs that captured different layers need to be planned again.

**Effective configuration** displays authored metadata and source previews for the selected repository's configured layers, including backend, tools, providers, components, associations, and validation policies. With multiple layers, use the layer picker to inspect each before merging. Credential-like source fields are redacted. This page does not resolve runtime values; use [Composer Resolution](using-composer.md#inspect-configuration-resolution) for the current draft's merged configuration and resolution history.

## Deployments and credentials

**Deployment settings** detects deployment roots and environments at the default-branch revision. Review the detected changes before saving, choose **Automatic GitOps** or **Manual runs only**, and enable deployment processing. Refreshing detection alone does not change saved settings. Inactive environments retain history, permissions, and credentials but cannot receive new runs. See [Deployment](deployment.md) for setup details.

**Credentials** manages phase-specific secret bindings and execution identities for the selected environment. Stored values are never returned by read APIs. **Refresh secrets** reloads available reference names. Bind only the phases that need access; Composer environment inspection uses `prepare` credentials, while stack preview receives none. Saving or revoking bindings invalidates pending approvals. See [Platform integrations](integrations.md) for external sources and refreshing cloud credentials.

Administrators assign scoped roles under **Stack roles**. See the [role matrix](roles.md) before granting access.

## Account and administration

**API tokens** lists your tokens across repositories independently of the selected workspace. Choose repository, environment, actions, and workloads explicitly when creating a token. Copy its value once; it cannot be retrieved later. Active, expired, and revoked filters and pagination help manage existing tokens. Only active tokens can be revoked.

Administrators use **Audit log** for platform-wide events and **Execution health** for worker or Kubernetes supervision status. Audit filters and pagination are retained in the URL. These sections, platform defaults, and GitHub connection do not require a selected repository.

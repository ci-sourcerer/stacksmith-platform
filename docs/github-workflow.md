# GitHub workflow

When GitHub App authentication is configured, Composer can discover stack repositories and propose the reviewed stack-file change as a pull request.

## Sign in and choose a stack

Sign in with GitHub, then use **Connect GitHub** to install the configured App on a personal account or organization. GitHub lets you grant access to all repositories or selected repositories. **Manage repository access** opens the installation settings to change that selection. Composer automatically discovers repositories accessible to both the signed-in user and the App, grouped by account. Platform registration and repository topics are not required. Composer discovers conventional stack files such as `stack.yaml`, `stack.yml`, `stack.json`, and their `*.stack.yaml`, `*.stack.yml`, or `*.stack.json` variants.

Repositories where the user has read access can be opened for review. Creating a pull request requires push permission to that repository and, in platform mode, the platform author capability. Fork-based contributions are not supported.

## Create a pull request

After review, Composer rechecks the source and repository permissions. It creates a deterministic `composer/…` branch with one changed stack file and opens a pull request against the branch from which the stack was read. Composer never writes directly to the default branch. Repeating the same proposal reuses the existing pull request.

Pull requests are ready for review by default. Set `STACKSMITH_PLATFORM_DRAFT_PULL_REQUESTS=true` to create draft pull requests instead. After creation, the review panel links to the pull request and offers its `.patch` file for download.

Composer checks that the base branch has not advanced before writing. If it has changed, reopen the stack and review the changes against the new revision. GitHub may still advance the branch after this check; normal pull request conflict handling then applies.

## CI and deployment

The pull request contains the reviewed source change. When platform execution is enabled, same-repository PR events queue plans that wait for maintainer authorization before receiving credentials. Default-branch pushes prepare deployments that wait for approval after previews. Use the Runs page to review reports and approve or cancel those runs. Repository CI can still run additional checks independently. See [Deployment](deployment.md) for App webhooks and environment configuration.

## Repository synchronization and deployments

App installation and repository-access webhooks synchronize the catalog. Stacksmith also reconciles at startup; administrators can use **Settings > Account > GitHub connection > Refresh repositories** to recover missed events. Workspace settings loads repository discovery when opened and provides Refresh choices to reload repositories, stack files, and environments. Revoked or suspended access makes repositories unavailable while retaining configuration and grants. Repository IDs preserve settings across renames.

Discovery does not enable deployment processing. A platform administrator must explicitly enable deployments under **Settings > Repository > Deployment settings**, then configure environments, permissions, and credentials. Both manual execution and deployment webhooks require activation. Existing registered repositories retain their deployment activation during the schema upgrade. Disabling deployments stops queued runs before another execution phase; it does not interrupt an already running phase.

Manual runs can target infrastructure using component tags. Enter comma-separated tags on the Runs page; components must match every tag, including tags supplied by their managed module mapping. Leave the field empty to target all components. Tags do not filter operations in mixed runs. Automatic runs target all components.

# Stacksmith Platform

Stacksmith Platform combines Composer's visual stack editor with GitHub-connected infrastructure execution. Author and review stack changes, open pull requests, run plans, approve deployments, and inspect their logs and structured reports.

GitHub supplies repository access and identity. The platform supplies durable execution, explicit environment permissions, credentials, approval history, and isolated Docker or Kubernetes runtimes. Same-repository PR plans require authorization before code receives credentials; default-branch deployments wait for approval after previews.

Use [Composer](using-composer.md) for visual authoring and the [GitHub workflow](github-workflow.md) for repository changes. [Settings](settings.md) manages workspace, repository discovery, configuration, and account controls. [Runs](runs.md) covers planning, execution, approvals, reports, and recovery.

Read [Configuration](configuration.md), [Deployment](deployment.md), [API](api.md), and [Architecture and trust](architecture-and-trust.md) before setting up hosted execution.

See [Platform integrations](integrations.md) for external secret sources and installed storage or execution providers.

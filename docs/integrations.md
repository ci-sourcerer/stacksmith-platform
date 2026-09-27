# Platform integrations

The platform defines public ABCs in `stacksmith_platform.integrations` for secret sources, execution credential providers, artifact storage, and isolated execution drivers. These integrations belong to Stacksmith Platform; they do not change Stacksmith's CLI or Python API. GitHub, browser authentication, database access, and orchestration remain platform-owned.

## Secret sources

`STACKSMITH_PLATFORM_SECRET_REFERENCES` keeps its existing mapping of reference names to deployment-supplied string values. These names and existing environment bindings continue to work.

Configure additional named sources through `STACKSMITH_PLATFORM_SECRET_SOURCES`. For example, the following JSON enables AWS Secrets Manager in one region.

```json
{
  "aws-prod": {
    "provider": "aws-secrets-manager",
    "options": {
      "region": "us-east-1",
      "tag_key": "stacksmith:platform"
    }
  }
}
```

Source instance names use lowercase letters, numbers, underscores, and hyphens, beginning with a letter. `configured` is reserved. An external reference has the form `aws-prod:production/api-token`. Existing configured-value names cannot begin with a configured source's prefix.

AWS discovery requires the exact, case-sensitive tag `stacksmith:platform=true`. The key is configurable per source; the value must be the string `true`. Discovery excludes secrets scheduled for deletion and verifies exact tag pairs after AWS's prefix filters. Listing is eventually consistent and may take several minutes to reflect newly tagged secrets. The Refresh secrets button reloads discovery.

The picker shows the secret name and source without fetching its value. Bindings can use the entire `SecretString` or an optional top-level JSON field containing a string. Field names are entered explicitly; values and JSON structure are never previewed. Binary secrets, malformed JSON, missing fields, and non-string selected fields fail resolution. AWS metadata is checked again before retrieval; removing the tag disables use even if an older discovery result still lists the secret.

The API principal needs `secretsmanager:ListSecrets` and `secretsmanager:DescribeSecret`. The worker additionally needs `secretsmanager:GetSecretValue` and applicable `kms:Decrypt` permission for customer-managed keys. Restrict IAM access to intended secrets and use tag conditions where supported. A tagged secret is available to all existing credential managers, consistent with configured references; this release adds no repository-specific secret catalog ACL.

AWS sources use boto3's standard credential chain. The optional source `profile` setting selects a profile in deployments that mount their AWS configuration. In Compose, export temporary AWS credentials before starting the platform, for example after signing in with your AWS CLI profile.

To assume a role before accessing Secrets Manager, set `role_arn` in that source's `options`. An optional `external_id` is passed to AWS STS and requires `role_arn`. The source identity comes from the selected profile or the default AWS credential chain, and the SDK refreshes the assumed-role credentials as needed. The API and supervisor each need a source identity allowed to assume the role, and the role must trust those identities and grant the Secrets Manager permissions described above. The source identity must also have access to STS. Source authentication is independent of credentials supplied to stack executions.

[AWS account root credentials cannot assume IAM roles](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_switch-role-api.html). Use an IAM user or a federated workload identity as the source for either secret-source or execution role assumption. The `root` example below can access a secret source directly, but cannot assume a configured role.

```sh
aws login --profile root
eval "$(aws configure export-credentials --profile root --format env)"
uv run poe stack-up
```

Compose forwards the standard AWS access key, secret key, and session token to the API and worker only. They are separate from S3 artifact credentials and are not automatically forwarded into execution containers. Prefix variables with `STACKSMITH_RUN_` only when you also intend to inject them into runs. With Helm, use workload identity through API and supervisor service-account annotations, or supply existing service-account names through `serviceAccounts.api.name` and `serviceAccounts.supervisor.name`. Both principals must be able to reach AWS endpoints.

Each execution phase reads a fresh snapshot of bound external secrets, deduplicating reads of the same reference within that phase. The same resolved strings supply both the HMAC fingerprint and the phase's environment. Changes between phases stop the run before launching the next phase and require a new run. Values remain in memory or the existing encrypted phase handoff; the database stores external reference identifiers, optional JSON fields, and keyed fingerprints, never external plaintext. Source failures fail the run with a sanitized message. Pending runs created before the fingerprint-format change should be recreated.

## Execution credential providers

Administrators configure approved execution identities through `STACKSMITH_PLATFORM_EXECUTION_CREDENTIAL_PROVIDERS`. Each entry selects an integration and its options. For example, this configuration allows credential managers to select separate AWS identities for planning and deployment.

```json
{
  "aws-plan": {
    "provider": "aws-assume-role",
    "options": {
      "region": "us-east-1",
      "role_arn": "arn:aws:iam::123456789012:role/stacksmith-plan"
    }
  },
  "aws-deploy": {
    "provider": "aws-assume-role",
    "options": {
      "region": "us-east-1",
      "role_arn": "arn:aws:iam::123456789012:role/stacksmith-deploy"
    }
  }
}
```

The built-in `aws-assume-role` provider accepts `role_arn`, an STS `region`, optional source `profile` and `external_id`, and `duration_seconds` from 900 through 43200, defaulting to 3600. AWS also enforces the target role's maximum duration and its role-chaining limits. The API uses its AWS credential chain to assume the selected role, so its source identity needs `sts:AssumeRole`, and the target role must trust that identity. This identity is independent of the identities used to retrieve deployment secrets. The API needs access to STS; execution workloads need access to their provider endpoints. Configure the workload's AWS region through its run variables or infrastructure provider configuration.

In **Settings > Credentials > Execution identities**, choose an approved identity and its phases. Each phase can have at most one identity. Bind a planning identity to `prepare` when configuration hooks or environment-backed Composer inspections need cloud access. Stack previews receive no deployment credentials. Bind deployment identities only to the phases that need their permissions. Saving or revoking a binding invalidates pending approvals. Global run variables and phase-specific secret bindings cannot override AWS credential keys, profiles, credential files, or credential endpoint controls in a phase with an AWS identity; conflicting configurations are rejected at binding time and checked again before launch.

Each isolated phase starts a credential endpoint on its own loopback interface and supplies the standard [AWS container credential provider](https://docs.aws.amazon.com/sdkref/latest/guide/feature-container-credentials.html) settings to subprocesses. Supported SDKs, AWS CLI, and Terraform/OpenTofu AWS consumers can refresh credentials during long phases. The execution receives a distinct phase-scoped capability rather than the platform's AWS keys. Every broker request checks the consumed phase claim, run status, fence, deadline, current workload permissions, environment revision, binding, and provider configuration. Completion, cancellation, expiry, permission revocation, or configuration changes prevent further refresh. Already issued AWS credentials retain their AWS expiration; revoking a platform binding does not revoke an STS session at AWS.

Credential values and all issued redaction values are encrypted in a separate phase record. Redactions commit before credentials are returned, so logs, reports, and inspection diagnostics cover credentials issued after phase launch. Refreshed values do not change the run's credential fingerprint; provider identity and configuration changes do. Phase credential records expire with private run metadata.

The catalog and binding APIs require environment credential-management access. The catalog returns names and integration types only; provider configuration and issued values are excluded. Provider integrations implement `ExecutionCredentialProvider.issue()` and return an `ExecutionCredentialLease` with credential values and an aware expiration, raising `ExecutionCredentialError` with a sanitized message on failure. Set `protocol` to a supported runtime delivery adapter and declare identity-related `conflicting_variables`. The initial adapter is `aws-container`; additional clouds can add their own adapters without changing Stacksmith. Register implementations under the `stacksmith_platform.execution_credential_providers` entry-point group.

Deploy the updated API, supervisor, and execution images together, run schema migrations and runtime role grants through `stacksmith-platform-init --grant-roles`, and create fresh runs using the updated immutable execution image. In Helm, put provider definitions in the existing shared configuration Secret. Compose forwards the new setting to the API and supervisor. The local Kubernetes helper keeps host AWS credentials available to the control plane but stops automatically copying them into every run when execution providers are configured; explicitly supplied run variables still apply.

## Installed providers

Install provider packages in the platform API and worker image. Register a class through the appropriate Python entry-point group.

```toml
[project.entry-points."stacksmith_platform.secret_sources"]
vault = "example_platform_plugins.vault:VaultSecretSource"

[project.entry-points."stacksmith_platform.artifact_stores"]
custom-store = "example_platform_plugins.storage:CustomArtifactStore"

[project.entry-points."stacksmith_platform.execution_drivers"]
custom-runner = "example_platform_plugins.runner:CustomExecutionDriver"
```

Implement the corresponding ABC and accept `(settings, options)` in the constructor. Set the class's `config_model` to a Pydantic model derived from `IntegrationConfig`; `options` is an instance of that model, validated before construction. Unsupported options are rejected and validation input values are hidden. Constructors should avoid network calls where possible. Only selected implementations are loaded; unknown names, duplicate registrations, incompatible classes, and incomplete ABC implementations fail construction.

`SecretSource` implements `discover()`, `validate(name)`, and `resolve(name)`. The first two return metadata or validate availability without retrieving plaintext. `resolve()` returns the current string value. Set `supports_json_key = True` to enable the platform's top-level JSON field selector. Raise `SecretReferenceError` for unavailable or unsupported references and `SecretSourceError` for source failures. Both must carry sanitized messages; do not include provider response bodies or secret values. The catalog adds the source namespace and handles selectors and phase-local deduplication.

`ArtifactStorage` implements `put`, `get`, `digest`, `url`, and `delete`. Preserve the platform's 16 MiB result read and one GiB workspace limits. URLs must authorize a single HTTP object transfer for the requested lifetime. Deletion is idempotent. Raise `ArtifactNotFoundError` for missing reads so the supervisor can handle incomplete execution handoffs independently of the storage SDK.

`ExecutionDriver` implements `inspect`, `launch`, `logs`, and `cleanup`. Inspection returns `missing`, `running`, `succeeded`, or `failed`. Preserve stable resource names, immutable images, phase deadlines, bounded logs, restricted workloads, and phase-scoped handoff credentials. Inspection must not launch work; cleanup is idempotent. Plugins execute as trusted deployment code and are selected only by deployment configuration.

Select storage through `STACKSMITH_PLATFORM_ARTIFACT_STORE` and configure it through `STACKSMITH_PLATFORM_ARTIFACT_OPTIONS`. Select the runner through `STACKSMITH_PLATFORM_RUNNER` and `STACKSMITH_PLATFORM_RUNNER_OPTIONS`. Defaults remain `s3` and `docker`; Helm selects `kubernetes`. Built-in storage and drivers retain their existing settings and accept no extra options. Installation alone does not enable a provider.


Kubernetes supervision is separate from the execution-driver contract. The chart selects `STACKSMITH_PLATFORM_SUPERVISION_MODE=kubernetes-jobs`; Compose keeps the default `worker` mode. `ExecutionDriver` still represents individual isolated phases. Temporary supervisors and recovery use the same source, storage, and driver factories as Compose. Supervisor Jobs reference the shared configuration Secret and the separate supervisor database Secret rather than copying the API's credentials.

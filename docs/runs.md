# Runs

Open `/runs` to plan and execute infrastructure or operations and review their results. Repository deployment processing must be enabled, and your [roles](roles.md) must cover the selected environment and workloads.

## Choose a target and action

Runs starts from your workspace once, then remembers its own repository and environment per account in this browser. Links use `repo` and `environment` query parameters without changing Composer's saved workspace. **All environments** shows repository history; choose a specific active environment to start a run. A repository with one active environment selects it automatically.

Under **Include**, select Infrastructure, Operations, or both. Enable **Apply after approval** to execute after reviewing previews, or **Plan to destroy** for removal plans. Select operation names to run a subset; an empty selection includes all declared operations and required dependencies.

Infrastructure tags are comma-separated and match every listed tag, including managed module tags. Leave tags empty to target all components. Tags do not filter operations. Changing the target clears operation selections, apply/destruction flags, and administrator overrides.

Manual runs capture the latest default-branch commit on the server. Administrators can use **Advanced run options** for an explicit commit override or allow a local backend on a non-mutating test plan. Local state is not shared with later runs. Commit overrides also require administrator approval before deployment.

## Review and approve

Run details show phase progress, bounded logs, structured reports, and authorized artifacts. Same-repository PR runs wait for maintainer authorization before code receives credentials. Mutating runs wait for deployment approval after previews. Approvals require an interactive browser session and matching workload access; configuration, credential, permission, or default-branch changes can invalidate them.

Apply uses the saved infrastructure plan from the reviewed workspace. Operation execution follows Stacksmith's lifecycle and can replan against current outputs. See [Architecture and trust](architecture-and-trust.md) for execution boundaries and approval expiry.

## Cancel, reconcile, or delete

Cancellation requests runtime termination. A mutating phase without a complete result can become **uncertain** and retain its deployment lock. Inspect remote infrastructure and state, then record reconciliation to release the lock. Reconciliation never resumes the interrupted command.

Administrators can delete terminal runs from history. Deletion permanently removes the run and its associated artifacts and execution records, leaving deployed infrastructure and audit events intact. Active runs cannot be deleted. Uncertain runs are considered terminal, and deletion releases their retained lock; inspect and reconcile infrastructure before deleting their history.

History uses server-side pagination. Administrators can open the run's filtered **Audit log** for recorded activity. See [API](api.md#run-requests) for automation.

ACTIONS = {
    "plan",
    "apply",
    "destroy",
    "destroy-plan",
    "plan-operation",
    "apply-operation",
}
MUTATIONS = {"apply", "destroy", "apply-operation"}
TERMINAL = {"succeeded", "failed", "cancelled", "expired", "uncertain"}


def phases(
    action: str,
    include_infrastructure: bool | None = None,
    include_operations: bool | None = None,
    apply_after_plan: bool | None = None,
    destroy_plan: bool | None = None,
) -> list[str]:
    """Return selected Stacksmith CI phases and any approval boundary.

    Args:
        action: Stacksmith command selected for the run.
        include_infrastructure: Whether to plan infrastructure changes.
        include_operations: Whether to plan stack operations.
        apply_after_plan: Whether to queue an apply after approval.
        destroy_plan: Whether infrastructure and operations should be destroyed.

    Returns:
        Ordered lifecycle phases.

    Raises:
        ValueError: The action and selected phases are incompatible.
    """
    if include_infrastructure is None:
        include_infrastructure = action not in {"plan-operation", "apply-operation"}
        include_operations = True
        apply_after_plan = action in MUTATIONS
        destroy_plan = action in {"destroy", "destroy-plan"}
    if any(
        value is None for value in (include_operations, apply_after_plan, destroy_plan)
    ):
        raise ValueError("All phase selections must be provided together")
    if not include_infrastructure and not include_operations:
        raise ValueError("At least one phase must be selected")
    if destroy_plan and not include_infrastructure:
        raise ValueError("Destroy plans require infrastructure planning")

    expected_action = (
        "destroy"
        if destroy_plan and apply_after_plan
        else "destroy-plan"
        if destroy_plan
        else "apply"
        if apply_after_plan and include_infrastructure
        else "apply-operation"
        if apply_after_plan
        else "plan"
        if include_infrastructure
        else "plan-operation"
    )
    if action != expected_action:
        raise ValueError("Action does not match the selected phases")

    selected = ["prepare"]
    if include_infrastructure:
        selected.append("plan")
    if include_operations:
        selected.append("plan-operation")
    if apply_after_plan:
        selected.append("approval")
        if destroy_plan and include_operations:
            selected.append("operation")
        if include_infrastructure:
            selected.append("destroy" if destroy_plan else "apply")
        if include_operations and not destroy_plan:
            selected.append("operation")
    return selected


def mutation_phase(action: str, phase: str) -> bool:
    """Identify phases that may mutate infrastructure or operation state.

    Args:
        action: Requested action.
        phase: Current phase.

    Returns:
        Whether this action and phase may mutate remote state.
    """
    return action in MUTATIONS and phase in {"apply", "destroy", "operation"}


def deployment_lock(connection, run) -> bool:
    """Acquire or retain the durable deployment lock for one mutating run.

    Args:
        connection: Active database transaction.
        run: Durable run record.

    Returns:
        Whether the run owns the lock.
    """
    from .database import locks, row

    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            "SELECT pg_advisory_xact_lock(hashtext(%s))", (run["lock_key"],)
        )
    lock = row(connection, locks, run["lock_key"])
    if lock:
        return lock["run"] == run["id"]
    connection.execute(locks.insert().values(id=run["lock_key"], run=run["id"]))
    return True

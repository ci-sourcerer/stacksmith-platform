from sqlalchemy import and_

from .database import grants
from .errors import ComposerError

WORKLOADS = {"infrastructure", "operations"}
MANAGEMENT = {"configure_environment", "manage_credentials"}
SHARED_MANAGEMENT = {"configure_repository", "author"}


def is_admin(settings, login):
    """Determine whether a login is a configured platform administrator.

    Args:
        settings: Platform settings.
        login: GitHub login.

    Returns:
        Whether the login has platform administration access.
    """
    return login.lower() in {value.lower() for value in settings.admin_logins}


def _assignment_capabilities(assignment):
    capabilities = {"view"}
    if assignment["role"] == "viewer":
        return capabilities
    for workload in assignment["workloads"]:
        capabilities.update({f"plan_{workload}", f"execute_{workload}"})
        if assignment["role"] == "provisioner":
            capabilities.update({f"approve_{workload}", f"reconcile_{workload}"})
    if assignment["role"] == "provisioner":
        capabilities.update(MANAGEMENT)
        if assignment["environment"] == "*":
            capabilities.update(SHARED_MANAGEMENT)
    return capabilities


def access_for(connection, settings, login, repository, environment="*"):
    """Compute additive capabilities without combining unrelated role scopes.

    Args:
        connection: Active database transaction.
        settings: Platform settings.
        login: GitHub login.
        repository: Registered repository name.
        environment: Environment name, or `*` for shared repository settings.

    Returns:
        Administrator status, applicable assignments, and effective capabilities.
    """
    assignments = [
        dict(item)
        for item in connection.execute(
            grants.select().where(
                and_(
                    grants.c.login == login.lower(),
                    grants.c.repository == repository,
                )
            )
        ).mappings()
    ]
    capabilities = set()
    administrator = is_admin(settings, login)
    if administrator:
        capabilities.update({"view", "manage_access", *MANAGEMENT, *SHARED_MANAGEMENT})
        capabilities.update(
            f"{action}_{workload}"
            for action in ("plan", "execute", "approve", "reconcile")
            for workload in WORKLOADS
        )
    for assignment in assignments:
        if assignment["environment"] in {"*", environment}:
            capabilities.update(_assignment_capabilities(assignment))
        elif environment == "*":
            capabilities.add("view")
    return {
        "administrator": administrator,
        "assignments": assignments,
        "capabilities": sorted(capabilities),
    }


def require_access(connection, settings, login, repository, environment, capability):
    """Require an effective role capability.

    Args:
        connection: Active database transaction.
        settings: Platform settings.
        login: GitHub login.
        repository: Repository name.
        environment: Requested environment or `*`.
        capability: Required capability.

    Returns:
        Effective access information.

    Raises:
        ComposerError: No applicable role grants the requested capability.
    """
    access = access_for(connection, settings, login, repository, environment)
    if capability not in access["capabilities"]:
        raise ComposerError("Your stack role does not allow this action.", 403)
    return access


def run_workloads(spec):
    """Identify all workloads selected by a run specification.

    Args:
        spec: Immutable run specification.

    Returns:
        Selected workload names, including legacy phase specifications.
    """
    phases = set(spec.get("phases", []))
    return {
        workload
        for workload, selected in (
            ("infrastructure", bool(phases & {"plan", "apply", "destroy"})),
            ("operations", bool(phases & {"plan-operation", "operation"})),
        )
        if selected
    }


def require_workloads(
    connection, settings, login, repository, environment, action, workloads
):
    """Require the same action for every selected workload.

    Args:
        connection: Active database transaction.
        settings: Platform settings.
        login: GitHub login.
        repository: Repository name.
        environment: Environment name.
        action: Planning, execution, approval, or reconciliation action.
        workloads: Selected workload names.

    Returns:
        None.

    Raises:
        ComposerError: Any selected workload is unauthorized.
    """
    for workload in workloads:
        require_access(
            connection, settings, login, repository, environment, f"{action}_{workload}"
        )

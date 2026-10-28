import argparse
import logging
import time
from contextlib import closing
from uuid import uuid4

from .database import Database, row, runs, scheduler_health
from .factories import artifact_storage, execution_driver
from .github_transport import GitHubTransport
from .provider import GitHubProvider
from .scheduling import ACTIONABLE, KubernetesDispatcher
from .settings import Settings
from .worker import RunReconciler


def supervise(
    reconciler: RunReconciler, run_id: str, generation: int, interval: float = 2
) -> None:
    """Supervise one active run without waiting for approval or unrelated work.

    Args:
        reconciler: Configured lifecycle reconciler with a unique owner identity.
        run_id: Target run identifier.
        generation: Current supervisor generation.
        interval: Active execution polling interval.

    Returns:
        None.
    """
    try:
        while True:
            with reconciler.database.transaction() as connection:
                run = row(connection, runs, run_id)
            if (
                not run
                or run["status"] not in ACTIONABLE
                or (run["supervision"] or {}).get("generation") != generation
            ):
                return
            if not reconciler.reconcile_run(run_id, generation):
                return
            _observe_supervisor(reconciler, run_id, generation, False)
            time.sleep(interval)
    finally:
        _observe_supervisor(reconciler, run_id, generation, True)


def _observe_supervisor(reconciler, run_id, generation, release):
    with reconciler.database.transaction() as connection:
        current = (
            connection.execute(
                runs.select().where(runs.c.id == run_id).with_for_update()
            )
            .mappings()
            .first()
        )
        if (
            not current
            or (current["supervision"] or {}).get("generation") != generation
            or current["worker"] not in {None, reconciler.identifier}
        ):
            return
        connection.execute(
            runs.update()
            .where(runs.c.id == run_id)
            .values(
                supervision={
                    **current["supervision"],
                    "state": (
                        "finished"
                        if current["status"] not in ACTIONABLE
                        else "interrupted"
                    )
                    if release
                    else "active",
                    "observed_at": time.time(),
                },
                **({"worker": None, "lease": None} if release else {}),
            )
        )


def _reconciler(settings, transport):
    if not settings.execution_enabled or settings.supervision_mode != "kubernetes-jobs":
        raise ValueError(
            "Finite supervisors require enabled Kubernetes Job supervision"
        )
    reconciler = RunReconciler(
        settings,
        Database(settings.database_url, settings.secret_key),
        execution_driver(settings),
        artifact_storage(settings),
        GitHubProvider(settings, transport),
    )
    reconciler.identifier = f"supervisor-{uuid4().hex}"
    return reconciler


def supervise_main() -> None:
    """Run the Kubernetes supervisor command for a single persisted generation.

    Returns:
        None.

    Raises:
        ValueError: Kubernetes supervision is not configured.
    """
    parser = argparse.ArgumentParser(description="Supervise one active platform run")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--generation", type=int, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    with closing(GitHubTransport()) as transport:
        supervise(_reconciler(Settings(), transport), args.run_id, args.generation)


def maintain(reconciler: RunReconciler, dispatcher: KubernetesDispatcher) -> int:
    """Perform finite maintenance and recover missed supervisor dispatches.

    Args:
        reconciler: Configured lifecycle reconciler.
        dispatcher: Supervisor Job dispatcher.

    Returns:
        Number of dispatched supervisors.
    """
    reconciler.maintenance()
    dispatched = dispatcher.dispatch_pending(limit=100, budget=90)
    with reconciler.database.transaction() as connection:
        previous = row(connection, scheduler_health, "recovery")
        completed = previous["completed"] if previous else None
        if not dispatcher.last_error:
            completed = time.time()
        connection.execute(
            scheduler_health.delete().where(scheduler_health.c.id == "recovery")
        )
        connection.execute(
            scheduler_health.insert().values(
                id="recovery",
                completed=completed,
                error="Kubernetes scheduling is unavailable; recovery will retry."
                if dispatcher.last_error
                else None,
            )
        )
    return dispatched


def maintain_main() -> None:
    """Run a bounded recovery pass from a Kubernetes CronJob.

    Returns:
        None.

    Raises:
        ValueError: Kubernetes supervision is not configured.
    """
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    with closing(GitHubTransport()) as transport:
        reconciler = _reconciler(settings, transport)
        maintain(reconciler, KubernetesDispatcher(settings, reconciler.database))

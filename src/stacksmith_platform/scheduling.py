import json
import time

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from sqlalchemy import or_
from urllib3.exceptions import HTTPError

from .database import row, runs
from .lifecycle import MUTATIONS, deployment_lock

ACTIONABLE = {"queued", "running", "cancelling"}


def requested_supervision(
    settings, status: str, previous: dict | None = None
) -> dict | None:
    """Persist a dispatch request together with its run transition.

    Args:
        settings: Platform deployment settings.
        status: New run status.
        previous: Existing supervisor identity and generation.

    Returns:
        Kubernetes dispatch metadata, or `None` for persistent-worker mode.
    """
    if settings.supervision_mode != "kubernetes-jobs":
        return None
    return {
        **(previous or {"generation": 0}),
        "requested": status in ACTIONABLE,
        "requested_at": time.time(),
    }


def _environment(settings):
    # Credentials come from referenced Secrets, never from the API's environment.
    fields = (
        "supervision_mode",
        "supervisor_image",
        "supervisor_namespace",
        "supervisor_config_secret",
        "supervisor_database_secret",
        "supervisor_service_account",
        "supervisor_image_pull_policy",
        "supervisor_cpu",
        "supervisor_memory",
        "execution_enabled",
        "runner",
        "execution_image",
        "allow_local_execution_image",
        "execution_namespace",
        "execution_service_account",
        "execution_cpu",
        "execution_memory",
        "public_url",
        "execution_callback_url",
        "admin_logins",
        "phase_timeout",
        "approval_ttl",
        "artifact_retention",
        "command_timeout",
        "inspection_wait_timeout",
        "inspection_poll_interval",
    )
    return [
        {
            "name": f"STACKSMITH_PLATFORM_{name.upper()}",
            "value": json.dumps(getattr(settings, name))
            if isinstance(getattr(settings, name), (bool, list))
            else str(getattr(settings, name)),
        }
        for name in fields
        if getattr(settings, name) is not None
    ]


def supervisor_deadline(settings, run) -> int:
    """Bound supervision through the next approval gate, including Job startup.

    Args:
        settings: Execution deadlines.
        run: Current run record.

    Returns:
        Deadline in seconds including scheduling overhead.
    """
    remaining = []
    for phase in run["spec"]["phases"][run["phase"] :]:
        if phase == "approval":
            break
        remaining.append(
            settings.command_timeout if phase == "inspect" else settings.phase_timeout
        )
    return 300 + sum(timeout + 300 for timeout in remaining)


def supervisor_job(settings, run, metadata) -> dict:
    """Build a supervisor Job that references deployment-managed credentials.

    Args:
        settings: Supervisor image, service account, and Secret references.
        run: Target run.
        metadata: Persisted dispatch identity.

    Returns:
        Restricted control-plane Job manifest without credential values.
    """
    labels = {
        "app": "stacksmith-supervisor",
        "run": run["id"],
        "generation": str(metadata["generation"]),
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": metadata["job"], "labels": labels},
        "spec": {
            "backoffLimit": 0,
            "ttlSecondsAfterFinished": 86400,
            "activeDeadlineSeconds": supervisor_deadline(settings, run),
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": settings.supervisor_service_account,
                    "automountServiceAccountToken": True,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 10001,
                        "runAsGroup": 10001,
                        "fsGroup": 10001,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "supervisor",
                            "image": settings.supervisor_image,
                            "imagePullPolicy": settings.supervisor_image_pull_policy,
                            "command": [
                                "stacksmith-platform-supervise",
                                "--run-id",
                                run["id"],
                                "--generation",
                                str(metadata["generation"]),
                            ],
                            "envFrom": [
                                {
                                    "secretRef": {
                                        "name": settings.supervisor_config_secret
                                    }
                                },
                                {
                                    "secretRef": {
                                        "name": settings.supervisor_database_secret
                                    }
                                },
                            ],
                            "env": _environment(settings),
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "256Mi"},
                                "limits": {
                                    "cpu": settings.supervisor_cpu,
                                    "memory": settings.supervisor_memory,
                                },
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                        }
                    ],
                    "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "256Mi"}}],
                },
            },
        },
    }


class KubernetesDispatcher:
    """Reserve supervisor identities before launching recoverable Jobs.

    Args:
        settings: Kubernetes supervision settings.
        database: Shared durable run database.
        batch: Optional Kubernetes API client for tests.
    """

    def __init__(self, settings, database, batch=None):
        self.settings = settings
        self.database = database
        if batch is None:
            config.load_incluster_config()
        self.batch = batch or client.BatchV1Api()
        self.last_error = False

    def _job(self, name):
        try:
            return self.batch.read_namespaced_job(
                name, self.settings.supervisor_namespace, _request_timeout=5
            )
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise

    def _reserve(self, identifier):
        with self.database.transaction() as connection:
            run = (
                connection.execute(
                    runs.select()
                    .where(runs.c.id == identifier)
                    .with_for_update(skip_locked=True)
                )
                .mappings()
                .first()
            )
            if not run or run["status"] not in ACTIONABLE or run["supervision"] is None:
                return None
            metadata = dict(run["supervision"])
            job = self._job(metadata["job"]) if metadata.get("job") else None
            if job:
                metadata.update(
                    state="succeeded"
                    if job.status.succeeded
                    else "failed"
                    if job.status.failed
                    else "active",
                    observed_at=time.time(),
                )
                connection.execute(
                    runs.update()
                    .where(runs.c.id == identifier)
                    .values(supervision=metadata)
                )
                if not (job.status.succeeded or job.status.failed):
                    return None
            if run["worker"] and run["lease"] and run["lease"] > time.time():
                return None
            if run["action"] in MUTATIONS and not deployment_lock(connection, run):
                return None
            if not metadata.get("job") or job or metadata.get("dispatched_at"):
                metadata = {
                    "generation": metadata.get("generation", 0) + 1,
                    "requested_at": metadata.get("requested_at", time.time()),
                }
                metadata["job"] = f"ss-supervisor-{identifier}-{metadata['generation']}"
            metadata.update(requested=True, error=None)
            connection.execute(
                runs.update()
                .where(runs.c.id == identifier)
                .values(supervision=metadata)
            )
            return dict(run), metadata

    def _record(self, identifier, metadata, error=None):
        with self.database.transaction() as connection:
            current = (
                connection.execute(
                    runs.select().where(runs.c.id == identifier).with_for_update()
                )
                .mappings()
                .first()
            )
            if (
                not current
                or (current["supervision"] or {}).get("generation")
                != metadata["generation"]
            ):
                return
            connection.execute(
                runs.update()
                .where(runs.c.id == identifier)
                .values(
                    supervision={
                        **current["supervision"],
                        "error": error,
                        "requested": bool(error),
                        **(
                            {"dispatched_at": time.time(), "state": "pending"}
                            if not error
                            else {}
                        ),
                    }
                )
            )

    def dispatch(self, identifier: str) -> bool:
        """Attempt one dispatch, retaining a durable request on API failure.

        Args:
            identifier: Target run identifier.

        Returns:
            Whether the current supervisor Job was created or already exists.
        """
        metadata = None
        try:
            reserved = self._reserve(identifier)
            if reserved is None:
                return False
            run, metadata = reserved
            try:
                self.batch.create_namespaced_job(
                    self.settings.supervisor_namespace,
                    supervisor_job(self.settings, run, metadata),
                    _request_timeout=5,
                )
            except ApiException as exc:
                if exc.status != 409:
                    raise
                existing = self._job(metadata["job"])
                if (
                    not existing
                    or existing.metadata.labels.get("run") != identifier
                    or existing.metadata.labels.get("generation")
                    != str(metadata["generation"])
                ):
                    raise
        except ApiException, HTTPError:
            self.last_error = True
            if metadata is None:
                with self.database.transaction() as connection:
                    current = row(connection, runs, identifier)
                    metadata = current["supervision"] if current else None
            if metadata:
                self._record(
                    identifier,
                    metadata,
                    "Kubernetes supervisor scheduling is unavailable; recovery will retry.",
                )
            return False
        self._record(identifier, metadata)
        return True

    def dispatch_pending(self, limit: int = 20, budget: float = 10) -> int:
        """Attempt bounded dispatch for runnable requests and interrupted supervisors.

        Args:
            limit: Maximum successful dispatches.
            budget: Maximum time to begin new attempts, in seconds.

        Returns:
            Number of dispatched supervisor Jobs.
        """
        self.last_error = False
        with self.database.transaction() as connection:
            pending = (
                connection.execute(
                    runs.select()
                    .with_only_columns(runs.c.id)
                    .where(
                        runs.c.status.in_(ACTIONABLE),
                        runs.c.supervision.is_not(None),
                        or_(
                            runs.c.worker.is_(None),
                            runs.c.lease < time.time(),
                            runs.c.supervision["requested"].as_boolean() == True,
                        ),
                    )
                    .order_by(runs.c.created)
                )
                .scalars()
                .all()
            )
        started = time.monotonic()
        dispatched = 0
        for identifier in pending:
            if dispatched >= limit or time.monotonic() - started >= budget:
                break
            dispatched += self.dispatch(identifier)
        return dispatched

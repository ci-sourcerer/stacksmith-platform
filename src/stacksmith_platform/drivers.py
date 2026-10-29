import docker
from docker.errors import NotFound
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from .certificates import CA_BUNDLE_MOUNT_PATH
from .integrations import ExecutionDriver


class DockerDriver(ExecutionDriver):
    """Launch disposable containers through a trusted Docker supervisor.

    Args:
        settings: Execution resource and network policy.
    """

    def __init__(self, settings, options=None):
        self.settings = settings
        self.client = docker.from_env()

    def inspect(self, name):
        """Inspect a named execution without launching a replacement.

        Args:
            name: Stable execution name.

        Returns:
            Missing, running, succeeded, or failed status.
        """
        try:
            container = self.client.containers.get(name)
        except NotFound:
            return "missing"
        if container.status in {"created", "running", "restarting"}:
            return "running"
        return (
            "succeeded" if container.attrs["State"].get("ExitCode") == 0 else "failed"
        )

    def launch(self, name, image, environment, timeout):
        """Launch an isolated execution with an optional read-only CA mount.

        Args:
            name: Stable execution name.
            image: Immutable execution image digest.
            environment: Single-phase handoff capabilities.
            timeout: Phase deadline in seconds.

        Returns:
            None.
        """
        self.client.containers.run(
            image,
            entrypoint=["stacksmith-platform-execute"],
            command=[],
            name=name,
            detach=True,
            environment=environment,
            labels={"stacksmith.platform/execution": name},
            network=self.settings.docker_network,
            mem_limit=self.settings.docker_memory,
            nano_cpus=int(float(self.settings.execution_cpu) * 1_000_000_000),
            pids_limit=512,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            read_only=True,
            volumes=(
                {
                    self.settings.ca_bundle: {
                        "bind": CA_BUNDLE_MOUNT_PATH,
                        "mode": "ro",
                    }
                }
                if self.settings.ca_bundle
                else None
            ),
            tmpfs={
                "/work": "rw,exec,nosuid,nodev,size=4g,uid=10001,gid=10001",
                "/tmp": "rw,exec,nosuid,nodev,size=1g,uid=10001,gid=10001",
            },
            user="10001:10001",
            working_dir="/work",
            init=True,
            log_config=docker.types.LogConfig(
                type="json-file", config={"max-size": "1m", "max-file": "2"}
            ),
        )

    def logs(self, name):
        """Collect bounded live execution output.

        Args:
            name: Stable execution name.

        Returns:
            UTF-8 log tail.
        """
        try:
            return (
                self.client.containers.get(name)
                .logs(tail=300)
                .decode(errors="replace")[-65536:]
            )
        except NotFound:
            return ""

    def cleanup(self, name):
        """Stop and remove a finished or cancelled execution.

        Args:
            name: Stable execution name.

        Returns:
            None.
        """
        try:
            self.client.containers.get(name).remove(force=True)
        except NotFound:
            return


class KubernetesDriver(ExecutionDriver):
    """Run execution phases as restricted Jobs in a dedicated namespace.

    Args:
        settings: Namespace, service account, and resource policy.
    """

    def __init__(self, settings, options=None):
        self.settings = settings
        config.load_incluster_config()
        self.batch = client.BatchV1Api()
        self.core = client.CoreV1Api()

    def inspect(self, name):
        """Inspect a phase Job.

        Args:
            name: Stable Job name.

        Returns:
            Missing, running, succeeded, or failed status.
        """
        try:
            status = self.batch.read_namespaced_job(
                name, self.settings.execution_namespace
            ).status
        except ApiException as exc:
            if exc.status == 404:
                return "missing"
            raise
        if status.succeeded:
            return "succeeded"
        if status.failed:
            return "failed"
        return "running"

    def launch(self, name, image, environment, timeout):
        """Create a non-retrying, bounded execution Job.

        Args:
            name: Stable Job name.
            image: Immutable execution image digest.
            environment: Phase-scoped capabilities.
            timeout: Execution deadline in seconds.

        Returns:
            None.
        """
        self.batch.create_namespaced_job(
            self.settings.execution_namespace,
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": {"name": name, "labels": {"app": "stacksmith-execution"}},
                "spec": {
                    "backoffLimit": 0,
                    "activeDeadlineSeconds": timeout,
                    "ttlSecondsAfterFinished": 86400,
                    "template": {
                        "metadata": {"labels": {"app": "stacksmith-execution"}},
                        "spec": {
                            "serviceAccountName": self.settings.execution_service_account,
                            "automountServiceAccountToken": False,
                            "restartPolicy": "Never",
                            "securityContext": {
                                "runAsNonRoot": True,
                                "runAsUser": 10001,
                                "runAsGroup": 10001,
                                "fsGroup": 10001,
                                "seccompProfile": {"type": "RuntimeDefault"},
                            },
                            "containers": [
                                {
                                    "name": "execution",
                                    "image": image,
                                    "imagePullPolicy": (
                                        "Never"
                                        if self.settings.allow_local_execution_image
                                        else "IfNotPresent"
                                    ),
                                    "command": ["stacksmith-platform-execute"],
                                    "workingDir": "/work",
                                    "env": [
                                        {"name": key, "value": value}
                                        for key, value in environment.items()
                                    ],
                                    "resources": {
                                        "requests": {"cpu": "100m", "memory": "256Mi"},
                                        "limits": {
                                            "cpu": self.settings.execution_cpu,
                                            "memory": self.settings.execution_memory,
                                        },
                                    },
                                    "securityContext": {
                                        "allowPrivilegeEscalation": False,
                                        "readOnlyRootFilesystem": True,
                                        "capabilities": {"drop": ["ALL"]},
                                    },
                                    "volumeMounts": [
                                        {"name": "work", "mountPath": "/work"},
                                        {"name": "tmp", "mountPath": "/tmp"},
                                    ],
                                }
                            ],
                            "volumes": [
                                {"name": "work", "emptyDir": {"sizeLimit": "4Gi"}},
                                {"name": "tmp", "emptyDir": {"sizeLimit": "1Gi"}},
                            ],
                        },
                    },
                },
            },
        )

    def logs(self, name):
        """Read a bounded execution pod log tail.

        Args:
            name: Job name.

        Returns:
            Available UTF-8 log text.
        """
        pods = self.core.list_namespaced_pod(
            self.settings.execution_namespace, label_selector=f"job-name={name}"
        ).items
        if not pods or not pods[0].status.container_statuses:
            return ""
        try:
            return self.core.read_namespaced_pod_log(
                pods[0].metadata.name,
                self.settings.execution_namespace,
                tail_lines=300,
                limit_bytes=65536,
            )
        except ApiException as exc:
            if exc.status in {400, 404}:
                return ""
            raise

    def cleanup(self, name):
        """Delete a Job and its execution pods.

        Args:
            name: Job name.

        Returns:
            None.
        """
        try:
            self.batch.delete_namespaced_job(
                name, self.settings.execution_namespace, propagation_policy="Foreground"
            )
        except ApiException as exc:
            if exc.status != 404:
                raise

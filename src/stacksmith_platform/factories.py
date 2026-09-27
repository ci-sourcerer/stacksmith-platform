from .drivers import DockerDriver, KubernetesDriver
from .integrations import ArtifactStorage, ExecutionDriver, create_integration
from .storage import ArtifactStore


def artifact_storage(settings) -> ArtifactStorage:
    """Build the configured artifact adapter.

    Args:
        settings: Platform settings.

    Returns:
        Configured artifact storage.

    Raises:
        ValueError: Plugin configuration or registration is invalid.
    """
    return create_integration(
        "artifact_stores",
        settings.artifact_store,
        ArtifactStorage,
        {"s3": ArtifactStore},
        settings,
        settings.artifact_options,
    )


def execution_driver(settings) -> ExecutionDriver:
    """Build the configured execution driver.

    Args:
        settings: Platform settings.

    Returns:
        Configured driver.

    Raises:
        ValueError: Plugin configuration or registration is invalid.
    """
    return create_integration(
        "execution_drivers",
        settings.runner,
        ExecutionDriver,
        {"docker": DockerDriver, "kubernetes": KubernetesDriver},
        settings,
        settings.runner_options,
    )

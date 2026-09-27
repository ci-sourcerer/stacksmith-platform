"""Build the independent Python and frontend images."""

import subprocess

from common_python_tasks.tasks import build_image
from dotenv import dotenv_values


def _image(key, default):
    return dotenv_values(".env", interpolate=False).get(key) or default


def platform(debug: bool = False, no_cache: bool = False) -> str:
    """Build the managed Python image and return its Dockerfile for watch mode.

    Args:
        debug: Include the project's Python debugging dependencies.
        no_cache: Disable Docker's build cache.

    Returns:
        The rendered managed Dockerfile.

    Raises:
        subprocess.CalledProcessError: The image build fails.
    """
    return build_image(
        "--tag",
        _image(
            "STACKSMITH_PLATFORM_DEBUG_IMAGE" if debug else "STACKSMITH_PLATFORM_IMAGE",
            "stacksmith-platform:debug" if debug else "stacksmith-platform:local",
        ),
        "--build-arg",
        "NONROOT_UID=10001",
        "--build-arg",
        "NONROOT_GID=10001",
        debug=debug,
        dockerfile_hook_path="scripts/python_dockerfile.py" if debug else None,
        no_cache=no_cache,
        single_arch=True,
    ).dockerfile_text


def frontend(debug: bool = False, no_cache: bool = False) -> None:
    """Build the frontend independently of Python packaging.

    Args:
        debug: Build the Node development image instead of the static server.
        no_cache: Disable Docker's build cache.

    Returns:
        None.

    Raises:
        subprocess.CalledProcessError: The frontend build fails.
    """
    subprocess.run(
        [
            "docker",
            "build",
            "--file",
            "deploy/frontend.Dockerfile",
            "--target",
            "development" if debug else "runtime",
            "--tag",
            _image(
                "STACKSMITH_PLATFORM_FRONTEND_DEBUG_IMAGE"
                if debug
                else "STACKSMITH_PLATFORM_FRONTEND_IMAGE",
                "stacksmith-platform-frontend:debug"
                if debug
                else "stacksmith-platform-frontend:local",
            ),
            *(["--no-cache"] if no_cache else []),
            ".",
        ],
        shell=False,
        check=True,
    )

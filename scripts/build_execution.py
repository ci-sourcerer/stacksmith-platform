"""Build a local immutable execution image without requiring a registry push."""

import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from dotenv import dotenv_values, set_key


def main(no_cache: bool = False) -> None:
    """Build the execution wrapper and save its immutable Docker ID in `.env`.

    Args:
        no_cache: Disable Docker's build cache.

    Returns:
        None.

    Raises:
        ValueError: No Stacksmith base or execution image is configured.
        subprocess.CalledProcessError: Wheel or image build fails.
    """
    values = dotenv_values(".env", interpolate=False)
    if not values.get("STACKSMITH_PLATFORM_STACKSMITH_IMAGE"):
        if values.get("STACKSMITH_PLATFORM_EXECUTION_IMAGE"):
            print("Using the configured prebuilt execution image.")
            return
        raise ValueError(
            "Set STACKSMITH_PLATFORM_STACKSMITH_IMAGE or a prebuilt STACKSMITH_PLATFORM_EXECUTION_IMAGE"
        )
    with TemporaryDirectory(prefix="stacksmith-platform-wheel-") as directory:
        subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", directory], shell=False, check=True
        )
        subprocess.run(
            [
                "docker",
                "build",
                *(["--no-cache"] if no_cache else []),
                "-f",
                "deploy/Dockerfile.execution",
                "--build-context",
                f"platform-wheel={directory}",
                "--build-arg",
                f"STACKSMITH_IMAGE={values['STACKSMITH_PLATFORM_STACKSMITH_IMAGE']}",
                "-t",
                "stacksmith-platform-execution:local",
                ".",
            ],
            shell=False,
            check=True,
        )
    identifier = subprocess.run(
        [
            "docker",
            "image",
            "inspect",
            "stacksmith-platform-execution:local",
            "--format",
            "{{.Id}}",
        ],
        shell=False,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", identifier):
        raise ValueError("Docker did not return an immutable execution image ID")
    set_key(".env", "STACKSMITH_PLATFORM_EXECUTION_IMAGE", identifier)
    Path(".env").chmod(0o600)
    print("Built execution image and saved its immutable Docker ID in .env.")


if __name__ == "__main__":
    main()

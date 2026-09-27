import os
from pathlib import Path

import common_python_tasks
import pytest


def main() -> int:
    """Run the project test suite without inheriting deployment configuration.

    Returns:
        Pytest's process exit code.
    """
    for key in tuple(os.environ):
        if (
            key.startswith("STACKSMITH_PLATFORM_")
            and key != "STACKSMITH_PLATFORM_TEST_DATABASE_URL"
        ):
            os.environ.pop(key)
    shared_config = Path(common_python_tasks.__file__).parent / "data" / "generic"
    return pytest.main(
        [
            "-vv",
            "-c",
            str(shared_config / "pytest.ini"),
            "--cov=stacksmith_platform",
            "--cov-report=term-missing",
            "--cov-report=xml:coverage.xml",
            f"--cov-config={shared_config / '.coveragerc'}",
        ]
    )

#!/usr/bin/env python3
"""Give the development user ownership of Compose watch's source directory."""

import sys
from pathlib import Path


def main() -> None:
    """Adapt the managed debug stage so Compose can synchronize source as non-root.

    Returns:
        None.

    Raises:
        OSError: The generated Dockerfile cannot be read or written.
        ValueError: The managed debug stage no longer matches the expected layout.
    """
    source = Path(sys.argv[1]).read_text()
    if source.count("COPY --from=builder /tmp/build /tmp/build") != 1:
        raise ValueError(
            "The managed debug stage changed; update its source ownership hook"
        )
    Path(sys.argv[1]).write_text(
        source.replace(
            "COPY --from=builder /tmp/build /tmp/build",
            "COPY --from=builder --chown=${NONROOT_UID}:${NONROOT_GID} /tmp/build /tmp/build",
        )
    )


if __name__ == "__main__":
    main()

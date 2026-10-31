import argparse

import uvicorn
from uvicorn.config import LOGGING_CONFIG


def main() -> None:
    """Run the Stacksmith Platform API server.

    Returns:
        None.
    """
    parser = argparse.ArgumentParser(description="Run the Stacksmith Platform API")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--reload-dir", action="append", default=None)
    args = parser.parse_args()
    uvicorn.run(
        "stacksmith_platform.app:create_app",
        factory=True,
        workers=1,
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=args.reload_dir,
        log_config={
            **LOGGING_CONFIG,
            "loggers": {
                **LOGGING_CONFIG["loggers"],
                "stacksmith_platform": {
                    "handlers": ["default"],
                    "level": "INFO",
                    "propagate": False,
                },
            },
        },
    )


if __name__ == "__main__":
    main()

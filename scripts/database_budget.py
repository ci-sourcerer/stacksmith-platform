import argparse
import json
import sys


def main(argv=None):
    """Calculate a hard connection budget before scaling API or supervisor pods.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Zero if query pools, listeners, probes, and operator reserve fit.

    Raises:
        SystemExit: Counts or limits are invalid.
    """
    parser = argparse.ArgumentParser(
        description="Budget maximum simultaneous processes, including rollout surge"
    )
    parser.add_argument("--api", type=int, default=1)
    parser.add_argument("--api-pool-size", type=int, default=5)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--supervisors", type=int, default=0)
    parser.add_argument("--maintenance", type=int, default=0)
    parser.add_argument("--migrations", type=int, default=0)
    parser.add_argument("--reserved", type=int, default=20)
    parser.add_argument("--max-connections", type=int, required=True)
    args = parser.parse_args(argv)
    if (
        min(
            args.api,
            args.workers,
            args.supervisors,
            args.maintenance,
            args.migrations,
            args.reserved,
        )
        < 0
        or min(args.api_pool_size, args.max_connections) < 1
    ):
        parser.error(
            "Process counts must be nonnegative and pool/database limits positive"
        )
    query = (
        args.api * args.api_pool_size
        + args.workers
        + args.supervisors
        + args.maintenance
        + args.migrations
    )
    required = query + args.api + args.workers + args.reserved
    print(
        json.dumps(
            {
                "query_connections": query,
                "dedicated_listeners": args.api,
                "probe_connections": args.workers,
                "reserved_connections": args.reserved,
                "required_connections": required,
                "max_connections": args.max_connections,
                "headroom_connections": args.max_connections - required,
                "passed": required <= args.max_connections,
            },
            indent=2,
        )
    )
    return 0 if required <= args.max_connections else 1


if __name__ == "__main__":
    sys.exit(main())

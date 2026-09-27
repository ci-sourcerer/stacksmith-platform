import json
import sys


def discover(payload):
    """Discover repository deployment candidates with Stacksmith.

    Args:
        payload: Validated repository discovery options.

    Returns:
        Stacksmith deployment-discovery envelope.
    """
    from stacksmith.ci.service import discover_deployments

    return discover_deployments(
        gitops_root=payload.get("gitops_root"),
        discovery_mode=payload.get("discovery_mode", "auto"),
    )


def main() -> None:
    """Write repository deployment discovery to standard output.

    Returns:
        None.
    """
    print(json.dumps(discover(json.load(sys.stdin))))


if __name__ == "__main__":
    main()

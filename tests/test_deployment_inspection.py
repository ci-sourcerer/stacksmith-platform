import sys
from types import SimpleNamespace
from unittest.mock import Mock

from stacksmith_platform.deployment_inspection import discover


def test_deployment_inspection_delegates_to_stacksmith(monkeypatch):
    scanner = Mock(return_value={"deployments": [{"gitops_root": ""}]})
    monkeypatch.setitem(
        sys.modules,
        "stacksmith.ci.service",
        SimpleNamespace(discover_deployments=scanner),
    )

    assert discover({"gitops_root": "infra", "discovery_mode": "folders"}) == {
        "deployments": [{"gitops_root": ""}]
    }
    scanner.assert_called_once_with(gitops_root="infra", discovery_mode="folders")

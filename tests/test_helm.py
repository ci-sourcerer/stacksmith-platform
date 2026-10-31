import shutil
import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML

pytestmark = pytest.mark.skipif(not shutil.which("helm"), reason="Helm CLI unavailable")


def _render(*options):
    return list(
        YAML(typ="safe").load_all(
            subprocess.run(
                [
                    "helm",
                    "template",
                    "test",
                    str(
                        Path(__file__).resolve().parents[1]
                        / "deploy/helm/stacksmith-platform"
                    ),
                    "--namespace",
                    "control",
                    *options,
                ],
                shell=False,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
    )


def test_chart_uses_temporary_supervisors_and_bounded_recovery():
    resources = _render()
    deployments = [item for item in resources if item["kind"] == "Deployment"]
    assert [item["metadata"]["name"] for item in deployments] == [
        "test-api",
        "test-frontend",
    ]
    api = deployments[0]["spec"]["template"]["spec"]
    assert api["automountServiceAccountToken"]
    for probe in ("readinessProbe", "livenessProbe"):
        assert api["containers"][0][probe]["httpGet"]["httpHeaders"] == [
            {"name": "Host", "value": "127.0.0.1"}
        ]
    cron = next(item for item in resources if item["kind"] == "CronJob")["spec"]
    assert cron["schedule"] == "* * * * *" and cron["concurrencyPolicy"] == "Forbid"
    assert cron["startingDeadlineSeconds"] == 50
    assert cron["jobTemplate"]["spec"]["activeDeadlineSeconds"] == 120
    assert cron["jobTemplate"]["spec"]["backoffLimit"] == 0
    recovery = cron["jobTemplate"]["spec"]["template"]["spec"]
    assert recovery["serviceAccountName"] == "test-supervisor"
    assert recovery["containers"][0]["command"] == ["stacksmith-platform-maintain"]
    for container in (api["containers"][0], recovery["containers"][0]):
        assert {
            "name": "STACKSMITH_PLATFORM_INSPECTION_POLL_INTERVAL",
            "value": "2",
        } in container["env"]
    assert (
        recovery["containers"][0]["envFrom"][1]["secretRef"]["name"]
        == "stacksmith-platform-supervisor-database"
    )
    assert (
        api["containers"][0]["envFrom"][1]["secretRef"]["name"]
        == "stacksmith-platform-api-database"
    )
    assert (
        next(
            item
            for item in resources
            if item["kind"] == "ServiceAccount"
            and item["metadata"]["name"] == "stacksmith-execution"
        )["automountServiceAccountToken"]
        is False
    )


def test_chart_exposes_inspection_poll_interval_override():
    for resource in _render("--set", "inspectionPollInterval=0.5"):
        if (
            resource["kind"] == "Deployment"
            and resource["metadata"]["name"] == "test-api"
        ):
            assert {
                "name": "STACKSMITH_PLATFORM_INSPECTION_POLL_INTERVAL",
                "value": "0.5",
            } in resource["spec"]["template"]["spec"]["containers"][0]["env"]


def test_frontend_owns_ingress_without_receiving_api_secrets():
    resources = _render("--set", "ingress.enabled=true")
    frontend = next(
        item
        for item in resources
        if item["kind"] == "Deployment" and item["metadata"]["name"] == "test-frontend"
    )["spec"]["template"]["spec"]
    assert frontend["automountServiceAccountToken"] is False
    container = frontend["containers"][0]
    assert "envFrom" not in container
    assert container["env"] == [
        {
            "name": "API_UPSTREAM",
            "value": "http://test-api.control.svc.cluster.local:8000",
        }
    ]
    assert container["securityContext"]["readOnlyRootFilesystem"]
    assert container["readinessProbe"]["httpGet"]["path"] == "/api/ready"
    ingress = next(item for item in resources if item["kind"] == "Ingress")
    assert (
        ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["name"]
        == "test-frontend"
    )


def test_existing_accounts_and_dispatch_rbac_follow_overrides():
    resources = _render(
        "--set",
        "serviceAccounts.api.name=existing-api",
        "--set",
        "serviceAccounts.supervisor.name=existing-supervisor",
    )
    accounts = {
        item["metadata"]["name"]
        for item in resources
        if item["kind"] == "ServiceAccount"
    }
    assert accounts == {"stacksmith-execution"}
    dispatch = next(
        item
        for item in resources
        if item["kind"] == "RoleBinding" and item["metadata"]["name"] == "test-dispatch"
    )
    assert {subject["name"] for subject in dispatch["subjects"]} == {
        "existing-api",
        "existing-supervisor",
    }
    execution = next(
        item
        for item in resources
        if item["kind"] == "RoleBinding"
        and item["metadata"]["name"] == "test-supervisor"
    )
    assert execution["subjects"][0]["name"] == "existing-supervisor"


def test_schema_hook_upgrades_and_grants_before_application_startup():
    schema = next(
        item
        for item in _render()
        if item["kind"] == "Job" and item["metadata"]["name"] == "test-schema"
    )
    assert (
        schema["metadata"]["annotations"]["helm.sh/hook"] == "pre-install,pre-upgrade"
    )
    container = schema["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["stacksmith-platform-init", "--grant-roles"]
    assert (
        container["envFrom"][1]["secretRef"]["name"]
        == "stacksmith-platform-migration-database"
    )


def test_review_profiling_is_opt_in():
    for arguments, expected in (
        ((), "false"),
        (("--set", "reviewProfiling=true"), "true"),
    ):
        deployment = next(
            item
            for item in _render(*arguments)
            if item["kind"] == "Deployment" and item["metadata"]["name"] == "test-api"
        )
        environment = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        assert (
            next(
                item["value"]
                for item in environment
                if item["name"] == "STACKSMITH_PLATFORM_REVIEW_PROFILING"
            )
            == expected
        )

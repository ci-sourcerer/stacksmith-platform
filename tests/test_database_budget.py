import json

from scripts.database_budget import main


def test_connection_budget_includes_listeners_probes_and_rollout_surge(capsys):
    assert main(["--workers", "1", "--max-connections", "100"]) == 0
    assert json.loads(capsys.readouterr().out)["required_connections"] == 28
    assert (
        main(
            [
                "--api",
                "2",
                "--supervisors",
                "15",
                "--maintenance",
                "1",
                "--migrations",
                "1",
                "--max-connections",
                "100",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["required_connections"] == 49
    assert main(["--api", "20", "--max-connections", "100"]) == 1
    assert json.loads(capsys.readouterr().out)["required_connections"] == 140

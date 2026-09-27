import pytest
from sqlalchemy import create_engine

from stacksmith_platform.database import (
    Database,
    configuration,
    environments,
    execution_credential_bindings,
    grants,
    locks,
    repositories,
    row,
    runs,
    secrets,
    tokens,
)
from stacksmith_platform.repositories import synchronize, synchronize_user


@pytest.fixture
def database():
    database = Database("sqlite://", "repository-tests")
    database.initialize()
    yield database
    database.engine.dispose()


def _repository(name="org/stack", identifier=42):
    return {
        "id": identifier,
        "full_name": name,
        "installation_id": 3,
        "default_branch": "main",
    }


def test_discovery_preserves_consent_configuration_and_grants(database):
    synchronize(database, [_repository()], complete=True)
    with database.transaction() as connection:
        assert row(connection, repositories, "org/stack")["deployment_enabled"] == 0
        connection.execute(
            repositories.update().values(deployment_enabled=1, config="approved-config")
        )
        connection.execute(
            grants.insert().values(id="grant", repository="org/stack", login="operator")
        )
    synchronize(database, [], complete=True)
    with database.transaction() as connection:
        assert row(connection, repositories, "org/stack")["enabled"] == 0
    synchronize(database, [_repository()], complete=True)
    with database.transaction() as connection:
        repository = row(connection, repositories, "org/stack")
        assert repository["deployment_enabled"] == 1
        assert repository["enabled"] == 1
        assert repository["config"] == "approved-config"
        assert row(connection, grants, "grant")["login"] == "operator"


def test_user_discovery_does_not_revoke_other_users_repositories(database):
    synchronize(database, [_repository()])
    synchronize_user(
        database,
        [
            {
                "github_id": "43",
                "name": "org/other",
                "installation_id": 4,
                "default_branch": "trunk",
            }
        ],
    )
    with database.transaction() as connection:
        assert row(connection, repositories, "org/stack")["enabled"] == 1
        assert row(connection, repositories, "org/other")["branch"] == "trunk"


def test_rename_preserves_repository_settings_and_references(database):
    synchronize(database, [_repository()])
    with database.transaction() as connection:
        connection.execute(repositories.update().values(deployment_enabled=1))
        connection.execute(
            configuration.insert().values(
                id="org/stack", policy={"mode": "inherit", "refs": []}
            )
        )
        connection.execute(
            environments.insert().values(
                id="org/stack:prod", repository="org/stack", name="prod"
            )
        )
        connection.execute(
            secrets.insert().values(
                id="org/stack:prod:KEY",
                environment="org/stack:prod",
                name="KEY",
                ciphertext="encrypted",
            )
        )
        connection.execute(
            execution_credential_bindings.insert().values(
                id="org/stack:prod:cloud",
                environment="org/stack:prod",
                provider="cloud",
                phases=["plan"],
            )
        )
        connection.execute(
            tokens.insert().values(id="token", scope={"repository": "org/stack"})
        )
        connection.execute(grants.insert().values(id="grant", repository="org/stack"))
        connection.execute(
            runs.insert().values(id="run", repository="org/stack", lock_key="org/stack")
        )
        connection.execute(locks.insert().values(id="org/stack", run="run"))
    synchronize(database, [_repository("org/renamed")], complete=True)
    with database.transaction() as connection:
        assert row(connection, repositories, "org/stack") is None
        assert row(connection, repositories, "org/renamed")["deployment_enabled"] == 1
        assert (
            row(connection, configuration, "org/renamed")["policy"]["mode"] == "inherit"
        )
        assert (
            row(connection, environments, "org/renamed:prod")["repository"]
            == "org/renamed"
        )
        assert (
            row(connection, secrets, "org/renamed:prod:KEY")["ciphertext"]
            == "encrypted"
        )
        assert row(connection, execution_credential_bindings, "org/renamed:prod:cloud")[
            "phases"
        ] == ["plan"]
        assert row(connection, tokens, "token")["scope"]["repository"] == "org/renamed"
        assert row(connection, grants, "grant")["repository"] == "org/renamed"
        assert row(connection, runs, "run")["lock_key"] == "org/renamed"
        assert row(connection, locks, "org/renamed")["run"] == "run"


def test_recreated_repository_does_not_inherit_previous_consent_or_roles(database):
    synchronize(database, [_repository()])
    with database.transaction() as connection:
        connection.execute(repositories.update().values(deployment_enabled=1))
        connection.execute(grants.insert().values(id="grant", repository="org/stack"))
    synchronize(database, [_repository(identifier=99)], complete=True)
    with database.transaction() as connection:
        assert row(connection, repositories, "org/stack")["deployment_enabled"] == 0
        assert row(connection, repositories, "github:42")["enabled"] == 0
        assert row(connection, grants, "grant")["repository"] == "github:42"


def test_unversioned_schema_is_rejected_without_changing_data(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.sqlite'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE repositories (id VARCHAR PRIMARY KEY, enabled INTEGER)"
        )
        connection.exec_driver_sql("INSERT INTO repositories VALUES ('org/stack', 1)")
    database = Database(f"sqlite:///{tmp_path / 'legacy.sqlite'}", "repository-tests")
    with pytest.raises(ValueError, match="Unversioned platform database"):
        database.initialize()
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT id, enabled FROM repositories"
        ).one() == ("org/stack", 1)
    database.engine.dispose()
    engine.dispose()

from threading import Lock

from sqlalchemy import or_

from .database import (
    audit,
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

_CATALOG_LOCK = Lock()


def _rename(connection, previous, name):
    old = previous["id"]
    for table in (environments, grants, runs):
        connection.execute(
            table.update().where(table.c.repository == old).values(repository=name)
        )
    connection.execute(
        runs.update().where(runs.c.lock_key == old).values(lock_key=name)
    )
    connection.execute(locks.update().where(locks.c.id == old).values(id=name))
    connection.execute(
        configuration.update().where(configuration.c.id == old).values(id=name)
    )
    for environment in (
        connection.execute(
            environments.select().where(environments.c.repository == name)
        )
        .mappings()
        .all()
    ):
        identifier = f"{name}:{environment['name']}"
        for secret in (
            connection.execute(
                secrets.select().where(secrets.c.environment == environment["id"])
            )
            .mappings()
            .all()
        ):
            connection.execute(
                secrets.update()
                .where(secrets.c.id == secret["id"])
                .values(id=f"{identifier}:{secret['name']}", environment=identifier)
            )
        for binding in connection.execute(
            execution_credential_bindings.select().where(
                execution_credential_bindings.c.environment == environment["id"]
            )
        ).mappings():
            connection.execute(
                execution_credential_bindings.update()
                .where(execution_credential_bindings.c.id == binding["id"])
                .values(
                    id=f"{identifier}:{binding['provider']}", environment=identifier
                )
            )
        connection.execute(
            environments.update()
            .where(environments.c.id == environment["id"])
            .values(id=identifier)
        )
    for token in connection.execute(tokens.select()).mappings().all():
        if token["scope"].get("repository") == old:
            connection.execute(
                tokens.update()
                .where(tokens.c.id == token["id"])
                .values(scope={**token["scope"], "repository": name})
            )
    connection.execute(
        repositories.update().where(repositories.c.id == old).values(id=name)
    )


def _repository_values(repository):
    return {
        "github_id": str(repository["id"]),
        "installation": repository["installation_id"],
        "branch": repository.get("default_branch", "main"),
        "enabled": 1,
    }


def _catalog_unchanged(database, catalog):
    with database.transaction() as connection:
        for repository in catalog:
            stored = row(connection, repositories, repository["full_name"])
            if not stored or any(
                stored[key] != value
                for key, value in _repository_values(repository).items()
            ):
                return False
    return True


def _upsert(connection, repository):
    name = repository["full_name"]
    identifier = str(repository["id"])
    previous = (
        connection.execute(
            repositories.select()
            .where(
                or_(
                    repositories.c.github_id == identifier,
                    (repositories.c.id == name) & repositories.c.github_id.is_(None),
                )
            )
            .with_for_update()
        )
        .mappings()
        .first()
    )
    occupied = (
        connection.execute(repositories.select().where(repositories.c.id == name))
        .mappings()
        .first()
    )
    if occupied and occupied["github_id"] not in {None, identifier}:
        _rename(connection, occupied, f"github:{occupied['github_id']}")
        connection.execute(
            repositories.update()
            .where(repositories.c.github_id == occupied["github_id"])
            .values(enabled=0, deployment_enabled=0)
        )
    if previous and previous["id"] != name:
        _rename(connection, previous, name)
        audit(
            connection,
            "github",
            "repository.renamed",
            previous=previous["id"],
            repository=name,
        )
    values = _repository_values(repository)
    if previous and any(previous[key] != value for key, value in values.items()):
        connection.execute(
            repositories.update().where(repositories.c.id == name).values(**values)
        )
    elif not previous:
        connection.execute(
            repositories.insert().values(
                id=name, config="", deployment_enabled=0, **values
            )
        )
        audit(connection, "github", "repository.discovered", repository=name)


def synchronize(database, catalog, *, complete=False):
    """Refresh repository metadata without changing deployment consent or roles.

    Args:
        database: Platform transactional database.
        catalog: Authoritative GitHub repository metadata with installation IDs.
        complete: Whether the catalog covers every active App installation.

    Returns:
        None.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: Repository synchronization cannot commit.
    """
    catalog = list(catalog)
    if not complete and catalog and _catalog_unchanged(database, catalog):
        return
    # Queue within this process before consuming a connection for the shared lock.
    with _CATALOG_LOCK, database.transaction() as connection:
        if connection.dialect.name == "postgresql":
            connection.exec_driver_sql(
                "SELECT pg_advisory_xact_lock(hashtext('github-repository-catalog'))"
            )
        if complete:
            connection.execute(repositories.update().values(enabled=0))
        for repository in catalog:
            _upsert(connection, repository)


def synchronize_user(database, discovered):
    """Persist the subset of repositories visible to a GitHub App user token.

    Args:
        database: Platform database.
        discovered: User and App repository intersection from GitHub.

    Returns:
        None.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: Repository synchronization cannot commit.
    """
    synchronize(
        database,
        [
            {
                "id": repository["github_id"],
                "full_name": repository["name"],
                "installation_id": repository["installation_id"],
                "default_branch": repository["default_branch"],
            }
            for repository in discovered
        ],
    )


def installation_event(connection, event, payload):
    """Invalidate revoked installation access before deployment event routing.

    Args:
        connection: Webhook transaction.
        event: GitHub event name.
        payload: Verified webhook payload.

    Returns:
        None.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: Access invalidation cannot commit.
    """
    if event == "installation" and payload.get("action") in {"deleted", "suspend"}:
        connection.execute(
            repositories.update()
            .where(repositories.c.installation == payload["installation"]["id"])
            .values(enabled=0)
        )
    if event == "installation_repositories":
        for repository in payload.get("repositories_removed", []):
            connection.execute(
                repositories.update()
                .where(
                    repositories.c.installation == payload["installation"]["id"],
                    or_(
                        repositories.c.github_id == str(repository["id"]),
                        repositories.c.id == repository["full_name"],
                    ),
                )
                .values(enabled=0)
            )

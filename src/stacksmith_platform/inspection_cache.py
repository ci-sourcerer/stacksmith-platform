import hashlib
import json
import time
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

from .database import inspection_cache, row, runs
from .integrations import ArtifactNotFoundError


def prepare_cache(controller, run, inspection):
    """Attach scoped encrypted input-cache capabilities to an inspection.

    Args:
        controller: Authorized inspection controller.
        run: Claimed durable run.
        inspection: Decrypted server-selected inspection request.

    Returns:
        Run and inspection copies containing private cache capabilities.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: Cache metadata cannot be read.
    """
    context = inspection.get("context") or {}
    manifest = context.get("_input_manifest") or {}
    if inspection["args"] != ["review"] or not manifest.get("cacheable"):
        return run, inspection
    identifier = hashlib.sha256(
        json.dumps(
            [
                run["actor"],
                context.get("repository"),
                context.get("path"),
                context.get("revision"),
                context.get("config_revision"),
                context.get("environment"),
                context.get("environment_revision"),
                manifest["digest"],
                controller.settings.execution_image,
                hashlib.sha256(
                    (inspection.get("config_source") or "").encode()
                ).hexdigest(),
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    with controller.database.transaction() as connection:
        cached = row(connection, inspection_cache, identifier)
    output_key = f"inspection-cache/{identifier}/{uuid4().hex}"
    encryption_key = Fernet.generate_key().decode()
    inspection = {
        **inspection,
        "_cache": {
            "output_url": controller.storage.url(
                output_key, "put_object", controller.settings.command_timeout + 300
            ),
            "output_key": encryption_key,
            **(
                {
                    "input_url": controller.storage.url(
                        cached["key"],
                        "get_object",
                        controller.settings.command_timeout + 300,
                    ),
                    "input_key": controller.database.decrypt(cached["encryption_key"]),
                }
                if cached and cached["expires"] > time.time()
                else {}
            ),
        },
    }
    run = {
        **run,
        "spec": {
            **run["spec"],
            "_inspection": controller.database.encrypt(json.dumps(inspection)),
            "_cache_output": {
                "id": identifier,
                "key": output_key,
                "encryption_key": controller.database.encrypt(encryption_key),
            },
        },
    }
    with controller.database.transaction() as connection:
        connection.execute(
            runs.update()
            .where(runs.c.id == run["id"], runs.c.worker == controller.identifier)
            .values(spec=run["spec"])
        )
    return run, inspection


def accept_cache(controller, connection, run, result):
    """Record a successful private immutable-input cache after validating encryption.

    Args:
        controller: Current fenced run owner.
        connection: Completion transaction.
        run: Completed inspection run.
        result: Inspection result with an optional cache upload marker.

    Returns:
        None. Invalid optional cache uploads are discarded.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: Cache metadata cannot commit.
    """
    output = run["spec"].get("_cache_output")
    if not result.pop("_cache_written", False) or not output or result["exit_code"]:
        return
    try:
        Fernet(controller.database.decrypt(output["encryption_key"]).encode()).decrypt(
            controller.storage.get(output["key"])
        )
    except InvalidToken, ArtifactNotFoundError:
        controller.storage.delete(output["key"])
        return
    if connection.dialect.name == "postgresql":
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": output["id"]}
        )
    previous = row(connection, inspection_cache, output["id"])
    if previous:
        controller.storage.delete(output["key"])
        return
    connection.execute(
        inspection_cache.insert().values(
            **output,
            expires=time.time() + controller.settings.inspection_cache_ttl,
        )
    )


def expire_cache(controller):
    """Delete expired cache metadata and encrypted objects during recovery.

    Args:
        controller: Inspection controller owning artifact cleanup.

    Returns:
        None.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: Cache expiry cannot commit.
    """
    with controller.database.transaction() as connection:
        for entry in (
            connection.execute(
                inspection_cache.select()
                .where(inspection_cache.c.expires <= time.time())
                .with_for_update(skip_locked=True)
                .limit(100)
            )
            .mappings()
            .all()
        ):
            controller.storage.delete(entry["key"])
            connection.execute(
                inspection_cache.delete().where(inspection_cache.c.id == entry["id"])
            )

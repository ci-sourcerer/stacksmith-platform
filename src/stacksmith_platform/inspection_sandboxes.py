import asyncio
import hashlib
import hmac
import json
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .database import inspection_sandboxes
from .errors import ComposerError
from .inspection_notifications import notify

router = APIRouter(prefix="/api/v1/inspection-sandboxes")


def _assignment(request, identifier):
    with request.app.state.database.transaction() as connection:
        sandbox = (
            connection.execute(
                inspection_sandboxes.select()
                .where(inspection_sandboxes.c.id == identifier)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if (
            not sandbox
            or sandbox["expires"] <= time.time()
            or not hmac.compare_digest(
                sandbox["token_digest"],
                hashlib.sha256(
                    request.headers.get("authorization", "")
                    .removeprefix("Bearer ")
                    .encode()
                ).hexdigest(),
            )
        ):
            raise ComposerError("Sandbox assignment is unavailable.", 403)
        if sandbox["status"] == "launching":
            connection.execute(
                inspection_sandboxes.update()
                .where(inspection_sandboxes.c.id == identifier)
                .values(status="ready")
            )
            notify(connection, identifier)
        if sandbox["status"] == "assigned":
            connection.execute(
                inspection_sandboxes.update()
                .where(inspection_sandboxes.c.id == identifier)
                .values(status="delivered", assignment=None, token="")
            )
            notify(connection, identifier)
            return json.loads(request.app.state.database.decrypt(sandbox["assignment"]))
        if sandbox["status"] not in {"launching", "ready", "reserved"}:
            raise ComposerError(
                "This sandbox assignment has already been consumed.", 409
            )
    return None


@router.get("/{identifier}/assignment")
async def assignment(request: Request, identifier: str):
    """Deliver one authenticated sandbox assignment through an async long poll.

    Args:
        request: Request authenticated by its expiring sandbox capability.
        identifier: Server-created sandbox identifier.

    Returns:
        One existing encrypted-handoff capability, or HTTP 204 while idle.

    Raises:
        ComposerError: The capability is invalid, expired, or already consumed.
    """
    if not request.app.state.settings.inspection_controller_enabled:
        raise ComposerError("Inspection pooling is disabled.", 404)
    with request.app.state.inspection_notifications.subscribe(identifier) as changed:
        deadline = time.monotonic() + 25
        while True:
            changed.clear()
            result = await asyncio.to_thread(_assignment, request, identifier)
            if result is not None:
                return JSONResponse(
                    result, headers={"Cache-Control": "no-store", "Pragma": "no-cache"}
                )
            try:
                await asyncio.wait_for(
                    changed.wait(), max(0, deadline - time.monotonic())
                )
            except TimeoutError:
                return Response(status_code=204, headers={"Cache-Control": "no-store"})

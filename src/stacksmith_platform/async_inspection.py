import asyncio
from time import perf_counter

from .adapter import StacksmithAdapter
from .errors import ComposerError


def _prepare(request, draft, deployment=True):
    from . import app

    snapshot, edits = app._draft_edits(request, draft, deployment)
    context = (
        app._inspection_context(request, snapshot, draft.environment)
        if deployment
        else None
    )
    if deployment and request.app.state.settings.execution_enabled:
        from .inspection_inputs import prepare_inputs

        context = prepare_inputs(request, context)
    if (draft.editor_id is None) != (draft.generation is None):
        raise ComposerError("Supply both editor_id and generation.", 422)
    adapter = (
        app._adapter(request, snapshot.get("repository"), context)
        if deployment
        else StacksmithAdapter(request.app.state.settings)
    )
    adapter.editor = {
        "actor": app._principal(request),
        "editor_id": draft.editor_id,
        "generation": draft.generation,
        "repository": snapshot.get("repository"),
        "path": snapshot["path"],
        "environment": draft.environment,
    }
    return snapshot, edits, context, adapter, app._github_token(request)


async def review(request, draft):
    """Prepare, await, and freshly validate an authorized compound review.

    Args:
        request: Authenticated Composer request.
        draft: Proposed source edits and optional editor generation.

    Returns:
        Snapshot and unchanged public review response.

    Raises:
        ComposerError: Access, inspection, or completion validation fails.
    """
    from . import app

    started = perf_counter()
    snapshot, edits, context, adapter, token = await asyncio.to_thread(
        _prepare, request, draft
    )
    timings = {"preflight": perf_counter() - started}
    stage = perf_counter()
    result = await adapter.areview(snapshot["source"], edits, snapshot["path"], token)
    timings["inspection"] = perf_counter() - stage
    return await asyncio.to_thread(
        app._finish_review,
        request,
        snapshot,
        context,
        adapter,
        result,
        started,
        timings,
    )


async def source(request, draft):
    """Render source independently of managed inspection.

    Args:
        request: Authenticated Composer request.
        draft: Proposed source edits.

    Returns:
        Original snapshot and authored source.

    Raises:
        ComposerError: Authorization or authoring fails.
    """
    from . import app

    snapshot, edits, _, adapter, _ = await asyncio.to_thread(
        _prepare, request, draft, False
    )
    proposed = await adapter.aauthor(snapshot["source"], edits, snapshot["path"])
    request.state.authoring_repositories = None
    if snapshot.get("repository"):
        await asyncio.to_thread(app._authoring_access, request, snapshot["repository"])
    await asyncio.to_thread(
        app._check_draft_source, request, snapshot, proposed["source"]
    )
    return snapshot, proposed["source"]


async def resolution(request, draft):
    """Await authorized source authoring and configuration resolution.

    Args:
        request: Authenticated Composer request.
        draft: Proposed edits and optional query.

    Returns:
        The existing public resolution response.

    Raises:
        ComposerError: Authorization or resolution fails.
    """
    from . import app

    _, authored = await source(request, draft)
    _, _, context, adapter, token = await asyncio.to_thread(_prepare, request, draft)
    result = await adapter.aresolution(authored, token, draft.query)
    await asyncio.to_thread(app._check_inspection_context, request, context, adapter)
    return {**result, "context": app._context_metadata(context)}

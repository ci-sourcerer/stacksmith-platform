import hashlib
import io
from unittest.mock import MagicMock

from botocore.response import StreamingBody

from stacksmith_platform.storage import ArtifactStore


def test_digest_handles_botocore_stream_context_returning_raw_response():
    content = b"workspace" * 200_000
    stream = io.BytesIO(content)
    store = ArtifactStore.__new__(ArtifactStore)
    store.bucket = "artifacts"
    store.client = MagicMock()
    store.client.get_object.return_value = {
        "ContentLength": len(content),
        "Body": StreamingBody(stream, len(content)),
    }
    assert store.digest("workspace") == hashlib.sha256(content).hexdigest()
    assert stream.closed


def test_s3_missing_object_uses_storage_contract_error():
    import pytest
    from botocore.exceptions import ClientError

    from stacksmith_platform.integrations import ArtifactNotFoundError

    store = ArtifactStore.__new__(ArtifactStore)
    store.bucket = "artifacts"
    store.client = MagicMock()
    store.client.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "private-storage-details"}},
        "GetObject",
    )
    with pytest.raises(ArtifactNotFoundError, match="Artifact is unavailable"):
        store.get("missing")

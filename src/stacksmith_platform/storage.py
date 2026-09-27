import hashlib

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .integrations import ArtifactNotFoundError, ArtifactStorage


class ArtifactStore(ArtifactStorage):
    """Store encrypted private context and redacted public review artifacts.

    Args:
        settings: S3-compatible endpoint and credentials.
    """

    def __init__(self, settings, options=None):
        self.bucket = settings.artifact_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.artifact_endpoint or None,
            region_name=settings.artifact_region,
            aws_access_key_id=settings.artifact_access_key,
            aws_secret_access_key=settings.artifact_secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def _object(self, key):
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"NoSuchKey", "404"}:
                raise ArtifactNotFoundError("Artifact is unavailable.") from None
            raise

    def put(self, key: str, content: bytes) -> str:
        """Write a private object.

        Args:
            key: Opaque object key.
            content: Object bytes.

        Returns:
            SHA256 content digest.
        """
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content)
        return hashlib.sha256(content).hexdigest()

    def get(self, key: str) -> bytes:
        """Read a private object with a bounded size.

        Args:
            key: Object key.

        Returns:
            Object bytes.

        Raises:
            ValueError: The object exceeds the execution handoff size limit.
            ArtifactNotFoundError: The object does not exist.
        """
        response = self._object(key)
        if response["ContentLength"] > 16 * 1024**2:
            response["Body"].close()
            raise ValueError("Review artifact or result exceeds 16 MiB")
        with response["Body"] as body:
            return body.read(16 * 1024**2 + 1)

    def digest(self, key: str) -> str:
        """Hash a large encrypted workspace without loading it into memory.

        Args:
            key: Private workspace object key.

        Returns:
            SHA256 digest of its stored ciphertext.

        Raises:
            ValueError: The context exceeds one GiB.
            ArtifactNotFoundError: The object does not exist.
        """
        response = self._object(key)
        if response["ContentLength"] > 1024**3:
            response["Body"].close()
            raise ValueError("Workspace exceeds one GiB")
        digest = hashlib.sha256()
        with response["Body"] as body:
            size = 0
            while chunk := body.read(1024**2):
                size += len(chunk)
                if size > 1024**3:
                    raise ValueError("Workspace exceeds one GiB")
                digest.update(chunk)
        return digest.hexdigest()

    def url(self, key: str, method: str, lifetime: int = 3600) -> str:
        """Issue one-object access for an execution phase.

        Args:
            key: Object key.
            method: `get_object` or `put_object`.
            lifetime: URL lifetime in seconds.

        Returns:
            Presigned object URL.
        """
        return self.client.generate_presigned_url(
            method, Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=lifetime
        )

    def delete(self, key: str) -> None:
        """Remove an expired artifact.

        Args:
            key: Object key.

        Returns:
            None.
        """
        self.client.delete_object(Bucket=self.bucket, Key=key)

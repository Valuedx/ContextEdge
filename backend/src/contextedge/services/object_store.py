"""S3-compatible object storage helpers backed by MinIO."""

from __future__ import annotations

import socket

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from contextedge.config import settings

_client = None


def _endpoint_url() -> str:
    scheme = "https" if settings.minio_use_ssl else "http"
    return f"{scheme}://{settings.minio_endpoint}"


def is_minio_reachable(timeout: float = 0.2) -> bool:
    """Quickly check if the MinIO endpoint is listening before invoking boto3."""
    try:
        endpoint = settings.minio_endpoint
        if ":" in endpoint:
            host, port_str = endpoint.split(":", 1)
            port = int(port_str)
        else:
            host = endpoint
            port = 443 if settings.minio_use_ssl else 9000
        # On Windows, resolving 'localhost' can attempt IPv6 and hang
        if host == "localhost":
            host = "127.0.0.1"
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def get_s3_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=_endpoint_url(),
            aws_access_key_id=settings.minio_root_user,
            aws_secret_access_key=settings.minio_root_password,
            region_name="us-east-1",
            config=Config(
                signature_version="s3v4",
                connect_timeout=1,
                read_timeout=1,
                retries={"max_attempts": 1, "mode": "standard"},
            ),
        )
    return _client


def ensure_bucket() -> None:
    if not is_minio_reachable():
        raise ConnectionError(f"MinIO endpoint {settings.minio_endpoint} is not reachable")
    client = get_s3_client()
    try:
        client.head_bucket(Bucket=settings.minio_bucket)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchBucket", "NotFound"}:
            client.create_bucket(Bucket=settings.minio_bucket)
            return
        raise



def upload_raw(tenant_id: str, raw_id: str, data: bytes) -> str:
    key = f"raw/{tenant_id}/{raw_id}.json"
    client = get_s3_client()
    client.put_object(
        Bucket=settings.minio_bucket,
        Key=key,
        Body=data,
        ContentType="application/json",
    )
    return key


def download_raw(key: str) -> bytes:
    client = get_s3_client()
    response = client.get_object(Bucket=settings.minio_bucket, Key=key)
    body = response["Body"]
    try:
        return body.read()
    finally:
        body.close()


def upload_artifact(
    tenant_id: str,
    evidence_id: str,
    artifact_id: str,
    filename: str,
    data: bytes,
    *,
    content_type: str = "application/octet-stream",
) -> str:
    safe_name = filename.replace("\\", "_").replace("/", "_")
    key = f"artifacts/{tenant_id}/{evidence_id}/{artifact_id}/{safe_name}"
    client = get_s3_client()
    client.put_object(
        Bucket=settings.minio_bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    return key


def download_artifact(key: str) -> bytes:
    client = get_s3_client()
    response = client.get_object(Bucket=settings.minio_bucket, Key=key)
    body = response["Body"]
    try:
        return body.read()
    finally:
        body.close()


def delete_object(key: str) -> bool:
    """Delete an object from MinIO by key. Returns True if deleted,
    False if the key was already absent (treated as success). Any
    other ClientError propagates so the caller can decide whether
    to retry.

    Used by the hard-delete cleanup sweep (review F-18) to reap
    orphaned raw + artifact blobs after their evidence row has been
    deleted via ``purge_archived_evidence(mode="hard_delete")``.
    """
    client = get_s3_client()
    try:
        client.delete_object(Bucket=settings.minio_bucket, Key=key)
        return True
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise

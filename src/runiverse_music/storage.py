"""생성 결과 업로드. 같은 키는 덮어쓴다(재요청 허용)."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol

from .config import Settings


class StorageError(RuntimeError):
    """결과 업로드 실패."""


def build_key(prefix: str, request_id: str, suffix: str) -> str:
    return f"{prefix.strip('/')}/{request_id}/{request_id}{suffix}"


class Storage(Protocol):
    def upload(self, local_path: Path, key: str) -> None: ...


class S3Storage:
    def __init__(self, bucket: str, client=None):
        if client is None:
            import boto3

            client = boto3.client("s3")
        self._bucket = bucket
        self._client = client

    def upload(self, local_path: Path, key: str) -> None:
        from boto3.exceptions import S3UploadFailedError
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._client.upload_file(
                str(local_path), self._bucket, key, ExtraArgs={"ContentType": "audio/wav"}
            )
        except (BotoCoreError, ClientError, S3UploadFailedError) as exc:
            raise StorageError(f"S3 업로드 실패: s3://{self._bucket}/{key}") from exc


class LocalStorage:
    def __init__(self, root: Path):
        self._root = Path(root)

    def upload(self, local_path: Path, key: str) -> None:
        target = self._root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, target)


def make_storage(settings: Settings) -> Storage:
    if settings.storage == "s3":
        return S3Storage(settings.output_bucket)
    return LocalStorage(settings.local_output_dir)

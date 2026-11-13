"""사진 읽기(사진 버킷)·생성 음악 업로드(AI 버킷). 같은 키는 덮어쓴다(재요청 허용).

키 규칙 정본: Notion「S3 버킷 저장 구조」
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol

from .config import Settings


CONTENT_TYPE = "audio/wav"


class StorageError(RuntimeError):
    """사진 읽기·결과 업로드 실패."""


def music_key(prefix: str, user_id: int, activity: str, record_card_id: int,
              record_card_image_id: int | None) -> str:
    """카드 음악 객체 키. 기본 음악은 base.wav, 사진 음악은 {record_card_image_id}.wav.

    파일명이 사진 ID라 재요청 사이에 사진 순서가 바뀌어도 같은 사진은 같은 키를 가리킨다.

    버킷 이름·s3://는 넣지 않는다 (백엔드 DB에 그대로 저장). prefix: test는 audio/test, prod는 audio.
    """
    name = "base" if record_card_image_id is None else str(record_card_image_id)
    return f"{prefix.strip('/')}/{user_id}/{activity}/{record_card_id}/{name}.wav"


class Storage(Protocol):
    def read_photo(self, key: str) -> bytes: ...

    def upload(self, local_path: Path, key: str) -> None: ...


class S3Storage:
    def __init__(self, bucket: str, photo_bucket: str, client=None):
        if client is None:
            import boto3

            client = boto3.client("s3")
        self._bucket = bucket              # AI 버킷: 생성 음악
        self._photo_bucket = photo_bucket  # 기존 사진 버킷: photos[].image_key
        self._client = client

    def read_photo(self, key: str) -> bytes:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return self._client.get_object(Bucket=self._photo_bucket, Key=key)["Body"].read()
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"사진 읽기 실패: s3://{self._photo_bucket}/{key}") from exc

    def upload(self, local_path: Path, key: str) -> None:
        from boto3.exceptions import S3UploadFailedError
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._client.upload_file(
                str(local_path), self._bucket, key, ExtraArgs={"ContentType": CONTENT_TYPE}
            )
        except (BotoCoreError, ClientError, S3UploadFailedError) as exc:
            raise StorageError(f"S3 업로드 실패: s3://{self._bucket}/{key}") from exc


class LocalStorage:
    def __init__(self, root: Path):
        self._root = Path(root)

    def read_photo(self, key: str) -> bytes:
        return (self._root / key).read_bytes()

    def upload(self, local_path: Path, key: str) -> None:
        target = self._root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, target)


def make_storage(settings: Settings) -> Storage:
    if settings.storage == "s3":
        return S3Storage(settings.output_bucket, settings.photo_bucket)
    return LocalStorage(settings.local_output_dir)

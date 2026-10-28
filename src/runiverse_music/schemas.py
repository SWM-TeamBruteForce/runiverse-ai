"""요청·응답 형식. request_id는 S3 키·파일명에 쓰므로 안전한 문자만 허용."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


class MusicRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    caption: str = Field(min_length=1, max_length=512)
    bpm: int = Field(ge=30, le=300)
    duration: float = Field(ge=10, allow_inf_nan=False)
    seed: int = Field(ge=0, le=2**32 - 1)
    instrumental: Literal[True] = True

    @field_validator("duration")
    @classmethod
    def _within_max_duration(cls, value: float, info: ValidationInfo) -> float:
        max_duration = (info.context or {}).get("max_duration")
        if max_duration is not None and value > max_duration:
            raise ValueError(f"duration은 {max_duration:g}초 이하여야 합니다.")
        return value


class MusicResponse(BaseModel):
    request_id: str
    audio_s3_key: str
    sample_rate: int
    channels: int
    actual_duration_seconds: float
    bytes: int
    generation_seconds: float
    peak_torch_allocated_gib: float
    manifest: dict[str, Any]


def parse_request(body: bytes, max_duration: float) -> MusicRequest:
    return MusicRequest.model_validate_json(body, context={"max_duration": max_duration})

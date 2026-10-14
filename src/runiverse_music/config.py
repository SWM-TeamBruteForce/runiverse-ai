"""컨테이너 환경변수 → Settings. 잘못된 값은 시작 시 ConfigError."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


class ConfigError(ValueError):
    """환경변수 값이 잘못됨."""


@dataclass(frozen=True)
class Settings:
    generator: str
    storage: str
    output_bucket: str | None
    output_prefix: str
    local_output_dir: Path | None
    max_duration_seconds: float
    inference_steps: int
    offload_to_cpu: bool
    audio_format: str
    log_level: str
    model_dir: Path
    acestep_root: Path
    acestep_commit: str
    image_tag: str


def _choice(env: Mapping[str, str], key: str, default: str, choices: tuple[str, ...]) -> str:
    value = env.get(key, default).strip().lower()
    if value not in choices:
        raise ConfigError(f"{key}={value!r}: {', '.join(choices)} 중 하나여야 합니다.")
    return value


def _flag(env: Mapping[str, str], key: str, default: str) -> bool:
    return _choice(env, key, default, ("true", "false")) == "true"


def _number(env: Mapping[str, str], key: str, default: str, cast: Callable, low: float, high: float):
    raw = env.get(key, default).strip()
    try:
        value = cast(raw)
    except ValueError:
        raise ConfigError(f"{key}={raw!r}: 숫자여야 합니다.") from None
    if not low <= value <= high:  # NaN도 여기서 걸림
        raise ConfigError(f"{key}={raw!r}: {low:g}~{high:g} 범위여야 합니다.")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    storage = _choice(env, "STORAGE", "s3", ("s3", "local"))
    bucket = env.get("OUTPUT_BUCKET", "").strip() or None
    if storage == "s3" and bucket is None:
        raise ConfigError("STORAGE=s3이면 OUTPUT_BUCKET이 필요합니다.")
    local_dir = env.get("LOCAL_OUTPUT_DIR", "").strip()
    if storage == "local" and not local_dir:
        raise ConfigError("STORAGE=local이면 LOCAL_OUTPUT_DIR이 필요합니다.")
    prefix = env.get("OUTPUT_PREFIX", "music/outputs").strip().strip("/")
    if not prefix:
        raise ConfigError("OUTPUT_PREFIX가 비어 있습니다.")

    return Settings(
        generator=_choice(env, "GENERATOR", "acestep", ("acestep", "fake")),
        storage=storage,
        output_bucket=bucket,
        output_prefix=prefix,
        local_output_dir=Path(local_dir) if local_dir else None,
        max_duration_seconds=_number(env, "MAX_DURATION_SECONDS", "120", float, 10, 600),
        inference_steps=_number(env, "INFERENCE_STEPS", "8", int, 1, 200),
        offload_to_cpu=_flag(env, "OFFLOAD_TO_CPU", "true"),
        audio_format=_choice(env, "AUDIO_FORMAT", "wav", ("wav",)),
        log_level=_choice(env, "LOG_LEVEL", "INFO", ("debug", "info", "warning", "error")).upper(),
        model_dir=Path(env.get("MODEL_DIR", "/opt/ml/model")),
        acestep_root=Path(env.get("ACESTEP_ROOT", "/opt/ace-step")),
        acestep_commit=env.get("ACESTEP_COMMIT", "unknown"),
        image_tag=env.get("IMAGE_TAG", "unknown"),
    )

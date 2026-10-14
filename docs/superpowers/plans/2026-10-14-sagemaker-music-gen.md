# 🎵 SageMaker 음악 생성 dev 배포 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

- **Goal:** Colab 베이스라인(ACE-Step 1.5 Turbo)을 SageMaker Async 엔드포인트(dev)용 BYOC 컨테이너·배포 스크립트로 구현
- **Architecture:** FastAPI 서빙 패키지 `runiverse_music`(config·schemas·storage·generator·app) + CUDA 컨테이너 + boto3 배포 스크립트(`scripts/`). torch·acestep 의존은 `generator.py`의 `AceStepGenerator` 안에만 둠 → 나머지는 GPU 없는 PC에서 테스트
- **Tech Stack:** Python 3.11(컨테이너)/3.14(로컬), FastAPI, Pydantic v2, uvicorn, boto3, PyYAML, huggingface_hub, pytest, Docker, ACE-Step 1.5 `ca1e85f`
- **Spec:** `docs/superpowers/specs/2026-10-14-sagemaker-music-gen-design.md`

## 📌 Global Constraints

- 저장소 루트: `C:\dev\runiverse\ai` (모든 경로는 루트 기준)
- ACE-Step 커밋: `ca1e85fe9430179831e6bc6be790c332190a3866`, `uv sync --frozen --no-dev --python 3.11`
- 모델 설정: `acestep-v15-turbo`, LM 미사용, `batch_size=1`, `timesignature="4"`, 가사 `[Instrumental]`
- 요청 범위: BPM 30~300, duration 10~`MAX_DURATION_SECONDS`(dev 120), `instrumental`은 `true`만
- 컨테이너: 8080 포트, `GET /ping`, `POST /invocations`, 워커 1개
- 배포 환경 고정값: `GENERATOR=acestep`, `STORAGE=s3` (다른 값이면 배포 거부)
- S3 경로: `music/models/ace-step-1.5/{rev}/model.tar.gz`, `music/requests/{id}.json`, `music/outputs/{id}/{id}.wav`, `music/responses/`, `music/failures/`
- 인스턴스 `ml.g4dn.xlarge`, 오토스케일링 min 0 / max 1, 리전 `ap-northeast-2`
- CPU 대체 금지 — CUDA 없으면 시작 실패
- 로컬 Python 코드는 3.11 문법까지만 사용 (컨테이너 3.11)
- 커밋 메시지: `✨ Feat:` / `🔧 Chore:` / `📝 Docs:` 접두어 + 세션 트레일러

## 🔍 Review Focus

1. Windows에서 쓴 `container/serve`·`*.sh`가 CRLF → 컨테이너에서 실행 실패 → `.gitattributes` + LF 검사 테스트 (Task 1, 7, 10)
2. `request_id`에 `../`·`/` 포함 → S3 키·로컬 경로 오염 없이 400 (Task 3, 6)
3. YAML의 따옴표 없는 불리언(`OFFLOAD_TO_CPU: true`) → 컨테이너에는 `"True"`가 아닌 `"true"`로 전달 (Task 8)
4. 같은 `request_id` 재요청(백엔드 재전송) → 오류 없이 같은 키에 덮어쓰기 (Task 4, 6)
5. OOM 등 생성 실패 직후 다음 요청 → 서비스 유지, 정상 처리 (Task 6)

## 🧰 사전 준비 (사용자)

| 도구 | 필요 Task | 비고 |
|---|---|---|
| Python 3.14 (설치됨) | 1~12 | 로컬 venv |
| Docker Desktop | 7, 10 | 미설치 — WSL2 백엔드 |
| AWS CLI v2 + 자격 증명 | 9, 10, 14 | 미설치 — `aws configure` |
| S3 버킷·SageMaker 실행 Role·SNS 토픽 | 14 | 생성 후 `dev.yaml`에 입력 |
| Colab GPU 런타임 | 13 | 기존 사용 환경 |

- 명령은 Git Bash 기준, 저장소 루트에서 실행
- 로컬 Python 실행: `.venv/Scripts/python`

## 🗂️ 파일 맵

| 파일 | 책임 | Task |
|---|---|---|
| `pyproject.toml`, `.gitignore`, `.gitattributes`, `README.md` | 패키지·저장소 설정 | 1, 14 |
| `notebooks/baseline_colab.ipynb` | 베이스라인 보관 | 1 |
| `src/runiverse_music/config.py` | 환경변수 → `Settings` | 2 |
| `src/runiverse_music/schemas.py` | `MusicRequest`·`MusicResponse`·`parse_request` | 3 |
| `src/runiverse_music/storage.py` | `S3Storage`·`LocalStorage`·`build_key` | 4 |
| `src/runiverse_music/generator.py` | `AceStepGenerator`·`FakeGenerator` | 5 |
| `src/runiverse_music/app.py` | FastAPI 앱 | 6 |
| `container/Dockerfile`, `container/serve`, `.dockerignore` | 컨테이너 | 7 |
| `scripts/deploy_config.py`, `deploy/envs/dev.yaml` | 배포 설정 | 8 |
| `scripts/download_models.py` | 가중치 → S3 | 9 |
| `scripts/build_and_push.sh` | 이미지 → ECR | 10 |
| `scripts/deploy.py` | 엔드포인트·오토스케일링 | 11 |
| `scripts/smoke_test.py` | dev 스모크 | 12 |
| `notebooks/colab_verify.ipynb` | Colab 실생성 검증 | 13 |

---

## 🔧 Task 1: 저장소 골격

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.gitattributes`, `README.md`, `src/runiverse_music/__init__.py`, `tests/test_package.py`, `notebooks/baseline_colab.ipynb`

**Interfaces:**
- Produces: 패키지 `runiverse_music`(src 레이아웃), pytest `pythonpath = ["src", "scripts"]`, extras `dev`·`deploy`

- [ ] **Step 1: 설정 파일 작성**

`pyproject.toml`
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "runiverse-music"
version = "0.1.0"
description = "Runiverse 음악 생성(ACE-Step 1.5) SageMaker 서빙"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110",
    "uvicorn>=0.29",
    "pydantic>=2.6",
    "boto3>=1.34",
]

[project.optional-dependencies]
dev = ["pytest>=8", "httpx>=0.27", "pyyaml>=6"]
deploy = ["pyyaml>=6", "huggingface_hub>=0.23"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src", "scripts"]
testpaths = ["tests"]
```

`.gitignore`
```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.model_build/
*.egg-info/
build/
dist/
```

`.gitattributes`
```
* text=auto
container/serve text eol=lf
*.sh text eol=lf
container/Dockerfile text eol=lf
*.ipynb text eol=lf
```

`README.md`
```markdown
# runiverse-ai

Runiverse 음악 생성(ACE-Step 1.5) — SageMaker Async 엔드포인트 서빙.

- 설계: `docs/superpowers/specs/2026-10-14-sagemaker-music-gen-design.md`
```

`src/runiverse_music/__init__.py`
```python
"""Runiverse 음악 생성 SageMaker 서빙."""
```

- [ ] **Step 2: 실패 테스트 작성** — `tests/test_package.py`

```python
def test_package_imports():
    import runiverse_music

    assert runiverse_music.__doc__
```

- [ ] **Step 3: venv 생성·설치 후 테스트 실행**

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -U pip
.venv/Scripts/python -m pip install -e ".[dev,deploy]"
.venv/Scripts/python -m pytest -v
```
- 기대: `test_package_imports PASSED`

- [ ] **Step 4: 베이스라인 노트북 보관**
- 소스: Google Drive `fileId=1bXALi_W9wAZowpc02C7gZnPDS5_hgXP7`
  - 에이전트: Drive 커넥터 `download_file_content` → base64 디코딩 → 임시 파일
  - 사용자: Colab → 파일 → 다운로드 → `.ipynb`
- 출력 제거 후 저장:
```bash
.venv/Scripts/python - "<다운로드한 파일 경로>" notebooks/baseline_colab.ipynb <<'EOF'
import json, sys
from pathlib import Path
nb = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for cell in nb["cells"]:
    if cell["cell_type"] == "code":
        cell["outputs"] = []
        cell["execution_count"] = None
    cell.get("metadata", {}).pop("executionInfo", None)
Path(sys.argv[2]).parent.mkdir(parents=True, exist_ok=True)
Path(sys.argv[2]).write_text(json.dumps(nb, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
EOF
```
- 확인: `grep -c '"cell_type"' notebooks/baseline_colab.ipynb` → 20 이상 (첫 셀 제목 `Runiverse · ACE-Step 1.5 추론 시작하기`)

- [ ] **Step 5: 커밋**
```bash
git add .
git commit -m "🔧 Chore: 저장소 골격과 베이스라인 노트북"
```

---

## ⚙️ Task 2: `config.py` — 환경변수 설정

**Files:**
- Create: `src/runiverse_music/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `class ConfigError(ValueError)`
  - `@dataclass(frozen=True) Settings`: `generator: str`, `storage: str`, `output_bucket: str | None`, `output_prefix: str`, `local_output_dir: Path | None`, `max_duration_seconds: float`, `inference_steps: int`, `offload_to_cpu: bool`, `audio_format: str`, `log_level: str`, `model_dir: Path`, `acestep_root: Path`, `acestep_commit: str`, `image_tag: str`
  - `load_settings(env: Mapping[str, str] | None = None) -> Settings`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_config.py`

```python
import pytest

from runiverse_music.config import ConfigError, load_settings

LOCAL = {"GENERATOR": "fake", "STORAGE": "local", "LOCAL_OUTPUT_DIR": "/tmp/out"}


def test_defaults_for_s3():
    s = load_settings({"OUTPUT_BUCKET": "bucket-a"})
    assert s.generator == "acestep"
    assert s.storage == "s3"
    assert s.output_bucket == "bucket-a"
    assert s.output_prefix == "music/outputs"
    assert s.max_duration_seconds == 120.0
    assert s.inference_steps == 8
    assert s.offload_to_cpu is True
    assert s.audio_format == "wav"
    assert s.log_level == "INFO"
    assert s.model_dir.as_posix() == "/opt/ml/model"
    assert s.acestep_root.as_posix() == "/opt/ace-step"


def test_local_storage_settings():
    s = load_settings(LOCAL)
    assert s.generator == "fake"
    assert s.storage == "local"
    assert s.local_output_dir.as_posix() == "/tmp/out"
    assert s.output_bucket is None


def test_s3_requires_bucket():
    with pytest.raises(ConfigError, match="OUTPUT_BUCKET"):
        load_settings({})


def test_local_requires_dir():
    with pytest.raises(ConfigError, match="LOCAL_OUTPUT_DIR"):
        load_settings({"STORAGE": "local"})


def test_case_insensitive_choices():
    s = load_settings({**LOCAL, "GENERATOR": "FAKE", "OFFLOAD_TO_CPU": "False", "LOG_LEVEL": "debug"})
    assert s.generator == "fake"
    assert s.offload_to_cpu is False
    assert s.log_level == "DEBUG"


def test_prefix_slashes_trimmed():
    assert load_settings({**LOCAL, "OUTPUT_PREFIX": "/a/b/"}).output_prefix == "a/b"


@pytest.mark.parametrize(
    "key,value",
    [
        ("GENERATOR", "gpu"),
        ("STORAGE", "gcs"),
        ("OFFLOAD_TO_CPU", "yes"),
        ("MAX_DURATION_SECONDS", "5"),
        ("MAX_DURATION_SECONDS", "abc"),
        ("MAX_DURATION_SECONDS", "nan"),
        ("INFERENCE_STEPS", "0"),
        ("INFERENCE_STEPS", "8.5"),
        ("AUDIO_FORMAT", "mp3"),
        ("LOG_LEVEL", "LOUD"),
        ("OUTPUT_PREFIX", "/"),
    ],
)
def test_rejects_invalid_values(key, value):
    with pytest.raises(ConfigError, match=key):
        load_settings({**LOCAL, key: value})
```

- [ ] **Step 2: 실패 확인**
- `.venv/Scripts/python -m pytest tests/test_config.py -v`
- 기대: `ModuleNotFoundError: runiverse_music.config`

- [ ] **Step 3: 구현** — `src/runiverse_music/config.py`

```python
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
```

- [ ] **Step 4: 통과 확인** — `.venv/Scripts/python -m pytest tests/test_config.py -v` → 전부 PASS

- [ ] **Step 5: 커밋**
```bash
git add src/runiverse_music/config.py tests/test_config.py
git commit -m "✨ Feat: 컨테이너 환경변수 설정 로더"
```

---

## 📥 Task 3: `schemas.py` — 요청·응답 형식

**Files:**
- Create: `src/runiverse_music/schemas.py`
- Test: `tests/test_schemas.py`

**Interfaces:**
- Produces:
  - `MusicRequest`: `request_id: str`, `caption: str`, `bpm: int`, `duration: float`, `seed: int`, `instrumental: Literal[True]`
  - `MusicResponse`: `request_id`, `audio_s3_key`, `sample_rate`, `channels`, `actual_duration_seconds`, `bytes`, `generation_seconds`, `peak_torch_allocated_gib`, `manifest: dict[str, Any]`
  - `parse_request(body: bytes, max_duration: float) -> MusicRequest` (실패 시 `pydantic.ValidationError`)

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_schemas.py`

```python
import json
import math

import pytest
from pydantic import ValidationError

from runiverse_music.schemas import MusicRequest, MusicResponse, parse_request

VALID = {
    "request_id": "req-001",
    "caption": "Energetic electronic running music",
    "bpm": 170,
    "duration": 30.0,
    "seed": 42,
    "instrumental": True,
}


def body(**over):
    return json.dumps({**VALID, **over}).encode()


def test_parses_valid_request():
    req = parse_request(body(), max_duration=120)
    assert (req.request_id, req.bpm, req.duration, req.seed) == ("req-001", 170, 30.0, 42)


def test_instrumental_defaults_true():
    data = {k: v for k, v in VALID.items() if k != "instrumental"}
    assert parse_request(json.dumps(data).encode(), 120).instrumental is True


@pytest.mark.parametrize("bpm", [30, 300])
def test_bpm_bounds_ok(bpm):
    assert parse_request(body(bpm=bpm), 120).bpm == bpm


def test_duration_limit_follows_setting():
    assert parse_request(body(duration=200), 300).duration == 200


@pytest.mark.parametrize(
    "over",
    [
        {"bpm": 29},
        {"bpm": 301},
        {"duration": 9.9},
        {"duration": 120.5},
        {"seed": -1},
        {"instrumental": False},
        {"caption": "   "},
        {"caption": "x" * 513},
        {"request_id": ""},
        {"request_id": "../etc"},
        {"request_id": "a/b"},
        {"request_id": "x" * 65},
        {"extra": 1},
    ],
)
def test_rejects_invalid(over):
    with pytest.raises(ValidationError):
        parse_request(body(**over), 120)


def test_rejects_nan_duration():
    with pytest.raises(ValidationError):
        MusicRequest.model_validate({**VALID, "duration": math.nan})


def test_rejects_malformed_json():
    with pytest.raises(ValidationError):
        parse_request(b"{not json", 120)


def test_response_serializes():
    resp = MusicResponse(
        request_id="r", audio_s3_key="k.wav", sample_rate=48000, channels=2,
        actual_duration_seconds=30.0, bytes=10, generation_seconds=1.0,
        peak_torch_allocated_gib=0.0, manifest={"a": 1},
    )
    assert resp.model_dump()["bytes"] == 10
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_schemas.py -v` → `ModuleNotFoundError`

- [ ] **Step 3: 구현** — `src/runiverse_music/schemas.py`

```python
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
```

- [ ] **Step 4: 통과 확인** — `pytest tests/test_schemas.py -v` → 전부 PASS

- [ ] **Step 5: 커밋**
```bash
git add src/runiverse_music/schemas.py tests/test_schemas.py
git commit -m "✨ Feat: 음악 생성 요청·응답 스키마"
```

---

## 🪣 Task 4: `storage.py` — 결과 업로드

**Files:**
- Create: `src/runiverse_music/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `Settings` (Task 2)
- Produces:
  - `class StorageError(RuntimeError)`
  - `build_key(prefix: str, request_id: str, suffix: str) -> str` → `"{prefix}/{id}/{id}{suffix}"`
  - `Storage` Protocol: `upload(local_path: Path, key: str) -> None`
  - `S3Storage(bucket: str, client=None)`, `LocalStorage(root: Path)`
  - `make_storage(settings: Settings) -> Storage`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_storage.py`

```python
import pytest
from botocore.exceptions import ClientError

from runiverse_music.config import load_settings
from runiverse_music.storage import LocalStorage, S3Storage, StorageError, build_key, make_storage


class FakeS3:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        self.calls.append((filename, bucket, key, ExtraArgs))
        if self.error:
            raise self.error


def test_build_key():
    assert build_key("music/outputs", "req-1", ".wav") == "music/outputs/req-1/req-1.wav"
    assert build_key("/music/outputs/", "r", ".wav") == "music/outputs/r/r.wav"


def test_local_storage_copies(tmp_path):
    src = tmp_path / "a.wav"
    src.write_bytes(b"RIFF")
    LocalStorage(tmp_path / "out").upload(src, "music/outputs/r/r.wav")
    assert (tmp_path / "out/music/outputs/r/r.wav").read_bytes() == b"RIFF"


def test_local_storage_overwrites_same_key(tmp_path):
    storage = LocalStorage(tmp_path / "out")
    src = tmp_path / "a.wav"
    src.write_bytes(b"first")
    storage.upload(src, "k/r.wav")
    src.write_bytes(b"second")
    storage.upload(src, "k/r.wav")
    assert (tmp_path / "out/k/r.wav").read_bytes() == b"second"


def test_s3_storage_uploads(tmp_path):
    src = tmp_path / "a.wav"
    src.write_bytes(b"RIFF")
    client = FakeS3()
    S3Storage("bkt", client=client).upload(src, "k.wav")
    assert client.calls == [(str(src), "bkt", "k.wav", {"ContentType": "audio/wav"})]


def test_s3_storage_wraps_errors(tmp_path):
    src = tmp_path / "a.wav"
    src.write_bytes(b"RIFF")
    error = ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "PutObject")
    with pytest.raises(StorageError, match="s3://bkt/k.wav"):
        S3Storage("bkt", client=FakeS3(error)).upload(src, "k.wav")


def test_make_storage_local(tmp_path):
    settings = load_settings({"STORAGE": "local", "LOCAL_OUTPUT_DIR": str(tmp_path)})
    assert isinstance(make_storage(settings), LocalStorage)
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_storage.py -v` → `ModuleNotFoundError`

- [ ] **Step 3: 구현** — `src/runiverse_music/storage.py`

```python
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
```

- [ ] **Step 4: 통과 확인** — `pytest tests/test_storage.py -v` → 전부 PASS

- [ ] **Step 5: 커밋**
```bash
git add src/runiverse_music/storage.py tests/test_storage.py
git commit -m "✨ Feat: S3·로컬 결과 저장소"
```

---

## 🎛️ Task 5: `generator.py` — 음악 생성기

**Files:**
- Create: `src/runiverse_music/generator.py`
- Test: `tests/test_generator.py`

**Interfaces:**
- Consumes: `Settings` (Task 2), `MusicRequest` (Task 3)
- Produces:
  - `class GenerationError(RuntimeError)`, `class CudaOutOfMemory(GenerationError)`
  - `@dataclass(frozen=True) GenerationResult`: `audio_path: Path`, `sample_rate: int`, `channels: int`, `actual_duration_seconds: float`, `size_bytes: int`, `generation_seconds: float`, `peak_torch_allocated_gib: float`
  - `Generator` Protocol: `manifest: dict[str, Any]`, `load() -> None`, `generate(request: MusicRequest, out_dir: Path) -> GenerationResult`
  - `FakeGenerator()`, `AceStepGenerator(settings: Settings)`
  - `check_audio_file(path: Path) -> Path`, `make_generator(settings: Settings) -> Generator`

> ⚠️ `AceStepGenerator.load/generate`는 로컬에서 실행 불가 → Task 13(Colab)에서 검증. 노트북 `infer_music.py` 호출 인자 그대로 유지

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_generator.py`

```python
import subprocess
import sys
import wave

import pytest

from runiverse_music.config import load_settings
from runiverse_music.generator import (
    AceStepGenerator, FakeGenerator, GenerationError, check_audio_file, make_generator,
)
from runiverse_music.schemas import MusicRequest

REQ = MusicRequest(request_id="r1", caption="c", bpm=170, duration=30, seed=42)
BASE_ENV = {"STORAGE": "local", "LOCAL_OUTPUT_DIR": "/tmp/o"}


def test_fake_generator_writes_wav(tmp_path):
    g = FakeGenerator()
    g.load()
    result = g.generate(REQ, tmp_path)
    assert result.audio_path == tmp_path / "r1.wav"
    with wave.open(str(result.audio_path)) as w:
        assert w.getframerate() == 48000
        assert w.getnchannels() == 2
    assert (result.sample_rate, result.channels) == (48000, 2)
    assert result.size_bytes == result.audio_path.stat().st_size > 0
    assert g.manifest == {"generator": "fake"}


def test_check_audio_file_missing(tmp_path):
    with pytest.raises(GenerationError):
        check_audio_file(tmp_path / "none.wav")


def test_check_audio_file_empty(tmp_path):
    path = tmp_path / "e.wav"
    path.write_bytes(b"")
    with pytest.raises(GenerationError):
        check_audio_file(path)


def test_make_generator():
    assert isinstance(make_generator(load_settings({**BASE_ENV, "GENERATOR": "fake"})), FakeGenerator)
    assert isinstance(make_generator(load_settings({**BASE_ENV, "GENERATOR": "acestep"})), AceStepGenerator)


def test_acestep_generate_before_load_fails(tmp_path):
    g = AceStepGenerator(load_settings(BASE_ENV))
    with pytest.raises(GenerationError, match="로드"):
        g.generate(REQ, tmp_path)


def test_module_import_does_not_load_torch():
    code = "import sys, runiverse_music.generator; print('torch' in sys.modules, 'acestep' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "False False"
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_generator.py -v` → `ModuleNotFoundError`

- [ ] **Step 3: 구현** — `src/runiverse_music/generator.py`

```python
"""음악 생성기. torch·acestep·soundfile은 AceStepGenerator 메서드 안에서만 import한다."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .config import Settings
from .schemas import MusicRequest

CONFIG_PATH = "acestep-v15-turbo"


class GenerationError(RuntimeError):
    """생성 실패 (요청 단위)."""


class CudaOutOfMemory(GenerationError):
    """GPU 메모리 부족."""


@dataclass(frozen=True)
class GenerationResult:
    audio_path: Path
    sample_rate: int
    channels: int
    actual_duration_seconds: float
    size_bytes: int
    generation_seconds: float
    peak_torch_allocated_gib: float


class Generator(Protocol):
    manifest: dict[str, Any]

    def load(self) -> None: ...

    def generate(self, request: MusicRequest, out_dir: Path) -> GenerationResult: ...


def check_audio_file(path: Path) -> Path:
    # 성공 플래그가 있어도 파일 저장이 실패할 수 있어 별도로 검사한다.
    if not path.is_file() or path.stat().st_size == 0:
        raise GenerationError(f"음원 파일이 없거나 비어 있습니다: {path}")
    return path


class FakeGenerator:
    """모델 없이 1초 무음 wav를 만든다. 로컬·컨테이너 계약 검증용."""

    SAMPLE_RATE = 48000
    CHANNELS = 2

    def __init__(self) -> None:
        self.manifest: dict[str, Any] = {"generator": "fake"}

    def load(self) -> None:
        pass

    def generate(self, request: MusicRequest, out_dir: Path) -> GenerationResult:
        path = Path(out_dir) / f"{request.request_id}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(self.CHANNELS)
            w.setsampwidth(2)
            w.setframerate(self.SAMPLE_RATE)
            w.writeframes(b"\x00" * self.SAMPLE_RATE * self.CHANNELS * 2)
        check_audio_file(path)
        return GenerationResult(path, self.SAMPLE_RATE, self.CHANNELS, 1.0, path.stat().st_size, 0.0, 0.0)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


class AceStepGenerator:
    """ACE-Step 1.5 Turbo. 노트북 infer_music.py의 로딩·생성 로직."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._dit = None
        self.manifest: dict[str, Any] = {}

    def load(self) -> None:
        import torch
        from acestep.handler import AceStepHandler

        s = self._settings
        if not torch.cuda.is_available():
            raise GenerationError("CUDA GPU가 필요합니다. CPU로 전환하지 않습니다.")
        if not s.model_dir.is_dir():
            raise GenerationError(f"모델 폴더가 없습니다: {s.model_dir}")
        os.environ["ACESTEP_CHECKPOINTS_DIR"] = str(s.model_dir)

        started = time.perf_counter()
        dit = AceStepHandler()
        # 반환 순서는 공식 API의 (status_message, success)
        status, success = dit.initialize_service(
            project_root=str(s.acestep_root),
            config_path=CONFIG_PATH,
            device="cuda",
            use_flash_attention=False,
            compile_model=False,
            quantization=None,
            offload_to_cpu=s.offload_to_cpu,
            offload_dit_to_cpu=False,
            prefer_source="huggingface",
        )
        if not success:
            raise GenerationError(f"DiT 초기화 실패: {status}")
        if str(dit.device) != "cuda":
            raise GenerationError(f"CUDA 요청이 {dit.device}로 전환되었습니다.")
        torch.cuda.synchronize()
        self._dit = dit
        self.manifest = {
            "source_commit": s.acestep_commit,
            "model_revisions": _read_json(s.model_dir / "model_revisions.json"),
            "uv_lock_sha256": _sha256(s.acestep_root / "uv.lock"),
            "image_tag": s.image_tag,
            "python": sys.version,
            "torch": torch.__version__,
            "torch_cuda": torch.version.cuda,
            "gpu": torch.cuda.get_device_name(0),
            "dtype": str(dit.dtype),
            "model_loading_seconds": time.perf_counter() - started,
            "settings": {
                "config_path": CONFIG_PATH,
                "inference_steps": s.inference_steps,
                "offload_to_cpu": s.offload_to_cpu,
                "audio_format": s.audio_format,
                "use_lm": False,
            },
        }

    def generate(self, request: MusicRequest, out_dir: Path) -> GenerationResult:
        if self._dit is None:
            raise GenerationError("모델이 로드되지 않았습니다.")

        import soundfile as sf
        import torch
        from acestep.inference import GenerationConfig, GenerationParams, generate_music

        s = self._settings
        params = GenerationParams(
            task_type="text2music",
            caption=request.caption,
            lyrics="[Instrumental]",
            instrumental=True,
            bpm=request.bpm,
            duration=request.duration,
            timesignature="4",
            seed=request.seed,
            inference_steps=s.inference_steps,
            thinking=False,
            use_cot_metas=False,
            use_cot_caption=False,
            use_cot_lyrics=False,
            use_cot_language=False,
        )
        config = GenerationConfig(
            batch_size=1,
            use_random_seed=False,
            seeds=[request.seed],
            audio_format=s.audio_format,
        )
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        started = time.perf_counter()
        try:
            with torch.inference_mode():
                result = generate_music(self._dit, None, params, config, save_dir=str(out_dir))
            torch.cuda.synchronize()
        except torch.cuda.OutOfMemoryError as exc:
            torch.cuda.empty_cache()
            raise CudaOutOfMemory("CUDA 메모리 부족") from exc
        elapsed = time.perf_counter() - started

        if not result.success:
            message = str(result.error or result.status_message)
            torch.cuda.empty_cache()
            if "out of memory" in message.lower():
                raise CudaOutOfMemory(message)
            raise GenerationError(message)
        if not result.audios:
            raise GenerationError("음원 결과가 없습니다.")
        raw_path = result.audios[0].get("path")
        if not raw_path:
            raise GenerationError("추론 결과의 음원 파일 경로가 비어 있습니다.")
        path = check_audio_file(Path(raw_path).resolve())
        info = sf.info(str(path))
        peak = torch.cuda.max_memory_allocated() / 2**30
        # 이전 결과 텐서가 다음 생성의 메모리를 차지하지 않게 해제
        del result
        return GenerationResult(
            path, int(info.samplerate), int(info.channels), float(info.duration),
            path.stat().st_size, elapsed, peak,
        )


def make_generator(settings: Settings) -> Generator:
    if settings.generator == "fake":
        return FakeGenerator()
    return AceStepGenerator(settings)
```

- [ ] **Step 4: 통과 확인** — `pytest tests/test_generator.py -v` → 전부 PASS

- [ ] **Step 5: 커밋**
```bash
git add src/runiverse_music/generator.py tests/test_generator.py
git commit -m "✨ Feat: ACE-Step 생성기와 검증용 가짜 생성기"
```

---

## 🌐 Task 6: `app.py` — FastAPI 서빙

**Files:**
- Create: `src/runiverse_music/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: Task 2~5 전부
- Produces:
  - `create_app(settings: Settings, generator: Generator, storage: Storage) -> FastAPI`
  - `build_app() -> FastAPI` (uvicorn `--factory` 진입점: `runiverse_music.app:build_app`)
  - 오류 응답: 400 `{"error": "invalid_request", "detail": [...]}` / 500 `{"error": "cuda_oom" | "generation_failed" | "upload_failed" | "internal_error", "request_id", "message"}`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_app.py`

```python
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runiverse_music.app import build_app, create_app
from runiverse_music.config import load_settings
from runiverse_music.generator import CudaOutOfMemory, FakeGenerator, GenerationError
from runiverse_music.storage import LocalStorage, StorageError

REQ = {"request_id": "req-1", "caption": "running music", "bpm": 170, "duration": 30, "seed": 42}


@pytest.fixture
def settings(tmp_path):
    return load_settings({"GENERATOR": "fake", "STORAGE": "local", "LOCAL_OUTPUT_DIR": str(tmp_path / "out")})


def make_client(settings, generator=None, storage=None):
    app = create_app(settings, generator or FakeGenerator(), storage or LocalStorage(settings.local_output_dir))
    return TestClient(app)


def post(client, payload):
    return client.post("/invocations", content=json.dumps(payload), headers={"Content-Type": "application/json"})


def test_ping_503_before_startup(settings):
    client = make_client(settings)  # with 없이 → lifespan 미실행
    assert client.get("/ping").status_code == 503


def test_ping_200_after_load(settings):
    with make_client(settings) as client:
        assert client.get("/ping").status_code == 200


def test_load_failure_aborts_startup(settings):
    class Broken(FakeGenerator):
        def load(self):
            raise GenerationError("no cuda")

    with pytest.raises(GenerationError):
        with make_client(settings, Broken()):
            pass


def test_invocation_success(settings):
    with make_client(settings) as client:
        r = post(client, REQ)
    assert r.status_code == 200
    data = r.json()
    assert data["request_id"] == "req-1"
    assert data["audio_s3_key"] == "music/outputs/req-1/req-1.wav"
    assert data["sample_rate"] == 48000
    assert data["bytes"] > 0
    assert data["manifest"]["generator"] == "fake"
    assert data["manifest"]["request"]["bpm"] == 170
    assert (settings.local_output_dir / data["audio_s3_key"]).is_file()


def test_same_request_twice_overwrites(settings):
    with make_client(settings) as client:
        first, second = post(client, REQ), post(client, REQ)
    assert first.status_code == second.status_code == 200
    assert first.json()["audio_s3_key"] == second.json()["audio_s3_key"]


@pytest.mark.parametrize(
    "payload",
    [{**REQ, "bpm": 500}, {**REQ, "duration": 121}, {**REQ, "request_id": "../x"}, {**REQ, "instrumental": False}],
)
def test_invalid_request_400(settings, payload):
    with make_client(settings) as client:
        r = post(client, payload)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"


def test_malformed_json_400(settings):
    with make_client(settings) as client:
        r = client.post("/invocations", content=b"{oops", headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_oom_then_recovers(settings):
    class OomOnce(FakeGenerator):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def generate(self, request, out_dir):
            self.calls += 1
            if self.calls == 1:
                raise CudaOutOfMemory("CUDA out of memory")
            return super().generate(request, out_dir)

    with make_client(settings, OomOnce()) as client:
        first, second = post(client, REQ), post(client, REQ)
    assert first.status_code == 500
    assert first.json()["error"] == "cuda_oom"
    assert second.status_code == 200


def test_generation_failure_500(settings):
    class Failing(FakeGenerator):
        def generate(self, request, out_dir):
            raise GenerationError("empty file")

    with make_client(settings, Failing()) as client:
        r = post(client, REQ)
    assert r.status_code == 500
    assert r.json() == {"error": "generation_failed", "request_id": "req-1", "message": "empty file"}


def test_upload_failure_500(settings):
    class BrokenStorage:
        def upload(self, local_path, key):
            raise StorageError("s3 down")

    with make_client(settings, storage=BrokenStorage()) as client:
        r = post(client, REQ)
    assert r.status_code == 500
    assert r.json()["error"] == "upload_failed"


def test_build_app_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GENERATOR", "fake")
    monkeypatch.setenv("STORAGE", "local")
    monkeypatch.setenv("LOCAL_OUTPUT_DIR", str(tmp_path))
    assert isinstance(build_app(), FastAPI)
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_app.py -v` → `ModuleNotFoundError`

- [ ] **Step 3: 구현** — `src/runiverse_music/app.py`

```python
"""SageMaker 서빙 앱: GET /ping, POST /invocations."""
from __future__ import annotations

import json
import logging
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from .config import Settings, load_settings
from .generator import CudaOutOfMemory, GenerationError, Generator, make_generator
from .schemas import MusicRequest, MusicResponse, parse_request
from .storage import Storage, StorageError, build_key, make_storage

logger = logging.getLogger("runiverse_music")


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    # CloudWatch에서 request_id로 검색할 수 있게 요청당 JSON 한 줄
    logger.log(level, json.dumps({"event": event, **fields}, ensure_ascii=False, default=str))


def failure(music_request: MusicRequest, code: str, exc: Exception) -> JSONResponse:
    log_event("request_failed", logging.ERROR, request_id=music_request.request_id, error=code, message=str(exc))
    return JSONResponse(
        status_code=500,
        content={"error": code, "request_id": music_request.request_id, "message": str(exc)},
    )


def create_app(settings: Settings, generator: Generator, storage: Storage) -> FastAPI:
    gpu_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 로드 실패 시 예외 → 서버 종료 → SageMaker가 엔드포인트를 Failed로 둠
        started = time.perf_counter()
        generator.load()
        app.state.ready = True
        log_event("model_loaded", generator=settings.generator, seconds=round(time.perf_counter() - started, 2))
        yield

    app = FastAPI(lifespan=lifespan)
    app.state.ready = False

    @app.get("/ping")
    def ping() -> Response:
        return Response(status_code=200 if app.state.ready else 503)

    @app.post("/invocations")
    async def invocations(request: Request) -> JSONResponse:
        body = await request.body()
        return await run_in_threadpool(handle, body)

    def handle(body: bytes) -> JSONResponse:
        try:
            music_request = parse_request(body, settings.max_duration_seconds)
        except ValidationError as exc:
            detail = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
            log_event("invalid_request", logging.WARNING, detail=detail)
            return JSONResponse(status_code=400, content={"error": "invalid_request", "detail": detail})
        with gpu_lock:
            return generate_and_upload(music_request)

    def generate_and_upload(music_request: MusicRequest) -> JSONResponse:
        started = time.perf_counter()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = generator.generate(music_request, Path(tmp))
                key = build_key(settings.output_prefix, music_request.request_id, result.audio_path.suffix)
                storage.upload(result.audio_path, key)
        except CudaOutOfMemory as exc:
            return failure(music_request, "cuda_oom", exc)
        except GenerationError as exc:
            return failure(music_request, "generation_failed", exc)
        except StorageError as exc:
            return failure(music_request, "upload_failed", exc)
        except Exception as exc:  # 예상 못 한 오류도 요청 단위 실패로 끝내고 서비스는 유지
            logger.exception("unexpected error")
            return failure(music_request, "internal_error", exc)

        response = MusicResponse(
            request_id=music_request.request_id,
            audio_s3_key=key,
            sample_rate=result.sample_rate,
            channels=result.channels,
            actual_duration_seconds=result.actual_duration_seconds,
            bytes=result.size_bytes,
            generation_seconds=result.generation_seconds,
            peak_torch_allocated_gib=result.peak_torch_allocated_gib,
            manifest={**generator.manifest, "request": music_request.model_dump()},
        )
        log_event(
            "generated",
            request_id=music_request.request_id,
            generation_seconds=round(result.generation_seconds, 2),
            peak_torch_allocated_gib=round(result.peak_torch_allocated_gib, 2),
            total_seconds=round(time.perf_counter() - started, 2),
        )
        return JSONResponse(content=response.model_dump())

    return app


def configure_logging(level: str) -> None:
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stdout, force=True)
    try:
        from loguru import logger as loguru_logger  # ACE-Step 로그 (컨테이너에만 있음)
    except ImportError:
        return
    loguru_logger.remove()
    loguru_logger.add(sys.stdout, level=level)


def build_app() -> FastAPI:
    """uvicorn --factory 진입점."""
    settings = load_settings()
    configure_logging(settings.log_level)
    return create_app(settings, make_generator(settings), make_storage(settings))
```

- [ ] **Step 4: 통과 확인** — `pytest -v` (전체) → 전부 PASS

- [ ] **Step 5: 커밋**
```bash
git add src/runiverse_music/app.py tests/test_app.py
git commit -m "✨ Feat: SageMaker /ping·/invocations 서빙 앱"
```

---

## 🐳 Task 7: 컨테이너 + 로컬 계약 검증 ②

**Files:**
- Create: `container/Dockerfile`, `container/serve`, `.dockerignore`
- Test: `tests/test_container_files.py`

**Interfaces:**
- Consumes: `runiverse_music.app:build_app` (Task 6)
- Produces: 이미지 내부 경로 — ACE-Step `/opt/ace-step`, venv `/opt/ace-step/.venv`, `/opt/ace-step/checkpoints → /opt/ml/model`, 진입 명령 `serve`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_container_files.py`

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_serve_uses_lf_and_shebang():
    data = (ROOT / "container" / "serve").read_bytes()
    assert data.startswith(b"#!/bin/sh\n")
    assert b"\r\n" not in data


def test_dockerfile_pins_acestep_commit():
    text = (ROOT / "container" / "Dockerfile").read_text(encoding="utf-8")
    assert "ca1e85fe9430179831e6bc6be790c332190a3866" in text
    assert "uv sync --frozen --no-dev --python 3.11" in text
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_container_files.py -v` → `FileNotFoundError`

- [ ] **Step 3: 파일 작성**

`container/serve` (LF 저장)
```sh
#!/bin/sh
# SageMaker는 `docker run <image> serve`로 이 파일을 실행한다.
set -e
exec python -m uvicorn runiverse_music.app:build_app --factory \
  --host 0.0.0.0 --port 8080 --workers 1 --timeout-keep-alive 75
```

`container/Dockerfile`
```dockerfile
# Runiverse 음악 생성 — SageMaker BYOC 컨테이너
# PyTorch cu128 휠이 CUDA 라이브러리를 포함 → base 이미지로 충분
FROM nvidia/cuda:12.8.1-base-ubuntu22.04

ARG ACESTEP_COMMIT=ca1e85fe9430179831e6bc6be790c332190a3866
ARG ACESTEP_REPO=https://github.com/ace-step/ACE-Step-1.5.git
ARG IMAGE_TAG=local

ENV DEBIAN_FRONTEND=noninteractive \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates ffmpeg libsndfile1 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8.4 /uv /uvx /usr/local/bin/

# ACE-Step 고정 커밋 + 공식 uv.lock 그대로 설치 (Colab 검증 환경 재현)
WORKDIR /opt/ace-step
RUN git init -q . \
 && git remote add origin "$ACESTEP_REPO" \
 && git fetch -q --depth 1 origin "$ACESTEP_COMMIT" \
 && git checkout -q --detach FETCH_HEAD \
 && uv python install 3.11 \
 && uv sync --frozen --no-dev --python 3.11 \
 && rm -rf checkpoints \
 && ln -s /opt/ml/model checkpoints

ENV PATH=/opt/ace-step/.venv/bin:$PATH

# 서빙 코드는 ACE-Step venv에 설치 (이미 있는 의존성 버전 우선)
COPY pyproject.toml README.md /opt/app/
COPY src /opt/app/src
RUN uv pip install --python /opt/ace-step/.venv/bin/python --no-cache /opt/app

COPY container/serve /usr/local/bin/serve
RUN chmod +x /usr/local/bin/serve

ENV ACESTEP_ROOT=/opt/ace-step \
    ACESTEP_COMMIT=$ACESTEP_COMMIT \
    IMAGE_TAG=$IMAGE_TAG \
    MODEL_DIR=/opt/ml/model \
    ACESTEP_CHECKPOINTS_DIR=/opt/ml/model \
    HF_HUB_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false \
    MPLBACKEND=Agg \
    PYTHONUNBUFFERED=1

EXPOSE 8080
```
- `HF_HUB_OFFLINE=1`: 가중치 누락 시 다운로드 시도 대신 즉시 실패

`.dockerignore`
```
.git
.venv
.pytest_cache
.model_build
**/__pycache__
docs
notebooks
tests
```

- [ ] **Step 4: 통과 확인** — `pytest tests/test_container_files.py -v` → PASS

- [ ] **Step 5: 이미지 빌드** (Docker Desktop 필요, 첫 빌드 수십 분·약 10GB)
```bash
docker build -f container/Dockerfile -t runiverse-music:local .
```
- 기대: 빌드 성공
- `uv:0.8.4` pull 실패 시: `docker pull ghcr.io/astral-sh/uv:0.8` 로 존재하는 태그 확인 후 Dockerfile 태그만 교체

- [ ] **Step 6: 가짜 생성기로 계약 검증**
```bash
docker run --rm -d --name rm-local -p 8080:8080 \
  -e GENERATOR=fake -e STORAGE=local -e LOCAL_OUTPUT_DIR=/tmp/out \
  runiverse-music:local serve
until curl -sf localhost:8080/ping; do sleep 2; done; echo "ping OK"
curl -s -X POST localhost:8080/invocations -H "Content-Type: application/json" \
  -d '{"request_id":"local-1","caption":"running music","bpm":170,"duration":30,"seed":42}'
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8080/invocations \
  -H "Content-Type: application/json" -d '{"request_id":"local-2","caption":"x","bpm":500,"duration":30,"seed":1}'
docker exec rm-local ls -l /tmp/out/music/outputs/local-1/
docker rm -f rm-local
```
- 기대: `ping OK` → 응답 JSON(`"audio_s3_key":"music/outputs/local-1/local-1.wav"`) → `400` → `local-1.wav` 존재

- [ ] **Step 7: GPU 없는 환경에서 실모드 시작 실패 확인** (CPU 대체 금지 검증)
```bash
docker run --rm -e STORAGE=local -e LOCAL_OUTPUT_DIR=/tmp/out runiverse-music:local serve; echo "exit=$?"
```
- 기대: 로그에 `CUDA GPU가 필요합니다`, `exit=` 0이 아닌 값

- [ ] **Step 8: 커밋**
```bash
git add container .dockerignore tests/test_container_files.py
git commit -m "✨ Feat: SageMaker BYOC 컨테이너"
```

---

## 🗒️ Task 8: 배포 설정 — `deploy_config.py` + `dev.yaml`

**Files:**
- Create: `scripts/deploy_config.py`, `deploy/envs/dev.yaml`
- Test: `tests/test_deploy_config.py`

**Interfaces:**
- Produces:
  - `class DeployConfigError(ValueError)`
  - `@dataclass(frozen=True) Autoscaling(min_capacity: int, max_capacity: int, target_backlog_per_instance: float, scale_in_cooldown: int, scale_out_cooldown: int)`
  - `@dataclass(frozen=True) DeployConfig`: `env_name, region, endpoint_name, ecr_repository, instance_type, initial_instance_count, max_concurrent_invocations_per_instance, model_data_download_timeout, container_startup_health_check_timeout, autoscaling: Autoscaling, bucket, execution_role_arn, model_data_url, sns_success_topic_arn, sns_error_topic_arn, inference_ami_version, container_env: dict[str, str]`
  - `LOCKED_ENV = {"GENERATOR": "acestep", "STORAGE": "s3"}`
  - `load_deploy_config(env_name: str, root: Path = ROOT) -> DeployConfig`
  - `require_deployable(cfg: DeployConfig) -> None`
  - CLI: `python scripts/deploy_config.py <env> <field>` → 값 출력

- [ ] **Step 1: `deploy/envs/dev.yaml` 작성**

```yaml
# runiverse-music dev 배포 설정 — deploy.py 실행 시 읽음 (값 변경 후 재배포만 하면 반영)
region: ap-northeast-2
endpoint_name: runiverse-music-dev
ecr_repository: runiverse-music
instance_type: ml.g4dn.xlarge
initial_instance_count: 1
max_concurrent_invocations_per_instance: 1
model_data_download_timeout: 600
container_startup_health_check_timeout: 600
autoscaling:
  min_capacity: 0
  max_capacity: 1
  target_backlog_per_instance: 1.0
  scale_in_cooldown: 600
  scale_out_cooldown: 300

# ▼ AWS 리소스 생성 후 입력 (bucket·execution_role_arn·model_data_url 비어 있으면 배포 거부)
bucket: ""
execution_role_arn: ""
model_data_url: ""          # download_models.py 출력값
sns_success_topic_arn: ""   # 비우면 알림 없음
sns_error_topic_arn: ""
inference_ami_version: ""   # 드라이버 호환 문제 시 지정

container_env:
  OUTPUT_PREFIX: music/outputs
  MAX_DURATION_SECONDS: "120"
  INFERENCE_STEPS: "8"
  OFFLOAD_TO_CPU: "true"
  AUDIO_FORMAT: wav
  LOG_LEVEL: INFO
```

- [ ] **Step 2: 실패 테스트 작성** — `tests/test_deploy_config.py`

```python
from pathlib import Path

import pytest
import yaml

from deploy_config import DeployConfigError, load_deploy_config, require_deployable

REPO_ROOT = Path(__file__).resolve().parents[1]


def env_with(tmp_path, container_env=None, **changes):
    raw = yaml.safe_load((REPO_ROOT / "deploy/envs/dev.yaml").read_text(encoding="utf-8"))
    raw.update(changes)
    if container_env:
        raw["container_env"].update(container_env)
    target = tmp_path / "deploy" / "envs"
    target.mkdir(parents=True)
    (target / "dev.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return tmp_path


def test_repo_dev_yaml_loads():
    cfg = load_deploy_config("dev", REPO_ROOT)
    assert cfg.endpoint_name == "runiverse-music-dev"
    assert cfg.instance_type == "ml.g4dn.xlarge"
    assert (cfg.autoscaling.min_capacity, cfg.autoscaling.max_capacity) == (0, 1)
    assert cfg.container_env["MAX_DURATION_SECONDS"] == "120"
    assert cfg.bucket == ""


def test_missing_env_file(tmp_path):
    with pytest.raises(DeployConfigError, match="설정 파일"):
        load_deploy_config("prod", tmp_path)


@pytest.mark.parametrize("env", [{"GENERATOR": "fake"}, {"STORAGE": "local"}])
def test_rejects_test_only_modes(tmp_path, env):
    with pytest.raises(DeployConfigError, match="만 허용"):
        load_deploy_config("dev", env_with(tmp_path, container_env=env))


def test_unquoted_bool_becomes_lowercase(tmp_path):
    cfg = load_deploy_config("dev", env_with(tmp_path, container_env={"OFFLOAD_TO_CPU": True}))
    assert cfg.container_env["OFFLOAD_TO_CPU"] == "true"


def test_unknown_key_rejected(tmp_path):
    with pytest.raises(DeployConfigError, match="형식"):
        load_deploy_config("dev", env_with(tmp_path, instanse_type="typo"))


def test_require_deployable_lists_missing():
    cfg = load_deploy_config("dev", REPO_ROOT)
    with pytest.raises(DeployConfigError) as info:
        require_deployable(cfg)
    for key in ("bucket", "execution_role_arn", "model_data_url"):
        assert key in str(info.value)


def test_require_deployable_ok(tmp_path):
    root = env_with(
        tmp_path, bucket="bkt", execution_role_arn="arn:aws:iam::1:role/r",
        model_data_url="s3://bkt/m/model.tar.gz",
    )
    require_deployable(load_deploy_config("dev", root))
```

- [ ] **Step 3: 실패 확인** — `pytest tests/test_deploy_config.py -v` → `ModuleNotFoundError: deploy_config`

- [ ] **Step 4: 구현** — `scripts/deploy_config.py`

```python
"""deploy/envs/<env>.yaml 로더. 배포 스크립트 공용.

사용: python scripts/deploy_config.py dev region  → 값 출력
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LOCKED_ENV = {"GENERATOR": "acestep", "STORAGE": "s3"}
REQUIRED_FOR_DEPLOY = ("bucket", "execution_role_arn", "model_data_url")


class DeployConfigError(ValueError):
    """배포 설정 오류."""


@dataclass(frozen=True)
class Autoscaling:
    min_capacity: int
    max_capacity: int
    target_backlog_per_instance: float
    scale_in_cooldown: int
    scale_out_cooldown: int


@dataclass(frozen=True)
class DeployConfig:
    env_name: str
    region: str
    endpoint_name: str
    ecr_repository: str
    instance_type: str
    initial_instance_count: int
    max_concurrent_invocations_per_instance: int
    model_data_download_timeout: int
    container_startup_health_check_timeout: int
    autoscaling: Autoscaling
    bucket: str
    execution_role_arn: str
    model_data_url: str
    sns_success_topic_arn: str
    sns_error_topic_arn: str
    inference_ami_version: str
    container_env: dict[str, str]


def _env_value(value) -> str:
    # YAML 불리언(true)이 "True"로 바뀌면 컨테이너 설정 검증에서 실패하므로 소문자로
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def load_deploy_config(env_name: str, root: Path = ROOT) -> DeployConfig:
    path = Path(root) / "deploy" / "envs" / f"{env_name}.yaml"
    if not path.is_file():
        raise DeployConfigError(f"설정 파일이 없습니다: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        autoscaling = Autoscaling(**raw.pop("autoscaling"))
        container_env = {str(k): _env_value(v) for k, v in (raw.pop("container_env") or {}).items()}
        fields = {k: ("" if v is None else v) for k, v in raw.items()}
        cfg = DeployConfig(env_name=env_name, autoscaling=autoscaling, container_env=container_env, **fields)
    except (KeyError, TypeError) as exc:
        raise DeployConfigError(f"{path} 형식 오류: {exc}") from exc
    for key, allowed in LOCKED_ENV.items():
        if cfg.container_env.get(key, allowed) != allowed:
            raise DeployConfigError(f"{key}는 배포 환경에서 {allowed}만 허용합니다.")
    return cfg


def require_deployable(cfg: DeployConfig) -> None:
    missing = [key for key in REQUIRED_FOR_DEPLOY if not getattr(cfg, key)]
    if missing:
        raise DeployConfigError(f"deploy/envs/{cfg.env_name}.yaml에 값을 채우세요: {', '.join(missing)}")


def main(argv: list[str]) -> int:
    env_name, field = argv
    print(getattr(load_deploy_config(env_name), field))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 5: 통과 확인** — `pytest tests/test_deploy_config.py -v` → 전부 PASS
- CLI 확인: `.venv/Scripts/python scripts/deploy_config.py dev region` → `ap-northeast-2`

- [ ] **Step 6: 커밋**
```bash
git add scripts/deploy_config.py deploy/envs/dev.yaml tests/test_deploy_config.py
git commit -m "✨ Feat: dev 배포 설정과 로더"
```

---

## 📦 Task 9: `download_models.py` — 가중치 → S3

**Files:**
- Create: `scripts/download_models.py`
- Test: `tests/test_download_models.py`

**Interfaces:**
- Consumes: `load_deploy_config` (Task 8)
- Produces:
  - `REPO_ID = "ACE-Step/Ace-Step1.5"`
  - `model_key(revision: str) -> str`
  - `write_revisions(checkpoints: Path, revisions: dict[str, str]) -> None` → `checkpoints/model_revisions.json` (컨테이너 `AceStepGenerator`가 `/opt/ml/model/model_revisions.json`으로 읽음)
  - `make_tarball(checkpoints: Path, tar_path: Path) -> None` → 압축 루트 = checkpoints 내용, `.cache` 제외

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_download_models.py`

```python
import json
import tarfile

import download_models as dm


def test_model_key():
    assert dm.model_key("abc") == "music/models/ace-step-1.5/abc/model.tar.gz"


def test_tarball_root_layout(tmp_path):
    ck = tmp_path / "checkpoints"
    (ck / "acestep-v15-turbo").mkdir(parents=True)
    (ck / "acestep-v15-turbo" / "model.safetensors").write_bytes(b"w")
    (ck / ".cache" / "huggingface").mkdir(parents=True)
    (ck / ".cache" / "huggingface" / "meta").write_text("m")
    dm.write_revisions(ck, {dm.REPO_ID: "abc"})

    tar_path = tmp_path / "model.tar.gz"
    dm.make_tarball(ck, tar_path)

    with tarfile.open(tar_path) as tar:
        assert sorted(tar.getnames()) == [
            "acestep-v15-turbo",
            "acestep-v15-turbo/model.safetensors",
            "model_revisions.json",
        ]
        assert json.load(tar.extractfile("model_revisions.json")) == {dm.REPO_ID: "abc"}
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_download_models.py -v` → `ModuleNotFoundError`

- [ ] **Step 3: 구현** — `scripts/download_models.py`

```python
"""ACE-Step 1.5 가중치(HF) → model.tar.gz → S3.

사용: python scripts/download_models.py --env dev [--revision <HF 커밋 sha>]
- 디스크: 모델 + 압축본 → 모델 크기의 약 2배 여유 필요
- 출력 S3 URI → deploy/envs/<env>.yaml 의 model_data_url 에 입력
"""
from __future__ import annotations

import argparse
import json
import sys
import tarfile
from pathlib import Path

from deploy_config import ROOT, load_deploy_config

REPO_ID = "ACE-Step/Ace-Step1.5"
SKIP_NAMES = {".cache"}  # huggingface_hub local_dir 메타데이터


def model_key(revision: str) -> str:
    return f"music/models/ace-step-1.5/{revision}/model.tar.gz"


def write_revisions(checkpoints: Path, revisions: dict[str, str]) -> None:
    (checkpoints / "model_revisions.json").write_text(json.dumps(revisions, indent=2), encoding="utf-8")


def make_tarball(checkpoints: Path, tar_path: Path) -> None:
    # SageMaker가 /opt/ml/model에 풀므로 압축 루트 = checkpoints 내용
    # safetensors는 거의 압축되지 않아 속도 우선(compresslevel=1)
    with tarfile.open(tar_path, "w:gz", compresslevel=1) as tar:
        for item in sorted(checkpoints.iterdir()):
            if item.name not in SKIP_NAMES:
                tar.add(item, arcname=item.name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ACE-Step 가중치를 S3 model.tar.gz로 올린다.")
    parser.add_argument("--env", default="dev")
    parser.add_argument("--revision", help="HF 커밋 sha (생략 시 현재 main)")
    parser.add_argument("--workdir", type=Path, default=ROOT / ".model_build")
    args = parser.parse_args(argv)

    cfg = load_deploy_config(args.env)
    if not cfg.bucket:
        print(f"deploy/envs/{args.env}.yaml의 bucket을 먼저 채우세요.", file=sys.stderr)
        return 2

    import boto3
    from huggingface_hub import HfApi, snapshot_download

    revision = args.revision or HfApi().model_info(REPO_ID).sha
    build_dir = args.workdir / revision
    checkpoints = build_dir / "checkpoints"
    print(f"다운로드: {REPO_ID}@{revision}", flush=True)
    snapshot_download(repo_id=REPO_ID, revision=revision, local_dir=str(checkpoints))
    write_revisions(checkpoints, {REPO_ID: revision})

    tar_path = build_dir / "model.tar.gz"
    print("압축 중 (수 분 소요)...", flush=True)
    make_tarball(checkpoints, tar_path)

    key = model_key(revision)
    print(f"업로드: s3://{cfg.bucket}/{key}", flush=True)
    boto3.client("s3", region_name=cfg.region).upload_file(str(tar_path), cfg.bucket, key)
    print(f"완료 → deploy/envs/{args.env}.yaml 의 model_data_url 에 입력:\ns3://{cfg.bucket}/{key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 통과 확인** — `pytest tests/test_download_models.py -v` → PASS

- [ ] **Step 5: 커밋**
```bash
git add scripts/download_models.py tests/test_download_models.py
git commit -m "✨ Feat: 모델 가중치 S3 패키징 스크립트"
```

---

## 🚢 Task 10: `build_and_push.sh` — 이미지 → ECR

**Files:**
- Create: `scripts/build_and_push.sh`
- Modify: `tests/test_container_files.py` (LF 검사 추가)

**Interfaces:**
- Consumes: `scripts/deploy_config.py` CLI (Task 8), `container/Dockerfile` (Task 7)
- Produces: 마지막 줄 stdout = 이미지 URI `<account>.dkr.ecr.<region>.amazonaws.com/<repo>:<git sha 12자리>` → Task 11 `--image-uri`

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_container_files.py` 끝에

```python
def test_build_script_uses_lf():
    data = (ROOT / "scripts" / "build_and_push.sh").read_bytes()
    assert data.startswith(b"#!/usr/bin/env bash\n")
    assert b"\r\n" not in data
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_container_files.py -v` → `FileNotFoundError`

- [ ] **Step 3: 구현** — `scripts/build_and_push.sh` (LF 저장)

```bash
#!/usr/bin/env bash
# 이미지 빌드 → ECR push. 태그 = 현재 git 커밋.
# 사용: scripts/build_and_push.sh [env]   (venv 활성화 상태, AWS CLI 로그인 상태)
set -euo pipefail

ENV_NAME="${1:-dev}"
PY="${PYTHON:-python}"
cd "$(dirname "$0")/.."

if [ -n "$(git status --porcelain)" ]; then
  echo "커밋되지 않은 변경이 있습니다. 태그와 코드가 어긋나므로 커밋 후 실행하세요." >&2
  exit 1
fi

REGION="$("$PY" scripts/deploy_config.py "$ENV_NAME" region)"
REPO="$("$PY" scripts/deploy_config.py "$ENV_NAME" ecr_repository)"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
TAG="$(git rev-parse --short=12 HEAD)"
REGISTRY="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
IMAGE="$REGISTRY/$REPO:$TAG"

aws ecr describe-repositories --repository-names "$REPO" --region "$REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$REPO" --region "$REGION" >/dev/null
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

docker build --platform linux/amd64 -f container/Dockerfile --build-arg IMAGE_TAG="$TAG" -t "$IMAGE" .
docker push "$IMAGE"
echo "$IMAGE"
```

- [ ] **Step 4: 통과 확인** — `pytest tests/test_container_files.py -v` → PASS
- 문법 확인: `bash -n scripts/build_and_push.sh` → 출력 없음

- [ ] **Step 5: 커밋**
```bash
git add scripts/build_and_push.sh tests/test_container_files.py
git commit -m "✨ Feat: ECR 이미지 빌드·푸시 스크립트"
```

---

## 🚀 Task 11: `deploy.py` — 엔드포인트·오토스케일링

**Files:**
- Create: `scripts/deploy.py`
- Test: `tests/test_deploy.py`

**Interfaces:**
- Consumes: `DeployConfig`, `Autoscaling`, `LOCKED_ENV`, `DeployConfigError`, `load_deploy_config`, `require_deployable` (Task 8)
- Produces:
  - `check_image_uri(image_uri: str) -> None`, `resource_name(cfg, image_uri, now) -> str`
  - `container_environment(cfg) -> dict[str, str]`
  - `build_model_request(cfg, name, image_uri) -> dict`, `build_endpoint_config_request(cfg, name, model_name) -> dict`
  - `build_scalable_target(cfg)`, `build_backlog_policy(cfg)`, `build_wake_policy(cfg)`, `build_wake_alarm(cfg, policy_arn)` → dict
  - `deploy(cfg, image_uri, sm, aas, cw, now) -> str` (생성한 Model/EndpointConfig 이름)
  - CLI: `python scripts/deploy.py --env dev --image-uri <URI> [--dry-run]`

> ℹ️ 기존 엔드포인트 갱신 시 오토스케일링 해제 → `update_endpoint` → `InService` 후 재등록 (스케일링 등록 상태에서는 갱신 불가)

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_deploy.py`

```python
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

import deploy
from deploy_config import Autoscaling, DeployConfig, DeployConfigError

NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)
IMAGE = "123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/runiverse-music:abc123def4567890"


def make_cfg(**over):
    base = dict(
        env_name="dev", region="ap-northeast-2", endpoint_name="runiverse-music-dev",
        ecr_repository="runiverse-music", instance_type="ml.g4dn.xlarge",
        initial_instance_count=1, max_concurrent_invocations_per_instance=1,
        model_data_download_timeout=600, container_startup_health_check_timeout=600,
        autoscaling=Autoscaling(0, 1, 1.0, 600, 300), bucket="bkt",
        execution_role_arn="arn:aws:iam::123456789012:role/sm",
        model_data_url="s3://bkt/music/models/ace-step-1.5/rev/model.tar.gz",
        sns_success_topic_arn="", sns_error_topic_arn="", inference_ami_version="",
        container_env={"MAX_DURATION_SECONDS": "120", "LOG_LEVEL": "INFO"},
    )
    base.update(over)
    return DeployConfig(**base)


class FakeClient:
    def __init__(self, responses=None, errors=None):
        self.calls = []
        self.responses = responses or {}
        self.errors = errors or {}

    def __getattr__(self, name):
        def method(**kwargs):
            self.calls.append((name, kwargs))
            if name in self.errors:
                raise self.errors[name]
            return self.responses.get(name, {})
        return method

    def get_waiter(self, name):
        self.calls.append(("get_waiter", {"name": name}))
        return SimpleNamespace(wait=lambda **kw: self.calls.append(("wait", kw)))


def client_error(code, op):
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


def test_resource_name():
    name = deploy.resource_name(make_cfg(), IMAGE, NOW)
    assert name == "runiverse-music-dev-abc123def456-20261014120000"
    assert len(name) <= 63


def test_image_uri_requires_tag():
    with pytest.raises(DeployConfigError):
        deploy.check_image_uri("123.dkr.ecr.ap-northeast-2.amazonaws.com/runiverse-music")
    deploy.check_image_uri(IMAGE)


def test_container_environment_locks_values():
    env = deploy.container_environment(make_cfg(container_env={"GENERATOR": "fake", "STORAGE": "local"}))
    assert env["GENERATOR"] == "acestep"
    assert env["STORAGE"] == "s3"
    assert env["OUTPUT_BUCKET"] == "bkt"
    assert env["OUTPUT_PREFIX"] == "music/outputs"


def test_model_request():
    req = deploy.build_model_request(make_cfg(), "m1", IMAGE)
    assert req["ModelName"] == "m1"
    assert req["ExecutionRoleArn"] == "arn:aws:iam::123456789012:role/sm"
    assert req["PrimaryContainer"]["Image"] == IMAGE
    assert req["PrimaryContainer"]["ModelDataUrl"].endswith("model.tar.gz")
    assert req["PrimaryContainer"]["Environment"]["MAX_DURATION_SECONDS"] == "120"


def test_endpoint_config_without_optional():
    req = deploy.build_endpoint_config_request(make_cfg(), "c1", "m1")
    variant = req["ProductionVariants"][0]
    assert variant["VariantName"] == "AllTraffic"
    assert variant["InstanceType"] == "ml.g4dn.xlarge"
    assert variant["ModelDataDownloadTimeoutInSeconds"] == 600
    assert "InferenceAmiVersion" not in variant
    assert req["AsyncInferenceConfig"]["ClientConfig"] == {"MaxConcurrentInvocationsPerInstance": 1}
    assert req["AsyncInferenceConfig"]["OutputConfig"] == {
        "S3OutputPath": "s3://bkt/music/responses/",
        "S3FailurePath": "s3://bkt/music/failures/",
    }


def test_endpoint_config_with_sns_and_ami():
    cfg = make_cfg(sns_success_topic_arn="arn:s", sns_error_topic_arn="arn:e", inference_ami_version="ami-x")
    req = deploy.build_endpoint_config_request(cfg, "c1", "m1")
    assert req["ProductionVariants"][0]["InferenceAmiVersion"] == "ami-x"
    assert req["AsyncInferenceConfig"]["OutputConfig"]["NotificationConfig"] == {
        "SuccessTopic": "arn:s", "ErrorTopic": "arn:e",
    }


def test_scaling_requests():
    cfg = make_cfg()
    target = deploy.build_scalable_target(cfg)
    assert target["ResourceId"] == "endpoint/runiverse-music-dev/variant/AllTraffic"
    assert (target["MinCapacity"], target["MaxCapacity"]) == (0, 1)
    spec = deploy.build_backlog_policy(cfg)["TargetTrackingScalingPolicyConfiguration"]
    assert spec["CustomizedMetricSpecification"]["MetricName"] == "ApproximateBacklogSizePerInstance"
    assert spec["TargetValue"] == 1.0
    wake = deploy.build_wake_policy(cfg)
    assert wake["PolicyType"] == "StepScaling"
    alarm = deploy.build_wake_alarm(cfg, "arn:policy")
    assert alarm["MetricName"] == "HasBacklogWithoutCapacity"
    assert alarm["AlarmActions"] == ["arn:policy"]


def test_deploy_creates_new_endpoint():
    sm = FakeClient(errors={"describe_endpoint": client_error("ValidationException", "DescribeEndpoint")})
    aas = FakeClient(responses={"put_scaling_policy": {"PolicyARN": "arn:policy"}})
    cw = FakeClient()
    name = deploy.deploy(make_cfg(), IMAGE, sm, aas, cw, NOW)
    assert name == "runiverse-music-dev-abc123def456-20261014120000"
    assert [c[0] for c in sm.calls] == [
        "create_model", "create_endpoint_config", "describe_endpoint", "create_endpoint", "get_waiter", "wait",
    ]
    assert [c[0] for c in aas.calls] == ["register_scalable_target", "put_scaling_policy", "put_scaling_policy"]
    assert cw.calls[0][1]["AlarmActions"] == ["arn:policy"]


def test_deploy_updates_existing_endpoint():
    sm = FakeClient()
    aas = FakeClient(
        responses={"put_scaling_policy": {"PolicyARN": "arn:policy"}},
        errors={"deregister_scalable_target": client_error("ObjectNotFoundException", "Deregister")},
    )
    deploy.deploy(make_cfg(), IMAGE, sm, aas, FakeClient(), NOW)
    names = [c[0] for c in sm.calls]
    assert "update_endpoint" in names and "create_endpoint" not in names
    assert aas.calls[0][0] == "deregister_scalable_target"


def test_main_rejects_unfilled_config(capsys):
    assert deploy.main(["--env", "dev", "--image-uri", IMAGE]) == 2
    assert "bucket" in capsys.readouterr().err
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_deploy.py -v` → `ModuleNotFoundError: deploy`

- [ ] **Step 3: 구현** — `scripts/deploy.py`

```python
"""dev.yaml 기준 SageMaker Async 엔드포인트 배포 + 0대 축소 오토스케일링.

사용: python scripts/deploy.py --env dev --image-uri <ECR URI:tag> [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone

from deploy_config import (
    LOCKED_ENV, DeployConfig, DeployConfigError, load_deploy_config, require_deployable,
)

VARIANT = "AllTraffic"
SCALABLE_DIMENSION = "sagemaker:variant:DesiredInstanceCount"


def check_image_uri(image_uri: str) -> None:
    if ":" not in image_uri.rsplit("/", 1)[-1]:
        raise DeployConfigError("--image-uri에 태그가 필요합니다 (build_and_push.sh 출력값).")


def resource_name(cfg: DeployConfig, image_uri: str, now: datetime) -> str:
    tag = re.sub(r"[^A-Za-z0-9-]", "-", image_uri.rsplit(":", 1)[-1])[:12]
    return f"{cfg.endpoint_name}-{tag}-{now:%Y%m%d%H%M%S}"


def container_environment(cfg: DeployConfig) -> dict[str, str]:
    env = {"OUTPUT_PREFIX": "music/outputs", **cfg.container_env, **LOCKED_ENV}
    env["OUTPUT_BUCKET"] = cfg.bucket
    return env


def build_model_request(cfg: DeployConfig, name: str, image_uri: str) -> dict:
    return {
        "ModelName": name,
        "ExecutionRoleArn": cfg.execution_role_arn,
        "PrimaryContainer": {
            "Image": image_uri,
            "ModelDataUrl": cfg.model_data_url,
            "Environment": container_environment(cfg),
        },
    }


def build_endpoint_config_request(cfg: DeployConfig, name: str, model_name: str) -> dict:
    variant = {
        "VariantName": VARIANT,
        "ModelName": model_name,
        "InstanceType": cfg.instance_type,
        "InitialInstanceCount": cfg.initial_instance_count,
        "ModelDataDownloadTimeoutInSeconds": cfg.model_data_download_timeout,
        "ContainerStartupHealthCheckTimeoutInSeconds": cfg.container_startup_health_check_timeout,
    }
    if cfg.inference_ami_version:
        variant["InferenceAmiVersion"] = cfg.inference_ami_version
    output = {
        "S3OutputPath": f"s3://{cfg.bucket}/music/responses/",
        "S3FailurePath": f"s3://{cfg.bucket}/music/failures/",
    }
    notification = {}
    if cfg.sns_success_topic_arn:
        notification["SuccessTopic"] = cfg.sns_success_topic_arn
    if cfg.sns_error_topic_arn:
        notification["ErrorTopic"] = cfg.sns_error_topic_arn
    if notification:
        output["NotificationConfig"] = notification
    return {
        "EndpointConfigName": name,
        "ProductionVariants": [variant],
        "AsyncInferenceConfig": {
            "ClientConfig": {"MaxConcurrentInvocationsPerInstance": cfg.max_concurrent_invocations_per_instance},
            "OutputConfig": output,
        },
    }


def _scaling_target(cfg: DeployConfig) -> dict:
    return {
        "ServiceNamespace": "sagemaker",
        "ResourceId": f"endpoint/{cfg.endpoint_name}/variant/{VARIANT}",
        "ScalableDimension": SCALABLE_DIMENSION,
    }


def build_scalable_target(cfg: DeployConfig) -> dict:
    return {
        **_scaling_target(cfg),
        "MinCapacity": cfg.autoscaling.min_capacity,
        "MaxCapacity": cfg.autoscaling.max_capacity,
    }


def build_backlog_policy(cfg: DeployConfig) -> dict:
    # 대기열 기준 증감. 대기열 0이 지속되면 0대까지 축소
    return {
        **_scaling_target(cfg),
        "PolicyName": f"{cfg.endpoint_name}-backlog",
        "PolicyType": "TargetTrackingScaling",
        "TargetTrackingScalingPolicyConfiguration": {
            "TargetValue": cfg.autoscaling.target_backlog_per_instance,
            "CustomizedMetricSpecification": {
                "MetricName": "ApproximateBacklogSizePerInstance",
                "Namespace": "AWS/SageMaker",
                "Dimensions": [{"Name": "EndpointName", "Value": cfg.endpoint_name}],
                "Statistic": "Average",
            },
            "ScaleInCooldown": cfg.autoscaling.scale_in_cooldown,
            "ScaleOutCooldown": cfg.autoscaling.scale_out_cooldown,
        },
    }


def build_wake_policy(cfg: DeployConfig) -> dict:
    # 0대일 때 요청이 오면 1대로 깨우기
    return {
        **_scaling_target(cfg),
        "PolicyName": f"{cfg.endpoint_name}-wake-from-zero",
        "PolicyType": "StepScaling",
        "StepScalingPolicyConfiguration": {
            "AdjustmentType": "ChangeInCapacity",
            "MetricAggregationType": "Average",
            "Cooldown": cfg.autoscaling.scale_out_cooldown,
            "StepAdjustments": [{"MetricIntervalLowerBound": 0, "ScalingAdjustment": 1}],
        },
    }


def build_wake_alarm(cfg: DeployConfig, policy_arn: str) -> dict:
    return {
        "AlarmName": f"{cfg.endpoint_name}-has-backlog-without-capacity",
        "MetricName": "HasBacklogWithoutCapacity",
        "Namespace": "AWS/SageMaker",
        "Dimensions": [{"Name": "EndpointName", "Value": cfg.endpoint_name}],
        "Statistic": "Average",
        "Period": 60,
        "EvaluationPeriods": 2,
        "DatapointsToAlarm": 2,
        "Threshold": 1,
        "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        "TreatMissingData": "missing",
        "AlarmActions": [policy_arn],
    }


def endpoint_exists(sm, name: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        sm.describe_endpoint(EndpointName=name)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ValidationException":
            return False
        raise


def remove_autoscaling(aas, cfg: DeployConfig) -> None:
    from botocore.exceptions import ClientError

    try:
        aas.deregister_scalable_target(**_scaling_target(cfg))
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ObjectNotFoundException":
            raise


def apply_autoscaling(aas, cw, cfg: DeployConfig) -> None:
    aas.register_scalable_target(**build_scalable_target(cfg))
    aas.put_scaling_policy(**build_backlog_policy(cfg))
    wake = aas.put_scaling_policy(**build_wake_policy(cfg))
    cw.put_metric_alarm(**build_wake_alarm(cfg, wake["PolicyARN"]))


def deploy(cfg: DeployConfig, image_uri: str, sm, aas, cw, now: datetime) -> str:
    name = resource_name(cfg, image_uri, now)
    sm.create_model(**build_model_request(cfg, name, image_uri))
    sm.create_endpoint_config(**build_endpoint_config_request(cfg, name, name))
    if endpoint_exists(sm, cfg.endpoint_name):
        remove_autoscaling(aas, cfg)
        sm.update_endpoint(EndpointName=cfg.endpoint_name, EndpointConfigName=name)
    else:
        sm.create_endpoint(EndpointName=cfg.endpoint_name, EndpointConfigName=name)
    print(f"{cfg.endpoint_name} 반영 대기 중 (최대 30분)...", flush=True)
    sm.get_waiter("endpoint_in_service").wait(
        EndpointName=cfg.endpoint_name, WaiterConfig={"Delay": 30, "MaxAttempts": 60}
    )
    apply_autoscaling(aas, cw, cfg)
    return name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SageMaker Async 엔드포인트 배포")
    parser.add_argument("--env", default="dev")
    parser.add_argument("--image-uri", required=True)
    parser.add_argument("--dry-run", action="store_true", help="요청 내용만 출력")
    args = parser.parse_args(argv)

    try:
        cfg = load_deploy_config(args.env)
        require_deployable(cfg)
        check_image_uri(args.image_uri)
    except DeployConfigError as exc:
        print(f"배포 거부: {exc}", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    if args.dry_run:
        name = resource_name(cfg, args.image_uri, now)
        print(json.dumps({
            "model": build_model_request(cfg, name, args.image_uri),
            "endpoint_config": build_endpoint_config_request(cfg, name, name),
            "scalable_target": build_scalable_target(cfg),
        }, indent=2, ensure_ascii=False))
        return 0

    import boto3
    from botocore.exceptions import WaiterError

    session = boto3.Session(region_name=cfg.region)
    sm = session.client("sagemaker")
    try:
        name = deploy(cfg, args.image_uri, sm, session.client("application-autoscaling"),
                      session.client("cloudwatch"), now)
    except WaiterError:
        reason = sm.describe_endpoint(EndpointName=cfg.endpoint_name).get("FailureReason", "알 수 없음")
        print(f"엔드포인트 배포 실패: {reason}", file=sys.stderr)
        return 1
    print(f"배포 완료: {cfg.endpoint_name} ← {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 통과 확인** — `pytest tests/test_deploy.py -v` → 전부 PASS

- [ ] **Step 5: 커밋**
```bash
git add scripts/deploy.py tests/test_deploy.py
git commit -m "✨ Feat: Async 엔드포인트 배포와 0대 축소 오토스케일링"
```

---

## 💨 Task 12: `smoke_test.py` — dev 스모크

**Files:**
- Create: `scripts/smoke_test.py`
- Test: `tests/test_smoke_test.py`

**Interfaces:**
- Consumes: `load_deploy_config` (Task 8), `parse_request` (Task 3, 테스트에서만)
- Produces:
  - `build_request(request_id: str) -> dict` (노트북 기본 요청: 170 BPM · 30초 · seed 42)
  - `parse_s3_uri(uri: str) -> tuple[str, str]`
  - `check_response(response: dict, expected_duration: float) -> list[str]` (문제 목록, 빈 목록 = 통과)
  - `wait_for_result(s3, output_uri, failure_uri, timeout, interval=15.0, sleep=time.sleep, clock=time.monotonic) -> tuple[str, str]` (`"success"|"failure"`, 본문)

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_smoke_test.py`

```python
import io
import json

import pytest
from botocore.exceptions import ClientError

import smoke_test as st
from runiverse_music.schemas import parse_request

NOT_FOUND = ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")


class FakeS3:
    def __init__(self, objects, appear_after=0):
        self.objects = objects
        self.appear_after = appear_after
        self.heads = 0

    def head_object(self, Bucket, Key):
        self.heads += 1
        path = f"{Bucket}/{Key}"
        if self.heads <= self.appear_after or path not in self.objects:
            raise NOT_FOUND
        return {"ContentLength": len(self.objects[path])}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[f"{Bucket}/{Key}"])}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


GOOD = {"sample_rate": 48000, "actual_duration_seconds": 30.0, "bytes": 100, "audio_s3_key": "music/outputs/r/r.wav"}


def test_build_request_is_valid():
    req = parse_request(json.dumps(st.build_request("smoke-1")).encode(), max_duration=120)
    assert (req.bpm, req.duration, req.seed) == (170, 30.0, 42)


def test_parse_s3_uri():
    assert st.parse_s3_uri("s3://bkt/a/b.json") == ("bkt", "a/b.json")
    with pytest.raises(ValueError):
        st.parse_s3_uri("https://x")


def test_check_response_ok():
    assert st.check_response(GOOD, 30.0) == []


@pytest.mark.parametrize(
    "over", [{"sample_rate": 44100}, {"actual_duration_seconds": 25.0}, {"bytes": 0}, {"audio_s3_key": "x.mp3"}]
)
def test_check_response_problems(over):
    assert len(st.check_response({**GOOD, **over}, 30.0)) == 1


def test_wait_success_after_polling():
    clock = Clock()
    s3 = FakeS3({"bkt/out.json": b'{"ok": 1}'}, appear_after=2)
    status, body = st.wait_for_result(s3, "s3://bkt/out.json", "s3://bkt/fail.json", 300, sleep=clock.sleep, clock=clock)
    assert (status, body) == ("success", '{"ok": 1}')
    assert clock.now > 0


def test_wait_failure():
    clock = Clock()
    s3 = FakeS3({"bkt/fail.json": b"boom"})
    assert st.wait_for_result(s3, "s3://bkt/out.json", "s3://bkt/fail.json", 300, sleep=clock.sleep, clock=clock) == ("failure", "boom")


def test_wait_timeout():
    clock = Clock()
    with pytest.raises(TimeoutError):
        st.wait_for_result(FakeS3({}), "s3://bkt/out.json", None, 30, interval=15, sleep=clock.sleep, clock=clock)
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_smoke_test.py -v` → `ModuleNotFoundError`

- [ ] **Step 3: 구현** — `scripts/smoke_test.py`

```python
"""dev 엔드포인트 스모크 테스트: 기본 요청 → Async 호출 → 결과 검증.

사용: python scripts/smoke_test.py --env dev [--timeout 1800]
- 인스턴스 0대에서 시작하면 콜드 스타트로 10분 이상 걸릴 수 있음
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

from deploy_config import load_deploy_config

DEFAULT_CAPTION = (
    "Energetic electronic running music, steady clear drum beat, "
    "uplifting synth melody, consistent rhythm, instrumental, no vocals"
)
EXPECTED_SAMPLE_RATE = 48000
DURATION_TOLERANCE_SECONDS = 1.0


def build_request(request_id: str) -> dict:
    return {
        "request_id": request_id,
        "caption": DEFAULT_CAPTION,
        "bpm": 170,
        "duration": 30.0,
        "seed": 42,
        "instrumental": True,
    }


def parse_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"S3 URI가 아닙니다: {uri}")
    bucket, _, key = uri[len("s3://"):].partition("/")
    return bucket, key


def check_response(response: dict, expected_duration: float) -> list[str]:
    problems = []
    if response.get("sample_rate") != EXPECTED_SAMPLE_RATE:
        problems.append(f"sample_rate={response.get('sample_rate')} (기대 {EXPECTED_SAMPLE_RATE})")
    actual = response.get("actual_duration_seconds")
    if not isinstance(actual, (int, float)) or abs(actual - expected_duration) > DURATION_TOLERANCE_SECONDS:
        problems.append(f"actual_duration_seconds={actual} (기대 약 {expected_duration:g})")
    if not response.get("bytes"):
        problems.append("bytes가 0입니다")
    if not str(response.get("audio_s3_key", "")).endswith(".wav"):
        problems.append(f"audio_s3_key={response.get('audio_s3_key')} (wav 아님)")
    return problems


def _exists(s3, uri: str) -> bool:
    from botocore.exceptions import ClientError

    bucket, key = parse_s3_uri(uri)
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def wait_for_result(s3, output_uri: str, failure_uri: str | None, timeout: float,
                    interval: float = 15.0, sleep=time.sleep, clock=time.monotonic) -> tuple[str, str]:
    deadline = clock() + timeout
    while True:
        for status, uri in (("success", output_uri), ("failure", failure_uri)):
            if uri and _exists(s3, uri):
                bucket, key = parse_s3_uri(uri)
                return status, s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        if clock() >= deadline:
            raise TimeoutError(f"{timeout:.0f}초 안에 결과가 없습니다: {output_uri}")
        sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="dev 엔드포인트 스모크 테스트")
    parser.add_argument("--env", default="dev")
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args(argv)

    cfg = load_deploy_config(args.env)
    if not cfg.bucket:
        print(f"deploy/envs/{args.env}.yaml의 bucket을 먼저 채우세요.", file=sys.stderr)
        return 2

    import boto3

    session = boto3.Session(region_name=cfg.region)
    s3 = session.client("s3")
    runtime = session.client("sagemaker-runtime")

    request_id = f"smoke-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    request_key = f"music/requests/{request_id}.json"
    s3.put_object(Bucket=cfg.bucket, Key=request_key, Body=json.dumps(build_request(request_id)).encode(),
                  ContentType="application/json")
    invoked = runtime.invoke_endpoint_async(
        EndpointName=cfg.endpoint_name,
        InputLocation=f"s3://{cfg.bucket}/{request_key}",
        ContentType="application/json",
        InferenceId=request_id,
        InvocationTimeoutSeconds=3600,
    )
    print(f"호출: {request_id} → {invoked['OutputLocation']}", flush=True)

    status, body = wait_for_result(s3, invoked["OutputLocation"], invoked.get("FailureLocation"), args.timeout)
    if status == "failure":
        print(f"실패:\n{body}", file=sys.stderr)
        return 1

    response = json.loads(body)
    problems = check_response(response, 30.0)
    if response.get("audio_s3_key"):
        head = s3.head_object(Bucket=cfg.bucket, Key=response["audio_s3_key"])
        if head["ContentLength"] != response.get("bytes"):
            problems.append(f"S3 크기 {head['ContentLength']} ≠ 응답 bytes {response.get('bytes')}")
    if problems:
        print("검증 실패:\n- " + "\n- ".join(problems), file=sys.stderr)
        return 1
    print(json.dumps(response, indent=2, ensure_ascii=False))
    print("✅ 스모크 테스트 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 통과 확인** — `pytest -v` (전체) → 전부 PASS

- [ ] **Step 5: 커밋**
```bash
git add scripts/smoke_test.py tests/test_smoke_test.py
git commit -m "✨ Feat: dev 엔드포인트 스모크 테스트"
```

---

## 📓 Task 13: Colab 실생성 검증 ③

**Files:**
- Create: `notebooks/colab_verify.ipynb`

**Interfaces:**
- Consumes: `runiverse_music.app:build_app`, 환경변수(Task 2), 응답 형식(Task 3)
- Produces: Colab에서 `AceStepGenerator` 실제 동작 확인 결과

- [ ] **Step 1: 노트북 생성** — 아래 스크립트 1회 실행 (스크립트는 커밋하지 않음)

```bash
.venv/Scripts/python - <<'EOF'
import json
from pathlib import Path

cells = [
("markdown", """# Runiverse · runiverse-ai Colab 실생성 검증
- 목적: 서버 코드(app·generator·storage)를 Colab GPU에서 그대로 실행
- 런타임: 런타임 유형 변경 → GPU(T4)
- 통과 기준: `/ping` 200 → `/invocations` 200 + wav 48kHz·약 30초 → BPM 500 요청 400"""),
("code", """import json, os, shutil, subprocess, sys, time
from pathlib import Path

assert Path("/content").exists(), "Google Colab에서 실행하세요."
ROOT = Path("/content/runiverse_acestep")
REPO = ROOT / "ACE-Step-1.5"
SOURCE_COMMIT = "ca1e85fe9430179831e6bc6be790c332190a3866"
REPO_URL = "https://github.com/ace-step/ACE-Step-1.5.git"
ROOT.mkdir(parents=True, exist_ok=True)
print(subprocess.run(["nvidia-smi"], check=True, text=True, capture_output=True).stdout)"""),
("code", """# ACE-Step 환경 (베이스라인 노트북 2번 셀과 동일)
def run(args, **kwargs):
    print("실행:", " ".join(map(str, args)))
    subprocess.run(list(map(str, args)), check=True, **kwargs)

run([sys.executable, "-m", "pip", "install", "--quiet", "uv>=0.8"])
UV = shutil.which("uv")
run([UV, "python", "install", "3.11"])
if not REPO.exists():
    run(["git", "clone", "--no-checkout", "--depth", "1", REPO_URL, REPO])
run(["git", "fetch", "--depth", "1", "origin", SOURCE_COMMIT], cwd=REPO)
run(["git", "checkout", "--detach", SOURCE_COMMIT], cwd=REPO)
run([UV, "sync", "--frozen", "--no-dev", "--python", "3.11"], cwd=REPO)
PY311 = REPO / ".venv" / "bin" / "python"
print("추론용 Python:", PY311)"""),
("code", """# 모델 다운로드 + 리비전 기록 (download_models.py와 같은 위치: checkpoints/model_revisions.json)
CHECKPOINTS = REPO / "checkpoints"
CHECKPOINTS.mkdir(exist_ok=True)
download = ROOT / "download_verify.py"
download.write_text('''
import json, sys
from pathlib import Path
from huggingface_hub import HfApi, snapshot_download
target = Path(sys.argv[1])
repo_id = "ACE-Step/Ace-Step1.5"
rev_file = target / "model_revisions.json"
revisions = json.loads(rev_file.read_text()) if rev_file.exists() else {}
revisions.setdefault(repo_id, HfApi().model_info(repo_id).sha)
snapshot_download(repo_id=repo_id, revision=revisions[repo_id], local_dir=str(target))
rev_file.write_text(json.dumps(revisions, indent=2))
print("모델 준비 완료:", revisions)
''', encoding="utf-8")
run([PY311, download, CHECKPOINTS], cwd=REPO)"""),
("code", """# runiverse-ai 코드 가져오기
# A) GitHub 원격 생성 후: GITHUB_URL 지정 (비공개면 토큰 포함 URL)
# B) 그 전: Drive의 DRIVE_CODE_DIR에 저장소 폴더 업로드
GITHUB_URL = ""
DRIVE_CODE_DIR = "/content/drive/MyDrive/Runiverse/runiverse-ai"
CODE_DIR = ROOT / "runiverse-ai"
if CODE_DIR.exists():
    shutil.rmtree(CODE_DIR)
if GITHUB_URL:
    run(["git", "clone", "--depth", "1", GITHUB_URL, CODE_DIR])
else:
    from google.colab import drive
    drive.mount("/content/drive")
    shutil.copytree(DRIVE_CODE_DIR, CODE_DIR,
                    ignore=shutil.ignore_patterns(".venv", ".git", "__pycache__", ".model_build"))
run([UV, "pip", "install", "--python", PY311, CODE_DIR])"""),
("code", """# SageMaker와 같은 /opt/ml/model 경로로 연결 후 서버 실행 (결과는 로컬 저장)
MODEL_DIR = Path("/opt/ml/model")
MODEL_DIR.parent.mkdir(parents=True, exist_ok=True)
if MODEL_DIR.is_symlink():
    MODEL_DIR.unlink()
if not MODEL_DIR.exists():
    MODEL_DIR.symlink_to(CHECKPOINTS, target_is_directory=True)

OUT_DIR = ROOT / "verify_outputs"
SERVER_LOG = ROOT / "server.log"
env = os.environ.copy()
env.update({
    "GENERATOR": "acestep", "STORAGE": "local", "LOCAL_OUTPUT_DIR": str(OUT_DIR),
    "MODEL_DIR": str(MODEL_DIR), "ACESTEP_ROOT": str(REPO), "ACESTEP_CHECKPOINTS_DIR": str(MODEL_DIR),
    "ACESTEP_COMMIT": SOURCE_COMMIT, "IMAGE_TAG": "colab", "HF_HUB_OFFLINE": "1",
    "TOKENIZERS_PARALLELISM": "false", "MPLBACKEND": "Agg", "LOG_LEVEL": "INFO",
})
server = subprocess.Popen(
    [PY311, "-m", "uvicorn", "runiverse_music.app:build_app", "--factory",
     "--host", "127.0.0.1", "--port", "8080", "--workers", "1"],
    cwd=REPO, env=env, stdout=SERVER_LOG.open("w"), stderr=subprocess.STDOUT,
)

import urllib.request
deadline = time.time() + 600
while True:
    if server.poll() is not None:
        print(SERVER_LOG.read_text()[-5000:])
        raise RuntimeError("서버가 종료되었습니다. 위 로그를 확인하세요.")
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/ping", timeout=5) as r:
            if r.status == 200:
                break
    except OSError:
        pass
    if time.time() > deadline:
        raise TimeoutError("10분 안에 /ping 200이 오지 않았습니다.")
    time.sleep(5)
print("/ping 200 — 모델 로드 완료")"""),
("code", """# 기본 요청 → 200 + 음원 확인
import requests
from IPython.display import Audio, display

payload = {
    "request_id": "colab-verify-001",
    "caption": ("Energetic electronic running music, steady clear drum beat, "
                "uplifting synth melody, consistent rhythm, instrumental, no vocals"),
    "bpm": 170, "duration": 30.0, "seed": 42, "instrumental": True,
}
r = requests.post("http://127.0.0.1:8080/invocations", json=payload, timeout=600)
print(r.status_code)
result = r.json()
print(json.dumps(result, indent=2, ensure_ascii=False))
assert r.status_code == 200, SERVER_LOG.read_text()[-5000:]
assert result["sample_rate"] == 48000
assert abs(result["actual_duration_seconds"] - 30) <= 1
display(Audio(filename=str(OUT_DIR / result["audio_s3_key"])))"""),
("code", """# 범위 위반 → 400
bad = requests.post("http://127.0.0.1:8080/invocations", json={**payload, "bpm": 500}, timeout=60)
print(bad.status_code, bad.json())
assert bad.status_code == 400"""),
("code", """# 서버 종료 + 로그 확인 (model_loaded·generated JSON 로그)
server.terminate()
server.wait(timeout=30)
print(SERVER_LOG.read_text()[-3000:])"""),
]

nb = {
    "cells": [
        {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True),
         **({"outputs": [], "execution_count": None} if kind == "code" else {})}
        for kind, src in cells
    ],
    "metadata": {"accelerator": "GPU", "colab": {"provenance": []},
                 "kernelspec": {"display_name": "Python 3", "name": "python3"},
                 "language_info": {"name": "python"}},
    "nbformat": 4,
    "nbformat_minor": 5,
}
Path("notebooks/colab_verify.ipynb").write_text(json.dumps(nb, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print("저장: notebooks/colab_verify.ipynb")
EOF
```
- 확인: `.venv/Scripts/python -c "import json; nb=json.load(open('notebooks/colab_verify.ipynb', encoding='utf-8')); print(len(nb['cells']))"` → `9`

- [ ] **Step 2: 커밋**
```bash
git add notebooks/colab_verify.ipynb
git commit -m "✨ Feat: Colab 실생성 검증 노트북"
```

- [ ] **Step 3: Colab 실행 (사용자)**
- 저장소 폴더를 Drive `MyDrive/Runiverse/runiverse-ai`에 업로드 (`.venv` 제외)
- `colab_verify.ipynb` 업로드 → GPU 런타임 → 위에서부터 실행
- 통과 기준: 모든 `assert` 통과, wav 재생, 마지막 로그에 `model_loaded`·`generated` 이벤트
- 실패 시: `server.log` 마지막 부분을 공유 → `generator.py` 수정 후 Task 5 테스트 재실행·재커밋

---

## 📘 Task 14: README 운영 가이드 + dev 배포 ④

**Files:**
- Modify: `README.md` (전체 교체)

**Interfaces:**
- Consumes: Task 7~13 명령

- [ ] **Step 1: README 작성** — `README.md`

````markdown
# 🎵 runiverse-ai

Runiverse 음악 생성(ACE-Step 1.5 Turbo) — SageMaker Async 엔드포인트 서빙.

- 설계: `docs/superpowers/specs/2026-10-14-sagemaker-music-gen-design.md`
- 구현 계획: `docs/superpowers/plans/2026-10-14-sagemaker-music-gen.md`

## 📁 구조

| 경로 | 내용 |
|---|---|
| `src/runiverse_music/` | 서빙 코드 (`/ping`, `/invocations`) |
| `container/` | Dockerfile, `serve` 진입점 |
| `scripts/` | 모델 패키징·이미지 푸시·배포·스모크 |
| `deploy/envs/dev.yaml` | dev 배포 설정 (재배포만으로 반영) |
| `notebooks/` | 베이스라인, Colab 검증 |

## 🧪 검증 단계

| 단계 | 명령 |
|---|---|
| ① 단위 테스트 | `.venv/Scripts/python -m pytest` |
| ② 컨테이너 계약 | `docker build -f container/Dockerfile -t runiverse-music:local .` → `docker run -p 8080:8080 -e GENERATOR=fake -e STORAGE=local -e LOCAL_OUTPUT_DIR=/tmp/out runiverse-music:local serve` |
| ③ Colab 실생성 | `notebooks/colab_verify.ipynb` |
| ④ dev 스모크 | `python scripts/smoke_test.py --env dev` |

## 🚀 dev 배포

> 사전: AWS CLI 로그인, Docker Desktop, venv 활성화(`source .venv/Scripts/activate`)

1. `deploy/envs/dev.yaml`에 `bucket`·`execution_role_arn`·(선택) SNS 토픽 입력
2. 가중치: `python scripts/download_models.py --env dev` → 출력 URI를 `model_data_url`에 입력
3. 이미지: `scripts/build_and_push.sh dev` → 마지막 줄 이미지 URI 복사
4. 미리보기: `python scripts/deploy.py --env dev --image-uri <URI> --dry-run`
5. 배포: `python scripts/deploy.py --env dev --image-uri <URI>`
6. 확인: `python scripts/smoke_test.py --env dev`

- 설정만 변경: `dev.yaml` 수정 → 5번만 재실행 (같은 이미지 URI)
- 코드 변경: 커밋 → 3·5번

## 📊 관측

- 로그: CloudWatch `/aws/sagemaker/Endpoints/runiverse-music-dev` (요청당 JSON 한 줄, `request_id`로 검색)
- 지표: `AWS/SageMaker` — `Invocations`, `ModelLatency`, `ApproximateBacklogSizePerInstance`
- Grafana: 기존 Grafana에 CloudWatch 데이터 소스 추가, 새로고침 1~5분

## ⚠️ 알려진 리스크

- GPU 호스트 드라이버 ↔ CUDA 12.8 불일치 시 → `dev.yaml`의 `inference_ami_version` 지정 후 재배포
- 0대에서 첫 요청 → 콜드 스타트 10분 이상 가능
````

- [ ] **Step 2: 커밋**
```bash
git add README.md
git commit -m "📝 Docs: 운영 가이드"
```

- [ ] **Step 3: dev 배포·스모크 (사용자, AWS 리소스 준비 후)**
- README `🚀 dev 배포` 1~6 순서대로 실행
- 통과 기준: `deploy.py` → `배포 완료`, `smoke_test.py` → `✅ 스모크 테스트 통과`
- 실패 시 확인 순서:
  1. `deploy.py` 출력의 `FailureReason`
  2. CloudWatch 로그 `model_loaded` 이벤트 유무
  3. CUDA·드라이버 오류 → `inference_ami_version` 지정 후 5번 재실행

---

## ✅ 스펙 대응표

| 스펙 섹션 | Task |
|---|---|
| 2 저장소 | 1 |
| 3 BYOC | 7 |
| 4 데이터 흐름·S3 경로 | 4, 9, 11, 12 |
| 5 컨테이너 내부 | 2~7 |
| 6 dev.yaml·환경변수 | 2, 8, 11 |
| 7 오류 처리 | 5, 6, 7 |
| 8 관측·스케일링 알람 | 6, 11, 14 |
| 9 재현성(manifest) | 5, 9 |
| 10 테스트 ①~④ | 1~6, 7, 13, 14 |
| 11 미결 리스크 | 8(`inference_ami_version`), 14 |

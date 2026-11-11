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

CONFIG_PATH = "acestep-v15-turbo"
LM_MODEL = "acestep-5Hz-lm-1.7B"  # Ace-Step1.5 모델 묶음(model.tar.gz)에 함께 들어 있는 LM


class GenerationError(RuntimeError):
    """생성 실패 (요청 단위)."""


class CudaOutOfMemory(GenerationError):
    """GPU 메모리 부족."""


@dataclass(frozen=True)
class MusicJob:
    """곡 1개 생성 입력. 수치는 caption에 쓰지 않고 ACE-Step 파라미터로 따로 넘긴다."""

    caption: str
    bpm: int
    keyscale: str       # "" → ACE-Step이 정함 (노트북 EXPERIMENT)
    timesignature: str
    duration: float
    seed: int


@dataclass(frozen=True)
class GenerationResult:
    audio_path: Path
    sample_rate: int
    channels: int
    actual_duration_seconds: float
    size_bytes: int
    generation_seconds: float
    peak_torch_allocated_gib: float
    lm_caption: str | None  # 5Hz LM이 개선한 caption. LM을 안 쓰면 None


class Generator(Protocol):
    manifest: dict[str, Any]

    def load(self) -> None: ...

    def generate(self, job: MusicJob, out_dir: Path) -> GenerationResult: ...


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

    def generate(self, job: MusicJob, out_dir: Path) -> GenerationResult:
        path = Path(out_dir) / "fake.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(self.CHANNELS)
            w.setsampwidth(2)
            w.setframerate(self.SAMPLE_RATE)
            w.writeframes(b"\x00" * self.SAMPLE_RATE * self.CHANNELS * 2)
        check_audio_file(path)
        return GenerationResult(path, self.SAMPLE_RATE, self.CHANNELS, 1.0, path.stat().st_size, 0.0, 0.0, None)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


class AceStepGenerator:
    """ACE-Step 1.5 Turbo. 노트북 infer_music.py의 로딩·생성 로직."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._dit = None
        self._lm = None
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
        if s.use_lm:
            self._lm = _load_lm(s, dit.dtype)
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
                "use_lm": s.use_lm,
                "lm_model": LM_MODEL if s.use_lm else None,
            },
        }

    def generate(self, job: MusicJob, out_dir: Path) -> GenerationResult:
        if self._dit is None:
            raise GenerationError("모델이 로드되지 않았습니다.")

        import soundfile as sf
        import torch
        from acestep.inference import GenerationConfig, GenerationParams, generate_music

        s = self._settings
        params = GenerationParams(
            task_type="text2music",
            caption=job.caption,
            lyrics="[Instrumental]",
            instrumental=True,
            bpm=job.bpm,
            keyscale=job.keyscale,
            timesignature=job.timesignature,
            duration=job.duration,
            seed=job.seed,
            inference_steps=s.inference_steps,
            # 문서 7장: LM은 caption 개선에만. 오디오 코드(thinking)·메타 추론은 끈다
            # 직접 준 bpm·keyscale·timesignature·duration은 LM이 덮어쓰지 않는다 (빈 값만 채움)
            thinking=False,
            use_cot_metas=False,
            use_cot_caption=self._lm is not None,
            use_cot_lyrics=False,
            use_cot_language=False,
        )
        config = GenerationConfig(
            batch_size=1,
            use_random_seed=False,
            seeds=[job.seed],
            audio_format=s.audio_format,
        )
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        started = time.perf_counter()
        try:
            with torch.inference_mode():
                result = generate_music(self._dit, self._lm, params, config, save_dir=str(out_dir))
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
        # LM을 안 쓰면 lm_metadata가 None
        lm_caption = (result.extra_outputs.get("lm_metadata") or {}).get("caption")
        # 이전 결과 텐서가 다음 생성의 메모리를 차지하지 않게 해제
        del result
        return GenerationResult(
            path, int(info.samplerate), int(info.channels), float(info.duration),
            path.stat().st_size, elapsed, peak, lm_caption,
        )


def _load_lm(s: Settings, dtype):
    """5Hz LM (PyTorch 백엔드). 노트북 infer_music.py와 같은 초기화."""
    from acestep.llm_inference import LLMHandler

    lm = LLMHandler()
    status, success = lm.initialize(
        checkpoint_dir=str(s.model_dir),
        lm_model_path=LM_MODEL,
        backend="pt",
        device="cuda",
        offload_to_cpu=s.offload_to_cpu,
        dtype=dtype,
    )
    if not success:
        raise GenerationError(f"5Hz LM 초기화 실패: {status}")
    return lm


def make_generator(settings: Settings) -> Generator:
    if settings.generator == "fake":
        return FakeGenerator()
    return AceStepGenerator(settings)

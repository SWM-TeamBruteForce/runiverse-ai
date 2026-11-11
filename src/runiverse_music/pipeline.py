"""카드 1건 처리: 전처리(①~⑥) → ACE-Step 곡별 생성 → 업로드·결과(⑦).

plan_card: 분석 → 공통 수치 → 곡별 caption_input
generate_tracks: 곡별 MusicJob → 음원
run_card: 위 전체 + 업로드 → 결과 JSON (형식: Notion「AI 기능 아키텍처 및 data 입출력」4장)
"""

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Literal

from runiverse_music import mapping_rules as rules
from runiverse_music.analyzer import (
    PROMPT_VERSION, Analyzer, ReviewAnalysis, default_review, parse_photo, parse_review,
)
from runiverse_music.generator import CudaOutOfMemory, GenerationResult, Generator, MusicJob
from runiverse_music.music_params import SharedParams, decide_shared
from runiverse_music.prompting import build_caption_input, build_photo_caption
from runiverse_music.schemas import CardRequest, PhotoInput
from runiverse_music.storage import Storage, music_key

logger = logging.getLogger("runiverse_music")


@dataclass(frozen=True)
class Track:
    """곡 1개의 생성 작업. 공통 수치는 CardPlan.shared에 있고, 곡마다 다른 값만 둔다."""

    type: Literal["BASE", "PHOTO"]
    photo_index: int | None  # 요청 photos 배열 순서(0부터). 결과·파일명에 쓴다. BASE는 None
    duration: float
    caption_input: str
    image_analysis_status: Literal["SUCCEEDED", "FAILED"] | None  # PHOTO만 값이 있다


@dataclass(frozen=True)
class CardPlan:
    record_card_id: int
    mapping_version: str
    shared: SharedParams
    review_analysis: ReviewAnalysis
    tracks: list[Track]

    def to_dict(self) -> dict:
        """문서 2장 출력 형식. asdict가 안쪽 dataclass(shared 등)까지 dict로 바꾼다."""
        return asdict(self)


@dataclass(frozen=True)
class TrackResult:
    """곡 1개 생성 결과. 실패한 곡이 있어도 다른 곡은 계속 만든다."""

    track: Track
    status: Literal["SUCCEEDED", "FAILED"]
    audio: GenerationResult | None
    error: str | None


def plan_card(card: CardRequest, analyzer: Analyzer, read_photo: Callable[[str], bytes]) -> CardPlan:
    """카드 입력 → 공통 설정 + 곡별 생성 작업. 분석이 실패해도 기본값으로 이어 간다."""
    review = _analyze_review(card, analyzer)  # 조성이 emotion_direction에 의존 → 공통 수치보다 먼저
    summary = card.running_summary
    shared = decide_shared(
        avg_pace=summary.avg_pace,
        weather_code=summary.weather_code,
        emotion_direction=review.emotion_direction,
    )
    base = build_caption_input(card.activity_type, review.music_cue_en)
    tracks = [Track("BASE", None, rules.BASE_DURATION_SECONDS, base, None)]
    for index, photo in enumerate(card.photos):
        cue = _analyze_photo(card, index, photo, analyzer, read_photo)
        caption = build_photo_caption(card.activity_type, cue)  # 실패하면 cue=None → 활동 맥락만
        status = "SUCCEEDED" if cue else "FAILED"
        tracks.append(Track("PHOTO", index, rules.PHOTO_DURATION_SECONDS, caption, status))
    return CardPlan(card.record_card_id, rules.MAPPING_VERSION, shared, review, tracks)


def music_jobs(plan: CardPlan) -> list[MusicJob]:
    """곡별 ACE-Step 입력. 공통 수치는 모든 곡 동일, caption·길이·seed만 곡마다 다르다."""
    shared = plan.shared
    return [
        MusicJob(
            caption=track.caption_input,
            bpm=shared.bpm,
            keyscale=shared.keyscale,
            timesignature=shared.timesignature,
            duration=track.duration,
            # 사진 곡은 배열 순서로 결정적 파생. +1: 첫 사진이 기본 음악과 같은 seed가 되지 않게
            seed=rules.DEFAULT_SEED + (0 if track.photo_index is None else track.photo_index + 1),
        )
        for track in plan.tracks
    ]


def generate_tracks(plan: CardPlan, generator: Generator, out_dir: Path) -> list[TrackResult]:
    """곡을 순서대로 생성. 곡마다 폴더를 나눠 ACE-Step 출력 파일이 섞이지 않게 한다."""
    results = []
    for track, job in zip(plan.tracks, music_jobs(plan)):
        track_dir = out_dir / ("base" if track.photo_index is None else f"photo-{track.photo_index}")
        track_dir.mkdir(parents=True, exist_ok=True)
        audio, error, seconds = _attempt(lambda: generator.generate(job, track_dir))
        extra = {"duration": job.duration}
        if audio is not None:  # generation_seconds: 모델 생성만 / seconds: 파일 검사 포함
            extra |= {"generation_seconds": round(audio.generation_seconds, 2),
                      "peak_vram_gib": round(audio.peak_torch_allocated_gib, 2)}
        _log_stage("generation", plan.record_card_id, track.photo_index, seconds, error, **extra)
        results.append(TrackResult(track, "SUCCEEDED" if audio else "FAILED", audio, error))
    return results


def run_card(
    card: CardRequest, analyzer: Analyzer, generator: Generator, storage: Storage, output_prefix: str, work_dir: Path,
) -> dict:
    """카드 1건 전체 → 성공 결과 JSON (Notion「AI 기능 아키텍처 및 data 입출력」4.2.1, HTTP 200).

    전체 성공/실패만 있다. 실패는 CardFailure로 올린다 → app이 400·500·502 + 실패 JSON(4.2.2)으로 응답.
    한 곡이라도 생성에 실패하면 아무것도 올리지 않는다 → 같은 키의 이전 결과를 반쪽 묶음으로 덮어쓰지 않는다.
    """
    _timed("qwen_to_gpu", card.record_card_id, analyzer.to_gpu)
    plan = plan_card(card, analyzer, storage.read_photo)
    # 카드당 GPU 모델 전환 1회: 분석을 모두 끝낸 뒤 생성으로 넘어간다
    _timed("qwen_to_cpu", card.record_card_id, analyzer.to_cpu)
    results = generate_tracks(plan, generator, work_dir)

    failed = [result for result in results if result.audio is None]
    if failed:
        reasons = ", ".join(f"{_label(r.track)}({r.error})" for r in failed)
        oom = any(r.error == CudaOutOfMemory.__name__ for r in failed)
        code = "MUSIC_GENERATION_OOM" if oom else "MUSIC_GENERATION_FAILED"
        raise CardFailure(500, code, f"음악 생성 실패: {reasons}")

    items = []
    for result in results:
        track = result.track
        key = music_key(output_prefix, card.user_id, card.activity_type, card.record_card_id, track.photo_index)
        started = time.perf_counter()
        try:
            storage.upload(result.audio.audio_path, key)
        except Exception as exc:  # 업로드 하나라도 실패하면 카드 실패 (이미 올린 곡은 남는다)
            _log_stage("upload", card.record_card_id, track.photo_index, _since(started), type(exc).__name__)
            raise CardFailure(502, "MUSIC_UPLOAD_FAILED", f"음악 저장 실패: {_label(track)}({type(exc).__name__})") from exc
        item = {"s3_key": key, "duration_seconds": round(result.audio.actual_duration_seconds, 2)}
        if track.type == "PHOTO":
            # 사진 ID가 없어 배열 순서와 원본 사진 키를 함께 돌려준다 → 백엔드가 어느 사진의 음악인지 확인
            item = {"photo_index": track.photo_index, "image_key": card.photos[track.photo_index].image_key, **item}
        _log_stage("upload", card.record_card_id, track.photo_index, _since(started))
        items.append(item)

    base, *photos = items
    # 회고가 없거나 해석에 실패해 기본값을 쓴 경우는 제공할 결과가 없다 → null
    review = plan.review_analysis
    return {
        "user_id": card.user_id,
        "record_card_id": card.record_card_id,
        "status": "SUCCEEDED",
        "review_mood": None if review == default_review() else asdict(review),
        "base_music": base,
        "photo_music": photos,
        "error": None,
        "manifest": {
            "model_version": generator.manifest.get("model_revisions"),  # ACE-Step·Qwen3-VL HF 리비전
            "prompt_mapping_version": plan.mapping_version,
            "review_mood_classifier_version": PROMPT_VERSION,
        },
    }


class CardFailure(Exception):
    """카드 전체 실패. HTTP 코드와 error.code를 함께 든다 (4.2.2 표)."""

    def __init__(self, http_status: int, code: str, message: str) -> None:
        super().__init__(code)
        self.http_status, self.code, self.message = http_status, code, message


def failure_body(user_id, record_card_id, code: str, message: str, detail: list | None = None) -> dict:
    """실패 JSON (4.2.2). SageMaker가 FailureLocation에 저장한다. 일부 완성 곡은 담지 않는다.

    message에는 실패한 곡·예외 종류만 넣고 회고·캡션 원문은 넣지 않는다. 요청을 못 읽으면 ID는 None.
    """
    error = {"code": code, "message": message}
    if detail is not None:
        error["detail"] = detail
    return {"user_id": user_id, "record_card_id": record_card_id, "status": "FAILED", "error": error}


def _label(track: Track) -> str:
    return "기본 음악" if track.photo_index is None else f"사진 {track.photo_index}"


def _analyze_review(card: CardRequest, analyzer: Analyzer) -> ReviewAnalysis:
    if not card.memo:  # 회고 미작성 → 모델을 부르지 않고 기본값
        return default_review()
    review, error, seconds = _attempt(lambda: parse_review(analyzer.analyze_review(card.memo, card.activity_type)))
    _log_stage("review_analysis", card.record_card_id, None, seconds, error)
    if review is None:
        return default_review()
    return review


def _analyze_photo(card: CardRequest, index: int, photo: PhotoInput, analyzer: Analyzer, read_photo) -> str | None:
    def run() -> str | None:
        image = read_photo(photo.image_key)
        return parse_photo(analyzer.analyze_photo(image, photo.caption, card.activity_type))

    cue, error, seconds = _attempt(run)  # seconds: 사진 버킷 읽기 + Qwen 분석
    _log_stage("photo_analysis", card.record_card_id, index, seconds, error)
    return cue


def _attempt(fn):
    """(결과, 실패 사유, 걸린 초). 모델·S3·생성 예외도 그 단계의 실패로 보고 카드 처리를 이어 간다.

    예외 메시지에는 원문이 섞일 수 있어 사유는 예외 타입명만 남긴다.
    """
    started = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:
        return None, type(exc).__name__, _since(started)
    return result, (None if result is not None else "invalid_output"), _since(started)


def _since(started: float) -> float:
    return round(time.perf_counter() - started, 2)


def _timed(stage: str, record_card_id: int, fn) -> None:
    """실패하면 예외를 그대로 올린다 (GPU 이동 실패는 카드 처리를 이어 갈 수 없다)."""
    started = time.perf_counter()
    fn()
    _log_stage(stage, record_card_id, None, _since(started))


def _log_stage(stage: str, record_card_id: int, photo_index: int | None, seconds: float,
               error: str | None = None, **extra) -> None:
    """단계마다 JSON 한 줄 → CloudWatch Logs. 성공 stage_done(INFO), 실패 stage_failed(WARNING).

    회고 원문·사진·사용자 캡션은 남기지 않는다 (개인정보). 숫자·식별자만.
    """
    fields = {"event": "stage_failed" if error else "stage_done", "stage": stage,
              "record_card_id": record_card_id, "photo_index": photo_index, "seconds": seconds, **extra}
    if error:
        fields["error"] = error
    logger.log(logging.WARNING if error else logging.INFO, json.dumps(fields))

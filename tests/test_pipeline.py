import json
import logging

from runiverse_music.analyzer import FakeAnalyzer
from runiverse_music.generator import CudaOutOfMemory, FakeGenerator, GenerationError, MusicJob
import pytest

from runiverse_music.pipeline import CardFailure, generate_tracks, music_jobs, plan_card, run_card
from runiverse_music.schemas import CardRequest
from runiverse_music.storage import LocalStorage, StorageError
from test_card_request import CARD, CARD_ID, PHOTO_ID, USER, card

PHOTO = CARD["photos"][0]


def read_ok(key):
    return b"jpg"


def read_fail(key):
    raise FileNotFoundError(key)


class BrokenAnalyzer(FakeAnalyzer):
    """회고 분석은 예외, 사진 분석은 형식이 틀린 출력."""

    def analyze_review(self, memo, activity):
        raise RuntimeError("model error")

    def analyze_photo(self, image, caption, activity):
        return "not json"


def plan(data=None, analyzer=None, read_photo=read_ok):
    return plan_card(CardRequest.model_validate(data or card()), analyzer or FakeAnalyzer(), read_photo).to_dict()


def test_plan_matches_doc_output_shape():
    assert plan() == {
        "record_card_id": CARD_ID,
        "mapping_version": "v1",
        "shared": {"bpm": 160, "keyscale": "E Major", "timesignature": "4", "instrumental": True},
        "review_analysis": {
            "emotion_direction": "성취·환희",
            "emotion_cue": "지친 끝에 찾아온 조용한 성취감",
            "music_cue_en": "quiet pride after a difficult finish",
        },
        "tracks": [
            {
                "type": "BASE",
                "photo_index": None,
                "duration": 30.0,
                "caption_input": "An instrumental piece reflecting a run while traveling and "
                "quiet pride after a difficult finish.",
                "image_analysis_status": None,
            },
            {
                "type": "PHOTO",
                "photo_index": 0,
                "duration": 10.0,
                "caption_input": "An open, airy coastal scene, an instrumental piece from a run while traveling.",
                "image_analysis_status": "SUCCEEDED",
            },
        ],
    }


def test_no_photos_makes_base_track_only():
    assert [t["type"] for t in plan(card(photos=[]))["tracks"]] == ["BASE"]


def test_missing_review_uses_default_without_model():
    result = plan(card(memo=None), analyzer=BrokenAnalyzer())  # 호출되면 예외
    assert result["review_analysis"]["emotion_direction"] == "평온·담담함"
    assert result["shared"]["keyscale"] == "F Major"  # 평온·담담함 × RAIN


def test_review_model_error_falls_back_to_default():
    assert plan(analyzer=BrokenAnalyzer())["review_analysis"]["music_cue_en"] == "a calm and steady mood"


def test_failed_photo_keeps_card_and_drops_photo_cue():
    missing = "cards/101/photos/202.jpg"
    photos = [PHOTO, {**PHOTO, "record_card_image_id": "5a6b7c8d-3333-4eee-afff-000000000202", "image_key": missing}]

    def read_photo(key):
        return read_fail(key) if key == missing else b"jpg"

    tracks = plan(card(photos=photos), read_photo=read_photo)["tracks"]
    assert [(t["photo_index"], t["image_analysis_status"]) for t in tracks] == [
        (None, None), (0, "SUCCEEDED"), (1, "FAILED"),
    ]
    assert tracks[2]["caption_input"] == "An instrumental piece reflecting a run while traveling."


def test_invalid_photo_output_is_failed():
    tracks = plan(analyzer=BrokenAnalyzer())["tracks"]
    assert tracks[1]["image_analysis_status"] == "FAILED"


def test_failure_logs_do_not_contain_private_text(caplog):
    with caplog.at_level(logging.WARNING, logger="runiverse_music"):
        plan(analyzer=BrokenAnalyzer(), read_photo=read_fail)
    logs = [json.loads(r.message) for r in caplog.records]
    assert [(e["stage"], e["photo_index"]) for e in logs] == [("review_analysis", None), ("photo_analysis", 0)]
    text = caplog.text
    assert CARD["memo"] not in text and PHOTO["caption"] not in text


# ── ⑦ ACE-Step 입력·출력 ─────────────────────────────────────
def card_plan(data=None):
    return plan_card(CardRequest.model_validate(data or card()), FakeAnalyzer(), read_ok)


def test_music_jobs_share_params_and_derive_photo_seed():
    jobs = music_jobs(card_plan())
    assert jobs == [
        MusicJob(
            caption="An instrumental piece reflecting a run while traveling and quiet pride after a difficult finish.",
            bpm=160, keyscale="E Major", timesignature="4", duration=30.0, seed=42,
        ),
        MusicJob(
            caption="An open, airy coastal scene, an instrumental piece from a run while traveling.",
            bpm=160, keyscale="E Major", timesignature="4", duration=10.0, seed=42 + 1,
        ),
    ]


class FailOnShortTrack(FakeGenerator):
    """사진 곡(10초)만 생성 실패."""

    def generate(self, job, out_dir):
        if job.duration == 10.0:
            raise CudaOutOfMemory("CUDA 메모리 부족")
        return super().generate(job, out_dir)


def test_generate_tracks_returns_audio_per_track(tmp_path):
    results = generate_tracks(card_plan(), FakeGenerator(), tmp_path)
    assert [(r.track.type, r.status) for r in results] == [("BASE", "SUCCEEDED"), ("PHOTO", "SUCCEEDED")]
    assert results[0].audio.audio_path.parent != results[1].audio.audio_path.parent  # 곡마다 폴더 분리


def test_failed_track_keeps_others_and_logs_type_only(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="runiverse_music"):
        results = generate_tracks(card_plan(), FailOnShortTrack(), tmp_path)
    assert [(r.status, r.error) for r in results] == [("SUCCEEDED", None), ("FAILED", "CudaOutOfMemory")]
    assert results[1].audio is None
    [log] = [json.loads(r.message) for r in caplog.records]
    assert (log["stage"], log["photo_index"], log["error"]) == ("generation", 0, "CudaOutOfMemory")


# ── 저장·결과 ({prefix}/{user_id}/{activity_type}/{record_card_id}/base|{record_card_image_id}.wav + 곡별 상태) ──
BASE_KEY = f"audio/test/{USER}/TRAVEL/{CARD_ID}/base.wav"
PHOTO_KEY = f"audio/test/{USER}/TRAVEL/{CARD_ID}/{PHOTO_ID}.wav"


def local_storage(tmp_path):
    """사진 버킷·AI 버킷을 로컬 폴더 하나로 흉내 낸다."""
    storage = LocalStorage(tmp_path / "s3")
    photo = tmp_path / "s3" / PHOTO["image_key"]
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"jpg")
    return storage


def run(tmp_path, analyzer=None, generator=None, storage=None):
    card_request = CardRequest.model_validate(card())
    return run_card(card_request, analyzer or FakeAnalyzer(), generator or FakeGenerator(),
                    storage or local_storage(tmp_path), "audio/test", tmp_path / "work")


def test_run_card_success_matches_contract(tmp_path):
    # Notion「AI 기능 아키텍처 및 data 입출력」4.1
    result = run(tmp_path)
    assert result == {
        "user_id": USER,
        "record_card_id": CARD_ID,
        "status": "SUCCEEDED",
        "review_mood": {
            "emotion_direction": "성취·환희",
            "emotion_cue": "지친 끝에 찾아온 조용한 성취감",
            "music_cue_en": "quiet pride after a difficult finish",
        },
        "base_music": {"s3_key": BASE_KEY, "duration_seconds": 1.0},
        "photo_music": [
            {"record_card_image_id": PHOTO_ID, "photo_index": 0, "image_key": PHOTO["image_key"],
             "s3_key": PHOTO_KEY, "duration_seconds": 1.0},
        ],
        "error": None,
        "manifest": {
            "model_version": None,  # FakeGenerator에는 모델 리비전이 없다
            "prompt_mapping_version": "v1",
            "review_mood_classifier_version": "prompt-v1",
        },
    }
    assert (tmp_path / "s3" / BASE_KEY).is_file() and (tmp_path / "s3" / PHOTO_KEY).is_file()


def test_review_mood_is_null_without_memo(tmp_path):
    card_request = CardRequest.model_validate(card(memo=None))
    result = run_card(card_request, FakeAnalyzer(), FakeGenerator(), local_storage(tmp_path), "audio/test", tmp_path)
    assert (result["status"], result["review_mood"]) == ("SUCCEEDED", None)


def failure(tmp_path, **kwargs):
    """실패는 CardFailure로 올라온다 → (HTTP 코드, error.code, message)."""
    with pytest.raises(CardFailure) as info:
        run(tmp_path, **kwargs)
    return info.value.http_status, info.value.code, info.value.message


def test_photo_oom_fails_card_without_upload(tmp_path):
    assert failure(tmp_path, generator=FailOnShortTrack()) == (
        500, "MUSIC_GENERATION_OOM", "음악 생성 실패: 사진 0(CudaOutOfMemory)",
    )
    assert not (tmp_path / "s3" / BASE_KEY).exists()  # 반쪽 묶음으로 이전 결과를 덮어쓰지 않음


def test_non_oom_generation_failure_code(tmp_path):
    class EmptyFile(FakeGenerator):
        def generate(self, job, out_dir):
            raise GenerationError("빈 파일")

    status, code, message = failure(tmp_path, generator=EmptyFile())
    assert (status, code) == (500, "MUSIC_GENERATION_FAILED")
    assert message == "음악 생성 실패: 기본 음악(GenerationError), 사진 0(GenerationError)"


class FailOnLongTrack(FakeGenerator):
    def generate(self, job, out_dir):
        if job.duration == 30.0:
            raise CudaOutOfMemory("CUDA 메모리 부족")
        return super().generate(job, out_dir)


def test_base_failure_fails_card_without_upload(tmp_path):
    assert failure(tmp_path, generator=FailOnLongTrack()) == (
        500, "MUSIC_GENERATION_OOM", "음악 생성 실패: 기본 음악(CudaOutOfMemory)",
    )
    assert not (tmp_path / "s3" / PHOTO_KEY).exists()


def test_upload_failure_fails_card(tmp_path):
    class FailPhotoUpload(LocalStorage):
        def upload(self, local_path, key):
            if key == PHOTO_KEY:
                raise StorageError("S3 업로드 실패")
            super().upload(local_path, key)

    local_storage(tmp_path)  # 사진만 준비
    assert failure(tmp_path, storage=FailPhotoUpload(tmp_path / "s3")) == (
        502, "MUSIC_UPLOAD_FAILED", "음악 저장 실패: 사진 0(StorageError)",
    )


def test_qwen_leaves_gpu_before_generation(tmp_path):
    events = []

    class TrackedAnalyzer(FakeAnalyzer):
        def to_gpu(self):
            events.append("qwen_on")

        def to_cpu(self):
            events.append("qwen_off")

    class TrackedGenerator(FakeGenerator):
        def generate(self, job, out_dir):
            events.append("generate")
            return super().generate(job, out_dir)

    run(tmp_path, analyzer=TrackedAnalyzer(), generator=TrackedGenerator())
    assert events == ["qwen_on", "qwen_off", "generate", "generate"]


def test_stage_logs_record_timing_without_private_text(tmp_path, caplog):
    # CloudWatch Logs Insights로 단계별 시간을 집계할 수 있게 단계마다 JSON 한 줄
    with caplog.at_level(logging.INFO, logger="runiverse_music"):
        run(tmp_path)
    logs = [json.loads(r.message) for r in caplog.records]
    assert [(e["event"], e["stage"], e["photo_index"]) for e in logs] == [
        ("stage_done", "qwen_to_gpu", None),
        ("stage_done", "review_analysis", None),
        ("stage_done", "photo_analysis", 0),
        ("stage_done", "qwen_to_cpu", None),
        ("stage_done", "generation", None),
        ("stage_done", "generation", 0),
        ("stage_done", "upload", None),
        ("stage_done", "upload", 0),
    ]
    assert all(isinstance(e["seconds"], float) for e in logs)
    assert {"duration", "generation_seconds", "peak_vram_gib"} <= logs[4].keys()
    assert CARD["memo"] not in caplog.text and PHOTO["caption"] not in caplog.text

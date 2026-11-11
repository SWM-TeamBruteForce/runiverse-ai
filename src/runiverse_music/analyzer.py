"""Qwen3-VL 회고·사진 분석: 프롬프트 구성, 출력 검증, 실제 분석기, 테스트용 FakeAnalyzer.

분석기는 모델이 낸 원문만 돌려주고, 검증은 parse_* 함수 한곳에서 한다.
torch·transformers·PIL import는 Qwen3VLAnalyzer 메서드 안에서만 한다.
"""

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from runiverse_music import mapping_rules as rules
from runiverse_music.config import Settings

# 모델 출력이 깨졌을 때 json·dict 접근·문자열 처리에서 나는 예외
_PARSE_ERRORS = (ValueError, KeyError, TypeError, AttributeError)

QWEN_DIR = "qwen3-vl"  # model.tar.gz 안의 Qwen3-VL 폴더

# 출력은 짧은 JSON 하나라 이 정도면 충분하다. 넘으면 JSON이 잘려 parse 실패 → 기본값
MAX_NEW_TOKENS = 128
# 이미지 토큰 수는 해상도에 비례한다. 폰 원본 사진을 그대로 넣으면 T4 VRAM이 모자랄 수 있다
MAX_IMAGE_SIDE = 1024

# REVIEW_PROMPT·PHOTO_PROMPT를 바꾸면 올린다 → 결과 manifest.review_mood_classifier_version
PROMPT_VERSION = "prompt-v1"

# 프롬프트는 호출 시점에 읽는다 → Colab에서 analyzer.REVIEW_PROMPT = ... 로 바꿔 실험 가능
REVIEW_PROMPT = """러닝 회고 글을 읽고 음악 생성에 쓸 감정 단서를 뽑아라.

활동: {activity}
회고: {review_text}

규칙:
- 회고 글에 드러난 감정만 근거로 한다. 글에 없는 감정을 지어내지 않는다.
- 감정이 여러 개면 감정의 흐름과 마지막 여운을 함께 담는다.
- emotion_direction: 다음 중 정확히 하나를 그대로 쓴다 — {directions}
- emotion_cue: 회고의 감정과 여운을 담은 한글 한 구절, 30자 이내
- music_cue_en: 음악 분위기를 나타내는 짧은 영어 소문자 명사구. 장르·악기·템포는 쓰지 않는다.

아래 형식의 JSON 하나만 출력한다.
{{"emotion_direction": "...", "emotion_cue": "...", "music_cue_en": "..."}}"""

PHOTO_PROMPT = """러닝 중 찍은 사진을 보고 음악 생성에 쓸 장면 단서를 뽑아라.

활동: {activity}
사용자 설명: {user_caption}

규칙:
- 사진에 보이는 장면과 분위기를 근거로 한다. 사용자 설명은 맥락을 확인하는 데만 쓴다.
- 사진에 보이지 않는 날씨나 시간대를 단정하지 않는다.
- music_cue_en: 이 사진만의 장면·빛·공기감·움직임이 드러나는 8~15단어 영어 소문자 명사구. 다른 사진과 구별되도록 구체적으로 쓴다.
- 장르·악기·템포는 쓰지 않는다.

아래 형식의 JSON 하나만 출력한다.
{{"music_cue_en": "..."}}"""


@dataclass(frozen=True)
class ReviewAnalysis:
    emotion_direction: str  # 조성표 조회용 7종 중 하나
    emotion_cue: str        # 회고의 감정·여운 (한글). 로그에는 이 값만 남긴다
    music_cue_en: str       # 기본 음악 caption_input에 넣는 영어 단서


class Analyzer(Protocol):
    def load(self) -> None: ...

    def to_gpu(self) -> None: ...

    def to_cpu(self) -> None: ...

    def analyze_review(self, review_text: str, activity: str) -> str: ...

    def analyze_photo(self, image: bytes, user_caption: str | None, activity: str) -> str: ...


def review_messages(review_text: str, activity: str) -> list[dict]:
    """회고 분석 입력 (Qwen3-VL chat 형식, 텍스트만)."""
    text = REVIEW_PROMPT.format(
        activity=rules.ACTIVITY_CONTEXT[activity],
        review_text=review_text,
        directions=" / ".join(rules.EMOTION_DIRECTIONS),
    )
    return [{"role": "user", "content": [{"type": "text", "text": text}]}]


def photo_messages(image, user_caption: str | None, activity: str) -> list[dict]:
    """사진 분석 입력. 이미지를 텍스트보다 앞에 둔다 (Qwen3-VL 공식 예시 순서)."""
    text = PHOTO_PROMPT.format(activity=rules.ACTIVITY_CONTEXT[activity], user_caption=user_caption or "없음")
    return [{"role": "user", "content": [{"type": "image", "image": image}, {"type": "text", "text": text}]}]


def default_review() -> ReviewAnalysis:
    """회고가 없거나 해석에 실패했을 때 쓰는 담담한 기본값."""
    return ReviewAnalysis(rules.DEFAULT_EMOTION_DIRECTION, rules.DEFAULT_EMOTION_CUE, rules.DEFAULT_MUSIC_CUE_EN)


def _load_json(raw: str):
    """```json 펜스나 앞뒤 설명이 붙어도 첫 '{' ~ 마지막 '}'만 읽는다. 없으면 ValueError."""
    return json.loads(raw[raw.find("{"): raw.rfind("}") + 1])


def parse_review(raw: str) -> ReviewAnalysis | None:
    """회고 분석 원문 → ReviewAnalysis. 형식이 틀리거나 방향이 7종 밖이면 None."""
    try:
        data = _load_json(raw)
        result = ReviewAnalysis(
            emotion_direction=data["emotion_direction"],
            emotion_cue=data["emotion_cue"].strip(),
            music_cue_en=data["music_cue_en"].strip(),
        )
    except _PARSE_ERRORS:
        return None
    valid = result.emotion_direction in rules.EMOTION_DIRECTIONS and result.emotion_cue and result.music_cue_en
    return result if valid else None


def parse_photo(raw: str) -> str | None:
    """사진 분석 원문 → 사진 음악 단서(영어). 형식이 틀리거나 비어 있으면 None."""
    try:
        cue = _load_json(raw)["music_cue_en"].strip()
    except _PARSE_ERRORS:
        return None
    return cue or None


class Qwen3VLAnalyzer:
    """Qwen3-VL Instruct. 컨테이너 시작 시 1회 로드하고 요청마다 재사용한다. GPU 필수.

    평소에는 CPU에 두고 분석할 때만 GPU로 올린다 → ACE-Step 생성 중 VRAM을 차지하지 않는다.
    """

    def __init__(self, model_dir: Path | str) -> None:
        self.model_dir = model_dir  # 서빙은 폴더, 노트북은 HF 모델 ID
        self._processor = None
        self._model = None

    def load(self) -> None:
        import torch
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA GPU가 필요합니다. CPU로 전환하지 않습니다.")
        self._processor = AutoProcessor.from_pretrained(self.model_dir)
        # T4는 bfloat16 연산을 지원하지 않아 float16으로 올린다
        self._model = Qwen3VLForConditionalGeneration.from_pretrained(self.model_dir, dtype=torch.float16)

    def to_gpu(self) -> None:
        self._model.to("cuda")

    def to_cpu(self) -> None:
        import torch

        self._model.to("cpu")
        torch.cuda.empty_cache()

    def analyze_review(self, review_text: str, activity: str) -> str:
        return self._generate(review_messages(review_text, activity))

    def analyze_photo(self, image: bytes, user_caption: str | None, activity: str) -> str:
        from PIL import Image

        picture = Image.open(io.BytesIO(image)).convert("RGB")
        picture.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))  # 비율 유지 축소. 작은 사진은 그대로
        return self._generate(photo_messages(picture, user_caption, activity))

    def _generate(self, messages: list[dict]) -> str:
        import torch

        inputs = self._processor.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors="pt",
        ).to(self._model.device)
        with torch.inference_mode():
            # do_sample=False: 같은 입력이면 같은 분석 → 재시도해도 조성이 바뀌지 않는다
            output = self._model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False)
        new_tokens = output[:, inputs["input_ids"].shape[1]:]  # 입력 프롬프트 부분은 잘라낸다
        return self._processor.batch_decode(new_tokens, skip_special_tokens=True)[0]


class FakeAnalyzer:
    """GPU 없이 쓰는 고정 결과 (문서 예시 값). ANALYZER=fake — 로컬·테스트 전용."""

    def load(self) -> None:
        pass

    def to_gpu(self) -> None:
        pass

    def to_cpu(self) -> None:
        pass

    def analyze_review(self, review_text: str, activity: str) -> str:
        return json.dumps(
            {
                "emotion_direction": "성취·환희",
                "emotion_cue": "지친 끝에 찾아온 조용한 성취감",
                "music_cue_en": "quiet pride after a difficult finish",
            },
            ensure_ascii=False,
        )

    def analyze_photo(self, image: bytes, user_caption: str | None, activity: str) -> str:
        return json.dumps({"music_cue_en": "an open, airy coastal scene"})


def make_analyzer(settings: Settings) -> Analyzer:
    if settings.analyzer == "fake":
        return FakeAnalyzer()
    return Qwen3VLAnalyzer(settings.model_dir / QWEN_DIR)

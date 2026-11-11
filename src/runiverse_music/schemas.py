"""카드 입력 형식. 정본: docs/AI_음악_전처리_모듈.md 1장."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from runiverse_music import mapping_rules as rules


class PhotoInput(BaseModel):
    """사진 1장. 별도 ID가 없어 photos 배열 순서(0부터)로 결과와 연결한다."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    image_key: str = Field(min_length=1)  # 분석할 원본 사진의 S3 키
    caption: str | None = None            # 사용자 사진 설명


class RunningSummary(BaseModel):
    # 거리·케이던스 등 나머지 지표는 현재 매핑에 쓰지 않아 받기만 하고 버린다
    model_config = ConfigDict(extra="ignore")

    avg_pace: int | None = None      # 초/km. 결측·0 이하는 music_params가 기본 BPM으로 처리
    weather_code: int | None = None  # WMO 4677 ww


class CardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_id: int
    record_card_id: int
    memo: str | None = None  # 회고 글
    activity_type: str
    running_summary: RunningSummary
    photos: list[PhotoInput] = []

    @field_validator("activity_type")
    @classmethod
    def _known_activity(cls, value: str) -> str:
        if value not in rules.ACTIVITY_CONTEXT:
            raise ValueError(f"activity_type은 {', '.join(rules.ACTIVITY_CONTEXT)} 중 하나여야 합니다.")
        return value

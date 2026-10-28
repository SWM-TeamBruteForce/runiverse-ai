# 🎵 Runiverse 음악 생성 — SageMaker dev 배포 설계

> 기록 카드 1장(러닝 기록 + 회고 글 + 날씨 + 사진 0~N장)을 받아 **Qwen3-VL로 분석하고 ACE-Step으로 기본 음악 1개(30초)와 사진별 음악 N개(10초)를 생성**하는 SageMaker 비동기 엔드포인트.

- 작성일: 2026-10-14
- 개정
  - 2026-10-27: 기능명세 반영 (Qwen3-VL 분석, 카드 단위 요청, 사진별 음악, 부분 실패)
  - 2026-10-28: 전처리 모듈 반영 (페이스 BPM, 회고 방향 × 날씨 조성, 30초/10초, 영어 caption, 입력 필드명)
- 기능명세: `docs/AI_러닝_음악_기능명세.md` (`AI-MUS-01` ~ `09`)
- 전처리 모듈: `docs/AI_음악_전처리_모듈.md` — 입력 필드·변환 규칙표·caption 템플릿의 **정본**. 이 문서는 규칙표를 옮겨 적지 않는다
- 베이스라인: `Runiverse_ACE_Step_1_5_Colab_Python311_CUDA.ipynb` (Google Drive)
- 상태: 설계 검토 중

---

## 🎯 1. 목표와 범위

**목표**
- 카드 1건을 Async 요청 1건으로 처리 (명세 6장 "생성 요청은 카드당 1건")
- 회고 글 → `emotion_cue`·`emotion_direction`·`music_emotion_en` (Qwen3-VL 텍스트 입력, `AI-MUS-03`)
- 평균 페이스 → BPM, 회고 방향 × 날씨 그룹 → 조성 → **카드 공통 설정** (`AI-MUS-02`)
- 사진 → 장면·분위기·시간대·음악 단서 (Qwen3-VL 이미지 입력, `AI-MUS-05`)
- 공통 설정 + 곡별 영어 caption으로 기본 음악 30초 1개, 사진별 음악 10초 N개 생성 (`AI-MUS-01`, `04`, `06`)
- 결과를 `card_id`·`photo_id` 기준으로 저장하고 곡별 상태 반환 (`AI-MUS-07`, `08`)
- 두 모델 모두 컨테이너 시작 시 1회 로드해 요청마다 재사용

**성공 기준**
- `/ping` 200 → 엔드포인트 `InService`
- 사진 0장 카드 → 기본 음악 wav 1개(30초) + 분석 JSON + 응답 JSON
- 사진 N장 카드 → 기본 음악 1개 + 사진별 wav N개(10초), `photo_id`별로 결과 매핑
- 카드 안 모든 곡의 BPM·조성·박자가 같음
- 사진 1장 생성 실패 → 나머지 결과는 유지, 실패 항목만 `FAILED`
- manifest에 코드·모델(ACE-Step·Qwen3-VL)·`mapping_version`·환경 버전 기록

**범위 밖**
- 백엔드의 날씨 조회, SNS 구독, 카드 상태 저장 / 앱 UI·재생 화면
- 음원 접근 권한 판단 (카드 공개 정책은 백엔드가 presigned URL 발급 시 적용, `AI-MUS-09`)
- prod 환경, CI/CD(GitHub Actions)
- ACE-Step LM(`use_lm`)·CoT 메타 생성, 보컬 생성
- SageMaker 로그의 Loki 통합

---

## 📁 2. 저장소

| 항목 | 값 |
|---|---|
| 원격 | `SWM-TeamBruteForce/runiverse-ai` (기본 브랜치 `dev`) |
| 로컬 | `C:\dev\runiverse\ai` |
| 범위 | 당분간 음악 생성 전용 → 코드는 루트에 직접 배치 |

```
C:\dev\runiverse\ai\
├─ README.md
├─ CLAUDE.md
├─ pyproject.toml                    # 서빙 코드 의존성 (ACE-Step은 컨테이너에서 고정)
├─ container/
│  ├─ Dockerfile
│  └─ serve                          # SageMaker 진입점
├─ src/runiverse_music/
│  ├─ config.py
│  ├─ schemas.py                     # 카드 요청·응답
│  ├─ music_params.py                # 🆕 페이스→BPM, 날씨 코드→그룹, 방향×그룹→조성, 곡 길이
│  ├─ analyzer.py                    # 🆕 Qwen3-VL 회고·사진 분석
│  ├─ prompting.py                   # 🆕 영어 caption 조립·512자 압축
│  ├─ generator.py                   # ACE-Step 생성 (keyscale 전달 추가)
│  ├─ pipeline.py                    # 🆕 카드 단위 오케스트레이션, 곡별 실패 격리
│  ├─ storage.py
│  └─ app.py
├─ scripts/
│  ├─ deploy_config.py               # envs/<env>.yaml 로더·검증
│  ├─ download_models.py             # HF → S3 model.tar.gz (ACE-Step + Qwen3-VL 번들)
│  ├─ build_and_push.sh              # 이미지 → ECR
│  ├─ deploy.py                      # 설정값 참고용 (배포는 Console에서 직접)
│  └─ smoke_test.py                  # 사진 0장·N장 카드 요청 본문 생성
├─ deploy/envs/dev.yaml
├─ notebooks/
│  ├─ baseline_colab.ipynb           # 기존 베이스라인 보관
│  └─ colab_verify.ipynb             # Colab GPU에서 서버 코드 실검증
├─ tests/
│  └─ fixtures/                      # 🆕 명세 예시 회고·사진 평가셋
└─ docs/
   ├─ AI_러닝_음악_기능명세.md
   ├─ AI_음악_전처리_모듈.md
   └─ superpowers/{specs,plans}/
```

- 🆕 = 아직 없는 파일. 기존 `schemas.py`·`generator.py`·`storage.py`·`app.py`는 단건 생성 기준이라 카드 단위로 바꾼다

---

## 🧱 3. 배포 방식

> ✅ **선택: BYOC 커스텀 컨테이너**

- Colab 검증 환경 그대로 재현: Python 3.11 · PyTorch 2.10 / CUDA 12.8 · ACE-Step `ca1e85f` · `uv.lock --frozen`
- Qwen3-VL은 같은 venv에 `transformers` 기반으로 추가하고, ACE-Step 고정 의존성과 충돌하는지 PoC에서 확인
- AWS 리소스(Model·Endpoint Config·Endpoint·오토스케일링)는 **Console에서 직접** 만든다. 값은 `dev.yaml` 기준

> ❌ **기각: AWS PyTorch DLC + `inference.py`**

- DLC의 Python·PyTorch 버전이 `uv.lock`과 불일치 가능성 높음
- 불일치 시 런타임 설치 → 콜드 스타트 증가, 재현성 저하

> ❌ **기각: Qwen3-VL을 별도 엔드포인트로 분리**

- 카드 1건에 엔드포인트 2개를 호출하게 되어 백엔드 상태 관리가 복잡해짐
- dev 트래픽에서는 GPU 2대 유지 비용이 이점보다 큼
- 처리량이 문제 되면 prod에서 다시 검토

---

## 🔀 4. 아키텍처와 데이터 흐름

```
[앱] 회고 글 + 활동유형 + 사진 0~N장(+캡션) → 카드 1회 제출
   ▼
[Spring Boot 백엔드]
   │ ① 사진 → s3://<bucket>/<image_s3_key>
   │ ② DB 러닝 기록·날씨 조회 → 카드 요청 JSON → s3://<bucket>/music/requests/{request_id}.json
   │ ③ InvokeEndpointAsync(InputLocation) → 카드 상태 "생성 중"
   ▼
[SageMaker Async Endpoint: runiverse-music-dev]   ※ 대기열 대기도 "생성 중"에 포함
   ├─ ① 입력 확인 (schemas)
   ├─ ② 회고 해석 (Qwen3-VL) → emotion_cue / emotion_direction / music_emotion_en
   ├─ ③ 공통 설정 (music_params) → bpm / keyscale / timesignature
   ├─ ④ 사진 분석 (Qwen3-VL) → 사진마다 장면·분위기·시간대·음악 단서
   ├─ ⑤ caption 조립 (prompting) → 곡별 영어 caption
   ├─ ⑥ 생성 (ACE-Step) → 기본 30초 1개 + 사진별 10초 N개 (곡별 실패 격리)
   ├─ 분석 JSON·wav → s3://<bucket>/music/cards/{card_id}/{request_id}/
   ├─ 응답 JSON → s3://<bucket>/music/responses/
   └─ SNS 성공/실패 토픽 → 백엔드가 카드·사진에 음원 연결 → 앱에는 presigned URL
```

**처리 순서**
- ②가 ③보다 먼저다. 조성이 `emotion_direction`에 따라 정해지기 때문
- ②·④(Qwen3-VL)를 모두 끝낸 뒤 ⑥(ACE-Step)으로 넘어간다 → 카드당 GPU 모델 전환 1회
- 카드 안의 연산은 순차 실행 (GPU 1장, 명세 6장)
- ⑤까지 끝나면 분석 결과 JSON을 먼저 저장 → 생성이 실패해도 분석 결과는 남음

**S3 경로 규칙**

| 용도 | 경로 |
|---|---|
| 모델 번들 | `s3://<bucket>/music/models/bundle/{bundle_version}/model.tar.gz` |
| 사진 (백엔드 업로드) | `s3://<bucket>/<image_s3_key>` — 예: `cards/{card_id}/photos/{photo_id}.jpg` |
| 요청 | `s3://<bucket>/music/requests/{request_id}.json` |
| 분석 결과 | `s3://<bucket>/music/cards/{card_id}/{request_id}/analysis.json` |
| 기본 음악 | `s3://<bucket>/music/cards/{card_id}/{request_id}/base.wav` |
| 사진별 음악 | `s3://<bucket>/music/cards/{card_id}/{request_id}/photos/{photo_id}.wav` |
| 응답 | `s3://<bucket>/music/responses/` (SageMaker 관리) |
| 실패 | `s3://<bucket>/music/failures/` (SageMaker 관리) |

- `request_id`를 경로에 넣어 재시도·재생성 결과가 이전 결과를 덮어쓰지 않게 함
- 음원은 `photo_id`로만 식별 → 사진 순서가 바뀌어도 섞이지 않음 (`AI-MUS-07`)

**핵심 결정**
- 호출 방식: **Async**. 사진 수만큼 생성 시간이 늘어나므로 60초 응답 제한이 없어야 하고, 인스턴스를 0대까지 축소할 수 있음
- 요청 단위: **카드 1건 = Async 1건**. 사진별로 쪼개지 않음
- 공통 설정: **카드당 1회 결정**해 모든 곡에 같은 BPM·조성·박자 적용. 곡마다 다른 값은 `duration`·`caption`뿐
- 가중치: **S3 `model.tar.gz` 번들 1개**에 두 모델 포함. SageMaker Model은 ModelDataUrl 1개만 받음
- 호출 주체: **백엔드**. 앱에 AWS 자격 증명을 두지 않음

---

## 🐳 5. 컨테이너 내부 구조

| 파일 | 역할 | 의존 |
|---|---|---|
| `container/Dockerfile` | CUDA 런타임 베이스 → uv + Python 3.11 → ACE-Step `ca1e85f` clone → `uv sync --frozen --no-dev` → Qwen3-VL 의존성 → 서빙 코드 설치 | — |
| `container/serve` | uvicorn `0.0.0.0:8080`, 워커 1개 (GPU 1장 = 모델 세트 1벌) | `app` |
| `config.py` | 환경변수 로드·검증, 잘못된 값이면 시작 시 실패 | — |
| `schemas.py` | `CardMusicRequest` / `CardMusicResponse` (Pydantic, 범위 검증) | — |
| `music_params.py` | 페이스 → BPM, `weather_code` → 날씨 그룹, `emotion_direction` × 그룹 → `keyscale`, 곡 길이. 규칙표는 `MAPPING_VERSION`과 함께 코드 상수로 둔다. **모델 호출 없음** | — |
| `analyzer.py` | `ReviewAnalyzer.analyze(text)` → `emotion_cue`·`emotion_direction`·`music_emotion_en` / `ImageAnalyzer.analyze(image)` → 장면·분위기·시간대·음악 단서. 출력 JSON 파싱·검증 실패 시 기본값. `FakeAnalyzer`: 고정 결과 | `transformers` |
| `prompting.py` | 공통 스타일 + 활동 맥락 + `music_emotion_en` (+ 사진 단서·`user_caption` 요약) → 영어 caption. 512자 압축. 템플릿 버전 기록 | — |
| `generator.py` | `MusicGenerator.load(model_dir)` 1회 / `generate(track, shared, seed, out_dir)` 곡마다. `FakeGenerator`: 짧은 무음 wav | `acestep` |
| `pipeline.py` | 카드 1건 처리: ①~⑥ + 업로드. 곡 단위 `try`로 실패 격리, 곡별 상태 기록 | 위 전부 |
| `storage.py` | 카드 경로 키 생성, 사진 다운로드, 업로드. `S3Storage` / `LocalStorage` | `boto3` |
| `app.py` | lifespan에서 두 모델 `load()` → `/ping`은 둘 다 로드된 뒤 200 → `/invocations`는 검증 → `pipeline` → 응답. GPU 작업은 lock으로 직렬화 | 전부 |

- **torch·acestep import는 `generator.py`만, torch·transformers import는 `analyzer.py`만** → 나머지는 GPU 없이 단위 테스트 가능

**가중치 배치** (`model.tar.gz` → `/opt/ml/model`)

```
/opt/ml/model/
├─ acestep/      # 기존 노트북 checkpoints/ 내용 (+ 고정 커밋 모델 코드)
└─ qwen3-vl/     # HF 스냅샷
```

- `ACESTEP_CHECKPOINTS_DIR=/opt/ml/model/acestep`, `project_root`는 ACE-Step clone 경로
- `QWEN_MODEL_DIR=/opt/ml/model/qwen3-vl`

**🎚️ 공통 설정** (`music_params.py`, 규칙표 정본은 전처리 모듈 4장)

| 파라미터 | 결정 방법 | 결측·실패 시 |
|---|---|---|
| `bpm` | `averagePaceSecondsPerKm` 4구간 → 180 / 170 / 160 / 150 | **160** |
| `keyscale` | `emotion_direction`(7종) × 날씨 그룹(5종) 표 조회 | 방향: `평온·담담함` / 날씨: **미정** (11장) |
| `timesignature` | 고정 `"4"` | — |
| `instrumental` | 고정 `true` | — |
| `duration` (곡별) | 기본 `30.0` / 사진 `10.0` | — |

- 회고 감정은 조성에만 영향, **BPM은 바꾸지 않는다** (명세 4장)
- 길이·규칙표는 기획 결정이라 환경변수가 아니라 **코드 상수 + `MAPPING_VERSION`**으로 관리 → 바꾸면 버전을 올리고 이미지 재빌드

**💬 분석 규칙** (`analyzer.py`)
- `emotion_cue`: 한글 한 구절, 30자 이내. 원문 표현 보관용 (caption에는 쓰지 않음)
- `emotion_direction`: 7종 중 하나. 목록 밖 값이면 실패로 처리
- `music_emotion_en`: caption용 영어 짧은 구
- 글에 없는 감정을 러닝 수치나 사진으로 만들어내지 않음. 여러 감정이면 글의 흐름과 마지막 여운을 함께 담음
- 판단 불가·해석 실패·형식 위반 → `담담하고 안정적인 분위기` + `평온·담담함` (+ 대응 영어 기본 구), `review_analysis_source=DEFAULT`
- 사진: 시간대를 판단할 수 없으면 `UNKNOWN`
- 사진 분석 실패 → 사진 단서 없이 공통 스타일·활동 맥락·회고 여운·`user_caption`으로 생성 계속, `image_analysis_status=FAILED`
- 분석 결과는 음악 생성용 내부 정보. 응답에는 담지만 카드에 사용자 감정으로 표시하지 않음 (백엔드 책임)

**🧩 caption 조립** (`prompting.py`, 템플릿 정본은 전처리 모듈 6장)

| 대상 | 조합 |
|---|---|
| 기본 음악 | 공통 스타일 + 활동 맥락 + `music_emotion_en` + `Instrumental, no vocals.` |
| 사진별 음악 | 위 + 사진 장면·분위기·음악 단서 + `user_caption` 핵심 요약 |

- **영어**. 공통 스타일 문장은 카드 안 모든 곡에서 동일
- 날씨는 확인된 값만 질감에 반영. 사진에 보이지 않는 날씨를 장면으로 단정하지 않음
- BPM·조성·박자·길이는 caption에 쓰지 않고 `GenerationParams`로 전달
- 512자 초과 → 사진 장면 설명부터 압축. 공통 스타일·회고 여운·연주곡 조건 보존
- 사진 분위기와 회고 감정이 달라도 회고 여운을 빼지 않음
- 사진별 음악은 기본 음악을 편집하지 않고 새로 생성 (명세 4장)
- seed: 기본 음악은 요청 `seed`, 사진별 음악은 `seed`와 `photo_id`로 결정적으로 파생 → 재현 가능

**📥 요청 (`CardMusicRequest`)**

전처리 모듈 1장의 카드 입력 + 서빙용 필드.

| 필드 | 타입 | 규칙 | 출처 |
|---|---|---|---|
| `card_id` | str | 필수 | 전처리 |
| `runningRecordId` | str | 필수 | 전처리 |
| `activity` | enum | `DAILY` / `TRAVEL` / `RACE`. 사진이 1장 이상이면 필수 | 전처리 |
| `averagePaceSecondsPerKm` | int | 선택. 결측·범위 밖이면 BPM 160 | 전처리 |
| `review_text` | str | 0~`MAX_REVIEW_CHARS`자. 비면 기본 분위기 | 전처리 |
| `weather_code` | int | 선택. WMO 4677 `ww` 0~99 | 전처리 |
| `photos[]` | list | `{photo_id, image_s3_key, user_caption?}` 0~`MAX_PHOTOS`장, `photo_id` 중복 금지 | 전처리 |
| `request_id` | str | 필수, 백엔드 발급, S3 키에 사용 | 서빙 |
| `seed` | int | 필수 | 서빙 |
| `targets` | list | 선택. `BASE`·`photo_id` 중 생성할 곡. 없으면 전부 (실패 곡만 재시도) | 서빙 |
| `review_analysis` | object | 선택. 재시도 때 이전 회고 해석을 넘기면 ② 생략 → 조성·분위기 일관성 유지 | 서빙 |

**📤 응답 (`CardMusicResponse`)**
- `request_id`, `card_id`, `analysis_s3_key`, `mapping_version`
- `review_analysis`: `emotion_cue`, `emotion_direction`, `music_emotion_en`, `source` (`MODEL` / `DEFAULT` / `REQUEST`)
- `shared`: `bpm`, `keyscale`, `timesignature`, `instrumental`, 결측 대체가 적용된 필드 목록
- `tracks[]`: 곡마다
  - `type` (`BASE` / `PHOTO`), `photo_id`
  - `status` (`SUCCEEDED` / `FAILED`), `error_code`
  - `duration`, `caption`, `seed`
  - `audio_s3_key`, `actual_duration_seconds`, `sample_rate`, `bytes`, `generation_seconds`
  - `image_analysis`, `image_analysis_status` (사진만)
- `card_status`: `SUCCEEDED` (전부 성공) / `PARTIAL` (기본 음악 성공, 사진 일부 실패) / `FAILED` (기본 음악 실패)
- `timings` (단계별), `peak_torch_allocated_gib`, `manifest` (9장)

> 📝 `shared`·`tracks`는 전처리 모듈 출력과 같은 모양이다. 최종 필드명과 분석 결과 분리 저장 여부는 백엔드·AI 데이터 계약에서 확정한다 (명세 4장).

---

## ⚙️ 6. 환경 설정 (`deploy/envs/dev.yaml`)

> Console에서 SageMaker Model·Endpoint를 만들 때 이 파일의 값을 입력한다. **값 변경은 재배포만으로 반영, 이미지 재빌드 불필요**

**인프라 값**

| 키 | 기본값 |
|---|---|
| `region` | `ap-northeast-2` (서울) |
| `endpoint_name` | `runiverse-music-dev` |
| `instance_type` | `ml.g4dn.xlarge` (T4 16GB). ⚠️ 두 모델 상주 시 VRAM·RAM 부족 가능 → PoC 측정 후 `ml.g4dn.2xlarge`(RAM 32GB) 또는 `ml.g6.xlarge`(L4 24GB)로 확정 |
| `autoscaling` | min 0 / max 1 |
| `max_concurrent_invocations_per_instance` | 1 |
| `invocation_timeout_seconds` | 3600 (Async 최대). 사진 수 상한과 곡당 생성 시간으로 재산정 |
| `bucket` | 미정 |
| `execution_role_arn` | 미정 (사진 prefix 읽기 권한 포함) |
| `sns_success_topic` / `sns_error_topic` | 미정 |
| `model_data_download_timeout` / `container_startup_health_check_timeout` | 각 900초 (번들 용량 증가 반영) |

**컨테이너 환경변수** (SageMaker Model `Environment`로 전달)

| 키 | 기본값 |
|---|---|
| `OUTPUT_BUCKET` / `OUTPUT_PREFIX` | `<bucket>` / `music/cards` |
| `MAX_PHOTOS` | 10 (명세 확정 전 가정) |
| `MAX_REVIEW_CHARS` | 1000 |
| `MAX_USER_CAPTION_CHARS` | 200 (가정) |
| `INFERENCE_STEPS` | 8 |
| `QWEN_MODEL_ID` | `Qwen3-VL-4B-Instruct` (PoC에서 2B와 비교 후 확정) |
| `QWEN_DTYPE` | `float16` (T4는 bf16 미지원) |
| `OFFLOAD_TO_CPU` | `true` |
| `AUDIO_FORMAT` | `wav` |
| `LOG_LEVEL` | `INFO` |
| `ANALYZER` | `qwen` (테스트용 `fake`) |
| `GENERATOR` | `acestep` (테스트용 `fake`) |
| `STORAGE` | `s3` (테스트용 `local`) |
| `LOCAL_OUTPUT_DIR` | `STORAGE=local`일 때만 사용 |

- 기존 `MAX_DURATION_SECONDS`는 없앤다. 곡 길이는 `music_params.py` 상수(30·10초)
- ⚠️ dev에서는 `ANALYZER=qwen`, `GENERATOR=acestep`, `STORAGE=s3` 고정 — `fake`·`local`은 로컬·Colab 검증 전용

**재빌드가 필요한 값** → `dev.yaml`이 아닌 코드·Dockerfile·`pyproject.toml`에서 관리
- ACE-Step 커밋, `transformers` 버전, CUDA 베이스 이미지, Python 의존성
- 규칙표·곡 길이·caption 템플릿 (`MAPPING_VERSION`)

---

## 🚨 7. 오류 처리

> 원칙: **요청 자체가 잘못됐거나 모델을 쓸 수 없을 때만 HTTP 오류**. 카드 안의 곡 실패는 200 응답의 `tracks[].status`로 전달한다 (명세 5장 부분 실패).

| 상황 | 처리 | 응답 |
|---|---|---|
| 모델 로드 실패 (가중치 없음, CUDA 없음) | `/ping` 실패 → 엔드포인트 `Failed`. **CPU 대체 금지** | — |
| 요청 범위 위반, `photo_id` 중복, 사진 있는데 `activity` 없음 | 생성하지 않음 | 400 → SNS 실패 |
| 페이스 결측·범위 밖 | BPM 160으로 계속, `shared`에 대체 기록 | 200 |
| 날씨 코드 결측·범위 밖 | 기본 날씨 그룹으로 조성 결정 (그룹은 11장 미정) | 200 |
| 회고 해석 실패·감정 불명확 | 기본 구절·`평온·담담함`으로 계속 (`source=DEFAULT`) | 200 |
| 사진 다운로드 실패·이미지 손상 | 해당 사진 곡 `FAILED`, 나머지 계속 | 200 |
| 사진 분석 실패 | 사진 단서 없이 해당 사진 곡 생성 계속 | 200 |
| 사진별 생성 실패 (OOM 포함) | `empty_cache()` 후 해당 곡 `FAILED`, 나머지 계속 | 200, `PARTIAL` |
| 기본 음악 생성 실패 | 사진별 생성은 계속, 정상 결과는 저장 | 200, `FAILED` |
| 생성 성공이나 파일 없음·0바이트 | 해당 곡 `FAILED` | 200 |
| S3 업로드 실패 | boto3 기본 재시도 후 해당 곡 `FAILED` | 200 |
| 분석 JSON 저장 실패 | 카드 전체 실패 (결과 추적 불가) | 500 |

**재시도**
- 컨테이너는 자동 재시도하지 않음
- 백엔드가 실패 곡만 `targets`에 넣고, 이전 `review_analysis`와 같은 `seed`로 새 `request_id`를 발급해 재요청 → 같은 조성·caption 유지
- 재시도 횟수와 실패 안내 문구는 기획 정책으로 확정 (11장)

---

## 📊 8. 관측

**CloudWatch (자동)**
- 로그: stdout → `/aws/sagemaker/Endpoints/runiverse-music-dev`
- 지표: `Invocations`, `ModelLatency`, 4xx/5xx, GPU·메모리 사용률
- 로그 형식: 단계마다 JSON 한 줄
  - 공통 키: `request_id`, `card_id`, `photo_id`(해당 시), `stage` (`review_analysis` / `music_params` / `image_analysis` / `caption` / `generate` / `upload`)
  - 값: 소요 시간, 피크 VRAM, 결과, `error_code`
  - 카드 종료 시 요약 한 줄: 사진 수, `bpm`, `keyscale`, `card_status`, 총 시간
- **회고 원문·사진·`user_caption`은 로그에 남기지 않음** (개인정보). `emotion_direction`·`emotion_cue`만 기록
- `LOG_LEVEL=INFO` → ACE-Step·transformers DEBUG 로그 억제 (수집 비용 절감)

**스케일링 알람** (Console에서 오토스케일링 설정 시 생성)
- `ApproximateBacklogSizePerInstance` 기반 타깃 추적
- `HasBacklogWithoutCapacity` 알람 → 0대에서 1대로 확장

**Grafana 연결** — 저장소 범위 밖, Grafana 쪽 설정
- 기존 호스팅 Grafana에 CloudWatch 데이터 소스 추가
- 자격 증명: EKS면 IRSA/Pod Identity, EC2면 인스턴스 Role
- IAM 읽기 권한: `cloudwatch:GetMetricData`, `ListMetrics`, `logs:StartQuery`, `GetQueryResults`, `DescribeLogGroups`
- 패널 후보: 카드당 처리 시간, 사진 수 대비 처리 시간, 단계별 실패율, 기본값 대체 비율(회고·페이스·날씨)
- ⚠️ Loki(LogQL)와 쿼리 통합 불가 → 같은 대시보드 패널로 병치, `request_id`·`card_id`로 대조
- ⚠️ 자동 새로고침 1~5분 권장 (조회량 과금)

**dev에서 제외**
- 커스텀 지표, 전용 대시보드, 장애 알람

---

## 🔁 9. 재현성

manifest에 기록:
- `source_commit` (ACE-Step)
- `model_revisions` (ACE-Step·Qwen3-VL HF 커밋 sha)
- `bundle_version`
- `mapping_version` (규칙표·곡 길이), caption 템플릿 버전, 분석 프롬프트 버전
- `uv_lock_sha256`
- `image_tag` (runiverse-ai git sha)
- `python`, `torch`, `torch_cuda`, `transformers`, `gpu`, `dtype`
- `model_loading_seconds` (모델별), 요청 설정

---

## 🧪 10. 테스트

> 로컬 PC에 GPU 없음 → ①② 로컬, ③ Colab GPU, ④ dev 엔드포인트 순으로 단계 검증

| 단계 | 위치 | 검증 대상 | GPU |
|---|---|---|---|
| ① 단위 테스트 | 로컬 `pytest` | 로직·검증·변환·조합·응답 코드 | ❌ |
| ② 컨테이너 계약 | 로컬 Docker | 이미지·`serve`·8080·SageMaker 계약 | ❌ |
| ③ 실생성·품질 | Colab T4 | 두 모델 실제 경로, 분석·음악 품질, VRAM·시간 | ✅ 무료 |
| ④ 스모크 | dev 엔드포인트 (Console) | 드라이버·콜드 스타트·Async·SNS | ✅ 과금 |

### ① 단위 테스트
- `schemas`: 범위 경계값, `photo_id` 중복, 사진 있는데 `activity` 없음, `user_caption` 길이
- `music_params`
  - 페이스 경계 `269/270`, `359/360`, `449/450`, 결측·0·음수 → 160
  - 날씨 코드 그룹 경계 (각 구간 시작·끝), 결측·범위 밖
  - 방향 7 × 그룹 5 = 35칸 전수 → 전처리 모듈 표와 일치
  - 길이: 기본 30.0 / 사진 10.0
- `analyzer` (파싱부): 잘못된 JSON, 30자 초과, 목록 밖 `emotion_direction`, 빈 회고 → 기본값, `UNKNOWN` 시간대
- `prompting`: 영어 출력, 공통 스타일이 모든 곡에서 동일, 기본 caption에 사진 단서 없음, 512자 압축 시 보존 대상 유지, BPM·조성 문자열 미포함
- `pipeline`: Fake 구성으로 사진 0장 / N장 / 사진 1장 실패 / 기본 음악 실패 → `tracks`·`card_status`, 모든 곡 `shared` 동일, `targets` 부분 재생성, `review_analysis` 재사용
- `config`: 환경변수 파싱·오류, 알 수 없는 `ANALYZER`·`GENERATOR`·`STORAGE` 값 거부
- `storage`: 카드 경로 키 규칙, `LocalStorage` 저장
- `app`: `/ping` 준비 전후, `/invocations` 200/400/500

### ② 컨테이너 계약 (Docker Desktop)
- `docker build` → `docker run -p 18080:8080 -e ANALYZER=fake -e GENERATOR=fake -e STORAGE=local -e LOCAL_OUTPUT_DIR=/tmp/out <image> serve`
- `curl /ping` 200, `/invocations` 카드 요청 → 응답 JSON 확인
- 잡는 문제: Dockerfile·진입점·포트·응답 형식 오류
- ⚠️ CUDA·PyTorch 포함 이미지 약 16GB → 첫 빌드 약 25분

### ③ Colab 실생성·품질 (`notebooks/colab_verify.ipynb`)
1. 베이스라인 셀로 ACE-Step 환경·모델 준비, Qwen3-VL 스냅샷 다운로드
2. runiverse-ai 코드를 같은 venv에 설치 (Drive mount 또는 `pip install git+https://...`)
3. 모델들을 `/opt/ml/model/{acestep,qwen3-vl}` 구조로 연결
4. 백그라운드 `uvicorn runiverse_music.app` → `localhost:8765`
5. `requests`로 `/ping`·`/invocations` 호출 → 응답 JSON 확인, wav 재생
- **분석 품질**: 명세 4장 회고 예시 4종 + 샘플 사진 → `emotion_direction` 분류가 맞는지, 없는 감정을 지어내는지
- **음악 품질**
  - `keyscale`이 실제 음원에 반영되는지 (장조·단조 차이 청취)
  - **10초 사진 음악**이 caption 분위기를 충분히 살리는지
  - 같은 카드의 곡들이 한 러닝으로 들리는지 (공통 스타일·BPM·조성)
- **자원 측정**: 사진 0·3·10장 카드의 총 시간, 피크 VRAM, RAM → 인스턴스 타입·`MAX_PHOTOS`·타임아웃 근거
- `ANALYZER=qwen`, `GENERATOR=acestep`, `STORAGE=local` (Colab에 AWS 키 미보관)
- 미검증: Docker 이미지, SageMaker 드라이버, Async 큐, SNS

### ④ dev 스모크 (Console)
- `smoke_test.py`로 사진 0장·2장 카드 요청 JSON을 만들고, Console에서 S3 업로드 → 엔드포인트 비동기 호출
- 확인: wav 존재, 48kHz, 기본 약 30초·사진 약 10초, `photo_id`별 키 매핑, 모든 곡 `shared` 동일
- ①~③ 통과 후 진행 (엔드포인트 수정 1회에 10분 이상 소요)

> 💡 ④에서 반복 실패 시: SageMaker Notebook Instance(GPU) local mode로 컨테이너 직접 실행 — 시간당 과금이라 필요할 때만

---

## ❓ 11. 가정과 미결 사항

**확정**
- 리전 = 서울 `ap-northeast-2` (S3·ECR·SageMaker·SNS·CloudWatch 모두 동일 리전)
- 회고 해석·사진 분석은 같은 Qwen3-VL 모델로 처리 (명세 6장)
- 생성 요청은 카드당 1건 (명세 6장)
- BPM = 평균 페이스 4구간, 결측 160 (케이던스·거리 미사용)
- 조성 = 회고 방향 7종 × 날씨 그룹 5종 표
- 박자 4/4, 연주곡, 길이 기본 30초 / 사진 10초
- caption 영어, 512자 상한
- 활동유형은 카드 단위

**가정** (명세 확정 시 교체)
- 호출 주체 = Spring Boot 백엔드, 사진은 백엔드가 S3에 먼저 업로드
- 사진 최대 10장, `user_caption` 최대 200자
- Qwen3-VL 4B Instruct, fp16
- 공통 스타일 문장 = 전처리 모듈 예시(`electronic pop`, steady running beat, synth palette)

**기획 확정 필요** (명세 7장)
- 공통 음악 스타일(장르·악기 구성) 확정
- 날씨 코드를 알 수 없을 때 쓸 기본 날씨 그룹
- 사진 업로드 개수, 제출 후 사진 추가·삭제 시 재생성 방식 (`targets`로 대응 가능)
- 회고 글 수정 시 재해석·재생성 여부
- 분위기 구절이 음악에 반영됐는지 확인할 방법
- 재시도 횟수, 부분 실패 표시(`PARTIAL`)와 실패 안내 문구
- 생성 중 카드 저장 시점, 기록 탭·피드 노출 시점

**백엔드 합의 필요**
- 필드명 표기 통일 (`runningRecordId`·`averagePaceSecondsPerKm`는 camelCase, 나머지는 snake_case)
- `weather_code` 출처(API)와 기준 시점·위치
- `image_s3_key`의 버킷·prefix 규칙
- 서빙용 필드(`request_id`·`seed`·`targets`·`review_analysis`) 추가 여부
- `card_status`와 앱 상태(생성 중·완료·실패) 매핑, SNS 알림 처리
- presigned URL 발급과 카드 공개 정책 연동

**미결 (실제 AWS 리소스 필요)**
- S3 버킷, 실행 IAM Role, SNS 토픽, ECR 저장소 이름

**⚠️ 검증 필요 리스크**
- **ACE-Step `keyscale` 파라미터**: 고정 커밋 `ca1e85f`의 `GenerationParams`가 조성을 이 이름·형식(`"E Major"`)으로 받는지 확인 필요
- **두 모델 상주 자원**: T4 16GB VRAM·g4dn.xlarge RAM 16GB로 부족할 수 있음 → PoC 측정 후 인스턴스 상향 또는 단계별 GPU↔CPU 이동
- **10초 음악 품질**: 짧은 길이에서 전개·분위기가 caption대로 나오는지 불확실
- **처리 시간**: 사진 수에 비례해 증가 → 사진 상한·타임아웃·앱 "생성 중" 체감 시간에 영향
- **Qwen3-VL ↔ ACE-Step 의존성 충돌**: `transformers`·`torch` 버전이 `uv.lock`과 맞지 않을 수 있음
- **`emotion_direction` 분류 안정성**: 7종 분류가 흔들리면 같은 회고에 다른 조성이 나옴 → 평가셋으로 확인
- SageMaker GPU 호스트 드라이버 ↔ PyTorch CUDA 12.8 호환 → 필요 시 ProductionVariant `InferenceAmiVersion` 지정
- `model.tar.gz` 용량 증가로 콜드 스타트 증가 → 느리면 비압축 S3 prefix(`CompressionType: None`) 전환 검토

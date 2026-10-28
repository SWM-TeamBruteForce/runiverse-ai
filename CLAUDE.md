# Project: Runiverse AI

Runiverse 앱의 **기록 카드 AI 음악 생성** 서빙 저장소.
러닝 기록 + 회고 글 + 사진 0~N장을 받아 기본 음악 1개와 사진별 음악 N개를 만든다.
SageMaker Async 엔드포인트(BYOC 컨테이너)로 dev 배포한다. 당분간 음악 생성만 담는다.
개발자는 **AWS SageMaker를 처음 쓴다.** AWS 작업은 CLI·스크립트가 아니라 **AWS Console에서 직접** 한다.

## 1. 주요 기능

| ID | 기능 | 핵심 |
|---|---|---|
| AI-MUS-01 | 회고 글만으로 기본 음악 | 사진 0장이어도 기본 음악 1개 |
| AI-MUS-02 | 공통 음악 설정 | 페이스 → BPM, 회고 방향 × 날씨 → 조성, 4/4, 연주곡 |
| AI-MUS-03 | 회고 감정 해석 | Qwen3-VL(텍스트) → `emotion_cue`·`emotion_direction`·`music_emotion_en` |
| AI-MUS-04 | 기본 음악 반영 | **30초**. 회고 분위기 반영, 공통 설정 유지 |
| AI-MUS-05 | 사진 분석 | Qwen3-VL(이미지) → 장면·분위기·시간대·음악 단서 |
| AI-MUS-06 | 사진별 음악 | 사진마다 **10초** 1개. 사진 단서 + 사용자 사진 캡션 + 회고 분위기 |
| AI-MUS-07 | 결과 연결 | 기본 음악은 `card_id`, 사진별 음악은 `photo_id`로만 식별 |
| AI-MUS-08 | 비동기 상태 | 생성 중(대기열 포함)·완료·실패 |
| AI-MUS-09 | 보관·재생 | 접근 권한은 **백엔드** 책임 (presigned URL) |

- **생성 요청은 카드당 1건** = Async 호출 1건. 사진별로 쪼개지 않는다
- 호출 주체는 Spring Boot 백엔드. 앱은 엔드포인트를 직접 부르지 않는다
- **구현 현황**: 지금 코드는 `caption`·`bpm`·`duration`을 받아 음악 1개를 만드는 **단건 생성까지만** 있다. 카드 단위 파이프라인(Qwen3-VL 분석, 사진별 음악, 부분 실패)은 설계만 끝났고 미구현이다 → 설계 문서 2장의 🆕 파일들

## 2. 음악 생성 단계별 핵심사항

> 입력 필드·규칙표·caption 템플릿의 정본은 `docs/AI_음악_전처리_모듈.md`. 아래는 요약이다.

카드 1건 처리 순서: **입력 검증 → 회고 해석 → 공통 설정 → 사진 분석 → caption 조립 → 생성 → 저장**. 분석을 모두 끝낸 뒤 생성으로 넘어간다(GPU 모델 전환 1회). 카드 안 연산은 순차 실행.

**① 입력 검증**
- 입력: `card_id` `runningRecordId` `activity`(카드 단위) `averagePaceSecondsPerKm`(초 정수) `review_text` `weather_code`(WMO 4677) `photos[]`(`photo_id` `image_s3_key` `user_caption`)
- `photo_id` 중복, 사진이 있는데 `activity` 없음 → 400

**② 회고 해석** (`analyzer.py`, Qwen3-VL 텍스트)
- `emotion_cue`: 한글 한 구절, **30자 이내**. 원문 표현 보관용
- `emotion_direction`: 조성 선택용 **7종 중 하나** (평온·담담함 / 설렘·가벼운 즐거움 / 성취·환희 / 감사·따뜻한 위로 / 아쉬움·그리움 / 고뇌·결연함 / 깊은 슬픔·상실감)
- `music_emotion_en`: caption에 넣을 영어 표현
- 글에 없는 감정을 러닝 수치·사진으로 **지어내지 않는다**. 여러 감정이면 흐름과 마지막 여운을 함께 담는다
- 해석 실패·판단 불가 → `담담하고 안정적인 분위기` + `평온·담담함`
- 분석 결과는 생성용 내부 정보다. 사용자 감정으로 표시하지 않는다

**③ 공통 설정** (`music_params.py`, 모델 호출 없음)
- BPM = 페이스 구간: `<270초` 180 / `<360` 170 / `<450` 160 / 그 이상 150 / **결측 160**
- 조성 = `emotion_direction` × 날씨 그룹(5종) 표 조회
- 박자 `"4"`, 연주곡. 길이는 **기본 30초 / 사진 10초**
- **기본·사진별 음악은 같은 BPM·조성·박자.** 회고 감정은 조성에만 영향, BPM은 바꾸지 않는다

**④ 사진 분석** (`analyzer.py`, Qwen3-VL 이미지)
- 장면·분위기·시간대·음악 단서. 시간대를 판단할 수 없으면 `UNKNOWN`
- 분석 실패 → 사진 단서 없이 생성 계속, `image_analysis_status=FAILED`

**⑤ caption 조립** (`prompting.py`)
- **영어.** 공통 스타일 문장 → 곡별 단서 → 출력 검증 순서
- 기본 음악: 공통 스타일 + 활동 맥락 + `music_emotion_en` + `Instrumental, no vocals.`
- 사진별 음악: 위 + 사진 장면·분위기 + `user_caption` 핵심 요약
- 공통 스타일 문장은 모든 track에서 **동일**. 사진 분위기가 회고와 달라도 회고 여운을 빼지 않는다
- 날씨는 실제로 알 때만 질감에 반영. 사진에 안 보이는 날씨를 장면으로 단정하지 않는다
- BPM·조성·박자·길이는 caption에 쓰지 않고 **별도 파라미터**로 넘긴다
- **512자 초과** → 사진 장면 설명부터 압축. 공통 스타일·회고 여운·연주곡 조건은 보존
- 사진별 음악은 기본 음악을 편집하지 않고 **새로 생성**한다
- seed: 기본은 요청 `seed`, 사진별은 `seed`+`photo_id`로 결정적 파생

**⑥ 생성** (`generator.py`, ACE-Step 1.5 Turbo)
- 모델은 컨테이너 시작 시 1회 로드, 요청마다 재사용. GPU 작업은 lock으로 직렬화
- instrumental만 (`instrumental=true`). ACE-Step LM·보컬은 범위 밖
- 성공 플래그가 있어도 파일 존재·0바이트를 따로 검사한다

**⑦ 저장·응답** (`storage.py`, `pipeline.py`)
- 경로: `music/cards/{card_id}/{request_id}/` 아래 `analysis.json` · `base.wav` · `photos/{photo_id}.wav`
- `request_id`를 경로에 넣어 재시도 결과가 이전 결과를 덮어쓰지 않게 한다
- 분석 JSON을 생성 전에 먼저 저장한다 → 생성이 실패해도 분석은 남는다

**오류 원칙**
- HTTP 오류는 **요청이 잘못됐거나(400) 모델을 쓸 수 없을 때만**. 카드 안 항목 실패는 200 + `items[].status`
- 사진 하나 실패 → 나머지 유지, `card_status=PARTIAL`. 기본 음악 실패 → `FAILED`
- 모델 로드 실패 시 `/ping` 실패로 둔다. **CPU 대체 금지**
- 컨테이너는 자동 재시도하지 않는다. 백엔드가 `targets`로 실패 항목만 재요청

## 3. 저장소 구조

- Python 3.11 · FastAPI · Pydantic 2 · boto3 / ACE-Step 커밋 `ca1e85f` · PyTorch 2.10 · CUDA 12.8 (컨테이너에서 `uv.lock --frozen`으로 고정)
- 리전 서울 `ap-northeast-2`. 인스턴스 `ml.g4dn.xlarge`(T4 16GB), 0~1대 오토스케일링

| 경로 | 내용 |
|---|---|
| `src/runiverse_music/` | 서빙 코드. `app`(`/ping`·`/invocations`) `config` `schemas` `generator` `storage` |
| `container/` | `Dockerfile`, `serve` 진입점 (uvicorn 8080, 워커 1개) |
| `scripts/` | `download_models`(HF→S3 `model.tar.gz`) `build_and_push.sh`(ECR) `deploy` `deploy_config` `smoke_test` |
| `deploy/envs/dev.yaml` | dev 배포 설정. **값 변경은 재배포만으로 반영** |
| `notebooks/` | `baseline_colab`(기존 베이스라인 보관) `colab_verify`(Colab GPU 실생성 검증) |
| `tests/` | pytest. GPU 없이 돈다 |
| `docs/` | 기능명세, 전처리 모듈, 설계 (`docs/superpowers/specs/`) |

- **torch·acestep import는 `generator.py` 메서드 안에서만.** (예정: torch·transformers는 `analyzer.py`만) → 나머지는 GPU 없이 테스트 가능
- 테스트·로컬 검증용 대체 구현: `GENERATOR=fake` · `STORAGE=local` (예정: `ANALYZER=fake`)
- 가중치 배치: `/opt/ml/model/acestep/` (예정: `/opt/ml/model/qwen3-vl/`)

## 4. 지침

### Commands

- 설치: `python -m venv .venv` → `.venv/Scripts/python -m pip install -e ".[dev,deploy]"`
- 테스트: `.venv/Scripts/python -m pytest` (파일 하나: `... -m pytest tests/test_schemas.py`)
- 컨테이너: `docker build -f container/Dockerfile -t runiverse-music:local .` → 실행 명령은 `README.md` 검증 단계 ②
- Git Bash: 한글 깨지면 `export PYTHONUTF8=1`, Docker 경로 변환 막으려면 `export MSYS_NO_PATHCONV=1`

### AWS 작업 (Console)

- S3·ECR·IAM·SageMaker·SNS·CloudWatch 작업은 **개발자가 Console에서 직접** 한다. Claude가 AWS CLI·boto3·`scripts/`로 대신 실행하지 않는다
- Claude는 **Console 단계별 안내**를 준다: 서비스 → 메뉴 경로 → 입력할 필드와 값 → 완료 확인 방법
- 처음 쓰는 개념(Model·Endpoint Config·Endpoint, Async 설정, 실행 Role 등)은 단계마다 1~2줄로 무엇인지와 왜 필요한지 붙인다
- 입력값의 근거는 `deploy/envs/dev.yaml`과 `scripts/deploy.py`다. 스크립트는 **설정값 참고용**으로 읽는다
- 리전은 항상 서울 `ap-northeast-2`인지 먼저 확인시킨다
- 과금이 시작·지속되는 단계(엔드포인트 생성, 인스턴스 유지)는 미리 알리고, 끝나면 0대 축소·삭제 방법도 함께 안내한다

### 검증 단계

로컬 PC에 GPU가 없다. 아래 순서로 올라간다. 앞 단계를 통과하기 전에 다음으로 가지 않는다.

| 단계 | 위치 | 잡는 것 |
|---|---|---|
| ① 단위 테스트 | 로컬 pytest | 로직·검증·매핑·응답 |
| ② 컨테이너 계약 | 로컬 Docker (`fake`·`local`) | Dockerfile·진입점·포트·응답 형식 |
| ③ 실생성·품질 | Colab T4 | 모델 실경로, 분석 품질, VRAM·시간 |
| ④ 스모크 | dev 엔드포인트, Console (**과금**) | 드라이버·콜드 스타트·Async·SNS |

- ④는 엔드포인트 수정 1회에 10분 이상 걸리고 비용이 든다. Console에서 개발자가 직접 호출·확인한다 (S3 입력 업로드 → 엔드포인트 테스트 → 출력·CloudWatch 로그 확인)

### Git

- 커밋: `<type>: <설명>` — **type 앞에 이모지가 붙는다.** `📍 Feat` `🔨 Fix` `📝 Docs` `🎨 Style` `🤖 Refactor` `✅ Test` `🚚 Chore` `✂️ Remove` `🔧 Rename` (frontend와 동일)
- 브랜치: `<type>/<domain>` 소문자 kebab-case (`feat/music-gen-serving`)
- **PR의 base는 `dev`.** 원격은 `upstream`=SWM-TeamBruteForce/runiverse-ai, `origin`=개인 fork
- 한 커밋에 논리적 변경 하나. 각 커밋에서 pytest가 통과하는지 본다
- **AI를 공동 작성자로 넣지 않는다.** `Co-Authored-By: Claude` 트레일러, `🤖 Generated with` 푸터 모두 금지

### Do

- 구현 전에 전제를 말한다. 해석이 갈리면 선택지를 제시하고, 불분명하면 멈추고 묻는다
- 새 모듈은 Fake 구현과 함께 만든다. GPU 없이 테스트할 수 있어야 한다
- 설정값은 `config.py`에서 검증한다. 잘못된 값이면 **시작 시 실패**
- 로그는 단계마다 JSON 한 줄. `request_id`·`card_id`·`photo_id`·`stage`를 키로 둔다
- 문서는 개조식 핵심 위주. 서술형 문단 대신 불릿·표
- 모르면 모른다고 한다. ACE-Step·Qwen3-VL에 없는 API를 만들어내지 않는다

### Don't

- 요청 없이 이 파일 수정 — 새 규칙이 필요하면 수정안만 제안한다
- 요청하지 않은 기능·추상화·설정 추가, 안 고장난 것 리팩터링
- `generator.py`(예정: `analyzer.py`) 밖에서 torch·acestep·transformers import
- `dev.yaml`에 `fake`·`local` 값 넣기 — 로컬·Colab 검증 전용이다
- 재빌드가 필요한 값(ACE-Step 커밋, CUDA 베이스, Python 의존성)을 `dev.yaml`로 옮기기
- **회고 원문·사진을 로그에 남기기** (개인정보). `emotion_cue`만 기록한다
- AWS 키·ARN·버킷 이름을 코드에 하드코딩, 시크릿 커밋
- AWS CLI·boto3·`scripts/`로 AWS 리소스를 생성·변경·삭제·호출 — Console 안내로 대신한다
- 검증하지 않은 코드를 "완성"이라고 보고

## Docs

| 주제 | 경로 |
|---|---|
| **기능명세 (정본)** | `docs/AI_러닝_음악_기능명세.md` |
| **전처리 모듈 (입력·규칙표·caption 정본)** | `docs/AI_음악_전처리_모듈.md` (원본: Notion "Ace-Step 전처리 모듈") |
| 설계 (아키텍처·요청·응답·오류·테스트) | `docs/superpowers/specs/2026-10-14-sagemaker-music-gen-design.md` |
| 운영·배포 가이드 (스크립트 기준, Console 작업 시 값 참고용) | `README.md` |

**저장소 밖**: 앱 전체 기획·화면은 `../Docs/`, 앱 코드는 `../frontend/`. 베이스라인 노트북 원본은 Google Drive `Runiverse_ACE_Step_1_5_Colab_Python311_CUDA.ipynb`.

**미확정 정책**(공통 음악 스타일, 날씨 결측 시 조성, 사진 개수 상한, 재시도 횟수 등)은 기능명세 7장과 설계 11장에 있다. 가정값으로 구현할 때는 그 사실을 PR에 적는다.

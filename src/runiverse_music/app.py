"""SageMaker 서빙 앱: GET /ping, POST /invocations (카드 1건 = 호출 1건)."""
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

from .analyzer import Analyzer, make_analyzer
from .config import Settings, load_settings
from .generator import Generator, make_generator
from .pipeline import CardFailure, failure_body, run_card
from .schemas import CardRequest
from .storage import Storage, make_storage

logger = logging.getLogger("runiverse_music")


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    # CloudWatch에서 record_card_id로 검색할 수 있게 요청당 JSON 한 줄
    logger.log(level, json.dumps({"event": event, **fields}, ensure_ascii=False, default=str))


def create_app(settings: Settings, analyzer: Analyzer, generator: Generator, storage: Storage) -> FastAPI:
    gpu_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 로드 실패 시 예외 → 서버 종료 → SageMaker가 엔드포인트를 Failed로 둠
        started = time.perf_counter()
        analyzer.load()
        generator.load()
        app.state.ready = True
        log_event("model_loaded", generator=settings.generator, seconds=round(time.perf_counter() - started, 2),
                  manifest=generator.manifest)
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
            card = CardRequest.model_validate_json(body)
        except ValidationError as exc:
            detail = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
            log_event("invalid_request", logging.WARNING, detail=detail)
            # 200이 아니면 SageMaker Async가 FailureLocation(async/failures/)에 저장하고 SNS 실패 토픽으로 알린다
            body_ids = _ids_from(body)
            content = failure_body(*body_ids, "INVALID_REQUEST", "요청 형식이 잘못되었습니다.", detail)
            return JSONResponse(status_code=400, content=content)
        with gpu_lock:
            return process(card)

    def process(card: CardRequest) -> JSONResponse:
        started = time.perf_counter()
        ids = {"user_id": card.user_id, "record_card_id": card.record_card_id}
        seconds = lambda: round(time.perf_counter() - started, 2)  # noqa: E731
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = run_card(card, analyzer, generator, storage, settings.output_prefix, Path(tmp))
        except CardFailure as failure:  # 곡 생성·업로드 실패 → 500·502
            log_event("card_failed", logging.WARNING, **ids, code=failure.code, total_seconds=seconds(),
                      photos=len(card.photos), **memory_gib())
            content = failure_body(card.user_id, card.record_card_id, failure.code, failure.message)
            return JSONResponse(status_code=failure.http_status, content=content)
        except Exception as exc:  # 예상 못 한 오류
            # 예외 메시지에 회고 원문이 섞일 수 있어 타입명만 남긴다
            log_event("request_failed", logging.ERROR, **ids, error=type(exc).__name__)
            content = failure_body(card.user_id, card.record_card_id, "INTERNAL_ERROR",
                                   f"음악 생성 중 예상하지 못한 오류가 발생했습니다 ({type(exc).__name__}).")
            return JSONResponse(status_code=500, content=content)
        log_event("generated", **ids, total_seconds=seconds(), photos=len(card.photos), **memory_gib())
        return JSONResponse(content=result)  # 성공만 200 → OutputLocation(async/outputs/) + SNS 성공 토픽

    return app


def memory_gib() -> dict[str, float]:
    """컨테이너 프로세스 RAM (Linux /proc). rss: 지금, peak_rss: 컨테이너 시작 후 최대. 못 읽으면 빈 dict."""
    try:
        status = Path("/proc/self/status").read_text()
    except OSError:
        return {}
    kib = {line.split(":")[0]: int(line.split()[1]) for line in status.splitlines()
           if line.startswith(("VmRSS:", "VmHWM:"))}
    names = {"VmRSS": "rss_gib", "VmHWM": "peak_rss_gib"}
    return {names[key]: round(value / 2**20, 2) for key, value in kib.items()}


def _ids_from(body: bytes) -> tuple[int | None, int | None]:
    """형식이 틀린 요청에서도 읽을 수 있으면 user_id·record_card_id를 꺼낸다 (실패 JSON 연결용)."""
    try:
        data = json.loads(body)
    except ValueError:
        return None, None
    if not isinstance(data, dict):
        return None, None
    pick = lambda key: data.get(key) if isinstance(data.get(key), int) else None  # noqa: E731
    return pick("user_id"), pick("record_card_id")


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
    return create_app(settings, make_analyzer(settings), make_generator(settings), make_storage(settings))

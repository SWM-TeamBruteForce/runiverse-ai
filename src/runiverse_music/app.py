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

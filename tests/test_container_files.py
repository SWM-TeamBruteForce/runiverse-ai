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


def test_build_script_uses_lf():
    data = (ROOT / "scripts" / "build_and_push.sh").read_bytes()
    assert data.startswith(b"#!/usr/bin/env bash\n")
    assert b"\r\n" not in data


def test_dockerfile_disables_uv_cache():
    # uv 캐시가 이미지에 남으면 torch 휠이 이중 저장돼 0→1 콜드 스타트가 길어진다
    text = (ROOT / "container" / "Dockerfile").read_text(encoding="utf-8")
    assert "UV_NO_CACHE=1" in text


def test_dockerignore_allows_every_copy_source():
    # .dockerignore는 허용 목록 방식 → Dockerfile에 COPY를 추가하고 여기를 빠뜨리면 빌드가 실패한다
    allowed = {
        line[1:].rstrip("/")
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.startswith("!")
    }
    copies = [
        line.split()[1:-1]
        for line in (ROOT / "container" / "Dockerfile").read_text(encoding="utf-8").splitlines()
        if line.startswith("COPY ") and "--from=" not in line
    ]
    sources = {source.rstrip("/") for group in copies for source in group}
    assert sources and sources <= allowed


def test_image_tag_arg_is_declared_after_heavy_layers():
    # 빌드 인자 값이 바뀌면 선언 이후 RUN이 모두 캐시를 잃는다 → 태그는 마지막 ENV 직전에 선언
    lines = (ROOT / "container" / "Dockerfile").read_text(encoding="utf-8").splitlines()
    arg = lines.index("ARG IMAGE_TAG=local")
    assert all(not line.startswith("RUN ") for line in lines[arg:])

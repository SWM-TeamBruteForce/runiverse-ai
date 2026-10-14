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

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

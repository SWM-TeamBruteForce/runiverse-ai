"""dev 엔드포인트 스모크 테스트: 기본 요청 → Async 호출 → 결과 검증.

사용: python scripts/smoke_test.py --env dev [--timeout 1800]
- 인스턴스 0대에서 시작하면 콜드 스타트로 10분 이상 걸릴 수 있음
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

from deploy_config import load_deploy_config

DEFAULT_CAPTION = (
    "Energetic electronic running music, steady clear drum beat, "
    "uplifting synth melody, consistent rhythm, instrumental, no vocals"
)
EXPECTED_SAMPLE_RATE = 48000
DURATION_TOLERANCE_SECONDS = 1.0


def build_request(request_id: str) -> dict:
    return {
        "request_id": request_id,
        "caption": DEFAULT_CAPTION,
        "bpm": 170,
        "duration": 30.0,
        "seed": 42,
        "instrumental": True,
    }


def parse_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"S3 URI가 아닙니다: {uri}")
    bucket, _, key = uri[len("s3://"):].partition("/")
    return bucket, key


def check_response(response: dict, expected_duration: float) -> list[str]:
    problems = []
    if response.get("sample_rate") != EXPECTED_SAMPLE_RATE:
        problems.append(f"sample_rate={response.get('sample_rate')} (기대 {EXPECTED_SAMPLE_RATE})")
    actual = response.get("actual_duration_seconds")
    if not isinstance(actual, (int, float)) or abs(actual - expected_duration) > DURATION_TOLERANCE_SECONDS:
        problems.append(f"actual_duration_seconds={actual} (기대 약 {expected_duration:g})")
    if not response.get("bytes"):
        problems.append("bytes가 0입니다")
    if not str(response.get("audio_s3_key", "")).endswith(".wav"):
        problems.append(f"audio_s3_key={response.get('audio_s3_key')} (wav 아님)")
    return problems


def _exists(s3, uri: str) -> bool:
    from botocore.exceptions import ClientError

    bucket, key = parse_s3_uri(uri)
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def wait_for_result(s3, output_uri: str, failure_uri: str | None, timeout: float,
                    interval: float = 15.0, sleep=time.sleep, clock=time.monotonic) -> tuple[str, str]:
    deadline = clock() + timeout
    while True:
        for status, uri in (("success", output_uri), ("failure", failure_uri)):
            if uri and _exists(s3, uri):
                bucket, key = parse_s3_uri(uri)
                return status, s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        if clock() >= deadline:
            raise TimeoutError(f"{timeout:.0f}초 안에 결과가 없습니다: {output_uri}")
        sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="dev 엔드포인트 스모크 테스트")
    parser.add_argument("--env", default="dev")
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args(argv)

    cfg = load_deploy_config(args.env)
    if not cfg.bucket:
        print(f"deploy/envs/{args.env}.yaml의 bucket을 먼저 채우세요.", file=sys.stderr)
        return 2

    import boto3

    session = boto3.Session(region_name=cfg.region)
    s3 = session.client("s3")
    runtime = session.client("sagemaker-runtime")

    request_id = f"smoke-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    request_key = f"music/requests/{request_id}.json"
    s3.put_object(Bucket=cfg.bucket, Key=request_key, Body=json.dumps(build_request(request_id)).encode(),
                  ContentType="application/json")
    invoked = runtime.invoke_endpoint_async(
        EndpointName=cfg.endpoint_name,
        InputLocation=f"s3://{cfg.bucket}/{request_key}",
        ContentType="application/json",
        InferenceId=request_id,
        InvocationTimeoutSeconds=3600,
    )
    print(f"호출: {request_id} → {invoked['OutputLocation']}", flush=True)

    status, body = wait_for_result(s3, invoked["OutputLocation"], invoked.get("FailureLocation"), args.timeout)
    if status == "failure":
        print(f"실패:\n{body}", file=sys.stderr)
        return 1

    response = json.loads(body)
    problems = check_response(response, 30.0)
    if response.get("audio_s3_key"):
        head = s3.head_object(Bucket=cfg.bucket, Key=response["audio_s3_key"])
        if head["ContentLength"] != response.get("bytes"):
            problems.append(f"S3 크기 {head['ContentLength']} ≠ 응답 bytes {response.get('bytes')}")
    if problems:
        print("검증 실패:\n- " + "\n- ".join(problems), file=sys.stderr)
        return 1
    print(json.dumps(response, indent=2, ensure_ascii=False))
    print("✅ 스모크 테스트 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())

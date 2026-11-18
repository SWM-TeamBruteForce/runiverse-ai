"""dev 엔드포인트 스모크 테스트: 사진 없는 카드 → Async 호출 → 결과 검증.

사용: python scripts/smoke_test.py --env dev [--timeout 1800]
- 인스턴스 0대에서 시작하면 콜드 스타트로 10분 이상 걸릴 수 있음
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid

from deploy_config import load_deploy_config

def input_key(s3_env: str, record_card_id: str, inference_id: str) -> str:
    """입력 JSON 키 (Notion「S3 버킷 저장 구조」6장). 호출마다 InferenceId가 달라 재요청이 이전 입력을 덮어쓰지 않는다."""
    return f"async/inputs/{s3_env}/{record_card_id}/{inference_id}.json"


SMOKE_USER_ID = "00000000-0000-4000-8000-000000000000"  # 스모크 전용. 실제 사용자 UUID와 겹치지 않는다


def build_request(record_card_id: str) -> dict:
    """사진 없는 카드 1건. ID는 백엔드처럼 UUID 문자열 (2026-11-17)."""
    return {
        "user_id": SMOKE_USER_ID,
        "record_card_id": record_card_id,
        "memo": None,
        "activity_type": "DAILY",
        "running_summary": {"avg_pace": 320, "weather_code": 0},
        "photos": [],
    }


def parse_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"S3 URI가 아닙니다: {uri}")
    bucket, _, key = uri[len("s3://"):].partition("/")
    return bucket, key


def check_response(response: dict) -> list[str]:
    problems = []
    if response.get("status") != "SUCCEEDED":
        problems.append(f"status={response.get('status')}, error={response.get('error')}")
    key = (response.get("base_music") or {}).get("s3_key")
    if not str(key).endswith(".wav"):
        problems.append(f"base_music.s3_key={key} (wav 아님)")
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

    record_card_id = str(uuid.uuid4())  # 스모크 전용 카드 ID
    inference_id = uuid.uuid4().hex  # 실제 서비스에서는 AI 서버가 호출마다 만든다
    request_key = input_key(cfg.s3_env, record_card_id, inference_id)
    s3.put_object(Bucket=cfg.bucket, Key=request_key, Body=json.dumps(build_request(record_card_id)).encode(),
                  ContentType="application/json")
    invoked = runtime.invoke_endpoint_async(
        EndpointName=cfg.endpoint_name,
        InputLocation=f"s3://{cfg.bucket}/{request_key}",
        ContentType="application/json",
        InferenceId=inference_id,
        InvocationTimeoutSeconds=3600,
    )
    print(f"호출: {request_key} → {invoked['OutputLocation']}", flush=True)

    status, body = wait_for_result(s3, invoked["OutputLocation"], invoked.get("FailureLocation"), args.timeout)
    if status == "failure":
        print(f"실패:\n{body}", file=sys.stderr)
        return 1

    response = json.loads(body)
    problems = check_response(response)
    if not problems:
        head = s3.head_object(Bucket=cfg.bucket, Key=response["base_music"]["s3_key"])
        if head["ContentLength"] == 0:
            problems.append("S3 음원이 0바이트입니다")
    if problems:
        print("검증 실패:\n- " + "\n- ".join(problems), file=sys.stderr)
        return 1
    print(json.dumps(response, indent=2, ensure_ascii=False))
    print("✅ 스모크 테스트 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())

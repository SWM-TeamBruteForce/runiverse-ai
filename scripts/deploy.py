"""dev.yaml 기준 SageMaker Async 엔드포인트 배포 + 0대 축소 오토스케일링.

사용: python scripts/deploy.py --env dev --image-uri <ECR URI:tag> [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone

from deploy_config import (
    LOCKED_ENV, DeployConfig, DeployConfigError, load_deploy_config, require_deployable,
)

VARIANT = "AllTraffic"
SCALABLE_DIMENSION = "sagemaker:variant:DesiredInstanceCount"


def check_image_uri(image_uri: str) -> None:
    if ":" not in image_uri.rsplit("/", 1)[-1]:
        raise DeployConfigError("--image-uri에 태그가 필요합니다 (build_and_push.sh 출력값).")


def resource_name(cfg: DeployConfig, image_uri: str, now: datetime) -> str:
    tag = re.sub(r"[^A-Za-z0-9-]", "-", image_uri.rsplit(":", 1)[-1])[:12]
    return f"{cfg.endpoint_name}-{tag}-{now:%Y%m%d%H%M%S}"


def container_environment(cfg: DeployConfig) -> dict[str, str]:
    env = {"OUTPUT_PREFIX": "music/outputs", **cfg.container_env, **LOCKED_ENV}
    env["OUTPUT_BUCKET"] = cfg.bucket
    return env


def build_model_request(cfg: DeployConfig, name: str, image_uri: str) -> dict:
    return {
        "ModelName": name,
        "ExecutionRoleArn": cfg.execution_role_arn,
        "PrimaryContainer": {
            "Image": image_uri,
            "ModelDataUrl": cfg.model_data_url,
            "Environment": container_environment(cfg),
        },
    }


def build_endpoint_config_request(cfg: DeployConfig, name: str, model_name: str) -> dict:
    variant = {
        "VariantName": VARIANT,
        "ModelName": model_name,
        "InstanceType": cfg.instance_type,
        "InitialInstanceCount": cfg.initial_instance_count,
        "ModelDataDownloadTimeoutInSeconds": cfg.model_data_download_timeout,
        "ContainerStartupHealthCheckTimeoutInSeconds": cfg.container_startup_health_check_timeout,
    }
    if cfg.inference_ami_version:
        variant["InferenceAmiVersion"] = cfg.inference_ami_version
    output = {
        "S3OutputPath": f"s3://{cfg.bucket}/music/responses/",
        "S3FailurePath": f"s3://{cfg.bucket}/music/failures/",
    }
    notification = {}
    if cfg.sns_success_topic_arn:
        notification["SuccessTopic"] = cfg.sns_success_topic_arn
    if cfg.sns_error_topic_arn:
        notification["ErrorTopic"] = cfg.sns_error_topic_arn
    if notification:
        output["NotificationConfig"] = notification
    return {
        "EndpointConfigName": name,
        "ProductionVariants": [variant],
        "AsyncInferenceConfig": {
            "ClientConfig": {"MaxConcurrentInvocationsPerInstance": cfg.max_concurrent_invocations_per_instance},
            "OutputConfig": output,
        },
    }


def _scaling_target(cfg: DeployConfig) -> dict:
    return {
        "ServiceNamespace": "sagemaker",
        "ResourceId": f"endpoint/{cfg.endpoint_name}/variant/{VARIANT}",
        "ScalableDimension": SCALABLE_DIMENSION,
    }


def build_scalable_target(cfg: DeployConfig) -> dict:
    return {
        **_scaling_target(cfg),
        "MinCapacity": cfg.autoscaling.min_capacity,
        "MaxCapacity": cfg.autoscaling.max_capacity,
    }


def build_backlog_policy(cfg: DeployConfig) -> dict:
    # 대기열 기준 증감. 대기열 0이 지속되면 0대까지 축소
    return {
        **_scaling_target(cfg),
        "PolicyName": f"{cfg.endpoint_name}-backlog",
        "PolicyType": "TargetTrackingScaling",
        "TargetTrackingScalingPolicyConfiguration": {
            "TargetValue": cfg.autoscaling.target_backlog_per_instance,
            "CustomizedMetricSpecification": {
                "MetricName": "ApproximateBacklogSizePerInstance",
                "Namespace": "AWS/SageMaker",
                "Dimensions": [{"Name": "EndpointName", "Value": cfg.endpoint_name}],
                "Statistic": "Average",
            },
            "ScaleInCooldown": cfg.autoscaling.scale_in_cooldown,
            "ScaleOutCooldown": cfg.autoscaling.scale_out_cooldown,
        },
    }


def build_wake_policy(cfg: DeployConfig) -> dict:
    # 0대일 때 요청이 오면 1대로 깨우기
    return {
        **_scaling_target(cfg),
        "PolicyName": f"{cfg.endpoint_name}-wake-from-zero",
        "PolicyType": "StepScaling",
        "StepScalingPolicyConfiguration": {
            "AdjustmentType": "ChangeInCapacity",
            "MetricAggregationType": "Average",
            "Cooldown": cfg.autoscaling.scale_out_cooldown,
            "StepAdjustments": [{"MetricIntervalLowerBound": 0, "ScalingAdjustment": 1}],
        },
    }


def build_wake_alarm(cfg: DeployConfig, policy_arn: str) -> dict:
    return {
        "AlarmName": f"{cfg.endpoint_name}-has-backlog-without-capacity",
        "MetricName": "HasBacklogWithoutCapacity",
        "Namespace": "AWS/SageMaker",
        "Dimensions": [{"Name": "EndpointName", "Value": cfg.endpoint_name}],
        "Statistic": "Average",
        "Period": 60,
        "EvaluationPeriods": 2,
        "DatapointsToAlarm": 2,
        "Threshold": 1,
        "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        "TreatMissingData": "missing",
        "AlarmActions": [policy_arn],
    }


class DeploymentError(RuntimeError):
    """엔드포인트가 새 설정으로 바뀌지 않음."""


def endpoint_status(sm, name: str) -> str | None:
    """엔드포인트 상태. 없으면 None."""
    from botocore.exceptions import ClientError

    try:
        return sm.describe_endpoint(EndpointName=name)["EndpointStatus"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ValidationException":
            return None
        raise


def remove_autoscaling(aas, cfg: DeployConfig) -> None:
    from botocore.exceptions import ClientError

    try:
        aas.deregister_scalable_target(**_scaling_target(cfg))
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ObjectNotFoundException":
            raise


def apply_autoscaling(aas, cw, cfg: DeployConfig) -> None:
    aas.register_scalable_target(**build_scalable_target(cfg))
    aas.put_scaling_policy(**build_backlog_policy(cfg))
    wake = aas.put_scaling_policy(**build_wake_policy(cfg))
    cw.put_metric_alarm(**build_wake_alarm(cfg, wake["PolicyARN"]))


def deploy(cfg: DeployConfig, image_uri: str, sm, aas, cw, now: datetime) -> str:
    name = resource_name(cfg, image_uri, now)
    sm.create_model(**build_model_request(cfg, name, image_uri))
    sm.create_endpoint_config(**build_endpoint_config_request(cfg, name, name))
    status = endpoint_status(sm, cfg.endpoint_name)
    if status == "Failed":
        # Failed 엔드포인트는 갱신할 수 없어 지우고 새로 만든다
        print(f"{cfg.endpoint_name}이 Failed 상태라 삭제 후 다시 만듭니다.", flush=True)
        remove_autoscaling(aas, cfg)
        sm.delete_endpoint(EndpointName=cfg.endpoint_name)
        sm.get_waiter("endpoint_deleted").wait(
            EndpointName=cfg.endpoint_name, WaiterConfig={"Delay": 15, "MaxAttempts": 40}
        )
        sm.create_endpoint(EndpointName=cfg.endpoint_name, EndpointConfigName=name)
    elif status:
        remove_autoscaling(aas, cfg)
        sm.update_endpoint(EndpointName=cfg.endpoint_name, EndpointConfigName=name)
    else:
        sm.create_endpoint(EndpointName=cfg.endpoint_name, EndpointConfigName=name)
    print(f"{cfg.endpoint_name} 반영 대기 중 (최대 30분)...", flush=True)
    sm.get_waiter("endpoint_in_service").wait(
        EndpointName=cfg.endpoint_name, WaiterConfig={"Delay": 30, "MaxAttempts": 60}
    )
    # 갱신 실패 시 SageMaker는 이전 설정으로 롤백해도 InService가 되므로 적용된 설정을 확인
    current = sm.describe_endpoint(EndpointName=cfg.endpoint_name)
    apply_autoscaling(aas, cw, cfg)
    if current.get("EndpointConfigName") != name:
        reason = current.get("FailureReason", "알 수 없음")
        raise DeploymentError(f"새 설정 {name}이 적용되지 않았습니다(이전 설정으로 롤백): {reason}")
    return name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SageMaker Async 엔드포인트 배포")
    parser.add_argument("--env", default="dev")
    parser.add_argument("--image-uri", required=True)
    parser.add_argument("--dry-run", action="store_true", help="요청 내용만 출력")
    args = parser.parse_args(argv)

    try:
        cfg = load_deploy_config(args.env)
        require_deployable(cfg)
        check_image_uri(args.image_uri)
    except DeployConfigError as exc:
        print(f"배포 거부: {exc}", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    if args.dry_run:
        name = resource_name(cfg, args.image_uri, now)
        print(json.dumps({
            "model": build_model_request(cfg, name, args.image_uri),
            "endpoint_config": build_endpoint_config_request(cfg, name, name),
            "scalable_target": build_scalable_target(cfg),
        }, indent=2, ensure_ascii=False))
        return 0

    import boto3
    from botocore.exceptions import WaiterError

    session = boto3.Session(region_name=cfg.region)
    sm = session.client("sagemaker")
    try:
        name = deploy(cfg, args.image_uri, sm, session.client("application-autoscaling"),
                      session.client("cloudwatch"), now)
    except WaiterError:
        reason = sm.describe_endpoint(EndpointName=cfg.endpoint_name).get("FailureReason", "알 수 없음")
        print(f"엔드포인트 배포 실패: {reason}", file=sys.stderr)
        return 1
    except DeploymentError as exc:
        print(f"엔드포인트 배포 실패: {exc}", file=sys.stderr)
        return 1
    print(f"배포 완료: {cfg.endpoint_name} ← {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

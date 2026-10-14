from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

import deploy
from deploy_config import Autoscaling, DeployConfig, DeployConfigError

NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)
IMAGE = "123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/runiverse-music:abc123def4567890"


def make_cfg(**over):
    base = dict(
        env_name="dev", region="ap-northeast-2", endpoint_name="runiverse-music-dev",
        ecr_repository="runiverse-music", instance_type="ml.g4dn.xlarge",
        initial_instance_count=1, max_concurrent_invocations_per_instance=1,
        model_data_download_timeout=600, container_startup_health_check_timeout=600,
        autoscaling=Autoscaling(0, 1, 1.0, 600, 300), bucket="bkt",
        execution_role_arn="arn:aws:iam::123456789012:role/sm",
        model_data_url="s3://bkt/music/models/ace-step-1.5/rev/model.tar.gz",
        sns_success_topic_arn="", sns_error_topic_arn="", inference_ami_version="",
        container_env={"MAX_DURATION_SECONDS": "120", "LOG_LEVEL": "INFO"},
    )
    base.update(over)
    return DeployConfig(**base)


class FakeClient:
    """responses[name]: dict·예외 하나, 또는 호출 순서대로 쓸 목록(마지막 값 반복)."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def __getattr__(self, name):
        def method(**kwargs):
            self.calls.append((name, kwargs))
            value = self.responses.get(name, {})
            if isinstance(value, list):
                value = value.pop(0) if len(value) > 1 else value[0]
            if isinstance(value, Exception):
                raise value
            return value
        return method

    def get_waiter(self, name):
        self.calls.append(("get_waiter", {"name": name}))
        return SimpleNamespace(wait=lambda **kw: self.calls.append(("wait", kw)))


def client_error(code, op):
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


def test_resource_name():
    name = deploy.resource_name(make_cfg(), IMAGE, NOW)
    assert name == "runiverse-music-dev-abc123def456-20261014120000"
    assert len(name) <= 63


def test_image_uri_requires_tag():
    with pytest.raises(DeployConfigError):
        deploy.check_image_uri("123.dkr.ecr.ap-northeast-2.amazonaws.com/runiverse-music")
    deploy.check_image_uri(IMAGE)


def test_container_environment_locks_values():
    env = deploy.container_environment(make_cfg(container_env={"GENERATOR": "fake", "STORAGE": "local"}))
    assert env["GENERATOR"] == "acestep"
    assert env["STORAGE"] == "s3"
    assert env["OUTPUT_BUCKET"] == "bkt"
    assert env["OUTPUT_PREFIX"] == "music/outputs"


def test_model_request():
    req = deploy.build_model_request(make_cfg(), "m1", IMAGE)
    assert req["ModelName"] == "m1"
    assert req["ExecutionRoleArn"] == "arn:aws:iam::123456789012:role/sm"
    assert req["PrimaryContainer"]["Image"] == IMAGE
    assert req["PrimaryContainer"]["ModelDataUrl"].endswith("model.tar.gz")
    assert req["PrimaryContainer"]["Environment"]["MAX_DURATION_SECONDS"] == "120"


def test_endpoint_config_without_optional():
    req = deploy.build_endpoint_config_request(make_cfg(), "c1", "m1")
    variant = req["ProductionVariants"][0]
    assert variant["VariantName"] == "AllTraffic"
    assert variant["InstanceType"] == "ml.g4dn.xlarge"
    assert variant["ModelDataDownloadTimeoutInSeconds"] == 600
    assert "InferenceAmiVersion" not in variant
    assert req["AsyncInferenceConfig"]["ClientConfig"] == {"MaxConcurrentInvocationsPerInstance": 1}
    assert req["AsyncInferenceConfig"]["OutputConfig"] == {
        "S3OutputPath": "s3://bkt/music/responses/",
        "S3FailurePath": "s3://bkt/music/failures/",
    }


def test_endpoint_config_with_sns_and_ami():
    cfg = make_cfg(sns_success_topic_arn="arn:s", sns_error_topic_arn="arn:e", inference_ami_version="ami-x")
    req = deploy.build_endpoint_config_request(cfg, "c1", "m1")
    assert req["ProductionVariants"][0]["InferenceAmiVersion"] == "ami-x"
    assert req["AsyncInferenceConfig"]["OutputConfig"]["NotificationConfig"] == {
        "SuccessTopic": "arn:s", "ErrorTopic": "arn:e",
    }


def test_scaling_requests():
    cfg = make_cfg()
    target = deploy.build_scalable_target(cfg)
    assert target["ResourceId"] == "endpoint/runiverse-music-dev/variant/AllTraffic"
    assert (target["MinCapacity"], target["MaxCapacity"]) == (0, 1)
    spec = deploy.build_backlog_policy(cfg)["TargetTrackingScalingPolicyConfiguration"]
    assert spec["CustomizedMetricSpecification"]["MetricName"] == "ApproximateBacklogSizePerInstance"
    assert spec["TargetValue"] == 1.0
    wake = deploy.build_wake_policy(cfg)
    assert wake["PolicyType"] == "StepScaling"
    alarm = deploy.build_wake_alarm(cfg, "arn:policy")
    assert alarm["MetricName"] == "HasBacklogWithoutCapacity"
    assert alarm["AlarmActions"] == ["arn:policy"]


NAME = "runiverse-music-dev-abc123def456-20261014120000"
NOT_FOUND = client_error("ValidationException", "DescribeEndpoint")
APPLIED = {"EndpointStatus": "InService", "EndpointConfigName": NAME}


def scaling_client():
    return FakeClient({
        "put_scaling_policy": {"PolicyARN": "arn:policy"},
        "deregister_scalable_target": client_error("ObjectNotFoundException", "Deregister"),
    })


def test_deploy_creates_new_endpoint():
    sm = FakeClient({"describe_endpoint": [NOT_FOUND, APPLIED]})
    aas = scaling_client()
    cw = FakeClient()
    assert deploy.deploy(make_cfg(), IMAGE, sm, aas, cw, NOW) == NAME
    assert [c[0] for c in sm.calls] == [
        "create_model", "create_endpoint_config", "describe_endpoint", "create_endpoint",
        "get_waiter", "wait", "describe_endpoint",
    ]
    assert [c[0] for c in aas.calls] == ["register_scalable_target", "put_scaling_policy", "put_scaling_policy"]
    assert cw.calls[0][1]["AlarmActions"] == ["arn:policy"]


def test_deploy_updates_existing_endpoint():
    sm = FakeClient({"describe_endpoint": [{"EndpointStatus": "InService", "EndpointConfigName": "old"}, APPLIED]})
    aas = scaling_client()
    deploy.deploy(make_cfg(), IMAGE, sm, aas, FakeClient(), NOW)
    names = [c[0] for c in sm.calls]
    assert "update_endpoint" in names and "create_endpoint" not in names
    assert aas.calls[0][0] == "deregister_scalable_target"


def test_deploy_detects_rolled_back_update():
    # 갱신 실패 시 SageMaker는 이전 설정으로 되돌리고 InService + FailureReason을 남긴다
    rolled_back = {"EndpointStatus": "InService", "EndpointConfigName": "old", "FailureReason": "ping timeout"}
    sm = FakeClient({"describe_endpoint": [{"EndpointStatus": "InService", "EndpointConfigName": "old"}, rolled_back]})
    aas = scaling_client()
    with pytest.raises(deploy.DeploymentError, match="ping timeout"):
        deploy.deploy(make_cfg(), IMAGE, sm, aas, FakeClient(), NOW)
    # 롤백된 엔드포인트에도 오토스케일링은 다시 걸어 둔다
    assert "register_scalable_target" in [c[0] for c in aas.calls]


def test_deploy_recreates_failed_endpoint():
    sm = FakeClient({"describe_endpoint": [{"EndpointStatus": "Failed", "EndpointConfigName": "old"}, APPLIED]})
    deploy.deploy(make_cfg(), IMAGE, sm, scaling_client(), FakeClient(), NOW)
    names = [c[0] for c in sm.calls]
    assert "update_endpoint" not in names
    assert names.index("delete_endpoint") < names.index("create_endpoint")
    assert ("get_waiter", {"name": "endpoint_deleted"}) in sm.calls


def test_main_rejects_unfilled_config(capsys):
    assert deploy.main(["--env", "dev", "--image-uri", IMAGE]) == 2
    assert "bucket" in capsys.readouterr().err

import pytest

from runiverse_music.config import ConfigError, load_settings

LOCAL = {"GENERATOR": "fake", "STORAGE": "local", "LOCAL_OUTPUT_DIR": "/tmp/out"}


def test_defaults_for_s3():
    s = load_settings({"OUTPUT_BUCKET": "bucket-a"})
    assert s.generator == "acestep"
    assert s.storage == "s3"
    assert s.output_bucket == "bucket-a"
    assert s.output_prefix == "music/outputs"
    assert s.max_duration_seconds == 120.0
    assert s.inference_steps == 8
    assert s.offload_to_cpu is True
    assert s.audio_format == "wav"
    assert s.log_level == "INFO"
    assert s.model_dir.as_posix() == "/opt/ml/model"
    assert s.acestep_root.as_posix() == "/opt/ace-step"


def test_local_storage_settings():
    s = load_settings(LOCAL)
    assert s.generator == "fake"
    assert s.storage == "local"
    assert s.local_output_dir.as_posix() == "/tmp/out"
    assert s.output_bucket is None


def test_s3_requires_bucket():
    with pytest.raises(ConfigError, match="OUTPUT_BUCKET"):
        load_settings({})


def test_local_requires_dir():
    with pytest.raises(ConfigError, match="LOCAL_OUTPUT_DIR"):
        load_settings({"STORAGE": "local"})


def test_case_insensitive_choices():
    s = load_settings({**LOCAL, "GENERATOR": "FAKE", "OFFLOAD_TO_CPU": "False", "LOG_LEVEL": "debug"})
    assert s.generator == "fake"
    assert s.offload_to_cpu is False
    assert s.log_level == "DEBUG"


def test_prefix_slashes_trimmed():
    assert load_settings({**LOCAL, "OUTPUT_PREFIX": "/a/b/"}).output_prefix == "a/b"


@pytest.mark.parametrize(
    "key,value",
    [
        ("GENERATOR", "gpu"),
        ("STORAGE", "gcs"),
        ("OFFLOAD_TO_CPU", "yes"),
        ("MAX_DURATION_SECONDS", "5"),
        ("MAX_DURATION_SECONDS", "abc"),
        ("MAX_DURATION_SECONDS", "nan"),
        ("INFERENCE_STEPS", "0"),
        ("INFERENCE_STEPS", "8.5"),
        ("AUDIO_FORMAT", "mp3"),
        ("LOG_LEVEL", "LOUD"),
        ("OUTPUT_PREFIX", "/"),
    ],
)
def test_rejects_invalid_values(key, value):
    with pytest.raises(ConfigError, match=key):
        load_settings({**LOCAL, key: value})

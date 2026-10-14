import io
import json

import pytest
from botocore.exceptions import ClientError

import smoke_test as st
from runiverse_music.schemas import parse_request

NOT_FOUND = ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")


class FakeS3:
    def __init__(self, objects, appear_after=0):
        self.objects = objects
        self.appear_after = appear_after
        self.heads = 0

    def head_object(self, Bucket, Key):
        self.heads += 1
        path = f"{Bucket}/{Key}"
        if self.heads <= self.appear_after or path not in self.objects:
            raise NOT_FOUND
        return {"ContentLength": len(self.objects[path])}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[f"{Bucket}/{Key}"])}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


GOOD = {"sample_rate": 48000, "actual_duration_seconds": 30.0, "bytes": 100, "audio_s3_key": "music/outputs/r/r.wav"}


def test_build_request_is_valid():
    req = parse_request(json.dumps(st.build_request("smoke-1")).encode(), max_duration=120)
    assert (req.bpm, req.duration, req.seed) == (170, 30.0, 42)


def test_parse_s3_uri():
    assert st.parse_s3_uri("s3://bkt/a/b.json") == ("bkt", "a/b.json")
    with pytest.raises(ValueError):
        st.parse_s3_uri("https://x")


def test_check_response_ok():
    assert st.check_response(GOOD, 30.0) == []


@pytest.mark.parametrize(
    "over", [{"sample_rate": 44100}, {"actual_duration_seconds": 25.0}, {"bytes": 0}, {"audio_s3_key": "x.mp3"}]
)
def test_check_response_problems(over):
    assert len(st.check_response({**GOOD, **over}, 30.0)) == 1


def test_wait_success_after_polling():
    clock = Clock()
    s3 = FakeS3({"bkt/out.json": b'{"ok": 1}'}, appear_after=2)
    status, body = st.wait_for_result(s3, "s3://bkt/out.json", "s3://bkt/fail.json", 300, sleep=clock.sleep, clock=clock)
    assert (status, body) == ("success", '{"ok": 1}')
    assert clock.now > 0


def test_wait_failure():
    clock = Clock()
    s3 = FakeS3({"bkt/fail.json": b"boom"})
    assert st.wait_for_result(s3, "s3://bkt/out.json", "s3://bkt/fail.json", 300, sleep=clock.sleep, clock=clock) == ("failure", "boom")


def test_wait_timeout():
    clock = Clock()
    with pytest.raises(TimeoutError):
        st.wait_for_result(FakeS3({}), "s3://bkt/out.json", None, 30, interval=15, sleep=clock.sleep, clock=clock)

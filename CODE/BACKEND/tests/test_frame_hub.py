import numpy as np

from BACKEND.services.frame_hub import FrameHub


def test_frame_hub_keeps_only_latest_frame_per_job():
    hub = FrameHub(max_jobs=2)
    frame = np.zeros((32, 48, 3), dtype=np.uint8)
    assert hub.publish("job-a", frame) == 1
    assert hub.publish("job-a", frame) == 2
    snapshot = hub.snapshot("job-a")
    assert snapshot is not None
    assert snapshot.sequence == 2
    assert snapshot.jpeg.startswith(b"\xff\xd8")


def test_frame_hub_caps_job_memory():
    hub = FrameHub(max_jobs=2)
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    hub.publish("job-a", frame)
    hub.publish("job-b", frame)
    hub.publish("job-c", frame)
    assert hub.snapshot("job-a") is None
    assert hub.snapshot("job-b") is not None
    assert hub.snapshot("job-c") is not None

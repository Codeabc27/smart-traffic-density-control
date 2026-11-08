import csv
import threading
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from BACKEND import database
from BACKEND.config import settings
from BACKEND.services import job_manager as job_manager_module
from BACKEND.services import video_processor as video_processor_module
from BACKEND.services.job_manager import JobManager


class FakeBox:
    cls = np.array([2])
    conf = np.array([0.95])
    xyxy = np.array([[24, 2, 40, 12]], dtype=float)


class FakeModel:
    names = {2: "car"}

    def __init__(self):
        self.started = threading.Event()
        self.continue_processing = threading.Event()
        self.first_frame = True

    def predict(self, _frame, **_kwargs):
        if self.first_frame:
            self.first_frame = False
            self.started.set()
            assert self.continue_processing.wait(timeout=10)
        return [SimpleNamespace(boxes=[FakeBox()])]


def _create_video(path):
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        10,
        (64, 48),
    )
    assert writer.isOpened()
    for _ in range(5):
        writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
    writer.release()


def test_job_manager_runs_and_persists_video_analysis(tmp_path, monkeypatch):
    database_path = tmp_path / "traffic.db"
    monkeypatch.setattr(database, "DATABASE_PATH", database_path)
    monkeypatch.setattr(job_manager_module, "JOB_DIR", tmp_path / "jobs")
    monkeypatch.setattr(settings, "db_sample_every_frames", 1)
    monkeypatch.setattr(settings, "runtime_save_every_frames", 1)
    database.initialize_database()

    with database.database_connection() as db:
        db.execute(
            "INSERT INTO users(name,email,password_hash,created_at) VALUES(?,?,?,?)",
            ("Test User", "worker@example.com", "hash", "2026-01-01T00:00:00+00:00"),
        )
        user_id = db.execute(
            "SELECT id FROM users WHERE email=?", ("worker@example.com",)
        ).fetchone()["id"]

    input_video = tmp_path / "input.mp4"
    _create_video(input_video)
    model = FakeModel()
    monkeypatch.setattr(video_processor_module, "get_model", lambda: model)

    manager = JobManager()
    job_id = manager.create_job(user_id, input_video, {"frames": 5})
    assert model.started.wait(timeout=10)
    worker = manager.jobs[job_id]["thread"]

    try:
        with pytest.raises(RuntimeError, match="already running"):
            manager.create_job(user_id, input_video, {"frames": 5})
    finally:
        model.continue_processing.set()
        worker.join(timeout=30)

    assert not worker.is_alive()
    with database.database_connection() as db:
        job = db.execute(
            "SELECT status,output_video,result_csv,progress FROM analysis_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        rows = db.execute(
            "SELECT total_count,north_count FROM traffic_results WHERE job_id=?",
            (job_id,),
        ).fetchall()

    assert job["status"] == "completed"
    assert job["progress"] == 1
    assert len(rows) == 5
    assert all(row["total_count"] == 1 for row in rows)
    assert all(row["north_count"] == 1 for row in rows)

    with Path(job["result_csv"]).open(newline="", encoding="utf-8") as csv_file:
        csv_rows = list(csv.DictReader(csv_file))
    assert len(csv_rows) == 5
    assert csv_rows[0]["total_count"] == "1"

    output_video = Path(job["output_video"])
    capture = cv2.VideoCapture(str(output_video))
    try:
        assert capture.isOpened()
        fourcc = int(capture.get(cv2.CAP_PROP_FOURCC))
        codec = "".join(chr((fourcc >> (8 * index)) & 0xFF) for index in range(4))
        assert codec.lower() in {"avc1", "h264"}
        assert capture.read()[0]
    finally:
        capture.release()

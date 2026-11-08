from contextlib import contextmanager
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient

from BACKEND import database
from BACKEND.main import app


def test_processed_video_accepts_session_cookie(tmp_path):
    output_video = tmp_path / "processed.mp4"
    writer = cv2.VideoWriter(
        str(output_video),
        cv2.VideoWriter_fourcc(*"mp4v"),
        10,
        (64, 48),
    )
    assert writer.isOpened()
    for _ in range(3):
        writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
    writer.release()

    class FakeDatabase:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def execute(self, _query, _params):
            return self

        def fetchone(self):
            return {"status": "completed", "output_video": str(output_video)}

    @contextmanager
    def fake_database_connection():
        yield FakeDatabase()

    with (
        patch(
            "BACKEND.routes.results.get_user_from_token",
            return_value={"id": 7},
        ),
        patch(
            "BACKEND.routes.results.database_connection",
            fake_database_connection,
        ),
    ):
        client = TestClient(app)
        client.cookies.set("traffic_session", "session-token")
        response = client.get("/api/results/video?job_id=job-1")

    assert response.status_code == 200
    output_video.write_bytes(response.content)
    capture = cv2.VideoCapture(str(output_video))
    try:
        assert capture.isOpened()
        fourcc = int(capture.get(cv2.CAP_PROP_FOURCC))
        codec = "".join(chr((fourcc >> (8 * index)) & 0xFF) for index in range(4))
        assert codec.lower() in {"avc1", "h264"}
        assert capture.read()[0]
    finally:
        capture.release()


def _create_saved_job(job_id, user_id, status="completed"):
    from BACKEND.database import database_connection

    with database_connection() as db:
        db.execute(
            "INSERT INTO users(id,name,email,password_hash,created_at) "
            "VALUES(?,?,?,?,?)",
            (
                user_id,
                f"User {user_id}",
                f"user{user_id}@example.com",
                "hash",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        db.execute(
            "INSERT INTO analysis_jobs("
            "id,user_id,status,created_at,total_frames,runtime_json,updated_at"
            ") VALUES(?,?,?,?,?,?,?)",
            (
                job_id,
                user_id,
                status,
                "2026-01-01T00:00:00+00:00",
                1,
                "{}",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        db.execute(
            "INSERT INTO traffic_results(job_id,frame,timestamp,density) VALUES(?,?,?,?)",
            (job_id, 1, 0.1, "LOW"),
        )


def test_delete_saved_job_removes_only_owned_job_and_files(tmp_path, monkeypatch):
    database_path = tmp_path / "traffic.db"
    jobs_dir = tmp_path / "jobs"
    monkeypatch.setattr(database, "DATABASE_PATH", database_path)
    monkeypatch.setattr("BACKEND.routes.results.JOB_DIR", jobs_dir)
    database.initialize_database()

    job_id = "saved-job"
    _create_saved_job(job_id, user_id=7)
    job_dir = jobs_dir / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "processed.mp4").write_bytes(b"video")

    with patch(
        "BACKEND.routes.results.get_user_from_token",
        return_value={"id": 7},
    ):
        client = TestClient(app)
        client.cookies.set("traffic_session", "session-token")
        response = client.delete(f"/api/results/{job_id}")

    assert response.status_code == 200
    assert not job_dir.exists()
    with database.database_connection() as db:
        assert db.execute(
            "SELECT id FROM analysis_jobs WHERE id=?", (job_id,)
        ).fetchone() is None
        assert db.execute(
            "SELECT id FROM traffic_results WHERE job_id=?", (job_id,)
        ).fetchone() is None


def test_delete_saved_job_rejects_another_users_job(tmp_path, monkeypatch):
    database_path = tmp_path / "traffic.db"
    jobs_dir = tmp_path / "jobs"
    monkeypatch.setattr(database, "DATABASE_PATH", database_path)
    monkeypatch.setattr("BACKEND.routes.results.JOB_DIR", jobs_dir)
    database.initialize_database()
    _create_saved_job("other-users-job", user_id=8)
    job_dir = jobs_dir / "other-users-job"
    job_dir.mkdir(parents=True)
    (job_dir / "processed.mp4").write_bytes(b"video")

    with patch(
        "BACKEND.routes.results.get_user_from_token",
        return_value={"id": 7},
    ):
        client = TestClient(app)
        client.cookies.set("traffic_session", "session-token")
        response = client.delete("/api/results/other-users-job")

    assert response.status_code == 404
    assert job_dir.exists()


def test_delete_saved_job_rejects_active_analysis(tmp_path, monkeypatch):
    database_path = tmp_path / "traffic.db"
    monkeypatch.setattr(database, "DATABASE_PATH", database_path)
    monkeypatch.setattr("BACKEND.routes.results.JOB_DIR", tmp_path / "jobs")
    database.initialize_database()
    _create_saved_job("active-job", user_id=7, status="running")

    with patch(
        "BACKEND.routes.results.get_user_from_token",
        return_value={"id": 7},
    ):
        client = TestClient(app)
        client.cookies.set("traffic_session", "session-token")
        response = client.delete("/api/results/active-job")

    assert response.status_code == 409

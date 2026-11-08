import json
import secrets
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..config import JOB_DIR
from ..database import database_connection
from .frame_hub import frame_hub
from .video_processor import VideoProcessor


class JobManager:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.RLock()

    @staticmethod
    def safe_token():
        return secrets.token_hex(8)

    def recover_after_restart(self):
        with database_connection() as db:
            db.execute(
                "UPDATE analysis_jobs SET status='failed',"
                "error_message='Server restarted before this analysis completed.',"
                "completed_at=datetime('now'),updated_at=datetime('now') "
                "WHERE status IN ('queued','running')"
            )

    def has_active_job(self, user_id):
        with database_connection() as db:
            row = db.execute(
                "SELECT id FROM analysis_jobs "
                "WHERE user_id=? AND status IN ('queued','running') LIMIT 1",
                (user_id,),
            ).fetchone()
        return bool(row)

    def create_job(self, user_id, input_path, metadata=None):
        metadata = metadata or {}
        job_id = uuid4().hex
        job_dir = JOB_DIR / job_id
        job_input = job_dir / "input" / Path(input_path).name
        output_path = job_dir / "output" / "processed.mp4"
        csv_path = job_dir / "output" / "results.csv"
        runtime = {
            "status": "queued",
            "progress": 0.0,
            "frame": 0,
            "total_frames": metadata.get("frames", 0),
            "summary": {},
            "signal": {},
            "video": metadata,
        }

        job_input.parent.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(input_path, job_input)
        cancel_event = threading.Event()
        processor = VideoProcessor(
            job_id,
            job_input,
            output_path,
            csv_path,
            runtime,
            cancel_event,
            frame_hub,
        )
        record = {
            "processor": processor,
            "runtime": runtime,
            "user_id": user_id,
            "thread": None,
            "cancel_event": cancel_event,
        }
        thread = threading.Thread(
            target=self._run,
            args=(job_id,),
            daemon=True,
            name=f"traffic-job-{job_id[:8]}",
        )
        record["thread"] = thread

        inserted = False
        worker_started = False
        try:
            with self.lock:
                with database_connection() as db:
                    db.execute("BEGIN IMMEDIATE")
                    active = db.execute(
                        "SELECT id FROM analysis_jobs "
                        "WHERE user_id=? AND status IN ('queued','running') LIMIT 1",
                        (user_id,),
                    ).fetchone()
                    if active:
                        raise RuntimeError(
                            "An analysis is already running for this user."
                        )

                    now = datetime.now(timezone.utc).isoformat()
                    db.execute(
                        "INSERT INTO analysis_jobs("
                        "id,user_id,status,input_video,output_video,result_csv,"
                        "created_at,total_frames,runtime_json,updated_at"
                        ") VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            job_id,
                            user_id,
                            "queued",
                            str(job_input),
                            str(output_path),
                            str(csv_path),
                            now,
                            runtime["total_frames"],
                            json.dumps(runtime),
                            now,
                        ),
                    )
                inserted = True

                self.jobs[job_id] = record
                try:
                    thread.start()
                    worker_started = True
                except Exception:
                    self.jobs.pop(job_id, None)
                    raise
        except Exception as exc:
            if not inserted or not worker_started:
                if inserted:
                    with database_connection() as db:
                        db.execute(
                            "UPDATE analysis_jobs SET status='failed',"
                            "error_message=?,completed_at=datetime('now'),"
                            "updated_at=datetime('now') WHERE id=? "
                            "AND status IN ('queued','running')",
                            (str(exc), job_id),
                        )
                shutil.rmtree(job_dir, ignore_errors=True)
            raise

        return job_id

    def _run(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
        if not job:
            return
        try:
            job["processor"].run()
        except Exception as exc:
            job["runtime"].update({"status": "failed", "error": str(exc)})
            with database_connection() as db:
                db.execute(
                    "UPDATE analysis_jobs SET status='failed',error_message=?,"
                    "completed_at=datetime('now'),runtime_json=?,"
                    "updated_at=datetime('now') "
                    "WHERE id=? AND status NOT IN ('cancelled','completed')",
                    (str(exc), json.dumps(job["runtime"]), job_id),
                )
        finally:
            with self.lock:
                self.jobs.pop(job_id, None)

    def cancel(self, job_id, user_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job and job["user_id"] == user_id:
                job["processor"].cancel_event.set()
                return True

        with database_connection() as db:
            row = db.execute(
                "SELECT status FROM analysis_jobs WHERE id=? AND user_id=?",
                (job_id, user_id),
            ).fetchone()
            if not row or row["status"] not in {"queued", "running"}:
                return False
            db.execute(
                "UPDATE analysis_jobs SET status='cancelled',"
                "completed_at=datetime('now'),updated_at=datetime('now') WHERE id=?",
                (job_id,),
            )
        return True

    def get_runtime(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
        if job:
            return dict(job["runtime"])

        with database_connection() as db:
            row = db.execute(
                "SELECT runtime_json,status,progress,frame,total_frames,error_message "
                "FROM analysis_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
        if not row:
            return None

        try:
            data = json.loads(row["runtime_json"] or "{}")
        except json.JSONDecodeError:
            data = {}
        data.setdefault("status", row["status"])
        data.setdefault("progress", row["progress"])
        data.setdefault("frame", row["frame"])
        data.setdefault("total_frames", row["total_frames"])
        if row["error_message"]:
            data["error"] = row["error_message"]
        return data

    def public_status(self, job):
        runtime = self.get_runtime(job["id"]) or {}
        progress = job.get("progress") or runtime.get("progress") or 0
        return {
            "job": job,
            "runtime": runtime,
            "status": job["status"],
            "progress": round(float(progress) * 100, 1),
            "message": job.get("error_message") or runtime.get("error"),
        }


job_manager = JobManager()

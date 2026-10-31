import json
from fastapi import APIRouter, Cookie, HTTPException
from ..auth import COOKIE_NAME, get_user_from_token
from ..database import database_connection
from ..services.job_manager import job_manager

router = APIRouter(prefix="/api/signals", tags=["signals"])


def current_user(token):
    user = get_user_from_token(token)
    if not user:
        raise HTTPException(401, "Authentication required")
    return user


@router.get("/current")
def current_signal(traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    user = current_user(traffic_session)
    with database_connection() as db:
        active = db.execute(
            "SELECT * FROM analysis_jobs WHERE user_id=? AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1",
            (user["id"],),
        ).fetchone()
        completed = db.execute(
            "SELECT * FROM analysis_jobs WHERE user_id=? AND status='completed' ORDER BY completed_at DESC LIMIT 1",
            (user["id"],),
        ).fetchone()

    source = active or completed
    if not source:
        return {"job_id": None, "status": "idle", "signal": None, "summary": None, "updated_at": None}

    runtime = job_manager.get_runtime(source["id"]) or {}
    signal = runtime.get("signal") or {}
    summary = runtime.get("summary") or {}

    # If the job is no longer active and its runtime snapshot is unavailable,
    # fall back to the latest persisted traffic result for that user's job.
    if not signal:
        with database_connection() as db:
            row = db.execute(
                "SELECT * FROM traffic_results WHERE job_id=? ORDER BY frame DESC LIMIT 1",
                (source["id"],),
            ).fetchone()
        if row:
            signal = {
                "phase": row["selected_phase"],
                "state": row["signal_state"],
                "green_seconds": row["green_seconds"],
                "remaining_seconds": row["remaining_seconds"],
                "signals": {"NORTH": "RED", "EAST": "RED", "SOUTH": "RED", "WEST": "RED"},
                "queues": {
                    "NORTH": row["north_queue"], "EAST": row["east_queue"],
                    "SOUTH": row["south_queue"], "WEST": row["west_queue"],
                },
            }
            if signal["phase"]:
                signal["signals"][signal["phase"]] = signal["state"] or "RED"
            summary = {
                "total": row["total_count"],
                "density": row["density"],
                "approaches": {
                    "NORTH": row["north_count"], "EAST": row["east_count"],
                    "SOUTH": row["south_count"], "WEST": row["west_count"],
                },
                "density_ratio": {
                    "NORTH": row["north_density"], "EAST": row["east_density"],
                    "SOUTH": row["south_density"], "WEST": row["west_density"],
                },
            }

    return {
        "job_id": source["id"],
        "status": source["status"],
        "signal": signal,
        "summary": summary,
        "updated_at": runtime.get("updated_at") or source["updated_at"],
    }

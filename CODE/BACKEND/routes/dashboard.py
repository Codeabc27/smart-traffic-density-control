from fastapi import APIRouter, Cookie, HTTPException

from ..auth import COOKIE_NAME, get_user_from_token
from ..database import database_connection

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def user_from_cookie(token):
    user = get_user_from_token(token)
    if not user:
        raise HTTPException(401, "Authentication required")
    return user


def row_dict(row):
    return dict(row) if row else None


@router.get("/summary")
def summary(traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    user = user_from_cookie(traffic_session)
    with database_connection() as db:
        active = db.execute(
            "SELECT * FROM analysis_jobs WHERE user_id=? AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1",
            (user["id"],),
        ).fetchone()
        latest = db.execute(
            "SELECT * FROM analysis_jobs WHERE user_id=? AND status='completed' ORDER BY completed_at DESC LIMIT 1",
            (user["id"],),
        ).fetchone()

        source_job = active or latest
        payload = {
            "active_job": row_dict(active),
            "latest_completed": row_dict(latest),
            "current": None,
            "trend": [],
            "model": None,
        }

        if source_job:
            result = db.execute(
                "SELECT * FROM traffic_results WHERE job_id=? ORDER BY frame DESC LIMIT 1",
                (source_job["id"],),
            ).fetchone()
            if result:
                payload["current"] = dict(result)
            trend = db.execute(
                "SELECT frame,timestamp,car_count,motorcycle_count,bus_count,truck_count,total_count,density,north_count,east_count,south_count,west_count,north_density,east_density,south_density,west_density,selected_phase,signal_state,green_seconds,remaining_seconds,queue_proxy,north_queue,east_queue,south_queue,west_queue FROM traffic_results WHERE job_id=? ORDER BY frame DESC LIMIT 180",
                (source_job["id"],),
            ).fetchall()
            payload["trend"] = [dict(x) for x in reversed(trend)]
            payload["current_job_id"] = source_job["id"]

        from ..config import resolve_model_path

        model_path = resolve_model_path()
        payload["model"] = {"ready": model_path.exists(), "path": str(model_path)}
        return payload


@router.get("/trends")
def trends(traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    user = user_from_cookie(traffic_session)
    with database_connection() as db:
        job = db.execute(
            "SELECT id FROM analysis_jobs WHERE user_id=? AND status='completed' ORDER BY completed_at DESC LIMIT 1",
            (user["id"],),
        ).fetchone()
        if not job:
            return {"job_id": None, "rows": []}
        rows = db.execute(
            "SELECT * FROM traffic_results WHERE job_id=? ORDER BY frame", (job["id"],)
        ).fetchall()
    return {"job_id": job["id"], "rows": [dict(r) for r in rows]}

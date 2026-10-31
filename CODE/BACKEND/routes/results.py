from pathlib import Path
from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import FileResponse
from ..auth import COOKIE_NAME, get_user_from_token
from ..database import database_connection

router = APIRouter(prefix="/api/results", tags=["results"])

def user(token):
    u = get_user_from_token(token)
    if not u:
        raise HTTPException(401, "Authentication required")
    return u

def ensure_completed(job):
    if job["status"] != "completed":
        raise HTTPException(409, f"Results are not available because this job is {job['status']}.")

@router.get("/recent")
def recent(traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    u = user(traffic_session)
    with database_connection() as db:
        jobs = db.execute("SELECT id,status,created_at,started_at,completed_at,error_message,progress,frame,total_frames FROM analysis_jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 20", (u["id"],)).fetchall()
        output=[]
        for job in jobs:
            item=dict(job)
            row=db.execute("SELECT * FROM traffic_results WHERE job_id=? ORDER BY frame DESC LIMIT 1", (job["id"],)).fetchone()
            if row:
                item.update({"total_vehicles": row["total_count"], "density": row["density"], "phase": row["selected_phase"], "signal_state": row["signal_state"]})
            else:
                item.update({"total_vehicles": None, "density": None, "phase": None, "signal_state": None})
            output.append(item)
    return {"jobs": output}


@router.get("/history")
def history(traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    u = user(traffic_session)
    with database_connection() as db:
        jobs = db.execute(
            "SELECT id,status,input_video,created_at,started_at,completed_at,error_message,progress,frame,total_frames FROM analysis_jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 30",
            (u["id"],),
        ).fetchall()
        output = []
        for job in jobs:
            item = dict(job)
            input_path = Path(item.get("input_video") or "")
            item["original_filename"] = input_path.name or "Unknown video"
            row = db.execute(
                "SELECT * FROM traffic_results WHERE job_id=? ORDER BY frame DESC LIMIT 1",
                (job["id"],),
            ).fetchone()
            if row:
                item.update({
                    "final_phase": row["selected_phase"],
                    "final_signal_state": row["signal_state"],
                    "final_density": row["density"],
                    "final_vehicle_count": row["total_count"],
                    "final_remaining_seconds": row["remaining_seconds"],
                })
                avg = db.execute(
                    "SELECT AVG(total_count) AS average_vehicle_count FROM traffic_results WHERE job_id=?",
                    (job["id"],),
                ).fetchone()
                item["average_vehicle_count"] = round(float(avg["average_vehicle_count"] or 0), 2)
            else:
                item.update({
                    "final_phase": None, "final_signal_state": None,
                    "final_density": None, "final_vehicle_count": None,
                    "final_remaining_seconds": None, "average_vehicle_count": None,
                })
            output.append(item)
    return {"runs": output}

@router.get("/video")
def video(job_id: str, traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    u=user(traffic_session)
    with database_connection() as db:
        row=db.execute("SELECT status,output_video FROM analysis_jobs WHERE id=? AND user_id=?", (job_id,u["id"])).fetchone()
    if not row:
        raise HTTPException(404,"Analysis job not found")
    ensure_completed(row)
    path=Path(row["output_video"] or "")
    if not path.exists():
        raise HTTPException(404,"Processed video is not available")
    return FileResponse(path, media_type="video/mp4", filename="processed_traffic.mp4")

@router.get("/csv")
def csv_file(job_id: str, traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    u=user(traffic_session)
    with database_connection() as db:
        row=db.execute("SELECT status,result_csv FROM analysis_jobs WHERE id=? AND user_id=?", (job_id,u["id"])).fetchone()
    if not row:
        raise HTTPException(404,"Analysis job not found")
    ensure_completed(row)
    path=Path(row["result_csv"] or "")
    if not path.exists():
        raise HTTPException(404,"CSV is not available")
    return FileResponse(path, media_type="text/csv", filename="traffic_results.csv")

@router.get("/{job_id}")
def detail(job_id: str, traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    u=user(traffic_session)
    with database_connection() as db:
        job=db.execute("SELECT * FROM analysis_jobs WHERE id=? AND user_id=?",(job_id,u["id"])).fetchone()
        if not job:
            raise HTTPException(404,"Result not found")
        rows=db.execute("SELECT * FROM traffic_results WHERE job_id=? ORDER BY frame",(job_id,)).fetchall()
    data=[dict(r) for r in rows]
    latest=data[-1] if data else None
    max_row=max(data,key=lambda r:r["total_count"]) if data else None
    averages={}
    if data:
        for key in ("car_count","motorcycle_count","bus_count","truck_count","total_count","north_count","east_count","south_count","west_count"):
            averages[key]=round(sum(float(r[key]) for r in data)/len(data),2)
    step=max(1,len(data)//300) if data else 1
    return {
        "job":dict(job),
        "metrics":latest,
        "max_vehicles": max_row["total_count"] if max_row else 0,
        "average_metrics": averages,
        "vehicle_types": {
            "car": round(sum(r["car_count"] for r in data)/len(data),2) if data else 0,
            "motorcycle": round(sum(r["motorcycle_count"] for r in data)/len(data),2) if data else 0,
            "bus": round(sum(r["bus_count"] for r in data)/len(data),2) if data else 0,
            "truck": round(sum(r["truck_count"] for r in data)/len(data),2) if data else 0,
        },
        "rows": data[::step],
    }

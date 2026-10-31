from pathlib import Path
import asyncio
import cv2
from fastapi import APIRouter, UploadFile, File, HTTPException, Cookie
from fastapi.responses import StreamingResponse, Response
from ..auth import COOKIE_NAME, get_user_from_token
from ..config import UPLOAD_DIR, SUPPORTED_VIDEO_EXTENSIONS, settings
from ..services.job_manager import job_manager
from ..services.frame_hub import frame_hub
from ..database import database_connection

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def user_from_cookie(token):
    user = get_user_from_token(token)
    if not user:
        raise HTTPException(401, "Authentication required")
    return user


def validate_video_readable(path: Path):
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise HTTPException(400, "The selected file is not a readable video.")
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if width <= 0 or height <= 0:
            raise HTTPException(400, "The video has invalid dimensions.")
        return {"width": width, "height": height, "fps": fps, "frames": frames}
    finally:
        cap.release()


@router.post("/upload")
async def upload(file: UploadFile = File(...), traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    user = user_from_cookie(traffic_session)
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if not filename:
        raise HTTPException(400, "Please select a video file")
    if suffix not in SUPPORTED_VIDEO_EXTENSIONS:
        raise HTTPException(400, "Unsupported video format. Use MP4, AVI, MOV, MKV, WEBM or M4V.")
    if job_manager.has_active_job(user["id"]):
        raise HTTPException(409, "An analysis is already running. Wait for it to finish or cancel it.")
    temp_path = UPLOAD_DIR / f"incoming_{user['id']}_{job_manager.safe_token()}{suffix}"
    size = 0
    try:
        with temp_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_size_mb * 1024 * 1024:
                    raise HTTPException(413, f"Video exceeds {settings.max_upload_size_mb} MB limit")
                out.write(chunk)
        metadata = validate_video_readable(temp_path)
        job_id = job_manager.create_job(user["id"], temp_path, metadata)
        return {"job_id": job_id, "status": "queued", "message": "Analysis queued successfully", "video": metadata}
    finally:
        temp_path.unlink(missing_ok=True)


@router.get("/jobs/active")
def active_job(traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    user = user_from_cookie(traffic_session)
    with database_connection() as db:
        row = db.execute(
            "SELECT * FROM analysis_jobs WHERE user_id=? AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1",
            (user["id"],),
        ).fetchone()
    if not row:
        return {"active": None}
    return {"active": job_manager.public_status(dict(row))}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    user = user_from_cookie(traffic_session)
    with database_connection() as db:
        row = db.execute("SELECT * FROM analysis_jobs WHERE id=? AND user_id=?", (job_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Analysis job not found")
    return job_manager.public_status(dict(row))


@router.get("/jobs/{job_id}/latest-frame")
def latest_frame(job_id: str, traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    user = user_from_cookie(traffic_session)
    with database_connection() as db:
        row = db.execute("SELECT id,status FROM analysis_jobs WHERE id=? AND user_id=?", (job_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Analysis job not found")
    snapshot = frame_hub.snapshot(job_id)
    if not snapshot:
        raise HTTPException(404, "No processed frame is available yet")
    return Response(content=snapshot.jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/jobs/{job_id}/stream")
async def stream_frames(job_id: str, traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    user = user_from_cookie(traffic_session)
    with database_connection() as db:
        row = db.execute("SELECT id,status FROM analysis_jobs WHERE id=? AND user_id=?", (job_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Analysis job not found")

    async def generator():
        last_sequence = 0
        idle_started = asyncio.get_running_loop().time()
        while True:
            snapshot = frame_hub.snapshot(job_id)
            if snapshot and snapshot.sequence != last_sequence:
                last_sequence = snapshot.sequence
                idle_started = asyncio.get_running_loop().time()
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Cache-Control: no-store\r\n\r\n"
                    + snapshot.jpeg
                    + b"\r\n"
                )

            with database_connection() as db:
                status_row = db.execute("SELECT status FROM analysis_jobs WHERE id=? AND user_id=?", (job_id, user["id"])).fetchone()
            if not status_row:
                break
            status = status_row["status"]
            if status in {"completed", "failed", "cancelled"}:
                # Keep the final annotated frame available to the browser, then end the stream.
                if snapshot and snapshot.sequence == last_sequence:
                    await asyncio.sleep(0.8)
                break

            if asyncio.get_running_loop().time() - idle_started > 30:
                # Prevent an abandoned connection from living forever when processing is stalled.
                idle_started = asyncio.get_running_loop().time()
            await asyncio.sleep(0.12)

    return StreamingResponse(
        generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    user = user_from_cookie(traffic_session)
    result = job_manager.cancel(job_id, user["id"])
    if not result:
        raise HTTPException(404, "Analysis job not found or already finished")
    return {"message": "Cancellation requested", "job_id": job_id, "status": "cancelled"}

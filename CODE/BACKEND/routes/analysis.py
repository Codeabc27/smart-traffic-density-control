import asyncio
from pathlib import Path
from typing import Annotated

import cv2
from fastapi import APIRouter, Cookie, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse

from ..auth import COOKIE_NAME, get_user_from_token
from ..config import SUPPORTED_VIDEO_EXTENSIONS, UPLOAD_DIR, settings
from ..database import database_connection
from ..services.frame_hub import frame_hub
from ..services.job_manager import job_manager

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def user_from_cookie(token):
    user = get_user_from_token(token)
    if not user:
        raise HTTPException(401, "Authentication required")
    return user


def validate_video_readable(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size == 0:
        raise HTTPException(
            status_code=400, detail="The uploaded file is empty or does not exist."
        )

    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise HTTPException(
                status_code=400, detail="The selected file is not a readable video."
            )

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        if width <= 0 or height <= 0:
            raise HTTPException(
                status_code=400, detail="The video has invalid dimensions."
            )

        # Read the first frame to verify actual codec decodability
        success, first_frame = cap.read()
        if not success or first_frame is None or first_frame.size == 0:
            raise HTTPException(
                status_code=400, detail="Unable to decode any frames from the video."
            )

        # Fallback for FPS if missing or corrupt in headers
        if fps <= 0 or fps > 120:
            fps = 30.0  # safe default fallback

        # If header frame count is unavailable, ensure at least 1 frame is reported
        if frames <= 0:
            frames = 1

        return {
            "width": width,
            "height": height,
            "fps": round(fps, 2),
            "frames": frames,
        }
    finally:
        cap.release()


@router.post("/upload")
async def upload(
    file: Annotated[UploadFile, File()],
    traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    user = user_from_cookie(traffic_session)
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if not filename:
        raise HTTPException(400, "Please select a video file")
    if suffix not in SUPPORTED_VIDEO_EXTENSIONS:
        raise HTTPException(
            400, "Unsupported video format. Use MP4, AVI, MOV, MKV, WEBM or M4V."
        )
    if job_manager.has_active_job(user["id"]):
        raise HTTPException(
            409, "An analysis is already running. Wait for it to finish or cancel it."
        )
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
                    raise HTTPException(
                        413, f"Video exceeds {settings.max_upload_size_mb} MB limit"
                    )
                out.write(chunk)
        metadata = validate_video_readable(temp_path)
        try:
            job_id = job_manager.create_job(user["id"], temp_path, metadata)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {
            "job_id": job_id,
            "status": "queued",
            "message": "Analysis queued successfully",
            "video": metadata,
        }
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
def job_status(
    job_id: str, traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)
):
    user = user_from_cookie(traffic_session)
    with database_connection() as db:
        row = db.execute(
            "SELECT * FROM analysis_jobs WHERE id=? AND user_id=?", (job_id, user["id"])
        ).fetchone()
    if not row:
        raise HTTPException(404, "Analysis job not found")
    return job_manager.public_status(dict(row))


@router.get("/jobs/{job_id}/latest-frame")
def latest_frame(
    job_id: str, traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)
):
    user = user_from_cookie(traffic_session)
    with database_connection() as db:
        row = db.execute(
            "SELECT id,status FROM analysis_jobs WHERE id=? AND user_id=?",
            (job_id, user["id"]),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Analysis job not found")
    snapshot = frame_hub.snapshot(job_id)
    if not snapshot:
        raise HTTPException(404, "No processed frame is available yet")
    return Response(
        content=snapshot.jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/jobs/{job_id}/stream")
async def stream_frames(
    job_id: str, traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)
):
    user = user_from_cookie(traffic_session)
    with database_connection() as db:
        row = db.execute(
            "SELECT id,status FROM analysis_jobs WHERE id=? AND user_id=?",
            (job_id, user["id"]),
        ).fetchone()
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
                    b"Cache-Control: no-store\r\n\r\n" + snapshot.jpeg + b"\r\n"
                )

            with database_connection() as db:
                status_row = db.execute(
                    "SELECT status FROM analysis_jobs WHERE id=? AND user_id=?",
                    (job_id, user["id"]),
                ).fetchone()
            if not status_row:
                break
            status = status_row["status"]
            if status in {"completed", "failed", "cancelled"}:
                # Keep the final annotated frame available to the browser, then end the stream.
                if snapshot and snapshot.sequence == last_sequence:
                    await asyncio.sleep(0.8)
                break

            if asyncio.get_running_loop().time() - idle_started > 30:
                break
            await asyncio.sleep(0.12)

    return StreamingResponse(
        generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/jobs/{job_id}/cancel")
def cancel(
    job_id: str, traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)
):
    user = user_from_cookie(traffic_session)
    result = job_manager.cancel(job_id, user["id"])
    if not result:
        raise HTTPException(404, "Analysis job not found or already finished")
    return {
        "message": "Cancellation requested",
        "job_id": job_id,
        "status": "cancelled",
    }

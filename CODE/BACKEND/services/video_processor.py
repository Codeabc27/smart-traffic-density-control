import csv
import json
import os
import shutil
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import cv2

from ..config import (
    APPROACH_ROIS,
    VEHICLE_CLASSES,
    resolve_model_path,
    settings,
)
from ..database import database_connection
from .signal_optimizer import SignalController
from .traffic_analyzer import (
    assign_approach,
    is_near_queue_zone,
    queue_proxy_for_detections,
    summarize,
)

try:
    import imageio_ffmpeg
except ImportError:
    imageio_ffmpeg = None


_MODEL_CACHE = {}
_MODEL_LOCK = threading.Lock()
_VIDEO_CONVERSION_LOCK = threading.Lock()
CSV_FIELDS = (
    "frame",
    "timestamp",
    "car_count",
    "motorcycle_count",
    "bus_count",
    "truck_count",
    "total_count",
    "density",
    "north_count",
    "east_count",
    "south_count",
    "west_count",
    "north_density",
    "east_density",
    "south_density",
    "west_density",
    "selected_phase",
    "signal_state",
    "green_seconds",
    "remaining_seconds",
    "queue_proxy",
    "north_queue",
    "east_queue",
    "south_queue",
    "west_queue",
)


CSVField = Literal[
    "frame",
    "timestamp",
    "car_count",
    "motorcycle_count",
    "bus_count",
    "truck_count",
    "total_count",
    "density",
    "north_count",
    "east_count",
    "south_count",
    "west_count",
    "north_density",
    "east_density",
    "south_density",
    "west_density",
    "selected_phase",
    "signal_state",
    "green_seconds",
    "remaining_seconds",
    "queue_proxy",
    "north_queue",
    "east_queue",
    "south_queue",
    "west_queue",
]
CSVRow = dict[CSVField, Any]


def get_model():
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError(
            "Ultralytics is not installed. Install the project requirements."
        ) from exc

    path = resolve_model_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"YOLO model not found: {path}. Place yolov8n.pt in MODELS "
            "or set MODEL_PATH in .env."
        )

    key = str(path.resolve())
    with _MODEL_LOCK:
        if key not in _MODEL_CACHE:
            _MODEL_CACHE[key] = YOLO(str(path))
        return _MODEL_CACHE[key]


def convert_to_web_h264(input_path: Path, output_path: Path):
    ffmpeg_exe = shutil.which("ffmpeg")
    if not ffmpeg_exe and imageio_ffmpeg is not None:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    if not ffmpeg_exe:
        raise RuntimeError(
            "FFmpeg is required to encode browser-compatible processed videos."
        )

    temp_output = output_path.with_name(f"{output_path.stem}.tmp.mp4")
    cmd = [
        str(ffmpeg_exe),
        "-y",
        "-i",
        str(input_path),
        "-vcodec",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-movflags",
        "+faststart",
        str(temp_output),
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        if not temp_output.is_file() or temp_output.stat().st_size == 0:
            raise RuntimeError("FFmpeg produced an empty processed video.")
        os.replace(temp_output, output_path)
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        temp_output.unlink(missing_ok=True)
        detail = getattr(exc, "stderr", None) or str(exc)
        raise RuntimeError(
            f"Failed to convert processed video to H.264: {detail}"
        ) from exc


def ensure_web_h264(video_path: Path):
    with _VIDEO_CONVERSION_LOCK:
        capture = cv2.VideoCapture(str(video_path))
        try:
            if not capture.isOpened():
                raise RuntimeError("The processed video cannot be opened.")
            fourcc = int(capture.get(cv2.CAP_PROP_FOURCC))
        finally:
            capture.release()

        codec = "".join(chr((fourcc >> (8 * index)) & 0xFF) for index in range(4))
        if codec.lower() not in {"avc1", "h264"}:
            convert_to_web_h264(video_path, video_path)


class VideoProcessor:
    def __init__(
        self,
        job_id,
        input_path,
        output_path,
        csv_path,
        runtime_state,
        cancel_event,
        frame_hub=None,
        model=None,
    ):
        self.job_id = job_id
        self.input_path = Path(input_path)
        self.output_path = Path(output_path)
        self.csv_path = Path(csv_path)
        self.runtime_state = runtime_state
        self.cancel_event = cancel_event
        self.frame_hub = frame_hub
        self.model = model

    def _persist_runtime(self, row):
        updated_at = datetime.now(UTC).isoformat()
        self.runtime_state["updated_at"] = updated_at
        with database_connection() as db:
            db.execute(
                "UPDATE analysis_jobs SET progress=?,frame=?,total_frames=?,runtime_json=?,updated_at=? WHERE id=?",
                (
                    row.get("progress", 0),
                    row.get("frame", 0),
                    row.get("total_frames", 0),
                    json.dumps(row),
                    updated_at,
                    self.job_id,
                ),
            )

    def _insert_result(self, db, row: CSVRow):
        db.execute(
            """
            INSERT INTO traffic_results(
                job_id,frame,timestamp,car_count,motorcycle_count,bus_count,
                truck_count,total_count,density,north_count,east_count,
                south_count,west_count,north_density,east_density,south_density,
                west_density,selected_phase,signal_state,green_seconds,
                remaining_seconds,queue_proxy,north_queue,east_queue,south_queue,
                west_queue
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.job_id,
                *(row[field] for field in CSV_FIELDS),
            ),
        )

    @staticmethod
    def _extract_detections(result, model, width, height):
        detections = []
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return detections

        names = model.names
        for box in boxes:
            class_id = int(box.cls[0].item())
            class_name = str(
                names.get(class_id, class_id)
                if isinstance(names, dict)
                else names[class_id]
            ).lower()
            if class_name not in VEHICLE_CLASSES:
                continue
            confidence = float(box.conf[0].item())
            x1, y1, x2, y2 = [int(value) for value in box.xyxy[0].tolist()]
            center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
            approach = assign_approach(center_x, center_y, width, height)
            detections.append(
                {
                    "class_name": class_name,
                    "confidence": confidence,
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "approach": approach,
                    "near_queue_zone": is_near_queue_zone(
                        center_x, center_y, approach, width, height
                    ),
                }
            )
        return detections

    def run(self):
        capture = None
        video_writer = None
        raw_path = self.output_path.with_name(f"{self.output_path.stem}.raw.mp4")
        completed = False

        try:
            model = self.model or get_model()
            capture = cv2.VideoCapture(str(self.input_path))
            if not capture.isOpened():
                raise RuntimeError("Unable to open the uploaded video.")

            fps = float(capture.get(cv2.CAP_PROP_FPS) or 25.0)
            if fps <= 0 or fps > 120:
                fps = 25.0
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if width <= 0 or height <= 0:
                raise RuntimeError("The video has invalid dimensions.")

            raw_path.parent.mkdir(parents=True, exist_ok=True)
            fourcc = cv2.VideoWriter.fourcc(*"mp4v")
            video_writer = cv2.VideoWriter(str(raw_path), fourcc, fps, (width, height))
            if not video_writer.isOpened():
                raise RuntimeError(
                    "Unable to create the processed output video. "
                    "Try an MP4/AVI input with a supported codec."
                )

            controller = SignalController()
            self.runtime_state.update(
                {
                    "status": "running",
                    "progress": 0.0,
                    "frame": 0,
                    "total_frames": total_frames,
                }
            )
            started_at = datetime.now(UTC).isoformat()
            with database_connection() as db:
                db.execute(
                    "UPDATE analysis_jobs SET status='running',started_at=?,"
                    "progress=0,frame=0,total_frames=?,runtime_json=?,updated_at=? "
                    "WHERE id=? AND status='queued'",
                    (
                        started_at,
                        total_frames,
                        json.dumps(self.runtime_state),
                        started_at,
                        self.job_id,
                    ),
                )

            previous_time = 0.0
            latest_row: CSVRow | None = None
            db_sample_every = max(1, settings.db_sample_every_frames)
            runtime_save_every = max(1, settings.runtime_save_every_frames)
            with self.csv_path.open("w", newline="", encoding="utf-8") as csv_file:
                csv_writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
                csv_writer.writeheader()
                frame_index = 0

                while not self.cancel_event.is_set():
                    ok, frame = capture.read()
                    if not ok:
                        break

                    video_time = frame_index / fps
                    delta = max(0.0, video_time - previous_time)
                    previous_time = video_time
                    result_list = model.predict(
                        frame,
                        conf=settings.vehicle_confidence,
                        verbose=False,
                    )
                    result = result_list[0] if result_list else None
                    detections = (
                        self._extract_detections(result, model, width, height)
                        if result is not None
                        else []
                    )
                    summary = summarize(detections, width, height)
                    queues = queue_proxy_for_detections(detections, width, height)
                    signal = controller.update(
                        summary["approaches"], queues, video_time, delta
                    )

                    for direction, roi in APPROACH_ROIS.items():
                        x1, y1 = int(roi["x1"] * width), int(roi["y1"] * height)
                        x2, y2 = int(roi["x2"] * width), int(roi["y2"] * height)
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (180, 180, 180), 1)
                        cv2.putText(
                            frame,
                            direction,
                            (x1 + 8, y1 + 22),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.55,
                            (230, 230, 230),
                            2,
                        )

                    colors = {
                        "car": (0, 220, 80),
                        "motorcycle": (255, 170, 0),
                        "bus": (0, 180, 255),
                        "truck": (180, 60, 255),
                    }
                    for detection in detections:
                        color = colors[detection["class_name"]]
                        cv2.rectangle(
                            frame,
                            (detection["x1"], detection["y1"]),
                            (detection["x2"], detection["y2"]),
                            color,
                            2,
                        )
                        cv2.putText(
                            frame,
                            f"{detection['class_name']} {detection['confidence']:.2f}",
                            (detection["x1"], max(20, detection["y1"] - 7)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5,
                            color,
                            2,
                        )

                    overlay = frame.copy()
                    cv2.rectangle(overlay, (10, 10), (500, 125), (15, 25, 40), -1)
                    frame = cv2.addWeighted(overlay, 0.76, frame, 0.24, 0)
                    cv2.putText(
                        frame,
                        f"PHASE: {signal['phase'] or '-'}  {signal['state']}",
                        (20, 38),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (255, 255, 255),
                        2,
                    )
                    cv2.putText(
                        frame,
                        f"VEHICLES IN FRAME: {summary['total']}  DENSITY: {summary['density']}",
                        (20, 66),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.55,
                        (255, 255, 255),
                        2,
                    )
                    cv2.putText(
                        frame,
                        f"GREEN: {signal['green_seconds']:.0f}s  REMAINING: {signal['remaining_seconds']:.0f}s",
                        (20, 92),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.55,
                        (220, 235, 255),
                        2,
                    )
                    cv2.putText(
                        frame,
                        "QUEUE = ESTIMATED OCCUPANCY PROXY",
                        (20, 115),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.42,
                        (210, 210, 210),
                        1,
                    )
                    video_writer.write(frame)
                    if self.frame_hub is not None:
                        self.frame_hub.publish(self.job_id, frame)

                    latest_row = {
                        "frame": frame_index + 1,
                        "timestamp": round(video_time, 3),
                        "car_count": summary["types"]["car"],
                        "motorcycle_count": summary["types"]["motorcycle"],
                        "bus_count": summary["types"]["bus"],
                        "truck_count": summary["types"]["truck"],
                        "total_count": summary["total"],
                        "density": summary["density"],
                        "north_count": summary["approaches"]["NORTH"],
                        "east_count": summary["approaches"]["EAST"],
                        "south_count": summary["approaches"]["SOUTH"],
                        "west_count": summary["approaches"]["WEST"],
                        "north_density": summary["density_ratio"]["NORTH"],
                        "east_density": summary["density_ratio"]["EAST"],
                        "south_density": summary["density_ratio"]["SOUTH"],
                        "west_density": summary["density_ratio"]["WEST"],
                        "selected_phase": signal["phase"],
                        "signal_state": signal["state"],
                        "green_seconds": signal["green_seconds"],
                        "remaining_seconds": signal["remaining_seconds"],
                        "queue_proxy": max(queues.values(), default=0.0),
                        "north_queue": queues["NORTH"],
                        "east_queue": queues["EAST"],
                        "south_queue": queues["SOUTH"],
                        "west_queue": queues["WEST"],
                    }
                    csv_writer.writerow(latest_row)
                    frame_index += 1
                    self.runtime_state.update(
                        {
                            "status": "running",
                            "progress": min(1.0, frame_index / max(1, total_frames)),
                            "frame": frame_index,
                            "total_frames": total_frames,
                            "summary": summary,
                            "signal": signal,
                            "updated_at": video_time,
                        }
                    )

                    if frame_index % db_sample_every == 0:
                        with database_connection() as db:
                            self._insert_result(db, latest_row)
                    if frame_index % runtime_save_every == 0:
                        self._persist_runtime(self.runtime_state)

            if self.cancel_event.is_set():
                self.runtime_state["status"] = "cancelled"
                with database_connection() as db:
                    db.execute(
                        "UPDATE analysis_jobs SET status='cancelled',completed_at=datetime('now'),progress=?,frame=?,runtime_json=?,updated_at=datetime('now') WHERE id=?",
                        (
                            self.runtime_state.get("progress", 0),
                            self.runtime_state.get("frame", 0),
                            json.dumps(self.runtime_state),
                            self.job_id,
                        ),
                    )
                return

            if latest_row is None:
                raise RuntimeError("No readable frames were found in the video.")

            video_writer.release()
            video_writer = None
            capture.release()
            capture = None
            convert_to_web_h264(raw_path, self.output_path)

            if latest_row["frame"] % db_sample_every:
                with database_connection() as db:
                    self._insert_result(db, latest_row)

            self.runtime_state.update(
                {
                    "status": "completed",
                    "progress": 1.0,
                    "frame": latest_row["frame"],
                    "total_frames": total_frames or latest_row["frame"],
                }
            )
            with database_connection() as db:
                self._persist_runtime_in_connection(db, self.runtime_state)
                db.execute(
                    "UPDATE analysis_jobs SET status='completed',output_video=?,result_csv=?,completed_at=datetime('now'),progress=1.0,frame=?,total_frames=?,runtime_json=?,updated_at=datetime('now') WHERE id=?",
                    (
                        str(self.output_path),
                        str(self.csv_path),
                        latest_row["frame"],
                        total_frames or latest_row["frame"],
                        json.dumps(self.runtime_state),
                        self.job_id,
                    ),
                )
            completed = True
        finally:
            if capture is not None:
                capture.release()
            if video_writer is not None:
                video_writer.release()
            raw_path.unlink(missing_ok=True)
            if not completed and self.runtime_state.get("status") == "cancelled":
                self.output_path.unlink(missing_ok=True)
                self.csv_path.unlink(missing_ok=True)

    def _persist_runtime_in_connection(self, db, row):
        updated_at = datetime.now(UTC).isoformat()
        row["updated_at"] = updated_at
        db.execute(
            "UPDATE analysis_jobs SET progress=?,frame=?,total_frames=?,runtime_json=?,updated_at=? WHERE id=?",
            (
                row.get("progress", 0),
                row.get("frame", 0),
                row.get("total_frames", 0),
                json.dumps(row),
                updated_at,
                self.job_id,
            ),
        )

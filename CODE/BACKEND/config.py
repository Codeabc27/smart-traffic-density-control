from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_BASE_DIR = PROJECT_ROOT / "DATA_BASE"
DATABASE_DIR = DATA_BASE_DIR / "database"
UPLOAD_DIR = DATA_BASE_DIR / "uploads"
JOB_DIR = DATA_BASE_DIR / "jobs"
MODELS_DIR = PROJECT_ROOT / "MODELS"
DATABASE_PATH = DATABASE_DIR / "traffic.db"

class Settings(BaseSettings):
    app_name: str = "AI Traffic Signal Control"
    app_version: str = "2.0.0"
    environment: str = "development"
    session_expire_hours: int = 24
    model_path: str = str(MODELS_DIR / "yolov8n.pt")
    vehicle_confidence: float = 0.35
    min_green: int = 15
    max_green: int = 60
    yellow_time: int = 3
    traffic_capacity: int = 30
    low_threshold: float = 0.30
    medium_threshold: float = 0.65
    max_upload_size_mb: int = 500
    db_sample_every_frames: int = 10
    runtime_save_every_frames: int = 10
    max_wait_seconds: int = 120
    fairness_threshold_seconds: int = 45

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()

VEHICLE_CLASSES = {"car": 2, "motorcycle": 3, "bus": 5, "truck": 7}
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
APPROACHES = ("NORTH", "EAST", "SOUTH", "WEST")

# Normalized ROIs. These are intentionally configurable and not simple quadrants.
APPROACH_ROIS = {
    "NORTH": {"x1": 0.25, "y1": 0.00, "x2": 0.75, "y2": 0.40},
    "EAST":  {"x1": 0.60, "y1": 0.25, "x2": 1.00, "y2": 0.75},
    "SOUTH": {"x1": 0.25, "y1": 0.60, "x2": 0.75, "y2": 1.00},
    "WEST":  {"x1": 0.00, "y1": 0.25, "x2": 0.40, "y2": 0.75},
}


def resolve_model_path() -> Path:
    path = Path(settings.model_path)
    return path if path.is_absolute() else PROJECT_ROOT / path


def ensure_directories() -> None:
    for directory in (DATABASE_DIR, UPLOAD_DIR, JOB_DIR, MODELS_DIR):
        directory.mkdir(parents=True, exist_ok=True)

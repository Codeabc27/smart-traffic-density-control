from fastapi import APIRouter
from ..config import settings, resolve_model_path
router = APIRouter(prefix="/api", tags=["about"])
@router.get("/about")
def about():
    return {
        "title": "AI Traffic Signal Control",
        "description": "Automated traffic signal control using real-time vehicle detection, traffic density analysis and adaptive four-way signal optimization.",
        "flow": ["Traffic Video", "YOLO Detection", "Vehicle Counting", "Approach Assignment", "Density", "Adaptive Signal Optimization", "Processed Video + CSV"],
        "technologies": ["Python", "FastAPI", "OpenCV", "Ultralytics YOLO", "SQLite", "Vanilla JavaScript", "Tailwind CSS", "Chart.js"],
        "configuration": {"min_green": settings.min_green, "max_green": settings.max_green, "yellow_time": settings.yellow_time, "model_ready": resolve_model_path().exists()},
    }

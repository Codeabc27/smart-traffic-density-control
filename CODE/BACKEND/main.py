from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .config import ensure_directories
from .database import initialize_database
from .services.job_manager import job_manager
from .routes.auth import router as auth_router
from .routes.analysis import router as analysis_router
from .routes.dashboard import router as dashboard_router
from .routes.results import router as results_router
from .routes.about import router as about_router
from .routes.signals import router as signals_router

PROJECT_ROOT=Path(__file__).resolve().parent.parent
FRONTEND_DIR=PROJECT_ROOT/"FRONTEND"
app=FastAPI(title="AI Traffic Signal Control",version="2.0.0",description="Real-time vehicle counting and adaptive four-way traffic signal optimization")
app.add_middleware(CORSMiddleware,allow_origins=["http://127.0.0.1:8000","http://localhost:8000"],allow_credentials=True,allow_methods=["*"],allow_headers=["*"])

@app.on_event("startup")
def startup():
    ensure_directories(); initialize_database(); job_manager.recover_after_restart()

app.include_router(auth_router); app.include_router(analysis_router); app.include_router(dashboard_router); app.include_router(results_router); app.include_router(about_router); app.include_router(signals_router)
app.mount("/static",StaticFiles(directory=str(FRONTEND_DIR)),name="static")

@app.get("/api/health")
def health():
    from .config import resolve_model_path
    return {"status":"ok","service":"AI Traffic Signal Control","version":"2.0.0","model_ready":resolve_model_path().exists()}

@app.get("/")
def index(): return FileResponse(FRONTEND_DIR/"index.html")

"""
FastAPI backend for the Stock Market Research Tool.

Endpoints:
    GET  /api/sectors              — available sector names
    POST /api/jobs                 — create & start a report job
    GET  /api/jobs/{id}            — poll job status + log
    GET  /api/jobs/{id}/stream     — SSE live log stream
    GET  /api/jobs/{id}/report     — download generated PDF

Start:
    uvicorn api:app --reload --port 8000
"""

import queue
import sys
import threading
import time
import types
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

# Ensure this file's directory is on sys.path so `from main import run` works
_HERE = Path(__file__).parent.resolve()
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

REPORTS_DIR = (_HERE / "reports").resolve()
REPORTS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Stock Research API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Job model
# ---------------------------------------------------------------------------

@dataclass
class Job:
    id: str
    status: str                   # "pending" | "running" | "done" | "error"
    created_at: float
    params: dict
    log_lines: list = field(default_factory=list)
    q: queue.Queue = field(default_factory=queue.Queue)
    error: Optional[str] = None
    output_path: Optional[str] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)


JOBS: dict[str, Job] = {}

# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class JobRequest(BaseModel):
    risk: int
    amount: int = 50_000
    platform: str = "questrade"
    universe_size: int = 30
    sectors: Optional[list[str]] = None
    include: Optional[list[str]] = None
    exclude: Optional[list[str]] = None
    output: str = "stock_report.pdf"

    @field_validator("risk")
    @classmethod
    def check_risk(cls, v):
        if not 1 <= v <= 10:
            raise ValueError("risk must be 1–10")
        return v

    @field_validator("amount")
    @classmethod
    def check_amount(cls, v):
        if v <= 0:
            raise ValueError("amount must be positive")
        return v

    @field_validator("universe_size")
    @classmethod
    def check_universe(cls, v):
        if not 10 <= v <= 85:
            raise ValueError("universe_size must be 10–85")
        return v

    @field_validator("output")
    @classmethod
    def check_output(cls, v):
        if not v.endswith(".pdf"):
            raise ValueError("output must end in .pdf")
        return v

    @field_validator("platform")
    @classmethod
    def check_platform(cls, v):
        valid = {"questrade", "wealthsimple", "td_direct"}
        if v not in valid:
            raise ValueError(f"platform must be one of {sorted(valid)}")
        return v

# ---------------------------------------------------------------------------
# Request schema — Custom Analysis
# ---------------------------------------------------------------------------

class CustomJobRequest(BaseModel):
    symbols: list[str]
    risk: int = 5
    amount: int = 50_000
    platform: str = "questrade"
    output: str = "custom_report.pdf"

    @field_validator("symbols")
    @classmethod
    def check_symbols(cls, v):
        cleaned = [s.strip().upper() for s in v if s.strip()]
        if not cleaned:
            raise ValueError("At least one symbol is required")
        if len(cleaned) > 50:
            raise ValueError("Maximum 50 symbols per request")
        return cleaned

    @field_validator("risk")
    @classmethod
    def check_risk(cls, v):
        if not 1 <= v <= 10:
            raise ValueError("risk must be 1–10")
        return v

    @field_validator("amount")
    @classmethod
    def check_amount(cls, v):
        if v <= 0:
            raise ValueError("amount must be positive")
        return v

    @field_validator("platform")
    @classmethod
    def check_platform(cls, v):
        if v not in {"questrade", "wealthsimple", "td_direct"}:
            raise ValueError("platform must be questrade, wealthsimple, or td_direct")
        return v

    @field_validator("output")
    @classmethod
    def check_output(cls, v):
        if not v.endswith(".pdf"):
            raise ValueError("output must end in .pdf")
        return v


# ---------------------------------------------------------------------------
# Background runners
# ---------------------------------------------------------------------------

def _build_args(params: dict) -> types.SimpleNamespace:
    """Convert API params dict into the argparse-style namespace run() expects."""
    ns = types.SimpleNamespace(
        risk=params["risk"],
        output=str(REPORTS_DIR / params["output"]),
        amount=params["amount"],
        platform=params["platform"],
        universe_size=params["universe_size"],
        sectors=params.get("sectors"),
        include=params.get("include"),
        exclude=params.get("exclude"),
        verbose=False,
    )
    return ns


def _build_custom_args(params: dict) -> types.SimpleNamespace:
    """Minimal namespace for run_custom() — no universe-selection fields."""
    return types.SimpleNamespace(
        risk=params["risk"],
        output=str(REPORTS_DIR / params["output"]),
        amount=params["amount"],
        platform=params["platform"],
        verbose=False,
    )


def _make_log_cb(job: Job):
    def log_cb(msg: str) -> None:
        line = str(msg)
        with job._lock:
            job.log_lines.append(line)
        job.q.put(line)
    return log_cb


def _run_job(job: Job) -> None:
    job.status = "running"
    try:
        from main import run
        args = _build_args(job.params)
        run(args, log=_make_log_cb(job))
        job.status = "done"
        job.output_path = args.output
    except RuntimeError as exc:
        job.status = "error"
        job.error = str(exc)
    except Exception as exc:
        job.status = "error"
        job.error = f"Unexpected error: {exc}"
    finally:
        job.q.put(None)


def _run_custom_job(job: Job) -> None:
    job.status = "running"
    try:
        from main import run_custom
        args = _build_custom_args(job.params)
        run_custom(job.params["symbols"], args, log=_make_log_cb(job))
        job.status = "done"
        job.output_path = args.output
    except RuntimeError as exc:
        job.status = "error"
        job.error = str(exc)
    except Exception as exc:
        job.status = "error"
        job.error = f"Unexpected error: {exc}"
    finally:
        job.q.put(None)

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/sectors")
def get_sectors():
    from data.ticker_selector import AVAILABLE_SECTORS
    return {"sectors": sorted(AVAILABLE_SECTORS)}


@app.post("/api/jobs", status_code=201)
def create_job(req: JobRequest):
    # Extra validation: sector names
    if req.sectors:
        from data.ticker_selector import AVAILABLE_SECTORS
        invalid = [s for s in req.sectors if s not in AVAILABLE_SECTORS]
        if invalid:
            raise HTTPException(422, detail=f"Unknown sectors: {invalid}")

    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        status="pending",
        created_at=time.time(),
        params=req.model_dump(),
    )
    JOBS[job_id] = job
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return {"job_id": job_id}


@app.post("/api/custom-jobs", status_code=201)
def create_custom_job(req: CustomJobRequest):
    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        status="pending",
        created_at=time.time(),
        params=req.model_dump(),
    )
    JOBS[job_id] = job
    threading.Thread(target=_run_custom_job, args=(job,), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    with job._lock:
        lines = list(job.log_lines)
    return {
        "job_id": job.id,
        "status": job.status,
        "created_at": job.created_at,
        "params": job.params,
        "log_lines": lines,
        "error": job.error,
        "output_path": job.output_path,
    }


@app.get("/api/jobs/{job_id}/stream")
def stream_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")

    def event_gen():
        # Replay all accumulated lines first (reconnect-safe)
        with job._lock:
            replay = list(job.log_lines)
        for line in replay:
            yield f"data: {line}\n\n"

        # If already finished, close immediately
        if job.status in ("done", "error"):
            sentinel = "[DONE]" if job.status == "done" else f"[ERROR] {job.error or ''}"
            yield f"data: {sentinel}\n\n"
            return

        # Live tail
        while True:
            try:
                msg = job.q.get(timeout=1.0)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if msg is None:
                sentinel = "[DONE]" if job.status == "done" else f"[ERROR] {job.error or ''}"
                yield f"data: {sentinel}\n\n"
                return
            yield f"data: {msg}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs/{job_id}/report")
def download_report(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    if job.status == "running":
        raise HTTPException(409, detail="Report is still generating")
    if job.status != "done" or not job.output_path:
        raise HTTPException(404, detail="Report not available")

    report_path = Path(job.output_path).resolve()
    # Path traversal guard
    if not str(report_path).startswith(str(REPORTS_DIR)):
        raise HTTPException(400, detail="Invalid report path")
    if not report_path.exists():
        raise HTTPException(404, detail="Report file not found on disk")

    return FileResponse(
        path=str(report_path),
        media_type="application/pdf",
        filename=report_path.name,
    )


# ---------------------------------------------------------------------------
# Serve React frontend (production build)
# ---------------------------------------------------------------------------

_DIST = _HERE / "ui" / "dist"
if _DIST.exists():
    # Mount assets before the catch-all so JS/CSS are served correctly
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    def _spa_root():
        return FileResponse(str(_DIST / "index.html"))

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa_fallback(full_path: str):
        return FileResponse(str(_DIST / "index.html"))

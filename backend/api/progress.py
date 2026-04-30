from __future__ import annotations

from fastapi import APIRouter

from backend.dependencies import get_services
from backend.models.schemas import ProgressState

router = APIRouter()


@router.get("/api/progress", response_model=ProgressState)
def get_progress() -> ProgressState:
    services = get_services()
    return ProgressState(completed_terms=services.progress["completed_terms"])


@router.put("/api/progress", response_model=ProgressState)
def set_progress(body: ProgressState) -> ProgressState:
    services = get_services()
    services.progress["completed_terms"] = body.completed_terms
    return ProgressState(completed_terms=services.progress["completed_terms"])

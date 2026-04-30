from __future__ import annotations

from fastapi import APIRouter

from backend.dependencies import get_services
from backend.models.schemas import CompareTermsRequest, CompareTermsResponse

router = APIRouter()


@router.post("/api/compare-terms", response_model=CompareTermsResponse)
def compare_terms(body: CompareTermsRequest) -> CompareTermsResponse:
    services = get_services()
    return services.glossary.compare(body.term_a, body.term_b)

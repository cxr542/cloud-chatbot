from __future__ import annotations

from backend.models.schemas import CompareTermsResponse


class GlossaryEngine:
    def __init__(self) -> None:
        self.term_pages = {
            "SSO": 76,
            "RBAC": 73,
            "ABAC": 74,
            "MFA": 77,
            "ACL": 78,
            "HA": 69,
            "DR": 70,
            "Observability": 71,
            "FinOps": 79,
            "쿠버네티스": 52,
        }

    def compare(self, term_a: str, term_b: str) -> CompareTermsResponse:
        key = f"{term_a.strip().upper()}|{term_b.strip().upper()}"
        if "RBAC" in key and "ABAC" in key:
            return CompareTermsResponse(left="RBAC: 역할 중심, 관리 단순", right="ABAC: 속성 기반, 정책 세밀", diff="RBAC는 운영 단순성, ABAC는 정책 유연성이 강점입니다.")
        if "HA" in key and "DR" in key:
            return CompareTermsResponse(left="HA: 장애 시 서비스 지속", right="DR: 재해 후 복구", diff="HA는 즉시성, DR은 복구 전략/절차 중심입니다.")
        return CompareTermsResponse(left=f"{term_a}: 기본 정의 중심 비교", right=f"{term_b}: 적용 시나리오 중심 비교", diff="두 용어 모두 시스템 안정성과 운영 효율에 기여합니다.")

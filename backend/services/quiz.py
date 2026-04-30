from __future__ import annotations

from backend.models.schemas import QuizQuestion


class QuizEngine:
    def __init__(self) -> None:
        self._mcq = [
            QuizQuestion(q="SSO의 핵심 목적은?", c=["한 번 로그인으로 여러 서비스 접근", "암호를 길게 만들기", "로그를 삭제하기", "비용 정산 자동화"], a=0, e="SSO는 단일 인증으로 다중 서비스 접근을 지원합니다."),
            QuizQuestion(q="RBAC는 무엇을 기준으로 권한을 부여하나요?", c=["위치", "역할(Role)", "시간", "디바이스"], a=1, e="RBAC는 역할 기반입니다."),
            QuizQuestion(q="ABAC의 특징은?", c=["역할만 사용", "정책+속성 기반", "수동 승인만 가능", "네트워크 속도 측정"], a=1, e="ABAC는 속성과 정책을 사용합니다."),
            QuizQuestion(q="MFA는 어떤 개념인가요?", c=["다중 장애 복구", "다중 인증 요소", "다중 클러스터", "다중 리전 과금"], a=1, e="MFA는 추가 인증요소를 요구합니다."),
            QuizQuestion(q="쿠버네티스의 대표 기능은?", c=["컨테이너 오케스트레이션", "문서 OCR", "비밀번호 복구", "DNS 루트 관리"], a=0, e="쿠버네티스는 컨테이너 운영 자동화를 담당합니다."),
        ]
        self._ox = [
            QuizQuestion(q="SSO는 한 번 로그인으로 여러 서비스 접근을 돕는다.", c=["O", "X"], a=0, e="맞습니다."),
            QuizQuestion(q="RBAC는 사용자 속성만으로 권한을 판단한다.", c=["O", "X"], a=1, e="아닙니다. 역할 기반입니다."),
        ]

    def generate(self, quiz_type: str, count: int) -> list[QuizQuestion]:
        if quiz_type == "ox":
            pool = self._ox
        elif quiz_type == "mix":
            pool = self._mcq[:3] + self._ox
        else:
            pool = self._mcq
        out: list[QuizQuestion] = []
        while len(out) < count:
            out.extend(pool)
        return out[:count]

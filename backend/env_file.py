"""`.env` 파일을 파싱해 `os.environ`에 넣습니다.

`python-dotenv` 패키지 없이도(맥 Homebrew 파이썬 등 PEP 668 환경) 동일하게
설정을 불러올 수 있게 합니다. 처리 형식은 일반적인 `KEY=VALUE` 한 줄 규칙을 따릅니다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def _strip_unquoted_inline_comment(value: str) -> str:
    """
    따옴표로 감싸지 않은 값 끝에 붙은 `` # 주석`` 을 잘라 냅니다.

    ``KEY=512  # MB`` 처럼 쓰면 기존에는 ``512  # MB`` 전체가 들어가 숫자 파싱이 깨졌습니다.

    Args:
        value: ``=`` 우측에서 앞뒤 공백을 제거한 문자열(따옴표 제거 전·후 모두 가능).

    Returns:
        주석을 뺀 값(비따옴표 경로에서만 잘라 냄).
    """
    m = re.search(r"\s+#", value)
    if m:
        return value[: m.start()].rstrip()
    return value


def load_env_file(env_path: Path, *, override: bool = True) -> None:
    """
    지정한 경로의 `.env` 내용을 환경 변수로 반영합니다.

    - 빈 줄·`#` 로 시작하는 줄은 무시합니다.
    - `export KEY=...` 형태도 `KEY`만 인식합니다.
    - 값 앞뒤의 작은따옴표/큰따옴표는 한 쌍일 때 제거합니다.
    - 따옴표로 감싸지 않은 값에서는, 공백 뒤 ` # ` 로 시작하는 부분부터 줄 끝을 주석으로 제거합니다.
    Args:
        env_path: `.env` 파일 경로입니다. 없으면 아무 것도 하지 않습니다.
        override: True이면 이미 있는 환경 변수도 덮어씁니다.
    """
    if not env_path.is_file():
        return
    # Excel 등에서 저장하면 선행 BOM(\\ufeff)이 붙을 수 있어 제거합니다.
    text = env_path.read_text(encoding="utf-8-sig")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, _, rest = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = rest.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = _strip_unquoted_inline_comment(value)
        if override or key not in os.environ:
            os.environ[key] = value

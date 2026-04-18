"""자동 기억 정책 — ask-gemma 브릿지로 스니펫에서 항목 추출.

plan 파일 §② "매 턴 종료 후 LLM(e4b)에게 기억할 것 추출 요청".
LLM이 없거나 실패해도 무해(빈 dict 반환).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .. import config
from . import project as project_layer
from . import user as user_layer

_VALID_CATEGORIES = {"decision", "failure", "preference", "fact"}
_VALID_SCOPES = {"project", "user"}

_EXTRACT_PROMPT = """너는 개인용 에이전트의 자동 기억 추출기다.
아래 스니펫에서 **장기 기억할 만한 항목**만 JSON 배열로 반환하라. 설명·코드블록 금지.

각 항목 스키마:
{{"text": "한 줄 요약", "category": "decision|failure|preference|fact", "scope": "project|user"}}

- 사용자 선호/스타일이면 scope=user, category=preference
- 프로젝트 결정/실패/사실이면 scope=project
- 해당 없으면 [] (빈 배열)만 반환

스니펫:
{snippet}
"""


def _run_ask_gemma(prompt: str, timeout: int) -> str:
    try:
        result = subprocess.run(
            [config.ASK_GEMMA_BIN],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    return result.stdout or ""


def _parse_items(raw: str) -> list[dict[str, Any]]:
    if not raw:
        return []
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(raw[start : end + 1])
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict) and item.get("text")]


def auto_capture(
    snippet: str,
    project_dir: Path | str | None = None,
    timeout: int = 30,
) -> dict[str, int]:
    """스니펫 → LLM 추출 → 해당 층에 기록. 층별 추가 개수 반환."""
    counts = {"project": 0, "user": 0, "skipped": 0}
    if not snippet or not snippet.strip():
        return counts

    raw = _run_ask_gemma(_EXTRACT_PROMPT.format(snippet=snippet), timeout=timeout)
    for item in _parse_items(raw):
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        category = str(item.get("category", "fact"))
        if category not in _VALID_CATEGORIES:
            category = "fact"
        scope = str(item.get("scope", "project"))
        if scope not in _VALID_SCOPES:
            scope = "project"

        added = False
        if scope == "user":
            added = user_layer.append(text, category=category)
            if added:
                counts["user"] += 1
        else:
            added = project_layer.append(text, category=category, project_dir=project_dir)
            if added:
                counts["project"] += 1
        if not added:
            counts["skipped"] += 1

    return counts

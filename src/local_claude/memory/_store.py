"""JSON append-only 메모리 저장소 공통 루틴.

project.py / user.py 가 경로만 바꿔서 공유한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        path.rename(path.with_suffix(path.suffix + ".corrupt"))
        return {"entries": []}
    if "entries" not in data or not isinstance(data["entries"], list):
        return {"entries": []}
    return data


def save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def append(path: Path, text: str, category: str, meta: dict[str, Any]) -> bool:
    """새 항목 추가. 동일 (text, category) 있으면 last_seen/touch_count 만 갱신하고 False.

    touch_count — decay 보호용. 같은 항목이 반복 remember 되면 증가 → 자주 쓰이는 지표.
    """
    data = load(path)
    for entry in data["entries"]:
        if entry.get("text") == text and entry.get("category") == category:
            entry["last_seen"] = _now_iso()
            entry["touch_count"] = int(entry.get("touch_count", 0)) + 1
            save(path, data)
            return False
    now = _now_iso()
    data["entries"].append(
        {
            "text": text,
            "category": category,
            "created_at": now,
            "last_seen": now,
            "touch_count": 0,
            **meta,
        }
    )
    save(path, data)
    return True


def entries(path: Path) -> list[dict[str, Any]]:
    return load(path).get("entries", [])

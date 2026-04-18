"""감사용 JSONL 전환 로그.

append-only. 각 엔트리:
  {ts, from, to, device_fp, trigger, meta}
  trigger: "manual" | "auto-wifi" | "init"
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from ctx.config import Paths
from ctx.device import get_or_init_device


@dataclass
class SwitchEvent:
    ts: str
    from_: str | None
    to: str
    device_fp: str
    trigger: str                     # manual | auto-wifi | init
    meta: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        d = asdict(self)
        d["from"] = d.pop("from_")
        return json.dumps(d, ensure_ascii=False)


def log_switch(
    to: str,
    from_: str | None,
    trigger: str = "manual",
    meta: dict[str, Any] | None = None,
    paths: Paths | None = None,
) -> SwitchEvent:
    paths = paths or Paths.load()
    paths.ensure()
    dev = get_or_init_device(paths)
    ev = SwitchEvent(
        ts=datetime.now(timezone.utc).isoformat(),
        from_=from_,
        to=to,
        device_fp=dev.fingerprint,
        trigger=trigger,
        meta=meta or {},
    )
    with paths.history.open("a", encoding="utf-8") as f:
        f.write(ev.to_json() + "\n")
    return ev


def read_history(paths: Paths | None = None) -> list[dict[str, Any]]:
    paths = paths or Paths.load()
    if not paths.history.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in paths.history.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out

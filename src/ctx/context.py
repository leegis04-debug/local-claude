"""Context TOML 로드 + key path 조회.

TOML 구조 예:
    display = "다겸 🏢"
    color   = "#0066cc"
    wifi    = ["Daegyeom-Wifi"]
    [gstar]       namespace = "daegyeom"
    [jw]          form_id = "2025-smart-service"
    [network]     gateway_mode = "ssh-tunnel"
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from ctx.config import Paths


class ContextNotFound(Exception):
    pass


def load_context(name: str, paths: Paths | None = None) -> dict[str, Any]:
    paths = paths or Paths.load()
    f = paths.contexts_dir / f"{name}.toml"
    if not f.exists():
        raise ContextNotFound(f"{f}")
    with f.open("rb") as fp:
        return tomllib.load(fp)


def list_context_names(paths: Paths | None = None) -> list[str]:
    paths = paths or Paths.load()
    if not paths.contexts_dir.exists():
        return []
    return sorted(p.stem for p in paths.contexts_dir.glob("*.toml"))


def current_context_name(paths: Paths | None = None) -> str | None:
    paths = paths or Paths.load()
    if not paths.current.exists():
        return None
    name = paths.current.read_text(encoding="utf-8").strip()
    return name or None


def set_current_context_name(name: str, paths: Paths | None = None) -> None:
    paths = paths or Paths.load()
    paths.ensure()
    paths.current.write_text(name + "\n", encoding="utf-8")


def write_context(name: str, data: dict[str, Any], paths: Paths | None = None) -> Path:
    """TOML 직렬화. 복잡한 타입은 제한적 — 기본 정적 템플릿용."""
    paths = paths or Paths.load()
    paths.ensure()
    f = paths.contexts_dir / f"{name}.toml"
    f.write_text(_to_toml(data), encoding="utf-8")
    return f


def get_key_path(data: dict[str, Any], key_path: str) -> Any:
    """'gstar.namespace' 같은 도트 경로 조회. 없으면 KeyError."""
    cur: Any = data
    for part in key_path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(key_path)
        cur = cur[part]
    return cur


def _to_toml(data: dict[str, Any]) -> str:
    """의존성 없이 간단 직렬화. 중첩 1단계(서브섹션) 까지."""
    lines: list[str] = []
    # 최상위 스칼라·리스트
    for k, v in data.items():
        if isinstance(v, dict):
            continue
        lines.append(f"{k} = {_toml_value(v)}")
    # 서브섹션
    for k, v in data.items():
        if isinstance(v, dict):
            lines.append("")
            lines.append(f"[{k}]")
            for kk, vv in v.items():
                lines.append(f"{kk} = {_toml_value(vv)}")
    return "\n".join(lines) + "\n"


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(v, list):
        items = ", ".join(_toml_value(x) for x in v)
        return f"[{items}]"
    raise TypeError(f"unsupported TOML value: {type(v)}")

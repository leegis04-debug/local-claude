"""월간 무료 티어 쿼터 추적 — 카드 등록 없이 한도 내 운영.

각 백엔드의 월 free limit 을 코드에 명시하고, `~/.gstar/enrich_quota.json`
에 월별 카운터 누적. 한도 초과 시 자동 다른 백엔드로 fallback.

사용 패턴:
    from gstar.enrich.quota import QuotaManager
    q = QuotaManager()
    backend = q.pick_backend(preferred=["tavily", "brave", "exa"])
    if backend:
        results = await web_search(query, backend=backend)
        q.record_query(backend)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


# 각 백엔드 월간 무료 티어 한도 (2026-04 기준, 카드 등록 불필요 우선)
FREE_TIER_MONTHLY: dict[str, int] = {
    "tavily": 1_000,
    "brave": 2_000,
    "exa": 1_000,
    "searchapi": 100,
    "perplexity": 0,          # $5 credit, 쿼리 수가 아니라 금액 기준
    "you": 100,
    "serpapi": 100,
    "firecrawl": 0,           # trial only
    # 무제한 (self-hosted / scrape)
    "ddg": 10_000,            # rate limit 있지만 월 한도 없음. 안전 상한으로 10k
    "naver": 10_000,
    "google": 5_000,
    "self_made": 10_000,
    "claude_style": 10_000,
    "mock": 999_999,
}


def _default_store_path() -> Path:
    return Path(os.environ.get("GP_QUOTA_PATH", str(Path.home() / ".gstar" / "enrich_quota.json")))


@dataclass
class QuotaSnapshot:
    month: str
    counters: dict[str, int]
    updated_at: str


class QuotaManager:
    """월 단위 백엔드 쿼터 관리. JSON 파일 persistence."""

    def __init__(self, store_path: Path | None = None):
        self.store_path = store_path or _default_store_path()
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load()

    def _load(self) -> dict:
        if not self.store_path.exists():
            return {"month": self._current_month(), "counters": {}, "updated_at": self._now()}
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"month": self._current_month(), "counters": {}, "updated_at": self._now()}
        # 월 바뀌면 리셋
        if data.get("month") != self._current_month():
            return {"month": self._current_month(), "counters": {}, "updated_at": self._now()}
        return data

    def _save(self) -> None:
        self._state["updated_at"] = self._now()
        tmp = self.store_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.store_path)

    @staticmethod
    def _current_month() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def used(self, backend: str) -> int:
        return int(self._state.get("counters", {}).get(backend, 0))

    def remaining(self, backend: str) -> int:
        limit = FREE_TIER_MONTHLY.get(backend, 0)
        return max(0, limit - self.used(backend))

    def can_query(self, backend: str, *, budget_ratio: float = 0.9) -> bool:
        """무료 한도의 budget_ratio 까지만 허용 (10% 버퍼 기본)."""
        limit = FREE_TIER_MONTHLY.get(backend, 0)
        if limit <= 0:
            return False
        budget = int(limit * budget_ratio)
        return self.used(backend) < budget

    def record_query(self, backend: str, count: int = 1) -> None:
        counters = self._state.setdefault("counters", {})
        counters[backend] = int(counters.get(backend, 0)) + count
        self._save()

    def pick_backend(self, preferred: list[str]) -> str | None:
        """선호 순서대로 검사, 쿼터 남은 첫 백엔드 반환. 모두 소진 시 None."""
        for b in preferred:
            if self.can_query(b):
                return b
        return None

    def snapshot(self) -> QuotaSnapshot:
        return QuotaSnapshot(
            month=self._state.get("month", self._current_month()),
            counters=dict(self._state.get("counters", {})),
            updated_at=self._state.get("updated_at", self._now()),
        )

    def report(self) -> str:
        snap = self.snapshot()
        lines = [f"# Enrich 월간 쿼터 현황 ({snap.month})", ""]
        lines.append("| backend | 사용 | 한도 | 남은 | 진행률 |")
        lines.append("|---|---:|---:|---:|---:|")
        rows = []
        for b, limit in FREE_TIER_MONTHLY.items():
            used = snap.counters.get(b, 0)
            remain = max(0, limit - used)
            pct = (used / limit * 100) if limit else 0
            rows.append((b, used, limit, remain, pct))
        rows.sort(key=lambda r: -r[4])
        for b, used, limit, remain, pct in rows:
            lines.append(f"| {b} | {used} | {limit} | {remain} | {pct:.1f}% |")
        return "\n".join(lines)

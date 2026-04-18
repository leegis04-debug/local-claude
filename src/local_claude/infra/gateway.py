"""Gateway HTTP 클라이언트.

글로벌 CLAUDE.md 규칙을 그대로 준수:
- URL:  http://100.79.251.53:8000  (기본)
- 헤더: X-Auth-Token: $ASST_TOKEN   (Bearer 금지)
- 엔드포인트: /search/hybrid        (/search 금지)

접근 실패(HTTP=000) 시 폴백 순서도 CLAUDE.md 그대로:
  1단계: SSH 포트 포워딩 시도 → URL 을 localhost 로 치환 후 재시도
  2단계: 그래도 실패하면 Gateway 스킵 허용 (호출자가 ok=False 받고 판단)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .. import config
from . import tunnel


# ── 토큰 로드 ────────────────────────────────────────────────────────────────

def _load_token_from_config(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "ASST_TOKEN":
                return v.strip().strip('"').strip("'") or None
    except OSError:
        return None
    return None


def load_token() -> str | None:
    """ASST_TOKEN 환경변수 → ~/.config/asst/config 폴백."""
    env = os.environ.get("ASST_TOKEN")
    if env:
        return env
    return _load_token_from_config(config.GATEWAY_TOKEN_CONFIG)


# ── 응답 ────────────────────────────────────────────────────────────────────

@dataclass
class GatewayResponse:
    ok: bool
    status_code: int  # 0 = 네트워크 실패
    data: Any = None
    error: str | None = None
    via: str = "direct"  # direct | tunnel | skipped
    url: str = ""

    def raise_for_skipped(self) -> None:
        if self.via == "skipped":
            raise RuntimeError(self.error or "gateway skipped")


# ── 클라이언트 ──────────────────────────────────────────────────────────────

@dataclass
class Gateway:
    """stateless 래퍼. 호출마다 토큰/URL 재확인 — 환경 변화에 즉응."""

    token: str | None = field(default_factory=load_token)
    direct_url: str = field(default_factory=lambda: config.GATEWAY_URL_DIRECT)
    tunnel_url: str = field(default_factory=lambda: config.GATEWAY_URL_TUNNEL)
    timeout_s: float = field(default_factory=lambda: config.GATEWAY_TIMEOUT_S)
    auto_tunnel: bool = True
    _transport: httpx.BaseTransport | None = None  # 테스트 주입용

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Auth-Token"] = self.token
        return headers

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout_s,
            transport=self._transport,
        )

    def _send(self, method: str, base: str, path: str, **kwargs: Any) -> GatewayResponse:
        url = base.rstrip("/") + path
        try:
            with self._client() as client:
                resp = client.request(method, url, headers=self._headers(), **kwargs)
        except (httpx.ConnectError, httpx.ReadError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            return GatewayResponse(ok=False, status_code=0, error=str(exc), url=url)

        try:
            payload = resp.json()
        except Exception:
            payload = resp.text

        return GatewayResponse(
            ok=resp.is_success,
            status_code=resp.status_code,
            data=payload,
            error=None if resp.is_success else f"HTTP {resp.status_code}",
            url=url,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> GatewayResponse:
        """가장 낮은 레이어. HTTP=000 시 SSH 터널 fallback 자동 시도."""
        kwargs: dict[str, Any] = {}
        if json is not None:
            kwargs["json"] = json
        if params is not None:
            kwargs["params"] = params

        # 1차: direct
        resp = self._send(method, self.direct_url, path, **kwargs)
        if resp.status_code != 0:
            resp.via = "direct"
            return resp

        if not self.auto_tunnel:
            resp.via = "skipped"
            return resp

        # 2차: SSH 터널 올리고 localhost 재시도
        tunnel.up()
        retry = self._send(method, self.tunnel_url, path, **kwargs)
        if retry.status_code != 0:
            retry.via = "tunnel"
            return retry

        # 3차: 포기
        return GatewayResponse(
            ok=False,
            status_code=0,
            error="direct + tunnel 모두 실패 (CLAUDE.md 2단계 스킵)",
            via="skipped",
            url=self.direct_url.rstrip("/") + path,
        )

    # ── 편의 메서드 ─────────────────────────────────────────────────────────

    def health(self) -> GatewayResponse:
        return self.request("GET", "/health")

    def search_hybrid(
        self,
        query: str,
        top_k: int = 5,
        exclude_sources: list[str] | None = None,
        sources: list[str] | None = None,
        **extra: Any,
    ) -> GatewayResponse:
        """/search/hybrid — govsupport 기본 제외 (A.7 2026-04-17 hallucination fix)."""
        body: dict[str, Any] = {"query": query, "top_k": top_k}
        if exclude_sources is None:
            exclude_sources = ["govsupport"]
        if exclude_sources:
            body["exclude_sources"] = exclude_sources
        if sources:
            body["sources"] = sources
        body.update(extra)
        return self.request("POST", "/search/hybrid", json=body)

    def graph_query(self, cypher: str, params: dict[str, Any] | None = None) -> GatewayResponse:
        """/graph/query — Neo4j cypher. gateway 쪽에서 파괴적 쿼리 차단."""
        return self.request("POST", "/graph/query", json={"cypher": cypher, "params": params or {}})

    def skill_draft(
        self,
        track: str,
        stage: str,
        project_name: str,
        input_context: str = "",
    ) -> GatewayResponse:
        return self.request(
            "POST",
            "/skill/draft",
            json={
                "track": track,
                "stage": stage,
                "project_name": project_name,
                "input_context": input_context,
            },
        )

    def mcp_jw(self, tool: str, payload: dict[str, Any]) -> GatewayResponse:
        """MCP jw-validator 프록시 — validate_step/finalize_step 등."""
        return self.request("POST", f"/mcp/jw/{tool}", json=payload)

    def mcp_doc(self, tool: str, payload: dict[str, Any]) -> GatewayResponse:
        """MCP doc-thinking 프록시 — critique_thought 등."""
        return self.request("POST", f"/mcp/doc/{tool}", json=payload)

    def notes_add(self, text: str, source: str = "lc", tags: list[str] | None = None) -> GatewayResponse:
        return self.request(
            "POST",
            "/notes",
            json={"text": text, "source": source, "tags": tags or []},
        )

"""MCP 서버 `assistant-mcp-g-trace` — Phase G1 skeleton.

Claude Code 에서 MCP tool 로 호출되어 현재 세션의 trace 를 G 에 기록.
실제 로직은 g-serve `/trace/record` 로 proxy.

노출 도구 (JSON-RPC 2.0 MCP 스타일이 아닌, 일단 HTTP POST 로 간소화.
기존 assistant-mcp-jw 의 패턴을 따름. 정식 MCP 로 승격은 다음 iteration).

엔드포인트:
  POST /tools/g_trace_bracket_open      body={task_id?, goal}
  POST /tools/g_trace_step              body={task_id, description, inputs?, outputs?, thinking?}
  POST /tools/g_trace_tool_use          body={task_id, tool_name, args_summary, result_summary}
  POST /tools/g_trace_decision          body={task_id, options, chosen, rationale}
  POST /tools/g_trace_bracket_close     body={task_id, outcome}
  GET  /health
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from ulid import ULID


G_SERVER_URL = os.environ.get("G_SERVER_URL", "http://100.79.251.53:9999")

app = FastAPI(title="assistant-mcp-g-trace", version="0.1.0")


def _g_record(body: dict[str, Any]) -> dict:
    try:
        r = httpx.post(f"{G_SERVER_URL}/trace/record", json=body, timeout=8.0)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"G server error: {e}") from e


@app.get("/health")
def health() -> dict:
    return {"ok": True, "g_server": G_SERVER_URL}


class BracketOpenRequest(BaseModel):
    task_id: str | None = None
    goal: str


@app.post("/tools/g_trace_bracket_open")
def bracket_open(req: BracketOpenRequest) -> dict:
    tid = req.task_id or str(ULID())
    _g_record({"source": "mcp", "phase": "open", "task_id": tid, "description": req.goal})
    return {"task_id": tid}


class StepRequest(BaseModel):
    task_id: str
    description: str
    inputs: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None
    thinking: str | None = None


@app.post("/tools/g_trace_step")
def step(req: StepRequest) -> dict:
    return _g_record(
        {
            "source": "mcp",
            "phase": "step",
            "task_id": req.task_id,
            "description": req.description,
            "inputs": req.inputs,
            "outputs": req.outputs,
            "thinking": req.thinking,
        }
    )


class ToolUseRequest(BaseModel):
    task_id: str
    tool_name: str
    args_summary: str
    result_summary: str


@app.post("/tools/g_trace_tool_use")
def tool_use(req: ToolUseRequest) -> dict:
    return _g_record(
        {
            "source": "mcp",
            "phase": "tool_use",
            "task_id": req.task_id,
            "description": f"{req.tool_name}: {req.args_summary[:120]}",
            "extras": {
                "tool_name": req.tool_name,
                "args_summary": req.args_summary,
                "result_summary": req.result_summary,
            },
        }
    )


class DecisionRequest(BaseModel):
    task_id: str
    options: list[str]
    chosen: str
    rationale: str


@app.post("/tools/g_trace_decision")
def decision(req: DecisionRequest) -> dict:
    return _g_record(
        {
            "source": "mcp",
            "phase": "decision",
            "task_id": req.task_id,
            "description": f"decided: {req.chosen[:100]}",
            "extras": {
                "options": req.options,
                "chosen": req.chosen,
                "rationale": req.rationale,
            },
        }
    )


class BracketCloseRequest(BaseModel):
    task_id: str
    outcome: str


@app.post("/tools/g_trace_bracket_close")
def bracket_close(req: BracketCloseRequest) -> dict:
    return _g_record(
        {
            "source": "mcp",
            "phase": "close",
            "task_id": req.task_id,
            "description": req.outcome,
        }
    )

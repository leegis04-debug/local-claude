"""Jira API 래퍼 — gre instruction 및 연관 stage 에서 티켓 메타 pull.

env:
  JIRA_SITE     — 예: yourco.atlassian.net
  JIRA_EMAIL
  JIRA_TOKEN
  JIRA_PROJECT  — 기본 "DT"

deep-tect/09-bridge/ri-gen.py 와 동일 env 공유.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from base64 import b64encode
from typing import Any


def _creds() -> tuple[str, str, str, str]:
    site = (os.environ.get("JIRA_SITE") or "").strip()
    email = (os.environ.get("JIRA_EMAIL") or "").strip()
    token = (os.environ.get("JIRA_TOKEN") or "").strip()
    project = (os.environ.get("JIRA_PROJECT") or "DT").strip()
    if not (site and email and token):
        raise RuntimeError(
            "JIRA env 미설정 — JIRA_SITE / JIRA_EMAIL / JIRA_TOKEN 필요"
        )
    return site, email, token, project


def _get(path: str, timeout: int = 15) -> dict:
    site, email, token, _ = _creds()
    url = f"https://{site}/rest/api/3{path}"
    auth = b64encode(f"{email}:{token}".encode()).decode()
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Basic {auth}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get_issue(key: str) -> dict:
    """Jira 티켓 단건 조회. summary/assignee/parent/labels/description 포함."""
    return _get(
        f"/issue/{key}"
        "?fields=summary,assignee,duedate,labels,parent,components,"
        "issuetype,priority,fixVersions,description"
    )


def _adf_to_text(adf: Any) -> str:
    """Atlassian Document Format → plain text (간이)."""
    if isinstance(adf, str):
        return adf
    if not isinstance(adf, dict):
        return ""
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text":
                parts.append(node.get("text", ""))
            for c in node.get("content", []) or []:
                walk(c)
            if node.get("type") in {"paragraph", "heading", "listItem"}:
                parts.append("\n")
        elif isinstance(node, list):
            for c in node:
                walk(c)

    walk(adf)
    return "".join(parts).strip()


def summarize_issue(issue: dict) -> dict:
    """_run_mode_e 에 주입할 핵심 필드만 추린 dict."""
    f = issue.get("fields", {})
    parent = f.get("parent") or {}
    components = f.get("components") or []
    assignee = f.get("assignee") or {}
    desc_raw = f.get("description")
    description = _adf_to_text(desc_raw)[:2000] if desc_raw else ""
    return {
        "key": issue.get("key", ""),
        "summary": f.get("summary", ""),
        "assignee": assignee.get("displayName") or "미할당",
        "due_date": f.get("duedate") or "",
        "epic_key": parent.get("key", ""),
        "epic_summary": (parent.get("fields") or {}).get("summary", ""),
        "phase": components[0]["name"] if components else "",
        "labels": list(f.get("labels") or []),
        "priority": (f.get("priority") or {}).get("name", ""),
        "description": description,
    }


def summary_to_context_block(summary: dict) -> str:
    """요약 dict → user_input 앞에 붙일 1~2KB markdown 블록."""
    lines = [
        f"# Jira {summary['key']}  —  {summary['summary']}",
        f"- Epic: {summary['epic_key']} / {summary['epic_summary']}" if summary['epic_key'] else "",
        f"- Phase: {summary['phase']}" if summary['phase'] else "",
        f"- Assignee: {summary['assignee']}",
        f"- Due: {summary['due_date']}" if summary['due_date'] else "",
        f"- Priority: {summary['priority']}" if summary['priority'] else "",
        f"- Labels: {', '.join(summary['labels'])}" if summary['labels'] else "",
    ]
    block = "\n".join(l for l in lines if l)
    if summary["description"]:
        block += f"\n\n## 티켓 설명\n{summary['description']}"
    return block

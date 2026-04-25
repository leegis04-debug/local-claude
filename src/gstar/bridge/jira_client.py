"""
Jira Cloud REST v3 래퍼 — bridge 도구 전용.

인증: Basic (email + API token). `~/.config/<slug>/env.sh` 의 `JIRA_*` env 사용.

제공:
  - verify_credentials() : /myself GET 으로 토큰 유효성 확인
  - list_tasks_by_project(): Task 목록 + key 매핑 (subtasks Parent 재생성용)
  - search_jql()         : 일반 JQL 검색
  - list_versions / create_version       : Fix Version 조회·생성 (provision)
  - list_components / create_component   : Component 조회·생성 (provision)

실패 시 httpx.HTTPStatusError 또는 JiraError 를 던진다 — 조용한 무시 금지.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


class JiraError(RuntimeError):
    pass


@dataclass
class JiraCredentials:
    site: str
    email: str
    token: str
    project_key: str

    @property
    def base_url(self) -> str:
        return f"https://{self.site}/rest/api/3"

    @property
    def auth(self) -> tuple[str, str]:
        return (self.email, self.token)


def credentials_from_env() -> JiraCredentials:
    site = os.environ.get("JIRA_SITE", "").strip()
    email = os.environ.get("JIRA_EMAIL", "").strip()
    token = os.environ.get("JIRA_TOKEN", "").strip()
    project_key = os.environ.get("JIRA_PROJECT", "").strip()
    missing = [k for k, v in [("JIRA_SITE", site), ("JIRA_EMAIL", email),
                              ("JIRA_TOKEN", token), ("JIRA_PROJECT", project_key)] if not v]
    if missing:
        raise JiraError(
            f"Jira env 누락: {', '.join(missing)}. "
            f"`source ~/.config/<slug>/env.sh` 후 재실행."
        )
    return JiraCredentials(site=site, email=email, token=token, project_key=project_key)


def verify_credentials(cred: JiraCredentials, *, timeout: float = 10.0) -> dict[str, Any]:
    """GET /myself — 200 이면 사용자 정보 dict, 아니면 JiraError."""
    r = httpx.get(
        f"{cred.base_url}/myself",
        auth=cred.auth,
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    if r.status_code == 401:
        raise JiraError("Jira 인증 실패 (401): email 또는 API token 확인.")
    if r.status_code == 403:
        raise JiraError("Jira 권한 부족 (403): 계정이 사이트 접근 권한 있는지 확인.")
    r.raise_for_status()
    return r.json()


def verify_project(cred: JiraCredentials, *, timeout: float = 10.0) -> dict[str, Any]:
    """GET /project/{key} — 프로젝트 존재 및 접근 가능 확인."""
    r = httpx.get(
        f"{cred.base_url}/project/{cred.project_key}",
        auth=cred.auth,
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    if r.status_code == 404:
        raise JiraError(
            f"Jira 프로젝트 '{cred.project_key}' 를 찾을 수 없음. "
            f"웹 (https://{cred.site}) 에서 먼저 프로젝트 생성 후 키 확인."
        )
    r.raise_for_status()
    return r.json()


def search_jql(cred: JiraCredentials, jql: str, *,
               fields: list[str] | None = None,
               max_results: int = 100,
               timeout: float = 30.0) -> list[dict]:
    """JQL 검색 — 페이지네이션 자동 처리."""
    if fields is None:
        fields = ["summary", "status", "issuetype", "parent", "customfield_10014",
                  "labels", "priority", "assignee", "duedate", "fixVersions", "components"]
    out: list[dict] = []
    next_token: str | None = None
    while True:
        payload: dict[str, Any] = {"jql": jql, "fields": fields, "maxResults": max_results}
        if next_token:
            payload["nextPageToken"] = next_token
        r = httpx.post(
            f"{cred.base_url}/search/jql",
            auth=cred.auth,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        out.extend(data.get("issues") or [])
        next_token = data.get("nextPageToken")
        if not next_token:
            break
    return out


def list_versions(cred: JiraCredentials, *, timeout: float = 15.0) -> list[dict[str, Any]]:
    """GET /project/{key}/versions — 프로젝트 내 모든 Fix Version. 비페이지네이션."""
    r = httpx.get(
        f"{cred.base_url}/project/{cred.project_key}/versions",
        auth=cred.auth,
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def create_version(
    cred: JiraCredentials,
    *,
    name: str,
    description: str = "",
    start_date: str = "",
    release_date: str = "",
    released: bool = False,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """POST /version — Fix Version 생성. start/release 는 'YYYY-MM-DD' 또는 빈문자열."""
    body: dict[str, Any] = {
        "project": cred.project_key,
        "name": name,
        "released": released,
        "archived": False,
    }
    if description:
        body["description"] = description
    if start_date:
        body["startDate"] = start_date
    if release_date:
        body["releaseDate"] = release_date
    r = httpx.post(
        f"{cred.base_url}/version",
        auth=cred.auth,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    if r.status_code >= 400:
        raise JiraError(f"Fix Version 생성 실패 ({r.status_code}) name={name}: {r.text}")
    return r.json()


def list_components(cred: JiraCredentials, *, timeout: float = 15.0) -> list[dict[str, Any]]:
    """GET /project/{key}/components — 프로젝트 내 모든 Component."""
    r = httpx.get(
        f"{cred.base_url}/project/{cred.project_key}/components",
        auth=cred.auth,
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def create_component(
    cred: JiraCredentials,
    *,
    name: str,
    description: str = "",
    timeout: float = 15.0,
) -> dict[str, Any]:
    """POST /component — Component 생성."""
    body: dict[str, Any] = {"project": cred.project_key, "name": name}
    if description:
        body["description"] = description
    r = httpx.post(
        f"{cred.base_url}/component",
        auth=cred.auth,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    if r.status_code >= 400:
        raise JiraError(f"Component 생성 실패 ({r.status_code}) name={name}: {r.text}")
    return r.json()


def list_tasks_with_ext_ids(cred: JiraCredentials, ext_id_field: str = "customfield_10100") -> dict[str, str]:
    """
    Task 의 External ID → Jira Key 매핑.

    ext_id_field 는 Jira 프로젝트마다 다름. 표준 "External issue ID" 를 custom field 로
    매핑해야 함 (import wizard 의 External ID 필드). 모르면 summary prefix 기반 fallback.

    반환: {"DT-T1.1.1": "DT-83", "DT-T1.2.3": "DT-95", ...}
    """
    issues = search_jql(
        cred,
        jql=f"project = {cred.project_key} AND issuetype = Task",
        fields=["summary", ext_id_field],
    )
    mapping: dict[str, str] = {}
    for iss in issues:
        key = iss["key"]
        ext = (iss.get("fields") or {}).get(ext_id_field) or ""
        if ext:
            mapping[ext] = key
        else:
            # fallback: summary 앞부분의 External ID (예: "[DT-T1.1.1] ...")
            summary = (iss.get("fields") or {}).get("summary") or ""
            if summary.startswith("["):
                end = summary.find("]")
                if end > 0:
                    mapping[summary[1:end]] = key
    return mapping


def search_users(cred: JiraCredentials, query: str, *, timeout: float = 15.0) -> list[dict[str, Any]]:
    """GET /user/assignable/search — displayName/email 로 accountId 조회."""
    import urllib.parse as _up
    r = httpx.get(
        f"{cred.base_url}/user/assignable/search",
        auth=cred.auth,
        params={"project": cred.project_key, "query": query},
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def list_fields(cred: JiraCredentials, *, timeout: float = 15.0) -> list[dict[str, Any]]:
    """전체 field 목록 — `/field` + `/field/search` 의 union.

    Jira Cloud 의 두 endpoint 는 **상호 배타적인 set** 을 반환하는 알려진 이상 동작:
      - `/field`        : 옛 글로벌 custom field 일부만 (예: 10117 External ID, 10118 Paper Role)
      - `/field/search` : 새 team-managed scoped field 일부만 (예: 10072 Paper-Role)
    둘 다 호출하고 id 단위로 dedup 해야 모든 필드를 잡을 수 있다.
    """
    by_id: dict[str, dict[str, Any]] = {}

    # /field (legacy, 비페이지네이션)
    try:
        r = httpx.get(
            f"{cred.base_url}/field",
            auth=cred.auth,
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        r.raise_for_status()
        for f in r.json() or []:
            if f.get("id"):
                by_id[f["id"]] = f
    except Exception:
        pass

    # /field/search (페이지네이션)
    start_at = 0
    while True:
        r = httpx.get(
            f"{cred.base_url}/field/search",
            auth=cred.auth,
            params={"startAt": start_at, "maxResults": 100},
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        r.raise_for_status()
        d = r.json()
        for f in d.get("values") or []:
            if f.get("id"):
                by_id.setdefault(f["id"], f)  # /field 결과 우선 보존
        if d.get("isLast", True):
            break
        start_at += d.get("maxResults", 100)

    return list(by_id.values())


def list_issuetypes(cred: JiraCredentials, *, timeout: float = 15.0) -> list[dict[str, Any]]:
    """GET /project/{key} 의 issueTypes. id/name/subtask 포함."""
    r = httpx.get(
        f"{cred.base_url}/project/{cred.project_key}",
        auth=cred.auth,
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json().get("issueTypes") or []


def find_issue_by_ext_id(
    cred: JiraCredentials,
    ext_id: str,
    *,
    ext_id_field: str = "customfield_10117",
    timeout: float = 15.0,
) -> str | None:
    """ext_id custom field 값으로 기존 이슈 검색. 멱등성 체크용.

    JQL 의 custom field 참조는 `cf[NNNNN]` 형식이 안전 — `"customfield_NNNNN"`
    (큰따옴표 감싸기) 는 일부 환경에서 매칭 실패.
    """
    cf_id = ext_id_field.replace("customfield_", "")
    # ext_id 안의 따옴표 escape (Jira JQL: \\" 형식)
    safe = ext_id.replace('\\', '\\\\').replace('"', '\\"')
    issues = search_jql(
        cred,
        jql=f'project = {cred.project_key} AND cf[{cf_id}] = "{safe}"',
        fields=[ext_id_field, "summary"],
        max_results=2,
    )
    if not issues:
        return None
    return issues[0]["key"]


def create_issue(
    cred: JiraCredentials,
    fields: dict[str, Any],
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """POST /issue — 이슈 생성. fields 는 호출자가 완전한 페이로드로 구성."""
    r = httpx.post(
        f"{cred.base_url}/issue",
        auth=cred.auth,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json={"fields": fields},
        timeout=timeout,
    )
    if r.status_code >= 400:
        raise JiraError(f"이슈 생성 실패 ({r.status_code}): {r.text}")
    return r.json()


def update_issue(
    cred: JiraCredentials,
    key: str,
    fields: dict[str, Any],
    *,
    timeout: float = 30.0,
) -> None:
    """PUT /issue/{key} — 필드 업데이트. 204 또는 200 응답."""
    r = httpx.put(
        f"{cred.base_url}/issue/{key}",
        auth=cred.auth,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json={"fields": fields},
        timeout=timeout,
    )
    if r.status_code >= 400:
        raise JiraError(f"이슈 {key} 업데이트 실패 ({r.status_code}): {r.text}")


def md_to_adf(text: str) -> dict[str, Any]:
    """간단 markdown(또는 plain text) → ADF 변환.

    빈 줄로 paragraph 분할, paragraph 내부 줄바꿈은 hardBreak.
    Heading(##) 같은 markdown 구문은 plain text 로 들어감 — 손실 적은 변환.
    """
    paras: list[dict[str, Any]] = []
    if not text:
        return {"version": 1, "type": "doc", "content": [{"type": "paragraph", "content": []}]}
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        lines = block.split("\n")
        content: list[dict[str, Any]] = []
        for i, ln in enumerate(lines):
            if i > 0:
                content.append({"type": "hardBreak"})
            if ln:
                content.append({"type": "text", "text": ln})
        paras.append({"type": "paragraph", "content": content or []})
    return {"version": 1, "type": "doc", "content": paras or [{"type": "paragraph", "content": []}]}


__all__ = [
    "JiraError",
    "JiraCredentials",
    "credentials_from_env",
    "verify_credentials",
    "verify_project",
    "search_jql",
    "list_tasks_with_ext_ids",
    "list_versions",
    "create_version",
    "list_components",
    "create_component",
    "search_users",
    "list_fields",
    "list_issuetypes",
    "find_issue_by_ext_id",
    "create_issue",
    "update_issue",
    "md_to_adf",
]

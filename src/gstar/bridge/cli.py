"""
bin/bridge CLI — bridge 서브커맨드 dispatcher.

서브커맨드:
  init          bridge.yaml skeleton 생성 (project 루트에서 실행)
  check-jira    Jira 토큰·프로젝트 접근 검증 (JIRA_* env 필요)
  csv           bridge.yaml + jira-breakdown.md 기반 3-file CSV 생성
  provision     bridge.yaml 의 fix_versions/components 를 Jira REST 로 멱등 생성
  import-issues Epic CSV 를 wizard 로 import 한 뒤 Task/Sub-task 를 REST 로 생성
                (Epic 누락 필드 PATCH + Task/Sub-task 풀필드, 멱등)
  update-issues plan.json 의 데이터를 기존 이슈에 PATCH (--fields, --create-missing, --dry-run)
  refresh-subtasks   (legacy CSV 워크플로우) Task External→Key 매핑 후 CSV 갱신
  ri-init       각 Task 에 dev/ri/<KEY>/ 8 단 폴더 scaffolding
  ri-rename     Task import 후 External ID 폴더 → Jira Key 폴더 rename
  env-example   ~/.config/<slug>/env.sh 스켈레톤 생성 (프로젝트별 Jira 설정)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from gstar.bridge.config import load_config, example_config_yaml
from gstar.bridge.jira_client import (
    credentials_from_env, verify_credentials, verify_project,
    list_tasks_with_ext_ids, JiraError,
    list_versions, create_version, list_components, create_component,
    search_users, list_fields, list_issuetypes,
    find_issue_by_ext_id, create_issue, update_issue, md_to_adf,
    search_jql,
)
from gstar.bridge.csv_gen import refresh_subtasks as _refresh_csv
from gstar.bridge.ri_scaffold import rename_folders_to_jira_keys


def _project_dir(args) -> Path:
    pd = Path(args.project_dir or os.getcwd()).resolve()
    if not pd.exists():
        print(f"오류: project-dir 없음 — {pd}", file=sys.stderr)
        sys.exit(1)
    return pd


# ── 공통 헬퍼 (import-issues / update-issues 공유) ─────────────────────
def _norm_name(s: str) -> str:
    """필드 이름 정규화 — 알파넘만 남기고 소문자. 'Paper Role' / 'Paper-Role' 둘 다 'paperrole'."""
    return "".join(c for c in (s or "").lower() if c.isalnum())


def _pick_cf(all_fields: list[dict], *candidate_names: str) -> str | None:
    """
    candidate_names 중 하나라도 매칭되는 custom field id 반환. 매칭 규칙:
      1. _norm_name 으로 같은 정규화 키를 가진 필드 후보 수집
      2. 하이픈 포함 표기 ('Paper-Role') 가 공백 표기 ('Paper Role') 보다 우선
      3. 동률이면 customfield_id 가 **작은** 것 (= 먼저 만들어 layout 등록 가능성↑) 우선

    중복 필드 (같은 이름의 필드가 두 개 이상) 시 layout 미등록 risk 가 있다.
    Jira Cloud 의 일부 endpoint 가 새로 만든 custom field 를 issue screen 에 자동
    추가하지 않으므로, id 작은 (먼저 만든) 필드를 default 로 선택하는 것이 안전.
    bridge.yaml 의 custom_fields.field_overrides 로 명시 매핑 가능.
    """
    by_norm: dict[str, list[dict]] = {}
    for f in all_fields:
        by_norm.setdefault(_norm_name(f.get("name", "")), []).append(f)
    for nm in candidate_names:
        candidates = by_norm.get(_norm_name(nm), [])
        if not candidates:
            continue
        hyphenated = [f for f in candidates if "-" in (f.get("name") or "")]
        pool = hyphenated if hyphenated else candidates
        try:
            return min(pool, key=lambda f: int(str(f["id"]).replace("customfield_", "") or "0"))["id"]
        except Exception:
            return pool[0]["id"]
    return None


def _collect_meta(cred, cfg, plan: dict) -> dict:
    """
    사전 정보 수집: Issue Type id · Custom field id 매핑 · accountId 매핑.
    실패 시 JiraError 또는 RuntimeError 던짐. 호출자가 try/except 처리.

    반환 dict 키:
      type_task, type_subtask: str (Issue Type id)
      cf: dict (plan.json key → customfield_id)
      acc: dict (displayName → accountId)
      missing_cf: list (plan 에서 쓰는데 Jira 에 없는 cf key 들 — patent_role 제외)
    """
    itypes = list_issuetypes(cred)
    all_fields = list_fields(cred)

    type_task = type_subtask = None
    for t in itypes:
        n = t.get("name", "")
        if t.get("subtask"):
            type_subtask = t["id"]
        elif n in ("Task", "작업", "태스크"):
            type_task = t["id"]
    if not type_task or not type_subtask:
        raise RuntimeError(
            f"Issue Type 매칭 실패: {[(t['id'],t['name'],t.get('subtask')) for t in itypes]}"
        )

    cf: dict[str, str | None] = {
        "story_points":      _pick_cf(all_fields, "Story point estimate", "Story Points"),
        "start_date":        _pick_cf(all_fields, "Start date"),  # 잠금 시스템 필드 (customfield_10015)
        "issue_color":       _pick_cf(all_fields, "Issue color"),  # 잠금 시스템 필드 (customfield_10017)
        "external_id":       _pick_cf(all_fields, "External ID", "External-ID"),
        "hypothesis":        _pick_cf(all_fields, "Hypothesis"),
        "paper_role":        _pick_cf(all_fields, "Paper Role", "Paper-Role"),
        "paper_venue_grade": _pick_cf(all_fields, "Paper Venue Grade", "Paper-Venue-Grade"),
        "patent_role":       _pick_cf(all_fields, "Patent Role", "Patent-Role"),
        "patent_id":         _pick_cf(all_fields, "Patent ID", "Patent-ID"),
        "paper_target":      _pick_cf(all_fields, "Paper Target", "Paper-Target"),
        "gpu_hours":         _pick_cf(all_fields, "GPU Hours", "GPU-Hours"),
        "gate_id":           _pick_cf(all_fields, "Gate ID", "Gate-ID"),
    }

    # plan.json 에 실제 사용되는 키 중 cf 가 없는 것 (patent 류는 옵셔널)
    OPTIONAL = {"patent_role", "patent_id", "paper_target"}
    missing_cf = [k for k, v in cf.items() if not v and k not in OPTIONAL]

    # accountId 매핑
    names = {cfg.pi.display_name, *(m.display_name for m in cfg.members)}
    for sec in ("epics", "tasks", "subtasks"):
        for it in plan.get(sec) or []:
            for k in ("assignee", "reporter"):
                v = (it.get(k) or "").strip()
                if v:
                    names.add(v)
    acc: dict[str, str] = {}
    for n in names:
        try:
            users = search_users(cred, n)
            if users:
                acc[n] = users[0]["accountId"]
        except Exception:
            pass

    return {
        "type_task": type_task,
        "type_subtask": type_subtask,
        "cf": cf,
        "acc": acc,
        "missing_cf": missing_cf,
    }


def _build_issue_fields(
    cfg, meta: dict, d: dict, *, parent_key: str | None, is_subtask: bool,
    only_fields: set[str] | None = None,
) -> dict:
    """
    Jira REST `fields` 페이로드 빌더. import (POST) / update (PUT) 둘 다 사용.

    only_fields: 지정하면 해당 키들만 페이로드에 포함. update --fields 옵션용.
    """
    cf = meta["cf"]
    acc = meta["acc"]

    def included(k: str) -> bool:
        return only_fields is None or k in only_fields

    f: dict = {}
    # 생성 시 필수
    if only_fields is None:
        f["project"] = {"key": cfg.project_key}
        f["issuetype"] = {"id": meta["type_subtask"] if is_subtask else meta["type_task"]}
        f["summary"] = d["summary"]
        if parent_key:
            f["parent"] = {"key": parent_key}
    else:
        # update 모드에서도 summary/parent 변경 명시 시 허용
        if "summary" in only_fields:
            f["summary"] = d["summary"]
        if "parent" in only_fields and parent_key:
            f["parent"] = {"key": parent_key}

    if included("description") and d.get("description"):
        f["description"] = md_to_adf(d["description"])
    if included("priority"):
        f["priority"] = {"name": d.get("priority", "High")}
    if included("labels"):
        f["labels"] = list(d.get("labels") or [])

    a = (d.get("assignee") or "").strip()
    if included("assignee") and a in acc:
        f["assignee"] = {"accountId": acc[a]}
    r = (d.get("reporter") or "").strip() or cfg.pi.display_name
    if included("reporter") and r in acc:
        f["reporter"] = {"accountId": acc[r]}

    if included("due_date") and d.get("due_date"):
        f["duedate"] = d["due_date"]
    if included("start_date") and d.get("start_date") and cf.get("start_date"):
        f[cf["start_date"]] = d["start_date"]
    if included("fix_version") and d.get("fix_version"):
        f["fixVersions"] = [{"name": d["fix_version"]}]
    if included("component") and d.get("component"):
        f["components"] = [{"name": d["component"]}]

    sp = d.get("story_points")
    if included("story_points") and sp is not None and cf["story_points"]:
        f[cf["story_points"]] = sp

    # custom single-select
    for plan_key in ("hypothesis", "paper_role", "paper_venue_grade", "patent_role", "gate_id"):
        if not included(plan_key):
            continue
        v = d.get(plan_key) or ""
        if isinstance(v, str):
            v = v.strip()
        if v and cf.get(plan_key):
            f[cf[plan_key]] = {"value": v}

    # custom text/number
    if included("ext_id") and d.get("ext_id") and cf["external_id"]:
        f[cf["external_id"]] = d["ext_id"]
    if included("patent_id") and (d.get("patent_id") or "").strip() and cf.get("patent_id"):
        f[cf["patent_id"]] = d["patent_id"].strip()
    if included("paper_target") and (d.get("paper_target") or "").strip() and cf.get("paper_target"):
        f[cf["paper_target"]] = d["paper_target"].strip()

    if included("gpu_hours"):
        gh = d.get("gpu_hours")
        if isinstance(gh, (int, float)) and gh > 0 and cf.get("gpu_hours"):
            f[cf["gpu_hours"]] = gh

    # Epic color: bridge.yaml 의 epics 정의에서 ext_id 매칭하면 issue_color 자동 PATCH
    # (sub-task 가 아니고 epic ext_id 가 cfg.epics 에 있을 때만)
    if included("color") and not is_subtask:
        epic_def = next((e for e in cfg.epics if e.ext_id == d.get("ext_id")), None)
        if epic_def and epic_def.color and cf.get("issue_color"):
            f[cf["issue_color"]] = epic_def.color

    return f


# ── init ─────────────────────────────────────────────────────────────
def cmd_init(args: argparse.Namespace) -> int:
    pd = _project_dir(args)
    bridge_dir = pd / "09-bridge"
    bridge_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = bridge_dir / "bridge.yaml"
    if yaml_path.exists() and not args.force:
        print(f"! {yaml_path} 이미 존재 — --force 로 덮어쓰기")
        return 1
    project_key = args.project_key or "P"
    project_name = args.project_name or pd.name
    yaml_path.write_text(example_config_yaml(project_key, project_name), encoding="utf-8")
    print(f"✓ {yaml_path} 생성 (project_key={project_key}, project_name={project_name})")
    print("  → bridge.yaml 을 편집해 팀·Fix Versions·Components·Epic 확정 후 `bin/bridge csv`.")
    return 0


# ── env-example ──────────────────────────────────────────────────────
def cmd_env_example(args: argparse.Namespace) -> int:
    pd = _project_dir(args)
    cfg_path = pd / "09-bridge" / "bridge.yaml"
    slug = args.slug
    if not slug and cfg_path.exists():
        cfg = load_config(cfg_path)
        slug = cfg.project_slug
    slug = slug or pd.name
    env_dir = Path.home() / ".config" / slug
    env_dir.mkdir(parents=True, exist_ok=True)
    env_path = env_dir / "env.sh"
    if env_path.exists() and not args.force:
        print(f"! {env_path} 이미 존재 — --force 로 덮어쓰기")
        return 1
    env_path.write_text(f"""# Jira 자격 — {slug}
# 파일 권한: chmod 600 {env_path}
#
# 토큰 발급: https://id.atlassian.com/manage-profile/security/api-tokens

export JIRA_SITE="your-org.atlassian.net"
export JIRA_EMAIL="you@example.com"
export JIRA_TOKEN=""            # 여기에 Atlassian API 토큰 붙여넣기
export JIRA_PROJECT=""          # Jira 프로젝트 키 (예: DT). 웹에서 프로젝트 먼저 생성 후 키 확인.

# 사용:
#   source {env_path}
#   bash $LOCAL_CLAUDE_HOME/bin/bridge check-jira
""", encoding="utf-8")
    try:
        env_path.chmod(0o600)
    except OSError:
        pass
    print(f"✓ {env_path} 생성 (chmod 600)")
    print(f"  편집 후: source {env_path} && bin/bridge check-jira")
    return 0


# ── check-jira ────────────────────────────────────────────────────────
def cmd_check_jira(args: argparse.Namespace) -> int:
    try:
        cred = credentials_from_env()
    except JiraError as e:
        print(f"✗ {e}", file=sys.stderr); return 1
    try:
        me = verify_credentials(cred)
    except Exception as e:
        print(f"✗ /myself 실패: {e}", file=sys.stderr); return 2
    print(f"✓ 인증 OK — {me.get('displayName','?')} <{me.get('emailAddress','?')}>")
    try:
        proj = verify_project(cred)
    except Exception as e:
        print(f"✗ 프로젝트 {cred.project_key} 접근 실패: {e}", file=sys.stderr); return 3
    print(f"✓ 프로젝트 OK — {proj.get('name','?')} ({proj.get('key')}) · {proj.get('projectTypeKey','?')}")
    return 0


# ── csv ──────────────────────────────────────────────────────────────
def cmd_csv(args: argparse.Namespace) -> int:
    """
    NOTE: 이 CLI 는 CSV 생성을 위한 "최소" 경로만 제공. Epic/Task/Sub-task 본문 입력은
    (a) Claude `/jw:award-to-dev` skill 이 `plan.json` 으로 구조화하거나
    (b) 사용자가 직접 `09-bridge/plan.json` 을 작성
    하는 두 방식을 모두 받는다.

    plan.json 스키마:
    {
      "epics": [{"ext_id":"DT-E1","summary":"...","description":"...","labels":["..."],...}],
      "tasks": [{"ext_id":"DT-T1.1.1","summary":"...","description":"...","epic_link":"DT-E1",...}],
      "subtasks": [{"ext_id":"DT-ST1.1.1a","summary":"...","description":"...","parent_ref":"DT-T1.1.1",...}]
    }
    """
    from gstar.bridge.csv_gen import (
        EpicRow, TaskRow, SubtaskRow, generate_csvs, write_import_readme,
    )
    pd = _project_dir(args)
    cfg_path = pd / "09-bridge" / "bridge.yaml"
    plan_path = pd / "09-bridge" / "plan.json"
    if not cfg_path.exists():
        print(f"✗ {cfg_path} 없음. `bin/bridge init` 먼저.", file=sys.stderr); return 1
    if not plan_path.exists():
        print(f"✗ {plan_path} 없음. /jw:award-to-dev skill 또는 수동 작성 필요.", file=sys.stderr); return 1
    cfg = load_config(cfg_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    def _epic(d: dict) -> EpicRow:
        return EpicRow(
            ext_id=d["ext_id"], summary=d["summary"], description=d.get("description",""),
            labels=list(d.get("labels") or []), priority=d.get("priority","High"),
            epic_name=d.get("epic_name") or d["summary"],
            due_date=d.get("due_date",""),
            assignee=d.get("assignee",""), reporter=d.get("reporter",""),
        )
    def _task(d: dict) -> TaskRow:
        return TaskRow(
            ext_id=d["ext_id"], summary=d["summary"], description=d.get("description",""),
            epic_link=d["epic_link"], priority=d.get("priority","High"),
            labels=list(d.get("labels") or []),
            story_points=d.get("story_points"),
            assignee=d.get("assignee",""), reporter=d.get("reporter",""),
            due_date=d.get("due_date",""), fix_version=d.get("fix_version",""),
            component=d.get("component",""),
        )
    def _sub(d: dict) -> SubtaskRow:
        return SubtaskRow(
            ext_id=d["ext_id"], summary=d["summary"], description=d.get("description",""),
            parent_ref=d["parent_ref"], priority=d.get("priority","Medium"),
            labels=list(d.get("labels") or []),
            story_points=d.get("story_points"),
            assignee=d.get("assignee",""), reporter=d.get("reporter",""),
            due_date=d.get("due_date",""), fix_version=d.get("fix_version",""),
            component=d.get("component",""),
        )

    epics = [_epic(e) for e in plan.get("epics") or []]
    tasks = [_task(t) for t in plan.get("tasks") or []]
    subs = [_sub(s) for s in plan.get("subtasks") or []]

    counts = generate_csvs(cfg, epics=epics, tasks=tasks, subtasks=subs, out_dir=pd / "09-bridge")
    for name, n in counts.items():
        print(f"✓ {name}: {n} rows")
    readme = write_import_readme(cfg, pd / "09-bridge", counts)
    print(f"✓ {readme.name} 갱신")
    return 0


# ── refresh-subtasks ─────────────────────────────────────────────────
def cmd_refresh_subtasks(args: argparse.Namespace) -> int:
    from gstar.bridge.csv_gen import SubtaskRow
    pd = _project_dir(args)
    cfg = load_config(pd / "09-bridge" / "bridge.yaml")
    plan = json.loads((pd / "09-bridge" / "plan.json").read_text(encoding="utf-8"))
    try:
        cred = credentials_from_env()
    except JiraError as e:
        print(f"✗ {e}", file=sys.stderr); return 1
    ext_to_key = list_tasks_with_ext_ids(cred, ext_id_field=args.ext_id_field)
    # 매핑 저장
    (pd / "09-bridge" / "task-keys.json").write_text(
        json.dumps(ext_to_key, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"✓ Task key 매핑 {len(ext_to_key)} 건 → task-keys.json")
    subs = [
        SubtaskRow(
            ext_id=s["ext_id"], summary=s["summary"], description=s.get("description",""),
            parent_ref=s["parent_ref"], priority=s.get("priority","Medium"),
            labels=list(s.get("labels") or []),
            story_points=s.get("story_points"),
            assignee=s.get("assignee",""), reporter=s.get("reporter",""),
            due_date=s.get("due_date",""), fix_version=s.get("fix_version",""),
            component=s.get("component",""),
        )
        for s in (plan.get("subtasks") or [])
    ]
    updated, unmatched = _refresh_csv(cfg, subtasks=subs, task_key_map=ext_to_key,
                                      out_dir=pd / "09-bridge")
    print(f"✓ jira-import-3-subtasks.csv 갱신 — updated={updated} unmatched={unmatched}")
    if unmatched:
        print("  ! unmatched > 0 — 해당 Sub-task Parent 는 여전히 External ID. Task import 먼저 완료했는지 확인.")
    return 0


# ── provision ────────────────────────────────────────────────────────
def cmd_provision(args: argparse.Namespace) -> int:
    """
    bridge.yaml 의 fix_versions / components 를 Jira REST API 로 멱등 생성.

    - 이미 존재하는 이름은 스킵 (status=exists).
    - --dry-run 은 호출 없이 plan 만 출력.
    - --versions-only / --components-only 로 한쪽만 실행.
    """
    pd = _project_dir(args)
    cfg = load_config(pd / "09-bridge" / "bridge.yaml")
    do_versions = not args.components_only
    do_components = not args.versions_only

    # JIRA_PROJECT 가 env 에 없으면 bridge.yaml 의 project.key 로 fallback (provision 한정).
    if not os.environ.get("JIRA_PROJECT"):
        os.environ["JIRA_PROJECT"] = cfg.project_key
    try:
        cred = credentials_from_env()
    except JiraError as e:
        print(f"✗ {e}", file=sys.stderr); return 1
    if cred.project_key != cfg.project_key:
        print(
            f"! JIRA_PROJECT={cred.project_key} 와 bridge.yaml project.key={cfg.project_key} 불일치. "
            f"env.sh 확인.", file=sys.stderr,
        )
        return 1

    rc = 0

    if do_versions:
        print(f"━━ Fix Versions (project={cfg.project_key}) ━━")
        try:
            existing = {v["name"]: v for v in list_versions(cred)}
        except Exception as e:
            print(f"✗ list_versions 실패: {e}", file=sys.stderr); return 2
        for fv in cfg.fix_versions:
            if fv.id in existing:
                ev = existing[fv.id]
                print(f"  · {fv.id:<18} exists   id={ev.get('id')} "
                      f"start={ev.get('startDate','-')} release={ev.get('releaseDate','-')}")
                continue
            if args.dry_run:
                print(f"  + {fv.id:<18} would-create  start={fv.start or '-'} release={fv.due or '-'}")
                continue
            try:
                resp = create_version(
                    cred,
                    name=fv.id,
                    description=fv.label + (f" — {fv.gate}" if fv.gate else ""),
                    start_date=fv.start,
                    release_date=fv.due,
                )
                print(f"  + {fv.id:<18} created  id={resp.get('id')} "
                      f"start={resp.get('startDate','-')} release={resp.get('releaseDate','-')}")
            except JiraError as e:
                print(f"  ✗ {fv.id:<18} {e}", file=sys.stderr); rc = 3

    if do_components:
        print(f"━━ Components (project={cfg.project_key}) ━━")
        try:
            existing_c = {c["name"]: c for c in list_components(cred)}
        except Exception as e:
            print(f"✗ list_components 실패: {e}", file=sys.stderr); return 2
        for name in cfg.components:
            if name in existing_c:
                print(f"  · {name:<24} exists   id={existing_c[name].get('id')}")
                continue
            if args.dry_run:
                print(f"  + {name:<24} would-create")
                continue
            try:
                resp = create_component(cred, name=name)
                print(f"  + {name:<24} created  id={resp.get('id')}")
            except JiraError as e:
                print(f"  ✗ {name:<24} {e}", file=sys.stderr); rc = 3

    return rc


# ── import-issues ─────────────────────────────────────────────────────
def cmd_import_issues(args: argparse.Namespace) -> int:
    """
    Epic 만 wizard 로 import 된 상태에서 시작:
      Phase 1: Jira 의 Epic 검색 (summary 매칭) + 누락 필드 PATCH (description/priority/labels)
      Phase 2: Task 47건 REST POST (모든 필드 + custom + Epic Link)
      Phase 3: Sub-task 42건 REST POST (모든 필드 + custom + Parent)

    멱등: 각 이슈는 ext_id custom field 로 검색해서 이미 있으면 skip.
    """
    pd = _project_dir(args)
    cfg = load_config(pd / "09-bridge" / "bridge.yaml")
    plan_path = pd / "09-bridge" / "plan.json"
    if not plan_path.exists():
        print(f"✗ {plan_path} 없음. /jw:award-to-dev 또는 /re:award-to-dev 먼저.", file=sys.stderr); return 1
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    if not os.environ.get("JIRA_PROJECT"):
        os.environ["JIRA_PROJECT"] = cfg.project_key
    try:
        cred = credentials_from_env()
    except JiraError as e:
        print(f"✗ {e}", file=sys.stderr); return 1
    if cred.project_key != cfg.project_key:
        print(f"! JIRA_PROJECT={cred.project_key} ≠ bridge.yaml project.key={cfg.project_key}", file=sys.stderr)
        return 1

    # ── 사전 정보 수집 ──────────────────────────────────────────────
    print(f"━━ 사전 정보 수집 (project={cfg.project_key}) ━━")
    try:
        meta = _collect_meta(cred, cfg, plan)
    except Exception as e:
        print(f"✗ 메타 조회 실패: {e}", file=sys.stderr); return 2
    cf = meta["cf"]
    missing_cf = meta["missing_cf"]
    print(f"  ✓ Issue Type: Task={meta['type_task']}  Sub-task={meta['type_subtask']}")
    for k, v in cf.items():
        mark = "✓" if v else "✗"
        print(f"  {mark} cf['{k}'] = {v or '(없음)'}")
    if cf["external_id"] is None:
        print("✗ External ID custom field 가 필수 (멱등성 키). Jira 에서 'External ID' (Short text) 만들어 주세요.", file=sys.stderr)
        return 2
    print(f"  ✓ accountId 매핑 {len(meta['acc'])}건: {list(meta['acc'].keys())}")

    # ── Phase 1: Epic PATCH ─────────────────────────────────────────
    print(f"\n━━ Phase 1: Epic {len(plan.get('epics') or [])}건 PATCH ━━")
    epic_jql = f'project = {cfg.project_key} AND issuetype in (Epic, "에픽")'
    try:
        existing_epics = search_jql(cred, jql=epic_jql, fields=["summary", cf["external_id"]])
    except Exception:
        existing_epics = search_jql(cred, jql=f'project = {cfg.project_key}', fields=["summary", "issuetype"])
        existing_epics = [e for e in existing_epics if (e["fields"].get("issuetype") or {}).get("name") in ("Epic", "에픽")]
    epic_by_summary = {e["fields"]["summary"]: e["key"] for e in existing_epics}
    epic_ext_to_key: dict[str, str] = {}
    epic_ok = epic_skip = epic_fail = 0
    for e in plan.get("epics") or []:
        key = epic_by_summary.get(e["summary"])
        if not key:
            print(f"  ⚠ {e['ext_id']:<8} summary 매칭 실패 — wizard 로 import 했는지 확인: {e['summary'][:50]}")
            epic_fail += 1
            continue
        epic_ext_to_key[e["ext_id"]] = key
        # 누락 필드 PATCH
        f: dict = {
            "description": md_to_adf(e.get("description", "")),
            "priority": {"name": e.get("priority", "High")},
            "labels": list(e.get("labels") or []),
        }
        if cf["external_id"]:
            f[cf["external_id"]] = e["ext_id"]
        try:
            update_issue(cred, key, f)
            print(f"  ✓ {key:<6} ← {e['ext_id']:<8} pri={e.get('priority'):<7} labels={len(f['labels'])}")
            epic_ok += 1
        except JiraError as ex:
            print(f"  ✗ {key:<6} ← {e['ext_id']:<8} {ex}", file=sys.stderr); epic_fail += 1
    print(f"  Epic: {epic_ok} 성공 / {epic_skip} skip / {epic_fail} 실패")

    if epic_fail and not args.skip_epic_fail:
        print("\n✗ Epic 매칭 실패가 있어 중단. wizard 로 Epic CSV import 먼저 (요약·이슈타입 매핑).", file=sys.stderr)
        print("  계속 진행하려면 --skip-epic-fail 사용.", file=sys.stderr)
        return 3

    # 페이로드 빌더는 _build_issue_fields(cfg, meta, ...) 모듈 헬퍼 사용

    # ── Phase 2: Task ───────────────────────────────────────────────
    print(f"\n━━ Phase 2: Task {len(plan.get('tasks') or [])}건 ━━")
    task_ext_to_key: dict[str, str] = {}
    t_ok = t_skip = t_fail = 0
    for t in plan.get("tasks") or []:
        # 멱등 체크
        existing = find_issue_by_ext_id(cred, t["ext_id"], ext_id_field=cf["external_id"])
        if existing:
            task_ext_to_key[t["ext_id"]] = existing
            t_skip += 1
            print(f"  · {existing:<6} ← {t['ext_id']:<14} exists (skip)")
            continue
        epic_key = epic_ext_to_key.get(t.get("epic_link", ""))
        try:
            resp = create_issue(cred, _build_issue_fields(cfg, meta, t, parent_key=epic_key, is_subtask=False))
            task_ext_to_key[t["ext_id"]] = resp["key"]
            t_ok += 1
            print(f"  ✓ {resp['key']:<6} ← {t['ext_id']:<14} {t['summary'][:50]}")
        except JiraError as ex:
            t_fail += 1
            print(f"  ✗ {t['ext_id']:<14} {str(ex)[:200]}", file=sys.stderr)
    print(f"  Task: {t_ok} 생성 / {t_skip} skip / {t_fail} 실패")

    # task ext_id ↔ key 매핑 저장 (RI scaffold rename 용)
    (pd / "09-bridge" / "task-keys-rest.json").write_text(
        json.dumps(task_ext_to_key, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    # ── Phase 3: Sub-task ───────────────────────────────────────────
    print(f"\n━━ Phase 3: Sub-task {len(plan.get('subtasks') or [])}건 ━━")
    s_ok = s_skip = s_fail = s_miss = 0
    for s in plan.get("subtasks") or []:
        existing = find_issue_by_ext_id(cred, s["ext_id"], ext_id_field=cf["external_id"])
        if existing:
            s_skip += 1
            print(f"  · {existing:<6} ← {s['ext_id']:<16} exists (skip)")
            continue
        parent_key = task_ext_to_key.get(s.get("parent_ref", ""))
        if not parent_key:
            s_miss += 1
            print(f"  ⚠ {s['ext_id']:<16} parent={s.get('parent_ref')} 매칭 실패")
            continue
        try:
            resp = create_issue(cred, _build_issue_fields(cfg, meta, s, parent_key=parent_key, is_subtask=True))
            s_ok += 1
            print(f"  ✓ {resp['key']:<6} ← {s['ext_id']:<16} parent={parent_key}")
        except JiraError as ex:
            s_fail += 1
            print(f"  ✗ {s['ext_id']:<16} {str(ex)[:200]}", file=sys.stderr)
    print(f"  Sub-task: {s_ok} 생성 / {s_skip} skip / {s_miss} parent 미매칭 / {s_fail} 실패")

    # ── 요약 ────────────────────────────────────────────────────────
    print(f"\n━━ 요약 ━━")
    print(f"  Epic    : {epic_ok} PATCH / {epic_fail} 실패")
    print(f"  Task    : {t_ok} 생성 / {t_skip} skip / {t_fail} 실패")
    print(f"  Sub-task: {s_ok} 생성 / {s_skip} skip / {s_miss} parent 미매칭 / {s_fail} 실패")
    if missing_cf:
        print(f"  ⚠ 누락된 custom field: {missing_cf} — 해당 필드 데이터는 채워지지 않음")
    return 0 if (epic_fail + t_fail + s_fail + s_miss) == 0 else 4


# ── update-issues ─────────────────────────────────────────────────────
def cmd_update_issues(args: argparse.Namespace) -> int:
    """
    plan.json 의 데이터를 기존 Jira 이슈에 PATCH (모든 필드 재동기화).

    - 멱등: 이슈는 ext_id custom field 로 검색해서 이미 있으면 PATCH, 없으면 skip
    - --create-missing: 없는 이슈는 새로 생성 (= import-issues 와 합체 모드)
    - --fields key1,key2: 지정한 키만 PATCH (예: --fields description,priority,labels)
    - --dry-run: API 호출 없이 변경 대상만 리스트
    """
    pd = _project_dir(args)
    cfg = load_config(pd / "09-bridge" / "bridge.yaml")
    plan_path = pd / "09-bridge" / "plan.json"
    if not plan_path.exists():
        print(f"✗ {plan_path} 없음.", file=sys.stderr); return 1
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    if not os.environ.get("JIRA_PROJECT"):
        os.environ["JIRA_PROJECT"] = cfg.project_key
    try:
        cred = credentials_from_env()
    except JiraError as e:
        print(f"✗ {e}", file=sys.stderr); return 1

    only_fields: set[str] | None = None
    if args.fields:
        only_fields = {s.strip() for s in args.fields.split(",") if s.strip()}
        print(f"━━ --fields 지정: {sorted(only_fields)} ━━")

    print(f"━━ 사전 정보 수집 (project={cfg.project_key}) ━━")
    try:
        meta = _collect_meta(cred, cfg, plan)
    except Exception as e:
        print(f"✗ 메타 조회 실패: {e}", file=sys.stderr); return 2
    cf = meta["cf"]
    if not cf["external_id"]:
        print("✗ External ID custom field 가 필수 (이슈 매칭 키).", file=sys.stderr); return 2
    print(f"  ✓ Issue Type: Task={meta['type_task']}  Sub-task={meta['type_subtask']}")
    for k, v in cf.items():
        if v: print(f"  ✓ cf['{k}'] = {v}")
        else: print(f"  ✗ cf['{k}'] = (없음)")

    # Epic ext_id → Jira key (summary 매칭)
    existing_epics = search_jql(cred, jql=f'project = {cfg.project_key}',
                                fields=["summary", "issuetype"])
    existing_epics = [e for e in existing_epics
                      if (e["fields"].get("issuetype") or {}).get("name") in ("Epic", "에픽")]
    epic_by_summary = {e["fields"]["summary"]: e["key"] for e in existing_epics}
    epic_ext_to_key: dict[str, str] = {}
    for e in plan.get("epics") or []:
        k = epic_by_summary.get(e["summary"])
        if k:
            epic_ext_to_key[e["ext_id"]] = k

    # Task ext_id → Jira key (Phase 2 후 sub-task parent 매핑용)
    task_ext_to_key: dict[str, str] = {}

    def section_phase(name: str, items: list, parent_resolver, is_subtask: bool):
        """
        items 순회하며 ext_id 로 검색 → 있으면 PATCH, 없으면 skip 또는 create.
        """
        ok = patched = created = skipped = missed = failed = 0
        for d in items:
            ext_id = d.get("ext_id", "")
            if not ext_id:
                continue
            existing = find_issue_by_ext_id(cred, ext_id, ext_id_field=cf["external_id"])
            parent_key = parent_resolver(d) if parent_resolver else None
            if existing:
                # PATCH
                fields = _build_issue_fields(
                    cfg, meta, d,
                    parent_key=parent_key, is_subtask=is_subtask,
                    only_fields=only_fields,
                )
                # update 시에는 immutable 필드 제거 (project/issuetype 변경 불가)
                fields = {k: v for k, v in fields.items() if k not in ("project", "issuetype")}
                if not fields:
                    skipped += 1
                    continue
                if args.dry_run:
                    print(f"  · {existing:<6} ← {ext_id:<16} would-patch keys={list(fields.keys())[:6]}...")
                    patched += 1
                else:
                    try:
                        update_issue(cred, existing, fields)
                        print(f"  ✓ {existing:<6} ← {ext_id:<16} patched ({len(fields)} fields)")
                        patched += 1
                    except JiraError as ex:
                        # screen 에 없는 custom field 자동 fallback: 거절된 필드 빼고 재시도
                        import re as _re
                        msg = str(ex)
                        bad_ids = set(_re.findall(r"'(customfield_\d+)' cannot be set", msg))
                        if bad_ids and any(k in fields for k in bad_ids):
                            for b in bad_ids:
                                fields.pop(b, None)
                            if fields:
                                try:
                                    update_issue(cred, existing, fields)
                                    print(f"  ⚠ {existing:<6} ← {ext_id:<16} retry-OK ({len(fields)}) "
                                          f"DROPPED={sorted(bad_ids)} (screen 미등록)")
                                    patched += 1
                                    continue
                                except JiraError as ex2:
                                    print(f"  ✗ {existing:<6} ← {ext_id:<16} retry-fail: {str(ex2)[:200]}", file=sys.stderr)
                                    failed += 1
                                    continue
                        print(f"  ✗ {existing:<6} ← {ext_id:<16} {str(ex)[:200]}", file=sys.stderr)
                        failed += 1
                if not is_subtask and name == "Task":
                    task_ext_to_key[ext_id] = existing
            elif args.create_missing:
                # CREATE
                fields = _build_issue_fields(
                    cfg, meta, d,
                    parent_key=parent_key, is_subtask=is_subtask,
                    only_fields=None,  # create 은 풀필드
                )
                if args.dry_run:
                    print(f"  + {ext_id:<16} would-create  parent={parent_key or '-'}")
                    created += 1
                else:
                    try:
                        resp = create_issue(cred, fields)
                        print(f"  + {resp['key']:<6} ← {ext_id:<16} created  parent={parent_key or '-'}")
                        created += 1
                        if not is_subtask and name == "Task":
                            task_ext_to_key[ext_id] = resp["key"]
                    except JiraError as ex:
                        print(f"  ✗ {ext_id:<16} {str(ex)[:200]}", file=sys.stderr)
                        failed += 1
            else:
                missed += 1
                if missed <= 3:
                    print(f"  ⚠ {ext_id:<16} 기존 이슈 없음 (--create-missing 필요)")
        return patched, created, skipped, missed, failed

    print(f"\n━━ Phase 1: Epic {len(plan.get('epics') or [])}건 ━━")
    p1, c1, s1, m1, f1 = section_phase(
        "Epic", plan.get("epics") or [], parent_resolver=None, is_subtask=False,
    )
    print(f"  Epic: {p1} patched / {c1} created / {s1} skip / {m1} missing / {f1} fail")

    print(f"\n━━ Phase 2: Task {len(plan.get('tasks') or [])}건 ━━")
    p2, c2, s2, m2, f2 = section_phase(
        "Task", plan.get("tasks") or [],
        parent_resolver=lambda t: epic_ext_to_key.get(t.get("epic_link", "")),
        is_subtask=False,
    )
    print(f"  Task: {p2} patched / {c2} created / {s2} skip / {m2} missing / {f2} fail")

    print(f"\n━━ Phase 3: Sub-task {len(plan.get('subtasks') or [])}건 ━━")
    p3, c3, s3, m3, f3 = section_phase(
        "Sub-task", plan.get("subtasks") or [],
        parent_resolver=lambda s: task_ext_to_key.get(s.get("parent_ref", "")),
        is_subtask=True,
    )
    print(f"  Sub-task: {p3} patched / {c3} created / {s3} skip / {m3} missing / {f3} fail")

    print(f"\n━━ 요약 ━━")
    print(f"  Epic    : {p1} patched / {c1} created / {m1} missing / {f1} fail")
    print(f"  Task    : {p2} patched / {c2} created / {m2} missing / {f2} fail")
    print(f"  Sub-task: {p3} patched / {c3} created / {m3} missing / {f3} fail")
    return 0 if (f1 + f2 + f3) == 0 else 4


# ── ri-init ──────────────────────────────────────────────────────────
def cmd_ri_init(args: argparse.Namespace) -> int:
    from gstar.bridge.ri_scaffold import RIContext, scaffold_many
    pd = _project_dir(args)
    cfg = load_config(pd / "09-bridge" / "bridge.yaml")
    plan = json.loads((pd / "09-bridge" / "plan.json").read_text(encoding="utf-8"))
    ctxs: list[RIContext] = []
    epic_by_id = {e["ext_id"]: e for e in plan.get("epics") or []}
    for t in plan.get("tasks") or []:
        ek = t.get("epic_link","")
        epic = epic_by_id.get(ek, {})
        ctxs.append(RIContext(
            jira_key=t["ext_id"],                 # import 전엔 External ID, 이후 rename
            project_key=cfg.project_key,
            summary=t["summary"],
            assignee=t.get("assignee") or cfg.pi.display_name,
            due_date=t.get("due_date",""),
            epic_key=ek,
            epic_summary=epic.get("summary",""),
            phase=t.get("component",""),
            labels=", ".join(t.get("labels") or []),
            fix_version=t.get("fix_version",""),
            priority=t.get("priority","High"),
            project_name=cfg.project_name,
            jira_site=cfg.jira_site,
        ))
    c, s = scaffold_many(ctxs, project_dir=pd, force=args.force)
    print(f"✓ RI 폴더 생성 {c} · 스킵 {s}")
    return 0


# ── ri-rename ────────────────────────────────────────────────────────
def cmd_ri_rename(args: argparse.Namespace) -> int:
    pd = _project_dir(args)
    mapping_path = pd / "09-bridge" / "task-keys.json"
    if not mapping_path.exists():
        print(f"✗ {mapping_path} 없음. `bin/bridge refresh-subtasks` 먼저.", file=sys.stderr); return 1
    ext_to_key = json.loads(mapping_path.read_text(encoding="utf-8"))
    renamed = rename_folders_to_jira_keys(pd, ext_to_key=ext_to_key)
    for a, b in renamed:
        print(f"  {a} → {b}")
    print(f"✓ RI 폴더 rename {len(renamed)} 건")
    return 0


# ── main ─────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bridge",
        description="09-bridge (award-to-dev) 공용 CLI — Jira CSV + RI scaffolding.")
    p.add_argument("--project-dir", help="프로젝트 루트 (기본: $PWD)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s_init = sub.add_parser("init", help="09-bridge/bridge.yaml skeleton 생성")
    s_init.add_argument("--project-key")
    s_init.add_argument("--project-name")
    s_init.add_argument("--force", action="store_true")
    s_init.set_defaults(fn=cmd_init)

    s_env = sub.add_parser("env-example", help="~/.config/<slug>/env.sh skeleton 생성")
    s_env.add_argument("--slug")
    s_env.add_argument("--force", action="store_true")
    s_env.set_defaults(fn=cmd_env_example)

    s_chk = sub.add_parser("check-jira", help="Jira 토큰·프로젝트 접근 검증")
    s_chk.set_defaults(fn=cmd_check_jira)

    s_csv = sub.add_parser("csv", help="plan.json + bridge.yaml → 3-file CSV 생성")
    s_csv.set_defaults(fn=cmd_csv)

    s_pv = sub.add_parser("provision",
        help="bridge.yaml 의 fix_versions/components 를 Jira REST 로 멱등 생성")
    s_pv.add_argument("--dry-run", action="store_true",
        help="API 호출 없이 plan(생성/스킵 대상)만 출력")
    g = s_pv.add_mutually_exclusive_group()
    g.add_argument("--versions-only", action="store_true",
        help="Fix Versions 만 생성")
    g.add_argument("--components-only", action="store_true",
        help="Components 만 생성")
    s_pv.set_defaults(fn=cmd_provision)

    s_im = sub.add_parser("import-issues",
        help="Epic 만 wizard 로 import 후 Task/Sub-task 를 REST 로 멱등 생성")
    s_im.add_argument("--skip-epic-fail", action="store_true",
        help="Epic summary 매칭 실패가 있어도 Task/Sub-task 진행")
    s_im.set_defaults(fn=cmd_import_issues)

    s_up = sub.add_parser("update-issues",
        help="plan.json 의 데이터를 기존 이슈에 PATCH (멱등)")
    s_up.add_argument("--fields", default="",
        help="콤마로 구분된 키만 PATCH (예: description,priority,labels)")
    s_up.add_argument("--create-missing", action="store_true",
        help="ext_id 매칭 안 되는 이슈는 새로 생성 (= import-issues 와 합체)")
    s_up.add_argument("--dry-run", action="store_true",
        help="API 호출 없이 변경 대상만 출력")
    s_up.set_defaults(fn=cmd_update_issues)

    s_rs = sub.add_parser("refresh-subtasks",
        help="Jira 에서 Task External→Key 매핑 수집 후 subtasks CSV 갱신")
    s_rs.add_argument("--ext-id-field", default="customfield_10100",
        help="Jira External ID 가 매핑된 custom field ID (기본 customfield_10100)")
    s_rs.set_defaults(fn=cmd_refresh_subtasks)

    s_ri = sub.add_parser("ri-init", help="각 Task 에 dev/ri/<KEY>/ 8 단 scaffolding")
    s_ri.add_argument("--force", action="store_true")
    s_ri.set_defaults(fn=cmd_ri_init)

    s_rr = sub.add_parser("ri-rename",
        help="RI 폴더 External ID → Jira Key 로 rename (Task import 완료 후)")
    s_rr.set_defaults(fn=cmd_ri_rename)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""lcai CLI — state/memory/perf 조회·제어."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import ask as ask_mod
from . import diagnostics
from .actions.base import REGISTRY as ACTION_REGISTRY
from .code import fs_index as code_fs
from .code import git_diff as code_git
from .code import patch as code_patch
from .code import symbols as code_symbols
from .code import test_runner as code_test_runner
from .infra import tunnel as infra_tunnel
from .infra.gateway import Gateway
from .memory import decay as memory_decay
from .memory import dedup as memory_dedup
from .memory import engine as memory_engine
from .memory import policy as memory_policy
from .memory import restore as memory_restore
from .orchestrator import executor as orch_executor
from .orchestrator import loop as orch_loop
from .orchestrator import parser as orch_parser
from .perf import logger as perf_logger
from .perf.schema import PerfEvent
from .reinforce import analyzer as reinforce_analyzer
from .reinforce import cycle as reinforce_cycle
from .reinforce import findings as reinforce_findings
from .reinforce import policy as reinforce_policy
from .reinforce import suggestions as reinforce_suggestions
from .state import compressor as state_mod
from .tools import manifest as tools_manifest
from .tools import runner as tools_runner
from .verify import detector as verify_detector
from .verify import reranker as verify_reranker


def _dump(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


# ── state ───────────────────────────────────────────────────────────────────

def cmd_state_show(args: argparse.Namespace) -> int:
    _dump(state_mod.load(args.project).model_dump())
    return 0


def cmd_state_update(args: argparse.Namespace) -> int:
    kwargs: dict[str, Any] = {}
    for scalar in ("current_goal", "next_action", "last_action"):
        val = getattr(args, scalar, None)
        if val is not None:
            kwargs[scalar] = val
    if args.exit_code is not None:
        kwargs["last_exit_code"] = args.exit_code

    # 리스트 필드는 append 의미 — 여러 번 주면 순서대로 추가됨.
    list_args = {
        "recent_failures": args.failure,
        "open_issues": args.issue,
        "decisions": args.decision,
        "changed_files": args.changed,
    }
    state = state_mod.load(args.project)
    for field, values in list_args.items():
        if not values:
            continue
        for v in values:
            # 단일 스칼라 append 반복 호출
            state = state_mod.update(args.project, **{field: v})
    if kwargs:
        state = state_mod.update(args.project, **kwargs)

    _dump(state.model_dump())
    return 0


# ── memory ──────────────────────────────────────────────────────────────────

def cmd_memory_recall(args: argparse.Namespace) -> int:
    _dump(memory_engine.recall(args.project))
    return 0


def cmd_memory_remember(args: argparse.Namespace) -> int:
    added = memory_engine.remember(
        args.text,
        scope=args.scope,
        category=args.category,
        project_dir=args.project,
    )
    _dump({"added": added, "scope": args.scope, "category": args.category})
    return 0


def cmd_memory_auto(args: argparse.Namespace) -> int:
    if args.stdin:
        snippet = sys.stdin.read()
    elif args.text is not None:
        snippet = args.text
    else:
        print("lcai memory auto: --stdin 또는 --text 필요", file=sys.stderr)
        return 2
    counts = memory_policy.auto_capture(snippet, project_dir=args.project, timeout=args.timeout)
    _dump(counts)
    return 0


def cmd_memory_decay(args: argparse.Namespace) -> int:
    _dump(memory_decay.decay(args.project, days=args.days))
    return 0


def cmd_memory_dedup(args: argparse.Namespace) -> int:
    result = memory_dedup.dedup(
        args.project, threshold=args.threshold, apply=args.apply
    )
    _dump(result)
    return 0


def cmd_memory_restore(args: argparse.Namespace) -> int:
    if not args.text and not args.category:
        print("lcai memory restore: --text 또는 --category 필요 (전체 복원은 방지)", file=sys.stderr)
        return 2
    result = memory_restore.restore(
        args.project, text=args.text, category=args.category
    )
    _dump(result.to_dict())
    return 0


# ── perf ────────────────────────────────────────────────────────────────────

def cmd_perf_log(args: argparse.Namespace) -> int:
    event = PerfEvent(
        action=args.action,
        model=args.model,
        duration_ms=args.duration_ms,
        exit_code=args.exit_code,
        validation=args.validation,
        retries=args.retries,
    )
    perf_logger.log(event, project_dir=args.project)
    _dump({"logged": event.model_dump()})
    return 0


def cmd_perf_tail(args: argparse.Namespace) -> int:
    path = perf_logger.log_path(args.project)
    if not path.exists():
        print(f"(오늘 로그 없음: {path})")
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines[-args.n :]:
        print(line)
    return 0


# ── action ──────────────────────────────────────────────────────────────────

def cmd_action_list(args: argparse.Namespace) -> int:
    _dump({"actions": sorted(ACTION_REGISTRY.keys())})
    return 0


def cmd_action_run(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {}
    if args.json:
        try:
            payload = json.loads(args.json)
            if not isinstance(payload, dict):
                raise ValueError("payload JSON must be object")
        except Exception as exc:
            print(f"lcai action run: --json 파싱 실패: {exc}", file=sys.stderr)
            return 2
    if args.stdin:
        payload["_body"] = sys.stdin.read()
    for kv in args.kv or []:
        if "=" not in kv:
            print(f"lcai action run: --set 형식은 key=value (got: {kv})", file=sys.stderr)
            return 2
        k, v = kv.split("=", 1)
        payload[k] = v

    executed = orch_executor.dispatch(
        (args.name, payload),
        project_dir=args.project,
        record_perf=not args.no_perf,
        record_state=not args.no_state,
    )
    _dump(
        {
            "name": executed.name,
            "duration_ms": executed.duration_ms,
            "result": executed.result.to_dict(),
        }
    )
    return 0 if executed.result.ok else 1


def cmd_action_parse(args: argparse.Namespace) -> int:
    text = sys.stdin.read() if args.stdin else (args.text or "")
    parsed = orch_parser.extract(text)
    _dump(
        [
            {"name": p.name, "payload": p.payload, "range": [p.start, p.end]}
            for p in parsed
        ]
    )
    return 0


def cmd_action_dispatch(args: argparse.Namespace) -> int:
    """stdin 의 LLM 응답을 파싱해 등장한 모든 action 을 순서대로 실행."""
    text = sys.stdin.read() if args.stdin else (args.text or "")
    parsed = orch_parser.extract(text)
    executed = orch_executor.dispatch_all(
        parsed, project_dir=args.project, stop_on_failure=args.stop_on_failure
    )
    _dump(
        [
            {
                "name": ex.name,
                "duration_ms": ex.duration_ms,
                "result": ex.result.to_dict(),
            }
            for ex in executed
        ]
    )
    return 0 if all(ex.result.ok for ex in executed) else 1


def cmd_orchestrate(args: argparse.Namespace) -> int:
    """plan→exec→verify 루프. planner 훅은 CLI 에서 지원하지 않음 (호출자가 Python API 사용)."""
    text = sys.stdin.read() if args.stdin else (args.text or "")
    result = orch_loop.run(
        text,
        project_dir=args.project,
        max_retries=args.max_retries,
    )
    _dump(result.summary())
    return 0 if result.verified else 1


# ── ask (Gemma 범용 Q&A) ───────────────────────────────────────────────────

def cmd_ask(args: argparse.Namespace) -> int:
    if args.stdin:
        prompt = sys.stdin.read()
    elif args.prompt:
        prompt = " ".join(args.prompt)
    else:
        print("lcai ask: prompt 가 필요 (또는 --stdin)", file=sys.stderr)
        return 2
    result = ask_mod.ask(
        prompt,
        deep=args.deep,
        rag=args.rag,
        rag_top_k=args.top_k,
        timeout_s=args.timeout,
    )
    if args.json_out:
        _dump(result.to_dict())
    else:
        # 사람 친화 — 근거 요약 + 답변 본문만.
        if result.rag_evidence:
            print(f"[근거 {len(result.rag_evidence)}개 사용]", file=sys.stderr)
        if result.ok:
            print(result.text)
        else:
            print(f"[error] {result.error}", file=sys.stderr)
    return 0 if result.ok else 1


# ── infra ───────────────────────────────────────────────────────────────────

def cmd_infra_gateway_health(args: argparse.Namespace) -> int:
    gw = Gateway(auto_tunnel=not args.no_tunnel)
    resp = gw.health()
    _dump(
        {
            "ok": resp.ok,
            "status": resp.status_code,
            "via": resp.via,
            "url": resp.url,
            "data": resp.data,
            "error": resp.error,
            "token_present": gw.token is not None,
        }
    )
    return 0 if resp.ok else 1


def cmd_infra_tunnel_status(args: argparse.Namespace) -> int:
    st = infra_tunnel.status()
    _dump({"listening": st.listening, "all_up": st.all_up})
    return 0 if st.all_up else 1


def cmd_infra_tunnel_up(args: argparse.Namespace) -> int:
    st = infra_tunnel.up()
    _dump({"listening": st.listening, "all_up": st.all_up})
    return 0 if st.all_up else 1


def cmd_infra_tunnel_down(args: argparse.Namespace) -> int:
    killed = infra_tunnel.down()
    _dump({"killed_pids": killed})
    return 0


# ── verify (Phase 3) ────────────────────────────────────────────────────────

def _load_json_arg(text_arg: str | None, file_arg: str | None, stdin_flag: bool) -> Any:
    """--json / --file / --stdin 중 하나에서 JSON 을 로드. 셋 다 없으면 None."""
    if stdin_flag:
        raw = sys.stdin.read()
    elif file_arg:
        raw = Path(file_arg).read_text(encoding="utf-8")
    elif text_arg:
        raw = text_arg
    else:
        return None
    return json.loads(raw)


def cmd_verify_rerank(args: argparse.Namespace) -> int:
    try:
        docs = _load_json_arg(args.json, args.file, args.stdin)
    except Exception as exc:
        print(f"lcai verify rerank: JSON 파싱 실패: {exc}", file=sys.stderr)
        return 2
    if not isinstance(docs, list):
        print("lcai verify rerank: 입력은 JSON 배열이어야 함", file=sys.stderr)
        return 2

    kwargs: dict[str, Any] = {}
    if args.alpha is not None:
        kwargs["alpha"] = args.alpha
    reranker = verify_reranker.get_reranker(args.mode, **kwargs)
    ranked = reranker.rerank(args.query, docs, top_k=args.top_k)
    _dump(
        {
            "mode": reranker.name,
            "query": args.query,
            "input_count": len(docs),
            "output_count": len(ranked),
            "ranked": [r.to_dict() for r in ranked],
        }
    )
    return 0


def cmd_verify_cross_check(args: argparse.Namespace) -> int:
    if args.answer_file:
        answer = Path(args.answer_file).read_text(encoding="utf-8")
    elif args.answer:
        answer = args.answer
    else:
        print("lcai verify cross-check: --answer 또는 --answer-file 필요", file=sys.stderr)
        return 2

    try:
        evidences = _load_json_arg(args.evidence_json, args.evidence_file, args.stdin)
    except Exception as exc:
        print(f"lcai verify cross-check: evidence JSON 파싱 실패: {exc}", file=sys.stderr)
        return 2
    if not isinstance(evidences, list) or not evidences:
        print(
            "lcai verify cross-check: --evidence-json / --evidence-file / --stdin 중 하나로 "
            "비어있지 않은 JSON 배열 제공 필요",
            file=sys.stderr,
        )
        return 2

    report = verify_detector.detect(
        answer,
        evidences,
        mode=args.mode,
        min_claim_score=args.min_claim_score,
        max_claims=args.max_claims,
    )
    _dump(report.to_dict())
    return 0 if report.overall in {"supported", "partial", "no_claims"} else 1


# ── code (Phase 4) ──────────────────────────────────────────────────────────

def _project_root_arg(args: argparse.Namespace) -> Path:
    return Path(args.project or ".").resolve()


def cmd_code_index(args: argparse.Namespace) -> int:
    root = _project_root_arg(args)
    exts = args.extensions.split(",") if args.extensions else None
    files = code_fs.source_files(root, extensions=exts, max_files=args.max_files)
    if args.format == "paths":
        _dump({"files": [str(f.relative_to(root)) for f in files], "count": len(files)})
        return 0
    index = code_symbols.build_index(files, project_root=root)
    _dump(
        {
            "file_count": len(files),
            "symbol_count": len(index.symbols),
            "symbols": index.to_list(),
        }
    )
    return 0


def cmd_code_impact(args: argparse.Namespace) -> int:
    root = _project_root_arg(args)
    report = code_git.impact(root, base=args.base)
    _dump(report.to_dict())
    return 0


def cmd_code_keyfiles(args: argparse.Namespace) -> int:
    root = _project_root_arg(args)
    _dump({"key_files": code_fs.key_files(root)})
    return 0


def cmd_code_diff(args: argparse.Namespace) -> int:
    a_text = Path(args.a).read_text(encoding="utf-8")
    b_text = Path(args.b).read_text(encoding="utf-8")
    diff = code_patch.make_diff(a_text, b_text, from_file=args.a, to_file=args.b, context=args.context)
    if diff:
        print(diff, end="" if diff.endswith("\n") else "\n")
        return 0
    print("(동일한 파일)")
    return 0


def cmd_code_tree(args: argparse.Namespace) -> int:
    root = _project_root_arg(args)
    tree = code_fs.file_tree(root, max_files=args.max_files)
    _dump(tree.to_dict())
    return 0


# ── test (Phase 4) ──────────────────────────────────────────────────────────

def cmd_test_run(args: argparse.Namespace) -> int:
    root = _project_root_arg(args)
    result = code_test_runner.run(
        root,
        cmd=args.cmd,
        retries=args.retries,
        timeout_s=args.timeout,
    )
    _dump(result.to_dict())
    return 0 if result.ok else 1


def cmd_test_detect(args: argparse.Namespace) -> int:
    root = _project_root_arg(args)
    cmd, detected = code_test_runner.detect_command(root)
    _dump({"detected": detected, "command": cmd})
    return 0 if cmd is not None else 1


# ── tools (Phase 5 — connect-ai 백엔드 인터페이스) ──────────────────────────

def cmd_tools_list(args: argparse.Namespace) -> int:
    _dump({"tools": tools_manifest.list_tools()})
    return 0


def cmd_tools_show(args: argparse.Namespace) -> int:
    spec = tools_manifest.get(args.name)
    if spec is None:
        print(f"lcai tools show: unknown tool {args.name}", file=sys.stderr)
        return 2
    _dump(spec.to_dict())
    return 0


def cmd_tools_call(args: argparse.Namespace) -> int:
    if args.json is not None:
        try:
            payload = json.loads(args.json)
        except Exception as exc:
            print(f"lcai tools call: --json 파싱 실패: {exc}", file=sys.stderr)
            return 2
    else:
        raw = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw else {}
    if not isinstance(payload, dict):
        print("lcai tools call: payload 는 JSON 객체여야 함", file=sys.stderr)
        return 2
    envelope = tools_runner.call(args.name, payload, project_dir=args.project)
    _dump(envelope)
    return 0 if envelope.get("ok") else 1


# ── status / doctor (Phase 5) ───────────────────────────────────────────────

def cmd_status(args: argparse.Namespace) -> int:
    _dump(diagnostics.status(args.project))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    report = diagnostics.doctor(args.project)
    _dump(report.to_dict())
    # fail 있으면 exit 1, warn 만 있으면 exit 0.
    return 0 if report.summary()["fail"] == 0 else 1


# ── parser ──────────────────────────────────────────────────────────────────

# ── reinforce (Phase 6) ─────────────────────────────────────────────────────

def cmd_reinforce_analyze(args: argparse.Namespace) -> int:
    report = reinforce_analyzer.analyze(args.project, days=args.days)
    _dump(report.to_dict())
    return 0


def cmd_reinforce_findings(args: argparse.Namespace) -> int:
    report = reinforce_analyzer.analyze(args.project, days=args.days)
    finds = reinforce_findings.derive(report)
    _dump({"count": len(finds), "findings": [f.to_dict() for f in finds]})
    return 0


def cmd_reinforce_suggest(args: argparse.Namespace) -> int:
    report = reinforce_analyzer.analyze(args.project, days=args.days)
    finds = reinforce_findings.derive(report)
    result = reinforce_suggestions.generate(finds, use_llm=not args.no_llm)
    _dump(result.to_dict())
    return 0


def cmd_reinforce_cycle(args: argparse.Namespace) -> int:
    result = reinforce_cycle.run(
        args.project,
        days=args.days,
        use_llm=not args.no_llm,
        apply_mode=args.apply,
    )
    _dump(result.to_dict())
    return 0


# ── policy (Phase 6) ────────────────────────────────────────────────────────

def cmd_policy_list(args: argparse.Namespace) -> int:
    _dump({"policies": [p.to_dict() for p in reinforce_policy.list_policies()]})
    return 0


def cmd_policy_show(args: argparse.Namespace) -> int:
    content = reinforce_policy.show(args.name)
    if content is None:
        print(f"lcai policy show: {args.name} 없음", file=sys.stderr)
        return 1
    print(content, end="" if content.endswith("\n") else "\n")
    return 0


def cmd_policy_set(args: argparse.Namespace) -> int:
    if args.file:
        content = Path(args.file).read_text(encoding="utf-8")
    else:
        content = sys.stdin.read()
    rev = reinforce_policy.set_policy(args.name, content)
    _dump({"name": args.name, "backup_rev": rev, "length": len(content)})
    return 0


def cmd_policy_revisions(args: argparse.Namespace) -> int:
    revs = reinforce_policy.list_revisions(args.name)
    _dump({"name": args.name, "revisions": revs})
    return 0


def cmd_policy_diff(args: argparse.Namespace) -> int:
    diff = reinforce_policy.diff_revision(args.name, args.rev)
    if not diff:
        print("(차이 없음 또는 revision 없음)")
        return 0
    print(diff, end="" if diff.endswith("\n") else "\n")
    return 0


def cmd_policy_rollback(args: argparse.Namespace) -> int:
    ok = reinforce_policy.rollback(args.name, args.rev)
    _dump({"rolled_back": ok, "name": args.name, "to_rev": args.rev})
    return 0 if ok else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lcai", description="local-claude CLI")
    parser.add_argument(
        "-p",
        "--project",
        type=Path,
        default=None,
        help="프로젝트 경로 (기본: cwd)",
    )
    root = parser.add_subparsers(dest="group", required=True)

    # state
    state_p = root.add_parser("state", help="상태 압축기")
    state_sub = state_p.add_subparsers(dest="sub", required=True)
    state_sub.add_parser("show", help="state.json 덤프").set_defaults(func=cmd_state_show)
    sup = state_sub.add_parser("update", help="필드 부분 갱신")
    sup.add_argument("--current-goal", dest="current_goal")
    sup.add_argument("--next-action", dest="next_action")
    sup.add_argument("--last-action", dest="last_action")
    sup.add_argument("--failure", action="append", help="리스트 append (반복 가능)")
    sup.add_argument("--issue", action="append")
    sup.add_argument("--decision", action="append")
    sup.add_argument("--changed", action="append")
    sup.add_argument("--exit-code", type=int, dest="exit_code", default=None)
    sup.set_defaults(func=cmd_state_update)

    # memory
    mem_p = root.add_parser("memory", help="3층 메모리")
    mem_sub = mem_p.add_subparsers(dest="sub", required=True)
    mem_sub.add_parser("recall", help="세 층 전체 덤프").set_defaults(func=cmd_memory_recall)
    rp = mem_sub.add_parser("remember", help="명시 기록")
    rp.add_argument("text")
    rp.add_argument("--scope", choices=["session", "project", "user"], default="project")
    rp.add_argument("--category", default="fact")
    rp.set_defaults(func=cmd_memory_remember)
    ap = mem_sub.add_parser("auto", help="ask-gemma 로 스니펫에서 자동 추출")
    ap.add_argument("--stdin", action="store_true", help="스니펫을 stdin 으로 받음")
    ap.add_argument("--text", default=None)
    ap.add_argument("--timeout", type=int, default=30)
    ap.set_defaults(func=cmd_memory_auto)
    dp = mem_sub.add_parser("decay", help="오래된 항목 archive")
    dp.add_argument(
        "--days",
        type=int,
        default=None,
        help="명시 시 모든 카테고리 동일 적용. 생략 시 카테고리별 TTL (decision=90d 등).",
    )
    dp.set_defaults(func=cmd_memory_decay)
    dd = mem_sub.add_parser("dedup", help="중복 후보 탐색 (기본 dry-run)")
    dd.add_argument("--threshold", type=float, default=0.85)
    dd.add_argument("--apply", action="store_true", help="실제 병합 수행")
    dd.set_defaults(func=cmd_memory_dedup)
    mr = mem_sub.add_parser("restore", help="archive → active 복원")
    mr.add_argument("--text", default=None, help="부분 일치 검색")
    mr.add_argument("--category", default=None, help="카테고리 완전 일치")
    mr.set_defaults(func=cmd_memory_restore)

    # perf
    perf_p = root.add_parser("perf", help="성능 로깅")
    perf_sub = perf_p.add_subparsers(dest="sub", required=True)
    lp = perf_sub.add_parser("log", help="이벤트 append")
    lp.add_argument("--action", required=True)
    lp.add_argument("--model", default=None)
    lp.add_argument("--duration-ms", type=int, dest="duration_ms", default=0)
    lp.add_argument("--exit-code", type=int, dest="exit_code", default=0)
    lp.add_argument("--validation", default=None)
    lp.add_argument("--retries", type=int, default=0)
    lp.set_defaults(func=cmd_perf_log)
    tp = perf_sub.add_parser("tail", help="오늘 로그 꼬리")
    tp.add_argument("-n", type=int, default=20)
    tp.set_defaults(func=cmd_perf_tail)

    # action
    action_p = root.add_parser("action", help="XML action 실행 (Phase 2)")
    action_sub = action_p.add_subparsers(dest="sub", required=True)

    action_sub.add_parser("list", help="등록된 action 이름").set_defaults(func=cmd_action_list)

    ar = action_sub.add_parser("run", help="단일 action 실행")
    ar.add_argument("name", help="action 이름 (예: search_rag)")
    ar.add_argument("--json", help="payload JSON 문자열")
    ar.add_argument("--set", action="append", dest="kv", help="key=value (반복 가능)")
    ar.add_argument("--stdin", action="store_true", help="stdin 을 _body 로")
    ar.add_argument("--no-perf", action="store_true")
    ar.add_argument("--no-state", action="store_true")
    ar.set_defaults(func=cmd_action_run)

    ap_ = action_sub.add_parser("parse", help="텍스트에서 action 태그 추출")
    ap_.add_argument("--stdin", action="store_true")
    ap_.add_argument("--text", default=None)
    ap_.set_defaults(func=cmd_action_parse)

    ad = action_sub.add_parser("dispatch", help="텍스트 파싱 + 순차 실행")
    ad.add_argument("--stdin", action="store_true")
    ad.add_argument("--text", default=None)
    ad.add_argument("--stop-on-failure", action="store_true")
    ad.set_defaults(func=cmd_action_dispatch)

    # orchestrate
    op = root.add_parser("orchestrate", help="plan→exec→verify 루프")
    op.add_argument("--stdin", action="store_true")
    op.add_argument("--text", default=None)
    op.add_argument("--max-retries", type=int, default=2)
    op.set_defaults(func=cmd_orchestrate)

    # infra
    infra_p = root.add_parser("infra", help="gateway/tunnel 제어")
    infra_sub = infra_p.add_subparsers(dest="sub", required=True)

    gw_p = infra_sub.add_parser("gateway", help="gateway 진단")
    gw_sub = gw_p.add_subparsers(dest="op", required=True)
    gh = gw_sub.add_parser("health", help="/health 호출 (+ 자동 터널 fallback)")
    gh.add_argument("--no-tunnel", action="store_true", help="tunnel fallback 비활성화")
    gh.set_defaults(func=cmd_infra_gateway_health)

    tn_p = infra_sub.add_parser("tunnel", help="SSH 포트포워딩 제어")
    tn_sub = tn_p.add_subparsers(dest="op", required=True)
    tn_sub.add_parser("status").set_defaults(func=cmd_infra_tunnel_status)
    tn_sub.add_parser("up").set_defaults(func=cmd_infra_tunnel_up)
    tn_sub.add_parser("down").set_defaults(func=cmd_infra_tunnel_down)

    # ask — Gemma 범용 Q&A (프로젝트 정체성의 기본 입구)
    ask_p = root.add_parser("ask", help="Gemma 에 질문 (기본 e4b, --deep 은 a4b)")
    ask_p.add_argument("prompt", nargs="*", help="질문 (여러 단어 가능, 또는 --stdin)")
    ask_p.add_argument("--stdin", action="store_true", help="stdin 에서 질문 읽기")
    ask_p.add_argument("--deep", action="store_true", help="4090 a4b 로 깊은 추론")
    ask_p.add_argument("--rag", action="store_true", help="gateway /search/hybrid 근거 주입")
    ask_p.add_argument("--top-k", type=int, dest="top_k", default=5, help="RAG top_k")
    ask_p.add_argument("--timeout", type=int, default=180)
    ask_p.add_argument("--json-out", action="store_true", dest="json_out", help="결과를 JSON envelope 로")
    ask_p.set_defaults(func=cmd_ask)

    # verify (Phase 3)
    verify_p = root.add_parser("verify", help="리랭커 + hallucination 검증")
    verify_sub = verify_p.add_subparsers(dest="sub", required=True)

    rr = verify_sub.add_parser("rerank", help="근거 목록 재정렬")
    rr.add_argument("--query", required=True)
    rr.add_argument(
        "--mode",
        choices=["noop", "heuristic", "llm"],
        default="llm",
        help="기본 llm (Gemma) — heuristic 은 빠른 로컬 fallback",
    )
    rr.add_argument("--top-k", type=int, dest="top_k", default=None)
    rr.add_argument("--alpha", type=float, default=None, help="heuristic 가중치")
    rr.add_argument("--json", default=None, help="문서 배열 JSON 문자열")
    rr.add_argument("--file", default=None, help="문서 배열 JSON 파일")
    rr.add_argument("--stdin", action="store_true", help="문서 배열을 stdin 으로")
    rr.set_defaults(func=cmd_verify_rerank)

    cc = verify_sub.add_parser("cross-check", help="답변 vs 근거 교차 검증")
    cc.add_argument("--answer", default=None)
    cc.add_argument("--answer-file", dest="answer_file", default=None)
    cc.add_argument(
        "--mode",
        choices=["local", "llm"],
        default="llm",
        help="기본 llm (Gemma) — local 은 토큰 매칭 fallback",
    )
    cc.add_argument("--min-claim-score", type=float, dest="min_claim_score", default=1.0)
    cc.add_argument("--max-claims", type=int, dest="max_claims", default=20)
    cc.add_argument("--evidence-json", dest="evidence_json", default=None)
    cc.add_argument("--evidence-file", dest="evidence_file", default=None)
    cc.add_argument("--stdin", action="store_true", help="evidence 배열을 stdin 으로")
    cc.set_defaults(func=cmd_verify_cross_check)

    # code (Phase 4)
    code_p = root.add_parser("code", help="심볼 인덱스 + 영향도 + diff")
    code_sub = code_p.add_subparsers(dest="sub", required=True)

    ci = code_sub.add_parser("index", help="심볼 인덱스 구축")
    ci.add_argument("--format", choices=["json", "paths"], default="json")
    ci.add_argument(
        "--extensions",
        default=".py,.js,.jsx,.ts,.tsx,.go,.rs,.java,.kt",
        help="콤마 구분 확장자 (기본: 주요 소스)",
    )
    ci.add_argument("--max-files", type=int, dest="max_files", default=200)
    ci.set_defaults(func=cmd_code_index)

    cim = code_sub.add_parser("impact", help="git 변경 영향도 분석")
    cim.add_argument("--base", default=None, help="비교 베이스 ref (예: main)")
    cim.set_defaults(func=cmd_code_impact)

    code_sub.add_parser("keyfiles", help="CLAUDE.md/README 등 감지").set_defaults(
        func=cmd_code_keyfiles
    )

    ct = code_sub.add_parser("tree", help="추적 파일 목록")
    ct.add_argument("--max-files", type=int, dest="max_files", default=200)
    ct.set_defaults(func=cmd_code_tree)

    cd = code_sub.add_parser("diff", help="두 파일의 unified diff")
    cd.add_argument("a")
    cd.add_argument("b")
    cd.add_argument("--context", type=int, default=3)
    cd.set_defaults(func=cmd_code_diff)

    # test (Phase 4)
    test_p = root.add_parser("test", help="프로젝트 테스트 실행")
    test_sub = test_p.add_subparsers(dest="sub", required=True)

    tr = test_sub.add_parser("run", help="자동 감지 또는 --cmd 로 실행")
    tr.add_argument("--cmd", default=None, help="명시적 테스트 명령 override")
    tr.add_argument("--retries", type=int, default=0)
    tr.add_argument("--timeout", type=int, default=600)
    tr.set_defaults(func=cmd_test_run)

    test_sub.add_parser("detect", help="감지된 테스트 명령 출력").set_defaults(
        func=cmd_test_detect
    )

    # tools (Phase 5 — connect-ai 호환 도구 인터페이스)
    tools_p = root.add_parser("tools", help="connect-ai 백엔드 도구 인터페이스")
    tools_sub = tools_p.add_subparsers(dest="sub", required=True)

    tools_sub.add_parser("list", help="사용 가능 도구 + 입력 스키마").set_defaults(
        func=cmd_tools_list
    )
    ts = tools_sub.add_parser("show", help="단일 도구 상세")
    ts.add_argument("name")
    ts.set_defaults(func=cmd_tools_show)
    tc = tools_sub.add_parser(
        "call", help="도구 실행 (stdin 또는 --json 으로 payload, stdout envelope)"
    )
    tc.add_argument("name")
    tc.add_argument("--json", default=None, help="payload JSON 문자열")
    tc.set_defaults(func=cmd_tools_call)

    # status / doctor (Phase 5)
    root.add_parser("status", help="시스템 요약").set_defaults(func=cmd_status)
    root.add_parser("doctor", help="의존성 + 파일 헬스체크").set_defaults(func=cmd_doctor)

    # reinforce (Phase 6 — P-Reinforce 자기개선 루프)
    rf_p = root.add_parser("reinforce", help="perf 로그 분석 + 개선안 생성")
    rf_sub = rf_p.add_subparsers(dest="sub", required=True)

    for name, handler, help_text in (
        ("analyze", cmd_reinforce_analyze, "perf 로그 집계"),
        ("findings", cmd_reinforce_findings, "집계 → 구조화 이슈"),
        ("suggest", cmd_reinforce_suggest, "findings → 개선안 (ask-gemma)"),
    ):
        sp = rf_sub.add_parser(name, help=help_text)
        sp.add_argument("--days", type=int, default=7)
        if name == "suggest":
            sp.add_argument("--no-llm", action="store_true", help="규칙 기반 fallback만")
        sp.set_defaults(func=handler)

    rc = rf_sub.add_parser("cycle", help="analyze → findings → suggest → (옵션) apply")
    rc.add_argument("--days", type=int, default=7)
    rc.add_argument("--no-llm", action="store_true")
    rc.add_argument("--apply", action="store_true", help="구조적 제안 자동 기록")
    rc.set_defaults(func=cmd_reinforce_cycle)

    # policy (Phase 6 — 정책 파일 버저닝)
    pol_p = root.add_parser("policy", help="정책 파일 버저닝")
    pol_sub = pol_p.add_subparsers(dest="sub", required=True)

    pol_sub.add_parser("list", help="활성 정책 목록").set_defaults(func=cmd_policy_list)

    ps = pol_sub.add_parser("show", help="정책 내용 출력")
    ps.add_argument("name")
    ps.set_defaults(func=cmd_policy_show)

    pset = pol_sub.add_parser("set", help="정책 내용 갱신 (stdin 또는 --file)")
    pset.add_argument("name")
    pset.add_argument("--file", default=None)
    pset.set_defaults(func=cmd_policy_set)

    pr = pol_sub.add_parser("revisions", help="revision 번호 리스트")
    pr.add_argument("name")
    pr.set_defaults(func=cmd_policy_revisions)

    pd = pol_sub.add_parser("diff", help="rev N 과 현재 active 의 diff")
    pd.add_argument("name")
    pd.add_argument("rev", type=int)
    pd.set_defaults(func=cmd_policy_diff)

    prb = pol_sub.add_parser("rollback", help="rev N 을 active 로 복원")
    prb.add_argument("name")
    prb.add_argument("rev", type=int)
    prb.set_defaults(func=cmd_policy_rollback)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())

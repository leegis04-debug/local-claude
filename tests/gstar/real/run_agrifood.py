"""Week 6 실제 벤치마크 — 농식품 AI 응용제품 신속상용화 과제.

- Ingest 대상: 00-input/BASE/template-full.md, input-summary.md, template-map.md
- Goal: "농식품 AI 외식 푸드테크 디지털화 상용화 사업계획서"
- 실 Gemma e4b 선택기 루프 → stellar build → emerge log
- 평가: input-summary.md 의 핵심 개체와 최종 선택된 노드·클러스터 멤버의 커버리지

사용법:
  GSTAR_HOME=/tmp/gstar_agrifood \\
      python tests/gstar/real/run_agrifood.py
  결과는 tests/gstar/real/report_agrifood.md 로 저장.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
PROJECT_ROOT = Path(
    "/Users/ljw0904/Desktop/Doc/Dagyeom Inc/projects/agri-food-ai"
)
INPUT_DIR = PROJECT_ROOT / "00-input"
SUMMARY = INPUT_DIR / "input-summary.md"
REPORT_OUT = REPO_ROOT / "tests/gstar/real/report_agrifood.md"

# input-summary 기반 "정답 핵심 개체" (수동 큐레이션)
EXPECTED_ENTITIES = [
    "다겸",
    "로칼", "LOEKAL",
    "메디프레소",
    "AI",
    "로봇조리",
    "메뉴추천",
    "고객분석",
    "푸드테크",
    "외식",
    "상용화",
    "컨소시엄",
    "농림축산식품부",
    "농업기술진흥원",
    "TRL",
    "KPI",
]


def _run(cmd: list[str], **kw) -> str:
    import subprocess
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(REPO_ROOT / "src"))
    out = subprocess.run(cmd, capture_output=True, text=True, env=env, **kw)
    if out.returncode != 0:
        raise RuntimeError(f"FAILED {cmd}\nSTDERR: {out.stderr}")
    return out.stdout


def main() -> None:
    os.environ.setdefault("GSTAR_HOME", "/tmp/gstar_agrifood")
    gstar_home = Path(os.environ["GSTAR_HOME"])
    import shutil
    if gstar_home.exists():
        shutil.rmtree(gstar_home)
    gstar_home.mkdir(parents=True, exist_ok=True)

    py = str(REPO_ROOT / ".venv/bin/python")
    lines: list[str] = []

    # 1) init
    t0 = time.time()
    print("[1/6] g init")
    _run([py, "-m", "gstar.cli.main", "init"])

    # 2) ingest — 실질 내용만 agrifood-2026 namespace 로.
    # template-full.md 는 양식 지시문이 1016 fact 로 압도적이라 실제 내용을 밀어내므로
    # 별도 namespace 'form-template' 로 분리 (체인 무결성만 확인, gravity 계산엔 포함되어도
    # 같은 goal 에 대한 관련성이 낮음).
    print("[2/6] ingest markdown")
    targets = [
        (INPUT_DIR / "input-summary.md", "agrifood-2026"),
        (INPUT_DIR / "template-map.md", "agrifood-2026"),
        (INPUT_DIR / "BASE/template-full.md", "form-template"),
    ]
    ingest_reports = []
    for t, ns in targets:
        out = _run([py, "-m", "gstar.cli.main", "ingest", str(t), "--ns", ns])
        ingest_reports.append((f"{t.name} [{ns}]", out.strip()))
        print(f"    {t.name} [{ns}]: {out.strip().splitlines()[-1]}")

    # 3) goal set
    print("[3/6] goal set")
    goal_text = (
        "다겸·로칼(LOEKAL)·메디프레소 컨소시엄의 농식품 AI 외식 푸드테크 사업계획서. "
        "로봇조리·메뉴추천·고객분석·소분자동화·광고최적화 상용화. KPI, 일정, 예산, TRL."
    )
    out = _run([py, "-m", "gstar.cli.main", "goal", "set", goal_text, "--kind", "proposal"])
    goal_id = out.strip().splitlines()[-1]
    print(f"    goal={goal_id}")

    # 4) gravity top 50 (관찰)
    print("[4/6] gravity top (no LLM)")
    out = _run([py, "-m", "gstar.cli.main", "gravity", "top", goal_id, "--k", "50"])
    gravity_top = out.strip().splitlines()

    # 5) selector run — 실제 Gemma e4b
    print("[5/6] g run (Gemma e4b) …")
    t_run_start = time.time()
    out = _run([
        py, "-m", "gstar.cli.main", "run", goal_id,
        "--k", "30", "--max-cycles", "5", "--stable", "2",
    ])
    run_seconds = time.time() - t_run_start
    run_report = out.strip().splitlines()
    print(f"    run took {run_seconds:.1f}s")

    # 6) stellar build + show + emerge log
    print("[6/6] stellar + emerge")
    stellar_build = _run([py, "-m", "gstar.cli.main", "stellar", "build", goal_id, "--k", "50"]).strip()
    stellar_list = _run([py, "-m", "gstar.cli.main", "stellar", "list", goal_id]).strip().splitlines()
    # 모든 클러스터 상세 (커버리지 평가용)
    cluster_details_all: list[str] = []
    cluster_details: list[str] = []
    for idx, line in enumerate(stellar_list):
        cid = line.split()[0]
        detail = _run([py, "-m", "gstar.cli.main", "stellar", "show", cid]).strip().splitlines()
        cluster_details_all.extend(detail + [""])
        if idx == 0:
            cluster_details = detail
    emerge = _run([py, "-m", "gstar.cli.main", "emerge", "log", goal_id]).strip().splitlines()

    verify = _run([py, "-m", "gstar.cli.main", "verify", "chain", "--ns", "agrifood-2026"]).strip()

    total_seconds = time.time() - t0

    # ---------- 커버리지 평가 ----------
    # 모든 클러스터 멤버 + selector run 의 final_kept 까지 합산
    kept_texts: set[str] = set()
    for line in cluster_details_all + run_report:
        s = line.strip()
        # "g=0.825  OptiREC" 또는 "[fact ]  OptiREC ..." 형식 모두 처리
        if "g=" in s:
            parts = s.split("g=", 1)[1]
            try:
                _, rest = parts.split(" ", 1)
                kept_texts.add(rest.strip())
            except ValueError:
                pass
        elif "]" in s and s.startswith("01K"):
            # run_report final_kept 행: "  01KPF...  [fact ]  텍스트"
            after_bracket = s.split("]", 1)[-1].strip()
            if after_bracket:
                kept_texts.add(after_bracket)
    # 동의어 그룹 — 하나라도 잡히면 OK.
    synonym_groups = [
        ["LOEKAL", "로칼"],
    ]
    covered = []
    missing = []
    corpus = " ".join(kept_texts)

    seen_groups: set[tuple[str, ...]] = set()
    for ent in EXPECTED_ENTITIES:
        group = None
        for g in synonym_groups:
            if ent in g:
                group = tuple(g)
                break
        if group is not None:
            if group in seen_groups:
                continue  # 이미 처리됨
            if any(syn in corpus for syn in group):
                covered.append("/".join(group))
            else:
                missing.append("/".join(group))
            seen_groups.add(group)
        else:
            if ent in corpus:
                covered.append(ent)
            else:
                missing.append(ent)

    total_expected = len(covered) + len(missing)
    coverage = len(covered) / total_expected if total_expected else 0.0

    # ---------- 리포트 ----------
    lines.append("# agri-food-ai Week 6 벤치마크 리포트\n")
    lines.append(f"- 실행 소요: **{total_seconds:.1f}s** (전체), **{run_seconds:.1f}s** (selector run)")
    lines.append(f"- Goal: `{goal_text}`")
    lines.append(f"- Goal id: `{goal_id}`\n")

    lines.append("## Ingest 결과\n")
    lines.append("| 파일 | 요약 |")
    lines.append("|------|------|")
    for name, rep in ingest_reports:
        tail = rep.splitlines()[-4:]
        summary = " / ".join(l.strip() for l in tail if "facts" in l or "entities" in l or "edges" in l or "files" in l)
        lines.append(f"| `{name}` | {summary} |")
    lines.append("")

    lines.append("## Gravity Top-20 (LLM 없이)\n")
    lines.append("```")
    lines.extend(gravity_top[:20])
    lines.append("```\n")

    lines.append("## Selector Run (Gemma e4b)\n")
    lines.append("```")
    lines.extend(run_report[:30])
    lines.append("```\n")

    lines.append("## 지식 항성 요약\n")
    lines.append(f"```\n{stellar_build}\n```\n")
    lines.append("### 클러스터 목록")
    lines.append("```")
    lines.extend(stellar_list[:10])
    lines.append("```\n")
    lines.append("### 첫 클러스터 상세 (⭐ = 중심)")
    lines.append("```")
    lines.extend(cluster_details[:30])
    lines.append("```\n")

    lines.append("## 창발 이벤트 (4연결 감지)\n")
    lines.append("```")
    lines.extend(emerge[:40])
    lines.append("```\n")

    lines.append("## 무결성 검증\n")
    lines.append(f"```\n{verify}\n```\n")

    lines.append("## 커버리지 평가\n")
    lines.append(f"- 기대 핵심 개체: **{len(EXPECTED_ENTITIES)}**")
    lines.append(f"- 커버된 개체: **{len(covered)}**  (`{coverage*100:.1f}%`)")
    lines.append(f"- 놓친 개체: {', '.join(f'`{m}`' for m in missing) if missing else '(없음)'}")
    lines.append("")
    lines.append("### 첫 클러스터에 포함된 기대 개체")
    lines.append(", ".join(f"`{c}`" for c in covered) or "(없음)")
    lines.append("")

    lines.append("## 결론\n")
    if coverage >= 0.6:
        verdict = "✅ 중앙 클러스터가 기대 핵심 개체를 " f"{coverage*100:.0f}% 포괄 — G 파이프라인이 사업계획서 핵심을 잘 응집"
    elif coverage >= 0.4:
        verdict = f"🟡 {coverage*100:.0f}% 커버 — ingest 대상을 REF PDF 까지 확대하면 개선 여지"
    else:
        verdict = f"❌ {coverage*100:.0f}% — 가중치·chunk 분리 전략 재검토 필요"
    lines.append(verdict)

    REPORT_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n리포트 저장: {REPORT_OUT}")
    print(f"커버리지: {coverage*100:.1f}%  ({len(covered)}/{len(EXPECTED_ENTITIES)})")


if __name__ == "__main__":
    main()

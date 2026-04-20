"""`g project ...` 서브커맨드 진입점.

CLI:
- `g project run <track> <stage> [--project <dir>] [--input "..."]`
- `g project list [--project <dir>] [--track <t>]`
- `g project stats <project_dir>`
- `g project plugins`
- `g project verify <output>`
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import typer

from gstar.config import EMBED_DIM_DEFAULT, Paths
from gstar.entity.linker import link, list_canonicals
from gstar.entity.types import EntityKind, Track
from gstar.projection.context_loader import (
    StageArtifact,
    load_form,
    load_project,
    prev_stages,
    stage_by_name,
)
from gstar.projection.fact_registry import upsert as fact_upsert
from gstar.projection.renderer import render_section
from gstar.projection.stellar_packer import pack_sections
from gstar.projection.summarizer import summarize_prev
from gstar.projection.tasks import get_task, list_tasks
from gstar.projection.writer import write as writer_write
from gstar.storage.duckdb_store import DuckStore
from gstar.storage.faiss_index import FaissStore


def _reverse_ingest(
    output_path: Path,
    store: DuckStore,
    paths: Paths,
    namespace: str,
    track: str,
) -> dict | None:
    """생성된 md 를 G 본체(node/edge) 에 역삽입. 이후 단계 retrieval 에서 재활용.

    원격 g-serve 가 가용하면 `GClient.ingest_web` 을 통해 서버 측 인덱스에 기록.
    실패 또는 `GP_WEB_REMOTE=off` 면 로컬 `ingest_path` 로 폴백.
    """
    if not output_path or not output_path.exists():
        return None

    try:
        from gstar.enrich.g_cache import _remote_available
        use_remote = _remote_available()
    except Exception:
        use_remote = False

    if use_remote:
        remote = _reverse_ingest_remote(output_path, namespace=namespace, track=track)
        if remote is not None and "error" not in remote:
            return remote

    try:
        from gstar.embedding.sbert import SBertEmbedder
        from gstar.ingest.pipeline import ingest_path

        embedder = SBertEmbedder()
        faiss = FaissStore(paths.faiss, dim=embedder.dim)
        report = ingest_path(
            output_path,
            store=store,
            faiss=faiss,
            embedder=embedder,
            namespace=namespace,
            track=track,
        )
        faiss.save()
        return {
            "facts": report.facts,
            "entities": report.entities,
            "edges": report.edges,
        }
    except Exception as e:
        return {"error": str(e)[:200]}


def _reverse_ingest_remote(
    output_path: Path, *, namespace: str, track: str
) -> dict | None:
    """md 파일/디렉터리 → GClient.ingest_web payload 로 변환해 서버 경로로 전송."""
    try:
        from gstar.client import GClient
    except ImportError:
        return None

    md_paths: list[Path] = []
    if output_path.is_file() and output_path.suffix.lower() in (".md", ".markdown"):
        md_paths = [output_path]
    elif output_path.is_dir():
        md_paths = sorted(p for p in output_path.rglob("*.md") if p.is_file())
    if not md_paths:
        return None

    results: list[dict] = []
    for p in md_paths:
        try:
            body = p.read_text(encoding="utf-8")
        except OSError:
            continue
        title = p.stem
        results.append({
            "url": f"file://{p.resolve()}",
            "title": title,
            "snippet": body[:400],
            "content": body,
        })
    if not results:
        return None

    try:
        c = GClient()
        try:
            resp = c.ingest_web(
                query=f"projection:{track}",
                results=results,
                namespace=namespace,
                ttl_days=0,  # projection 산출물은 TTL dedupe 안 함 (매번 반영)
            )
        finally:
            c.close()
    except Exception as e:
        return {"error": str(e)[:200]}

    return {
        "facts": int(resp.get("facts", 0)),
        "entities": int(resp.get("entities", 0)),
        "edges": int(resp.get("edges", 0)),
        "via": "remote",
    }


def _maybe_enrich_start(topic: str, store: DuckStore, pid: str, track: str):
    """ENRICH_HOST 설정돼 있으면 백그라운드 enrich subscriber 시작. 없으면 None."""
    host = os.environ.get("GP_ENRICH_HOST")
    if not host or os.environ.get("GP_ENRICH", "off").lower() == "off":
        return None
    try:
        from gstar.enrich.client import EnrichSubscriber
        from gstar.enrich.policy import EnrichRequest
        from gstar.stellar.gap_analysis import gap_stats_for_nodes

        ent_rows = store.conn.execute(
            "SELECT node_id FROM entity_canonical WHERE project_id=? AND node_id IS NOT NULL "
            "ORDER BY mentions DESC LIMIT 30",
            [pid],
        ).fetchall()
        anchor_ids = [r[0] for r in ent_rows if r[0]]
        gap = gap_stats_for_nodes(anchor_ids, store) if anchor_ids else None
        if gap is None or not gap.under_connected:
            return None
        req = EnrichRequest(
            cluster_topic=topic[:200],
            under_connected=[
                {
                    "node_id": g.node_id,
                    "text": g.text,
                    "kind": g.kind,
                    "current_degree": g.current_degree,
                    "shortfall": g.shortfall,
                    "neighbor_ids": g.neighbor_ids,
                }
                for g in gap.under_connected[:8]
            ],
            project_id=pid,
        )
        sub = EnrichSubscriber(host)
        sub.start(req)
        return sub
    except Exception:
        return None


def _drain_enrich(sub, store: DuckStore) -> dict | None:
    if sub is None:
        return None
    try:
        from gstar.enrich.client import drain_queue

        def hash_checker(h: str) -> bool:
            row = store.conn.execute(
                "SELECT 1 FROM node WHERE content_hash = ? LIMIT 1", [h]
            ).fetchone()
            return row is not None

        report = drain_queue(sub.queue, store, embedder=None, hash_checker=hash_checker)
        return {
            "facts": report.facts_inserted,
            "edges": report.edges_inserted,
            "dup": report.dropped_duplicate,
            "invalid": report.dropped_invalid,
        }
    except Exception as e:
        return {"error": str(e)[:200]}


project_app = typer.Typer(help="P축 — Claude↔Gemma 격차 보강 투영기")


def _project_id_from_dir(project_dir: Path) -> str:
    return project_dir.resolve().name


def _retrieval_hits(query: str, top_k: int, namespace: str | None) -> list:
    """Retrieval 전략:
    1. `GP_LOCAL_RETRIEVAL=on` (기본 on) — 로컬 DuckDB+FAISS 에서 gravity 검색
    2. 로컬 실패·off 이면 원격 g-serve GClient.search() 폴백
    """
    mode = os.environ.get("GP_LOCAL_RETRIEVAL", "on").lower()
    if mode == "on":
        local = _retrieval_hits_local(query, top_k, namespace)
        if local:
            return local
    try:
        from gstar.client import from_env

        client = from_env()
        hits = client.search(query, top_k=top_k, namespace=namespace)
        return list(hits)
    except Exception:
        return []


def _retrieval_hits_local(query: str, top_k: int, namespace: str | None) -> list:
    """로컬 DuckStore+FAISS 로 gravity 검색. GClient 와 동일 필드를 가진 dict 반환."""
    try:
        from gstar.config import Weights
        from gstar.embedding.sbert import SBertEmbedder
        from gstar.gravity.field import compute_gravity
        from gstar.schema import Goal

        paths = Paths.load()
        if not paths.db.exists():
            return []
        weights = Weights.load(paths.config)
        embedder = SBertEmbedder()
        faiss = FaissStore(paths.faiss, dim=embedder.dim)
        store = DuckStore(paths.db)
        try:
            goal_emb = embedder.encode([query])[0]
            tmp_goal = Goal(text=query, kind="proposal")
            entries = compute_gravity(tmp_goal, goal_emb, store, faiss, weights)
            out: list = []
            for e in entries[: top_k * 3]:  # 여유 후 namespace 필터
                n = store.get_node(e.node_id)
                if n is None:
                    continue
                if namespace and n.source_namespace != namespace:
                    continue
                out.append(
                    {
                        "text": n.text,
                        "score": float(e.total),
                        "source": f"{n.source_namespace}/{(n.attrs or {}).get('source', '')}",
                        "node_id": n.id,
                        "content_hash": n.content_hash,
                        "namespace": n.source_namespace,
                    }
                )
                if len(out) >= top_k:
                    break
            return out
        finally:
            store.close()
    except Exception:
        return []


def _entities_for_project(store: DuckStore, project_id: str, track: str) -> list:
    return list_canonicals(store, project_id, track)


def _register_input_entities(
    artifact: StageArtifact,
    project_id: str,
    track: str,
    store: DuckStore,
) -> None:
    """단계 원문에서 엔티티 추출 후 canonical 로 등록. 최소 등록 — 형태소 기반."""
    try:
        from gstar.entity.classifier import classify
        from gstar.entity.normalizer import extract_content_terms
    except Exception:
        return
    terms = extract_content_terms(artifact.text, join_adjacent=True)
    seen: set[str] = set()
    for t in terms:
        if t in seen or len(t) < 2:
            continue
        seen.add(t)
        try:
            kind = classify(t, context=artifact.text[:120], track=track)
        except Exception:
            kind = EntityKind.OTHER
        try:
            link(t, project_id, track, store, kind_hint=kind)
        except ValueError:
            continue


@project_app.command("plugins")
def plugins_cmd() -> None:
    """등록된 트랙 목록."""
    for name in list_tasks():
        typer.echo(name)


@project_app.command("stats")
def stats_cmd(project_dir: Path = typer.Argument(".", help="프로젝트 디렉터리")) -> None:
    """프로젝트 상태: 단계 산출물 · fact · projection_run 이력."""
    project_dir = Path(project_dir).resolve()
    paths = Paths.load()
    store = DuckStore(paths.db)
    try:
        artifacts = load_project(project_dir)
        pid = _project_id_from_dir(project_dir)
        fact_rows = store.conn.execute(
            "SELECT track, kind, COUNT(*) FROM projection_fact WHERE project_id = ? GROUP BY track, kind",
            [pid],
        ).fetchall()
        run_rows = store.conn.execute(
            "SELECT track, stage, COUNT(*), AVG(duration_ms), AVG(coherence_retries) "
            "FROM projection_run WHERE project_id = ? GROUP BY track, stage ORDER BY stage",
            [pid],
        ).fetchall()
    finally:
        store.close()

    typer.echo(f"project:  {project_dir.name}  ({project_dir})")
    typer.echo(f"stages:   {len(artifacts)}")
    for a in artifacts:
        typer.echo(f"  {a.seq:02d}-{a.stage:15s} chars={a.words}  {a.path.name}")
    typer.echo(f"\nfacts by track/kind:")
    for row in fact_rows:
        typer.echo(f"  {row[0]:10s} {row[1]:14s} {row[2]}")
    typer.echo(f"\nprojection runs:")
    for row in run_rows:
        avg_dur = f"{int(row[3] or 0)}ms"
        typer.echo(
            f"  {row[0]:10s} {row[1]:15s} n={row[2]}  avg={avg_dur}  "
            f"retries={float(row[4] or 0):.1f}"
        )


@project_app.command("list")
def list_cmd(
    project_dir: Path = typer.Option(Path("."), "--project", "-P"),
    track: str = typer.Option(None, "--track"),
) -> None:
    """projection_run 이력 조회."""
    paths = Paths.load()
    store = DuckStore(paths.db)
    pid = _project_id_from_dir(project_dir)
    try:
        sql = (
            "SELECT id, track, stage, output_path, created_at, duration_ms, coherence_retries "
            "FROM projection_run WHERE project_id = ?"
        )
        params = [pid]
        if track:
            sql += " AND track = ?"
            params.append(track)
        sql += " ORDER BY created_at DESC LIMIT 50"
        rows = store.conn.execute(sql, params).fetchall()
    finally:
        store.close()
    if not rows:
        typer.echo("(no runs)")
        return
    for row in rows:
        typer.echo(
            f"{row[0][:10]}  {row[1]:10s}  {row[2]:15s}  {row[5]}ms  "
            f"retries={row[6]}  {row[3]}"
        )


@project_app.command("verify")
def verify_cmd(output: Path = typer.Argument(..., exists=True)) -> None:
    """출력 md 의 citations 블록 해시 검증."""
    import re as _re

    text = output.read_text(encoding="utf-8")
    m = _re.search(r"<!--\s*citations:\s*([a-f0-9,\s]+)\s*-->", text)
    if not m:
        typer.echo("citations 블록 없음")
        raise typer.Exit(1)
    hashes = [h.strip() for h in m.group(1).split(",") if h.strip()]
    typer.echo(f"citations: {len(hashes)}")
    paths = Paths.load()
    store = DuckStore(paths.db)
    try:
        found = 0
        for h in hashes:
            row = store.conn.execute(
                "SELECT COUNT(*) FROM projection_fact WHERE source_hash = ?", [h]
            ).fetchone()
            if row and row[0] > 0:
                found += 1
        typer.echo(f"registered: {found}/{len(hashes)}")
    finally:
        store.close()


_STAGE_SEQ_MAP = {
    "input": "00",
    # 공통 (proposal·research 동명)
    "idea": "01", "debate": "02", "structure": "03", "spec": "04",
    "risk-check": "05", "experiment-plan": "06", "proposal": "07",
    "final-doc": "08",
    # research bottom-up 실험 / 지시 — proposal 트랙에서도 동일 번호로 공유
    "lab-note": "09", "lab-compare": "10", "award-to-dev": "11", "instruction": "12",
    # document·coding 트랙
    "outline": "01", "draft": "02", "revise": "03", "finalize": "04",
    "explore": "01", "plan": "02", "implement": "03", "test": "04", "review": "05",
}


def _resolve_stage_dir(project_dir: Path, stage: str) -> Path:
    """NN-<stage> 폴더 경로 결정 (writer.py 와 동일 규약).

    1) `[0-9][0-9]-<stage>*` 로 기존 폴더 매칭 (사용자가 이름 일부 커스텀한 경우도 수용)
    2) 없으면 seq_map 기반 `01-idea` 같이 생성
    """
    # 기존 폴더 우선
    for d in sorted(project_dir.glob(f"[0-9][0-9]-{stage}*")):
        if d.is_dir():
            return d
    prefix = _STAGE_SEQ_MAP.get(stage, "NN")
    return project_dir / f"{prefix}-{stage}"


def _find_form_md(project_dir: Path) -> Path | None:
    """00-input/BASE/ 에서 양식 markdown 탐지.

    우선순위:
      1) 00-input/BASE/template-full.md
      2) 00-input/BASE/*.md  (가장 큰 파일)
      3) 00-input/template-full.md
    """
    candidates = [
        project_dir / "00-input" / "BASE" / "template-full.md",
        project_dir / "00-input" / "template-full.md",
    ]
    for c in candidates:
        if c.exists():
            return c
    base = project_dir / "00-input" / "BASE"
    if base.exists() and base.is_dir():
        mds = sorted(base.glob("*.md"), key=lambda p: p.stat().st_size, reverse=True)
        if mds:
            return mds[0]
    return None


def _should_answer_question(q, stage: str) -> bool:
    """stage 에 맞춰 이 질문을 답변 대상으로 채택할지.

    - idea: 상위 heading (depth<=3) 만 - 너무 잘게 나누면 아이디어 구상엔 과함
    - structure: heading + table_row_label (실질 질문 전부, 답변 슬롯 제외)
    - spec / proposal / final-doc: leaves 전부 (최대 해상도)
    - 그 외: heading + table_row_label

    boilerplate (서명문·[별지]·Section N·HWPX 메타 등) 은 stage 무관 제외.
    """
    from gstar.forms.question_tree import is_boilerplate
    if is_boilerplate(q.title):
        return False

    # stage 분류 (해상도 기준):
    #  coarse : 상위 heading 만 (굵은 주제 탐색)
    #  label  : + table_row_label (실질 질문 라벨)
    #  fine   : + table_header (셀 단위 최대 해상도)
    coarse_stages = {"idea", "debate"}
    label_stages = {
        "structure", "risk-check",
        # bottom-up 흐름: lab-note 는 개별 실험 기록이라 label 까지
        "lab-note",
    }
    fine_stages = {
        "spec", "proposal", "experiment-plan", "final-doc",
        # 신규 stage — 상세 기술/지시 필요
        "lab-compare",    # 메트릭 셀 하나하나가 질문
        "award-to-dev",   # 태스크·마일스톤 셀 단위
        "instruction",    # 지시 요건 셀 단위
    }

    if stage in coarse_stages:
        return q.type == "heading" and q.depth <= 3
    if stage in label_stages:
        return q.type in {"heading", "table_row_label"}
    if stage in fine_stages:
        return q.type in {"heading", "table_row_label", "table_header"}
    return q.type in {"heading", "table_row_label"}


def _question_goal(q, user_input: str, stage: str) -> str:
    """질문 + 사용자 input → Selector goal 문자열."""
    path_hint = q.path.split(" > ")[-3:]  # 마지막 3 path 만
    bc = " > ".join(path_hint)
    topic = f"[{stage}] {user_input}" if user_input else f"[{stage}]"
    return f"{topic} — 질문: {q.title} (맥락: {bc})"


def _run_mode_e(
    *,
    track_name: str,
    stage: str,
    project_dir: Path,
    user_input: str,
    mode: str,
) -> None:
    """Phase E pipeline (sv|svr|svrr). Ollama 필요.

    Phase P-Q2: 양식(template-full.md) 에서 질문 트리 추출 가능하면
    질문별 per-question run_pipeline 반복. 없으면 legacy single-section.

    env:
      GP_FORM_QUESTIONS=off → 질문 모드 비활성 (legacy 동작)
      GP_QUESTION_LIMIT=N   → 디버그용 처음 N개만
    """
    from gstar.client import from_env as gclient_from_env
    from gstar.projection.pipeline_e import run_pipeline
    from gstar.projection.projector import SectionSpec

    paths = Paths.load()
    store = DuckStore(paths.db)
    gclient = gclient_from_env()

    # Jira 연동 — GP_JIRA_KEY 있으면 티켓 메타를 user_input 에 prepend
    jira_key = (os.environ.get("GP_JIRA_KEY") or "").strip()
    if jira_key:
        try:
            from gstar.integrations.jira import (
                get_issue, summarize_issue, summary_to_context_block,
            )
            issue = get_issue(jira_key)
            summ = summarize_issue(issue)
            context = summary_to_context_block(summ)
            user_input = f"{context}\n\n---\n\n{user_input}".strip()
            typer.echo(f"[jira] {jira_key}: {summ['summary'][:60]}")
        except Exception as exc:
            typer.echo(f"[jira] {jira_key} pull 실패: {type(exc).__name__}: {exc}")

    # dev/ri/<KEY>/ 를 질문 source 로 사용 — GP_FROM_RI 또는 GP_JIRA_KEY 시
    from_ri_key = (os.environ.get("GP_FROM_RI") or jira_key).strip()
    ri_dir = (project_dir / "dev" / "ri" / from_ri_key) if from_ri_key else None

    if ri_dir is not None and ri_dir.exists():
        stage_dir = ri_dir
    else:
        stage_dir = _resolve_stage_dir(project_dir, stage)
    stage_dir.mkdir(parents=True, exist_ok=True)
    out_dir = stage_dir / f"_mode_{mode}"
    out_dir.mkdir(parents=True, exist_ok=True)

    use_questions = os.environ.get("GP_FORM_QUESTIONS", "on").lower() in {"on", "1", "true"}
    # --from-ri 시 ri_dir 의 템플릿 md 들을 양식으로 사용
    if ri_dir is not None and ri_dir.exists() and use_questions:
        # ri_dir 안 md 들 중 stage 에 매칭되는 것 우선, 없으면 전체 합본 을 질문소스로
        stage_md = next(
            (p for p in sorted(ri_dir.glob("*.md"))
             if stage.replace("-", "") in p.stem.replace("-", "")),
            None,
        )
        if stage_md is None:
            # fallback — 전체 ri 디렉터리의 md 를 합쳐 임시 양식 생성
            combined = "\n\n".join(
                p.read_text(encoding="utf-8")
                for p in sorted(ri_dir.glob("*.md"))
                if not p.name.startswith("_")
            )
            stage_md = ri_dir / "_combined_for_questions.md"
            stage_md.write_text(combined, encoding="utf-8")
        form_md = stage_md
        typer.echo(f"[from-ri] {from_ri_key}: 질문 source={form_md.name}")
    else:
        form_md = _find_form_md(project_dir) if use_questions else None

    if form_md is not None:
        from gstar.forms.question_tree import load_questions
        tree = load_questions(form_md)
        candidates = [q for q in tree.nodes if _should_answer_question(q, stage)]

        # 랭킹 (기본 on — limit 주는 경우 상위부터 선택)
        rank_mode = os.environ.get("GP_QUESTION_RANK", "on").lower() in {"on", "1", "true"}
        rank_rag = os.environ.get("GP_QUESTION_RANK_RAG", "off").lower() in {"on", "1", "true"}
        ranking_report = None
        if rank_mode and candidates:
            from gstar.forms.ranking import rank_questions, rank_with_rag
            id_to_q = {q.id: q for q in candidates}
            if rank_rag:
                gurl = os.environ.get("GSTAR_SERVER_URL", "http://100.79.251.53:9999")
                ranked = rank_with_rag(candidates, stage, g_url=gurl)
            else:
                ranked = rank_questions(candidates, stage)
            candidates = [id_to_q[r.q_id] for r in ranked]
            ranking_report = [
                {"q_id": r.q_id, "score": r.score, "title": r.title[:70],
                 "type": r.type, "breakdown": r.breakdown}
                for r in ranked
            ]

        limit = int(os.environ.get("GP_QUESTION_LIMIT", "0") or "0")
        if limit > 0:
            candidates = candidates[:limit]
        typer.echo(
            f"[question-mode] form={form_md.name} "
            f"total={len(tree)} matched={len(candidates)} stage={stage} "
            f"rank={'on' if rank_mode else 'off'}{'(+rag)' if rank_rag else ''}"
        )
        if ranking_report is not None:
            # 랭킹 로그 저장
            import json as _json_rank
            (out_dir / "ranking.json").write_text(
                _json_rank.dumps(ranking_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        all_texts: list[str] = []
        reports: list[dict] = []
        q_out_dir = out_dir / "q"
        q_out_dir.mkdir(parents=True, exist_ok=True)

        import json as _json
        for i, q in enumerate(candidates, start=1):
            goal = _question_goal(q, user_input, stage)
            section = SectionSpec(
                name=f"{stage}::{q.id}",
                instruction=q.title + (f"\n경로: {q.path}" if q.path else ""),
                target_tokens=400,
            )
            try:
                rep = run_pipeline(
                    goal=goal,
                    track=track_name,
                    section=section,
                    gclient=gclient,
                    store=store,
                    mode=mode,
                )
            except Exception as exc:
                typer.echo(f"  [{i}/{len(candidates)}] {q.id} 실패: {type(exc).__name__}: {exc}")
                continue

            text = (rep.projector.text if rep.projector else "").strip()
            has_answer = bool(text) and "(Projector 호출 실패" not in text
            if has_answer:
                (q_out_dir / f"{q.id}.md").write_text(
                    f"# {q.title}\n\n_path_: {q.path}\n_type_: {q.type}\n\n{text}\n",
                    encoding="utf-8",
                )
                all_texts.append(f"## {q.title}\n\n{text}\n")
            else:
                # 빈 답변이면 파일 생성 금지 — warnings 만 기록
                typer.echo(
                    f"  [{i}/{len(candidates)}] {q.id} skip (fact 없음/Projector 실패)"
                )

            reports.append({
                "q_id": q.id, "q_title": q.title, "q_type": q.type,
                "ok": has_answer,
                "warnings": rep.warnings,
            })

            # incremental report — 중간 crash 시에도 진행 상황 확인 가능
            try:
                (out_dir / "report.json").write_text(
                    _json.dumps(
                        {"stage": stage, "in_progress": True, "done": i,
                         "total": len(candidates), "questions": reports},
                        ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass

            if i % 10 == 0:
                ok_cnt = sum(1 for r in reports if r["ok"])
                typer.echo(f"  [{i}/{len(candidates)}] ok={ok_cnt} skip={i-ok_cnt}")

        # 기존 stage.md 있으면 _versions/ 로 백업 후 덮어쓰기
        main_path = stage_dir / f"{stage}.md"
        if main_path.exists():
            ver_dir = stage_dir / "_versions"
            ver_dir.mkdir(exist_ok=True)
            n = len(list(ver_dir.glob(f"{stage}-v*.md"))) + 1
            ver_path = ver_dir / f"{stage}-v{n}.md"
            ver_path.write_text(main_path.read_text(encoding="utf-8"), encoding="utf-8")
            typer.echo(f"기존 {main_path.name} → {ver_path.relative_to(stage_dir)} 백업")
        main_path.write_text("\n".join(all_texts), encoding="utf-8")
        ok_cnt = sum(1 for r in reports if r["ok"])
        skip_cnt = len(reports) - ok_cnt
        typer.echo(
            f"stage 합본 저장: {main_path} (ok={ok_cnt} skip={skip_cnt} total={len(reports)})"
        )
        report_path = out_dir / "report.json"
        report_path.write_text(
            _json.dumps({
                "stage": stage,
                "in_progress": False,
                "summary": {"total": len(reports), "ok": ok_cnt, "skip": skip_cnt},
                "questions": reports,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        typer.echo(f"report: {report_path}")
        return

    # Legacy 단일 섹션 경로 (양식 없거나 비활성)
    section = SectionSpec(
        name=stage,
        instruction=user_input or f"{stage} 단계 섹션 작성",
        target_tokens=500,
    )
    goal = user_input or stage

    rep = run_pipeline(
        goal=goal,
        track=track_name,
        section=section,
        gclient=gclient,
        store=store,
        mode=mode,
    )

    main_path = stage_dir / f"{stage}.md"
    if rep.projector is not None:
        if main_path.exists():
            ver_dir = stage_dir / "_versions"
            ver_dir.mkdir(exist_ok=True)
            n = len(list(ver_dir.glob(f"{stage}-v*.md"))) + 1
            (ver_dir / f"{stage}-v{n}.md").write_text(
                main_path.read_text(encoding="utf-8"), encoding="utf-8"
            )
        main_path.write_text(rep.projector.text, encoding="utf-8")
        typer.echo(f"섹션 저장: {main_path}")
    else:
        typer.echo(f"[mode={mode}] Projector skip. warnings={rep.warnings}")

    report_path = out_dir / "report.json"
    import json as _json
    report_path.write_text(_json.dumps(rep.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo(f"report: {report_path}")


@project_app.command("run")
def run_cmd(
    track_name: str = typer.Argument(..., help="proposal|research|coding|document"),
    stage: str = typer.Argument(..., help="단계 이름"),
    project_dir: Path = typer.Option(Path("."), "--project", "-P"),
    user_input: str = typer.Option("", "--input", "-i", help="단계 입력 (idea 용)"),
    form_id: str = typer.Option(None, "--form"),
    coherence_mode: str = typer.Option(
        os.environ.get("GP_COHERENCE", "strict"),
        "--coherence",
        help="strict|loose|off",
    ),
    ollama_model: str = typer.Option(None, "--model"),
    mode: str = typer.Option(
        os.environ.get("PROJECTION_MODE", "classic"),
        "--mode",
        help="classic|sv|svr|svrr — svrr 은 Selector→Projector→Verifier L1+L2→Reinforce (Phase E4)",
    ),
) -> None:
    """단일 단계 실행. PREV_CONTEXT 을 summarizer 로 압축하여 섹션 생성.

    mode=classic (기본) → 기존 renderer 경로.
    mode=sv|svr|svrr → Phase E pipeline_e.run_pipeline 으로 위임.
      svrr 이 권장. Gemma Ollama 호출 필요 (SELECTOR_OLLAMA_HOST + GP_OLLAMA_HOST env).
    """
    if mode in {"sv", "svr", "svrr"}:
        _run_mode_e(
            track_name=track_name,
            stage=stage,
            project_dir=project_dir,
            user_input=user_input,
            mode=mode,
        )
        return

    project_dir = Path(project_dir).resolve()
    project_dir.mkdir(parents=True, exist_ok=True)

    try:
        task = get_task(track_name)
    except KeyError as e:
        typer.echo(str(e))
        raise typer.Exit(1)

    if stage not in task.stages:
        typer.echo(f"stage '{stage}' not in {task.name}.stages={task.stages}")
        raise typer.Exit(1)

    role = task.stage_role(stage)
    model = ollama_model or role.ollama_model
    pid = _project_id_from_dir(project_dir)

    paths = Paths.load()
    store = DuckStore(paths.db)
    t0 = time.time()
    enrich_sub = _maybe_enrich_start(
        topic=(user_input or stage)[:200],
        store=store,
        pid=_project_id_from_dir(project_dir),
        track=track_name,
    )
    try:
        existing_artifacts = load_project(project_dir)
        previous = prev_stages(existing_artifacts + [], stage) if existing_artifacts else []

        if user_input:
            pseudo = StageArtifact(
                stage="input",
                seq=0,
                path=project_dir / "00-input" / "prompt.md",
                text=user_input,
                words=len(user_input),
                created_at=time.time(),  # type: ignore[arg-type]
            )
            _register_input_entities(pseudo, pid, task.name, store)
            previous = [pseudo] + previous

        for a in previous:
            _register_input_entities(a, pid, task.name, store)

        prev_sum = summarize_prev(
            previous,
            role,
            task.name,
            store=store,
            target_kinds=task.fact_kinds,
            use_ollama=os.environ.get("GP_SUMMARIZER", "on").lower() == "on",
        )

        fact_upsert(prev_sum.facts, store, pid, track=task.name)

        hits = _retrieval_hits(
            role.retrieval_query.format(input=user_input or stage, project_goal=stage),
            role.retrieval_top_k,
            namespace=None,
        )
        entities = _entities_for_project(store, pid, task.name)

        try:
            form_ctx = load_form(project_dir)
        except Exception:
            form_ctx = None
        try:
            schema = task.section_schema(stage, form_context=form_ctx)  # type: ignore[call-arg]
        except TypeError:
            schema = task.section_schema(stage)
        sections_input = pack_sections(
            schema,
            prev_sum,
            hits,
            entities,
        )

        rendered: list = []
        for si in sections_input:
            rendered.append(
                render_section(
                    si,
                    role,
                    store,
                    pid,
                    task.name,
                    coherence_mode=coherence_mode,
                    ollama_model=model,
                    stage=stage,
                )
            )

        duration_ms = int((time.time() - t0) * 1000)
        result = writer_write(
            rendered,
            project_dir,
            stage,
            output_format=task.output_format(stage),
            title=None,
            citations=prev_sum.citations,
            fact_ids=[f.text[:40] for f in prev_sum.facts[:20]],
            store=store,
            project_id=pid,
            track=task.name,
            duration_ms=duration_ms,
            ollama_model=model,
        )

        ingest_report = None
        if os.environ.get("GP_REVERSE_INGEST", "on").lower() != "off":
            ingest_report = _reverse_ingest(
                result.output_path, store, paths, namespace=pid, track=task.name
            )

        enrich_report = _drain_enrich(enrich_sub, store)
    finally:
        if enrich_sub is not None:
            try:
                enrich_sub.stop(wait_s=0.2)
            except Exception:
                pass
        store.close()

    typer.echo(f"track:   {task.name}")
    typer.echo(f"stage:   {stage}")
    typer.echo(f"output:  {result.output_path}")
    typer.echo(f"bytes:   {result.bytes_written}")
    typer.echo(f"elapsed: {duration_ms}ms")
    typer.echo(f"run_id:  {result.run_id}")
    retries = sum((s.attempts - 1) for s in rendered)
    typer.echo(f"coherence_retries: {retries}")
    if ingest_report:
        if "error" in ingest_report:
            typer.echo(f"reverse_ingest: ERROR {ingest_report['error']}")
        else:
            typer.echo(
                f"reverse_ingest: facts={ingest_report['facts']} "
                f"entities={ingest_report['entities']} edges={ingest_report['edges']}"
            )
    if enrich_report:
        if "error" in enrich_report:
            typer.echo(f"enrich: ERROR {enrich_report['error']}")
        else:
            typer.echo(
                f"enrich: facts={enrich_report['facts']} edges={enrich_report['edges']} "
                f"dup={enrich_report['dup']} invalid={enrich_report['invalid']}"
            )


def main() -> None:
    project_app()

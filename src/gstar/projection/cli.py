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
    """생성된 md 를 G 본체(node/edge) 에 역삽입. 이후 단계 retrieval 에서 재활용."""
    if not output_path or not output_path.exists():
        return None
    try:
        from gstar.embedding.sbert import SBertEmbedder
        from gstar.ingest.pipeline import ingest_path

        embedder = SBertEmbedder()
        faiss = FaissStore(paths.faiss, dim=embedder.dim)
        try:
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
        finally:
            pass
    except Exception as e:
        return {"error": str(e)[:200]}


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
) -> None:
    """단일 단계 실행. PREV_CONTEXT 을 summarizer 로 압축하여 섹션 생성."""
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

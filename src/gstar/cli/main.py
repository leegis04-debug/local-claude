"""g CLI 엔트리포인트."""

from __future__ import annotations

import os
from pathlib import Path

import typer

from gstar.config import EMBED_DIM_DEFAULT, Paths, Weights
from gstar.storage.duckdb_store import DuckStore
from gstar.storage.faiss_index import FaissStore

app = typer.Typer(help="G — 지식 항성 모델 CLI")
goal_app = typer.Typer(help="목표 관리")
gravity_app = typer.Typer(help="중력 점수")
provenance_app = typer.Typer(help="namespace (이직·공유 대비)")
verify_app = typer.Typer(help="무결성 검증")
stellar_app = typer.Typer(help="지식 항성 (클러스터)")
emerge_app = typer.Typer(help="창발(4연결) 이벤트")
entity_app = typer.Typer(help="Phase A — 엔티티 서브시스템")

from gstar.projection.cli import project_app  # noqa: E402
app.add_typer(goal_app, name="goal")
app.add_typer(gravity_app, name="gravity")
app.add_typer(provenance_app, name="provenance")
app.add_typer(verify_app, name="verify")
app.add_typer(stellar_app, name="stellar")
app.add_typer(emerge_app, name="emerge")
app.add_typer(entity_app, name="entity")
app.add_typer(project_app, name="project")


def _ctx_namespace_hint() -> str | None:
    """ctx 패키지가 설치돼 있고 활성 context 에 gstar.namespace 가 있으면 반환."""
    try:
        from ctx.config import Paths as CtxPaths
        from ctx.context import (
            current_context_name,
            get_key_path,
            load_context,
        )
    except Exception:
        return None
    try:
        cpaths = CtxPaths.load()
        if not cpaths.current.exists():
            return None
        name = current_context_name(cpaths)
        if not name:
            return None
        data = load_context(name, cpaths)
        return str(get_key_path(data, "gstar.namespace"))
    except Exception:
        return None


@app.command()
def init(
    dim: int = typer.Option(EMBED_DIM_DEFAULT, help="임베딩 차원"),
    force: bool = typer.Option(False, "--force", help="기존 상태 파일 덮어쓰기"),
) -> None:
    """$GSTAR_HOME 에 DuckDB + FAISS 인덱스 초기화."""

    paths = Paths.load()
    paths.ensure()

    if paths.db.exists() and not force:
        typer.echo(f"이미 존재: {paths.db} (덮어쓰려면 --force)")
    else:
        if paths.db.exists():
            paths.db.unlink()
        store = DuckStore(paths.db)
        store.close()
        typer.echo(f"DuckDB 생성: {paths.db}")

    faiss_ids = paths.faiss.with_suffix(".ids.npy")
    if paths.faiss.exists() and not force:
        typer.echo(f"FAISS 인덱스 이미 존재: {paths.faiss}")
    else:
        for p in (paths.faiss, faiss_ids):
            if p.exists():
                p.unlink()
        fstore = FaissStore(paths.faiss, dim=dim)
        fstore.save()
        typer.echo(f"FAISS 인덱스 생성: {paths.faiss} (dim={dim})")


@app.command()
def status() -> None:
    """현재 저장소 요약."""

    paths = Paths.load()
    if not paths.db.exists():
        typer.echo("아직 init 되지 않음. `g init` 실행 필요.")
        raise typer.Exit(1)

    store = DuckStore(paths.db)
    try:
        goals = store.list_goals()
        node_count = store.count_nodes()
    finally:
        store.close()

    typer.echo(f"home:  {paths.home}")
    typer.echo(f"db:    {paths.db}")
    typer.echo(f"faiss: {paths.faiss}")
    typer.echo(f"nodes: {node_count}")
    typer.echo(f"goals: {len(goals)}")
    for g in goals:
        typer.echo(f"  - {g.id[:8]}  [{g.kind}] {g.text}")


@app.command()
def search(
    query: str = typer.Argument(..., help="검색 쿼리"),
    top_k: int = typer.Option(5, "--k", help="상위 개수"),
    namespace: str = typer.Option(None, "--ns", help="특정 namespace 로 제한"),
    mode: str = typer.Option("auto", "--mode", help="remote|local|auto"),
) -> None:
    """semantic search. remote 면 g-serve `/search/hybrid` 호출, local 은 gravity 계산."""
    mode_norm = (mode or "auto").strip().lower()
    if mode_norm not in ("remote", "local", "auto"):
        typer.echo("invalid --mode", err=True)
        raise typer.Exit(2)

    use_remote = False
    if mode_norm in ("remote", "auto"):
        try:
            from gstar.enrich.g_cache import _remote_available
            use_remote = _remote_available() if mode_norm == "auto" else True
        except Exception:
            use_remote = mode_norm == "remote"

    if use_remote:
        try:
            from gstar.client import GClient
            c = GClient()
            try:
                hits = c.search(query, top_k=top_k, namespace=namespace)
            finally:
                c.close()
            typer.echo("[remote]")
            for h in hits:
                text = (h.text or "").replace("\n", " ")[:80]
                typer.echo(f"{h.score:.3f}  [{h.namespace[:12]:12s}]  {text}")
            return
        except Exception as e:
            if mode_norm == "remote":
                typer.echo(f"원격 검색 실패: {e}", err=True)
                raise typer.Exit(4)
            typer.echo(f"(원격 실패, 로컬 폴백: {str(e)[:80]})", err=True)

    from gstar.embedding.sbert import SBertEmbedder
    from gstar.gravity.field import compute_gravity
    from gstar.schema import Goal

    paths = Paths.load()
    if not paths.db.exists():
        typer.echo("먼저 `g init` 을 실행하세요.")
        raise typer.Exit(1)
    weights = Weights.load(paths.config)
    embedder = SBertEmbedder()
    faiss = FaissStore(paths.faiss, dim=embedder.dim)
    store = DuckStore(paths.db)
    try:
        goal = Goal(text=query, kind="proposal")
        emb = embedder.encode([query])[0]
        entries = compute_gravity(goal, emb, store, faiss, weights)
        shown = 0
        typer.echo("[local]")
        for e in entries:
            if shown >= top_k:
                break
            node = store.get_node(e.node_id)
            if node is None:
                continue
            if namespace and node.source_namespace != namespace:
                continue
            text = (node.text or "").replace("\n", " ")[:80]
            typer.echo(f"{e.total:.3f}  [{node.source_namespace[:12]:12s}]  {text}")
            shown += 1
    finally:
        store.close()


@app.command()
def ingest(
    path: Path = typer.Argument(..., exists=True, help="파일 또는 디렉토리"),
    root: Path = typer.Option(
        None, "--root", help="상대 경로 기준점 (기본: path 의 부모)"
    ),
    min_entity_count: int = typer.Option(
        2, help="엔티티 후보 최소 등장 횟수"
    ),
    namespace: str = typer.Option(
        None, "--ns", help="source_namespace. 미지정 시 현재 활성 namespace"
    ),
    mode: str = typer.Option(
        "auto", "--mode",
        help="remote|local|auto. remote=g-serve 원격, local=맥북 DB, auto=원격 가능 시 원격",
    ),
) -> None:
    """문서를 fact/entity 노드로 분해하고 관계를 저장한다.

    --mode auto (기본): g-serve 가 응답하면 원격 ingest, 아니면 로컬 폴백.
    --mode remote: 원격만 시도. 실패 시 에러.
    --mode local: 로컬 DuckStore 직접.
    """

    from gstar.embedding.sbert import SBertEmbedder
    from gstar.ingest.pipeline import ingest_path

    # --ns 미지정 시 ctx 활성 context 의 gstar.namespace 를 힌트로 사용.
    if namespace is None:
        ctx_ns = _ctx_namespace_hint()
        if ctx_ns:
            namespace = ctx_ns
            typer.echo(f"(ctx 활성 context 기반 namespace: {namespace})", err=True)

    mode_norm = (mode or "auto").strip().lower()
    if mode_norm not in ("remote", "local", "auto"):
        typer.echo(f"invalid --mode: {mode}. remote|local|auto 중 하나.", err=True)
        raise typer.Exit(2)

    use_remote = False
    if mode_norm in ("remote", "auto"):
        try:
            from gstar.enrich.g_cache import _remote_available
            use_remote = _remote_available() if mode_norm == "auto" else True
        except Exception:
            use_remote = mode_norm == "remote"

    if use_remote:
        try:
            from gstar.client import GClient

            md_paths: list[Path] = []
            if path.is_file() and path.suffix.lower() in (".md", ".markdown", ".txt"):
                md_paths = [path]
            elif path.is_dir():
                md_paths = sorted(p for p in path.rglob("*.md") if p.is_file())
            if not md_paths:
                typer.echo("원격 ingest 는 .md 파일만 지원합니다. --mode local 사용.", err=True)
                raise typer.Exit(3)
            results = []
            for p in md_paths:
                try:
                    body = p.read_text(encoding="utf-8")
                except OSError:
                    continue
                results.append({
                    "url": f"file://{p.resolve()}",
                    "title": p.stem,
                    "snippet": body[:400],
                    "content": body,
                })
            c = GClient()
            try:
                resp = c.ingest_web(
                    query=f"cli:ingest:{path.name}",
                    results=results,
                    namespace=namespace or "web_cache",
                    ttl_days=0,
                )
            finally:
                c.close()
            typer.echo("[remote]")
            typer.echo(f"files:    {len(md_paths)}")
            typer.echo(f"facts:    {resp.get('facts', 0)}")
            typer.echo(f"entities: {resp.get('entities', 0)}")
            typer.echo(f"edges:    {resp.get('edges', 0)}")
            return
        except Exception as e:
            if mode_norm == "remote":
                typer.echo(f"원격 ingest 실패: {e}", err=True)
                raise typer.Exit(4)
            typer.echo(f"(원격 실패, 로컬 폴백: {str(e)[:80]})", err=True)

    paths = Paths.load()
    if not paths.db.exists():
        typer.echo("먼저 `g init` 을 실행하세요.")
        raise typer.Exit(1)

    embedder = SBertEmbedder()
    # init 시 dim 이 기본값(384)이 아니면 차원 미스매치 발생. 여기서 보정.
    faiss = FaissStore(paths.faiss, dim=embedder.dim)
    store = DuckStore(paths.db)
    try:
        report = ingest_path(
            path,
            store=store,
            faiss=faiss,
            embedder=embedder,
            root=root,
            min_entity_count=min_entity_count,
            namespace=namespace,
        )
        faiss.save()
    finally:
        store.close()

    typer.echo("[local]")
    typer.echo(f"files:    {report.files_scanned}")
    typer.echo(f"facts:    {report.facts}")
    typer.echo(f"entities: {report.entities}")
    typer.echo(f"edges:    {report.edges}")


@goal_app.command("set")
def goal_set(
    text: str = typer.Argument(..., help="목표 서술"),
    kind: str = typer.Option("proposal", "--kind", help="proposal|code|research"),
) -> None:
    """목표 생성 + 임베딩 색인. id 를 표준출력으로 반환."""

    from gstar.embedding.sbert import SBertEmbedder
    from gstar.gravity.field import add_goal

    paths = Paths.load()
    if not paths.db.exists():
        typer.echo("먼저 `g init` 을 실행하세요.")
        raise typer.Exit(1)

    embedder = SBertEmbedder()
    faiss = FaissStore(paths.faiss, dim=embedder.dim)
    store = DuckStore(paths.db)
    try:
        goal, _ = add_goal(text=text, kind=kind, store=store, faiss=faiss, embedder=embedder)
    finally:
        store.close()

    typer.echo(goal.id)


@goal_app.command("list")
def goal_list() -> None:
    paths = Paths.load()
    if not paths.db.exists():
        typer.echo("먼저 `g init` 을 실행하세요.")
        raise typer.Exit(1)
    store = DuckStore(paths.db)
    try:
        goals = store.list_goals()
    finally:
        store.close()
    for g in goals:
        typer.echo(f"{g.id}  [{g.kind}]  {g.text}")


@gravity_app.command("top")
def gravity_top(
    goal_id: str = typer.Argument(..., help="Goal id"),
    k: int = typer.Option(20, "--k", help="상위 몇 개"),
    show_breakdown: bool = typer.Option(False, "--breakdown", help="항목별 점수 출력"),
) -> None:
    """주어진 goal 에 대한 상위 중력 노드."""

    from gstar.embedding.sbert import SBertEmbedder
    from gstar.gravity.field import compute_gravity, get_goal_embedding

    paths = Paths.load()
    if not paths.db.exists():
        typer.echo("먼저 `g init` 을 실행하세요.")
        raise typer.Exit(1)

    weights = Weights.load(paths.config)
    embedder = SBertEmbedder()
    faiss = FaissStore(paths.faiss, dim=embedder.dim)
    store = DuckStore(paths.db)
    try:
        goal = store.get_goal(goal_id)
        if goal is None:
            typer.echo(f"goal not found: {goal_id}")
            raise typer.Exit(1)
        goal_emb = get_goal_embedding(faiss, goal_id)
        if goal_emb is None:
            # 없으면 즉석 인코딩 (init 후 외부 복원 등 케이스)
            goal_emb = embedder.encode([goal.text])[0]
        entries = compute_gravity(goal, goal_emb, store, faiss, weights)
        for e in entries[:k]:
            node = store.get_node(e.node_id)
            if node is None:
                continue
            text = node.text[:60].replace("\n", " ")
            line = f"{e.total:.3f}  [{node.kind[:5]:5s}]  {text}"
            if show_breakdown:
                b = e.breakdown
                line += (
                    f"   (rel={b['rel']:.2f} rec={b['rec']:.2f} cent={b['cent']:.2f} "
                    f"ver={b['ver']:.2f} pur={b['pur']:.2f} stab={b['stab']:.2f})"
                )
            typer.echo(line)
    finally:
        store.close()


@provenance_app.command("list")
def provenance_list() -> None:
    """등록된 namespace 목록."""

    paths = Paths.load()
    if not paths.db.exists():
        typer.echo("먼저 `g init` 을 실행하세요.")
        raise typer.Exit(1)
    store = DuckStore(paths.db)
    try:
        for ns in store.list_namespaces():
            flag = "*" if ns.is_active else " "
            typer.echo(f"{flag} {ns.name:20s}  {ns.description}")
    finally:
        store.close()


@provenance_app.command("set-ns")
def provenance_set_ns(
    name: str = typer.Argument(..., help="새 활성 namespace 이름"),
    description: str = typer.Option("", "--description", help="설명"),
) -> None:
    """namespace 전환(없으면 생성). 이후 ingest 는 이 namespace 로 들어간다."""

    from gstar.schema import Namespace

    paths = Paths.load()
    if not paths.db.exists():
        typer.echo("먼저 `g init` 을 실행하세요.")
        raise typer.Exit(1)
    store = DuckStore(paths.db)
    try:
        if description:
            store.upsert_namespace(
                Namespace(name=name, description=description, is_active=False)
            )
        store.set_active_namespace(name)
    finally:
        store.close()
    typer.echo(f"active namespace: {name}")


@verify_app.command("chain")
def verify_chain_cmd(
    namespace: str = typer.Option(
        None, "--ns", help="검증할 namespace. 미지정 시 활성 namespace"
    ),
) -> None:
    """namespace 내 해시 체인 무결성 검증."""

    from gstar.integrity.verify import verify_chain

    paths = Paths.load()
    if not paths.db.exists():
        typer.echo("먼저 `g init` 을 실행하세요.")
        raise typer.Exit(1)
    store = DuckStore(paths.db)
    try:
        ns = namespace or store.active_namespace()
        nodes = store.nodes_by_namespace(ns)
        result = verify_chain(nodes)
    finally:
        store.close()

    typer.echo(f"namespace: {ns}")
    typer.echo(f"checked: {result.checked}")
    if result.ok:
        typer.echo("chain: OK")
    else:
        typer.echo(f"chain: FAIL ({len(result.bad_node_ids)} issues)")
        for r in result.bad_reasons[:10]:
            typer.echo(f"  - {r}")
        raise typer.Exit(1)


@verify_app.command("cluster")
def verify_cluster_cmd(
    cluster_id: str = typer.Argument(..., help="Cluster id"),
) -> None:
    """Cluster Merkle root 재계산·대조."""

    from gstar.integrity.merkle import merkle_root

    paths = Paths.load()
    if not paths.db.exists():
        typer.echo("먼저 `g init` 을 실행하세요.")
        raise typer.Exit(1)
    store = DuckStore(paths.db)
    try:
        cluster = store.get_cluster(cluster_id)
        if cluster is None:
            typer.echo(f"cluster not found: {cluster_id}")
            raise typer.Exit(1)
        members = store.cluster_members(cluster_id)
        hashes = []
        for node_id, _ in members:
            n = store.get_node(node_id)
            if n and n.content_hash:
                hashes.append(n.content_hash)
        computed = merkle_root(hashes)
    finally:
        store.close()

    typer.echo(f"members: {len(hashes)}")
    typer.echo(f"stored:   {cluster.merkle_root}")
    typer.echo(f"computed: {computed}")
    if cluster.merkle_root == computed:
        typer.echo("merkle: OK")
    else:
        typer.echo("merkle: FAIL")
        raise typer.Exit(1)


@app.command()
def run(
    goal_id: str = typer.Argument(..., help="Goal id"),
    top_k: int = typer.Option(30, "--k", help="사이클당 후보 수"),
    max_cycles: int = typer.Option(6, "--max-cycles"),
    stable_window: int = typer.Option(2, "--stable", help="연속 안정 사이클"),
    ollama_host: str = typer.Option("http://localhost:11434", "--host"),
    model: str = typer.Option("gemma4:e4b", "--model"),
) -> None:
    """선택기 루프 실행. Gemma e4b 경계 판정으로 지식 항성 후보 고정."""

    from gstar.selector.gemma_client import OllamaChatClient
    from gstar.selector.selection_loop import run_selection

    paths = Paths.load()
    if not paths.db.exists():
        typer.echo("먼저 `g init` 을 실행하세요.")
        raise typer.Exit(1)

    weights = Weights.load(paths.config)
    store = DuckStore(paths.db)
    faiss = FaissStore(paths.faiss, dim=EMBED_DIM_DEFAULT)
    try:
        goal = store.get_goal(goal_id)
        if goal is None:
            typer.echo(f"goal not found: {goal_id}")
            raise typer.Exit(1)

        judge = OllamaChatClient(host=ollama_host, model=model)
        result = run_selection(
            goal=goal,
            store=store,
            faiss=faiss,
            weights=weights,
            judge=judge,
            top_k=top_k,
            max_cycles=max_cycles,
            stable_window=stable_window,
        )
    finally:
        store.close()

    typer.echo(f"goal: {goal.text}")
    typer.echo(f"cycles: {len(result.cycles)}  converged: {result.converged}")
    for cr in result.cycles:
        typer.echo(
            f"  cycle {cr.cycle}: {cr.candidates} 후보 → "
            f"kept={len(cr.kept)} dropped={len(cr.dropped)} undecided={len(cr.undecided)}"
        )
    typer.echo(f"final_kept ({len(result.final_kept)}):")
    for nid in result.final_kept[:20]:
        n = None
        # 재오픈해서 미리보기
        s2 = DuckStore(paths.db)
        try:
            n = s2.get_node(nid)
        finally:
            s2.close()
        if n:
            typer.echo(f"  {nid[:10]}  [{n.kind[:5]:5s}]  {n.text[:60]}")


@stellar_app.command("build")
def stellar_build(
    goal_id: str = typer.Argument(..., help="Goal id"),
    k: int = typer.Option(30, "--k", help="중력 top-k 후보 범위"),
    min_members: int = typer.Option(2, "--min", help="클러스터 최소 멤버 수"),
) -> None:
    """중력 상위 노드들을 connected components 로 묶어 클러스터 생성."""

    from gstar.embedding.sbert import SBertEmbedder
    from gstar.gravity.field import compute_gravity, get_goal_embedding
    from gstar.stellar.cluster import build_clusters
    from gstar.stellar.emergence import scan_emergence
    from gstar.stellar.stability import update_cluster_stability

    paths = Paths.load()
    if not paths.db.exists():
        typer.echo("먼저 `g init` 을 실행하세요.")
        raise typer.Exit(1)

    weights = Weights.load(paths.config)
    embedder = SBertEmbedder()
    faiss = FaissStore(paths.faiss, dim=embedder.dim)
    store = DuckStore(paths.db)
    try:
        goal = store.get_goal(goal_id)
        if goal is None:
            typer.echo(f"goal not found: {goal_id}")
            raise typer.Exit(1)
        goal_emb = get_goal_embedding(faiss, goal_id)
        if goal_emb is None:
            goal_emb = embedder.encode([goal.text])[0]

        entries = compute_gravity(goal, goal_emb, store, faiss, weights)
        top = entries[:k]
        gravity_by_id = {e.node_id: e.total for e in top}

        br = build_clusters(goal, gravity_by_id.keys(), gravity_by_id, store)

        # 각 클러스터 stability 갱신
        for cid in br.clusters:
            update_cluster_stability(cid, store)

        # 창발 스캔 (top-k 후보만)
        er = scan_emergence(goal, list(gravity_by_id.keys()), store)
    finally:
        store.close()

    typer.echo(f"clusters built: {len(br.clusters)}")
    typer.echo(f"singletons (제외): {br.singletons}")
    typer.echo(
        f"emergence: new={er.detected} dup={er.already_logged} "
        f"under_threshold={er.under_threshold}"
    )


@stellar_app.command("list")
def stellar_list(goal_id: str = typer.Argument(..., help="Goal id")) -> None:
    paths = Paths.load()
    store = DuckStore(paths.db)
    try:
        clusters = store.clusters_for_goal(goal_id)
    finally:
        store.close()
    if not clusters:
        typer.echo("(no clusters — g stellar build 먼저)")
        return
    for c in clusters:
        # cluster id 전체를 출력해야 `g stellar show` 입력으로 바로 쓸 수 있다.
        typer.echo(
            f"{c.id}  center={c.center_node_id[:10] if c.center_node_id else '-':10}  "
            f"gmean={c.gravity_mean:.3f}  stab={c.stability_score:.3f}  "
            f"merkle={(c.merkle_root or '')[:12]}"
        )


@stellar_app.command("show")
def stellar_show(cluster_id: str = typer.Argument(..., help="Cluster id")) -> None:
    paths = Paths.load()
    store = DuckStore(paths.db)
    try:
        c = store.get_cluster(cluster_id)
        if c is None:
            typer.echo(f"cluster not found: {cluster_id}")
            raise typer.Exit(1)
        members = store.cluster_members(cluster_id)
        typer.echo(f"cluster: {c.id}")
        typer.echo(f"goal:    {c.goal_id}")
        typer.echo(f"center:  {c.center_node_id}")
        typer.echo(f"gmean:   {c.gravity_mean:.3f}")
        typer.echo(f"stab:    {c.stability_score:.3f}")
        typer.echo(f"merkle:  {c.merkle_root}")
        typer.echo(f"members ({len(members)}):")
        for node_id, g in members:
            n = store.get_node(node_id)
            tag = "⭐" if node_id == c.center_node_id else " "
            text = (n.text[:60] if n else "?").replace("\n", " ")
            typer.echo(f"  {tag} {node_id[:10]}  g={g:.3f}  {text}")
    finally:
        store.close()


@emerge_app.command("log")
def emerge_log(goal_id: str = typer.Argument(..., help="Goal id")) -> None:
    paths = Paths.load()
    store = DuckStore(paths.db)
    try:
        events = store.emergence_for_goal(goal_id)
    finally:
        store.close()
    if not events:
        typer.echo("(no emergence events — 4연결 노드 없음)")
        return
    for ev in events:
        typer.echo(f"{ev.created_at.isoformat()}")
        typer.echo(f"  trigger: {ev.trigger_node_id[:10]}")
        typer.echo(f"  neighbors ({len(ev.connected_node_ids)}): " + ", ".join(
            nid[:10] for nid in ev.connected_node_ids
        ))
        typer.echo(f"  →  {ev.new_node_candidate}")


@app.command("export")
def export_cmd(
    namespace: str = typer.Option(
        None, "--ns", help="내보낼 namespace. 미지정 시 활성 namespace"
    ),
) -> None:
    """namespace 전체를 JSONL 로 stdout 출력. 서명 필드 포함."""

    import json as _json

    paths = Paths.load()
    if not paths.db.exists():
        typer.echo("먼저 `g init` 을 실행하세요.")
        raise typer.Exit(1)
    store = DuckStore(paths.db)
    try:
        ns = namespace or store.active_namespace()
        for n in store.nodes_by_namespace(ns):
            typer.echo(_json.dumps(n.model_dump(mode="json"), ensure_ascii=False))
    finally:
        store.close()


@app.command("import")
def import_cmd(
    bundle: Path = typer.Argument(..., exists=True, help="JSONL bundle"),
) -> None:
    """export 산출물을 읽어 노드 삽입. content_hash/prev_hash 는 파일 값 그대로 유지."""

    import json as _json

    from gstar.schema import Node

    paths = Paths.load()
    if not paths.db.exists():
        typer.echo("먼저 `g init` 을 실행하세요.")
        raise typer.Exit(1)
    store = DuckStore(paths.db)
    count = 0
    try:
        for line in bundle.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = _json.loads(line)
            n = Node.model_validate(obj)
            store.insert_node(n)
            count += 1
    finally:
        store.close()
    typer.echo(f"imported: {count}")


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(9999, "--port"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """G FastAPI 서버 실행 (미니 PC 상주용)."""

    import uvicorn
    uvicorn.run("gstar.serve:app", host=host, port=port, reload=reload)


@verify_app.command("anchor")
def verify_anchor(
    cluster_id: str = typer.Argument(..., help="Cluster id"),
    snapshot_dir: Path = typer.Option(
        Path("/Volumes/gstar-snapshots"), "--snapshot-dir",
        help="DS218 Hyper Backup 마운트 경로"
    ),
) -> None:
    """DS218 snapshot 앵커 검증 — Merkle root 가 외부에 기록돼 있는지."""
    from gstar.integrity.anchor import DS218SnapshotAnchor

    paths = Paths.load()
    store = DuckStore(paths.db)
    try:
        c = store.get_cluster(cluster_id)
        if c is None or not c.merkle_root:
            typer.echo(f"cluster not found or no merkle_root: {cluster_id}")
            raise typer.Exit(1)
    finally:
        store.close()

    anchor = DS218SnapshotAnchor(snapshot_dir)
    tx_ref = str(anchor.anchors_dir / f"{c.merkle_root}.txt")
    ok = anchor.verify(c.merkle_root, tx_ref)
    typer.echo(f"cluster:  {cluster_id}")
    typer.echo(f"merkle:   {c.merkle_root}")
    typer.echo(f"tx_ref:   {tx_ref}")
    typer.echo(f"anchored: {'OK' if ok else 'MISSING'}")
    if not ok:
        raise typer.Exit(1)


@verify_app.command("anchor-write")
def verify_anchor_write(
    cluster_id: str = typer.Argument(..., help="Cluster id"),
    snapshot_dir: Path = typer.Option(
        Path("/Volumes/gstar-snapshots"), "--snapshot-dir",
    ),
) -> None:
    """Cluster Merkle root 를 DS218 snapshot 에 기록 (최초 앵커링)."""
    from gstar.integrity.anchor import DS218SnapshotAnchor

    paths = Paths.load()
    store = DuckStore(paths.db)
    try:
        c = store.get_cluster(cluster_id)
        if c is None or not c.merkle_root:
            typer.echo("cluster/merkle 없음")
            raise typer.Exit(1)
    finally:
        store.close()

    anchor = DS218SnapshotAnchor(snapshot_dir)
    tx = anchor.anchor(c.merkle_root)
    typer.echo(f"anchored: {tx}")


# ==================== Phase A — entity 서브커맨드 ====================


@entity_app.command("stats")
def entity_stats_cmd(
    project_id: str = typer.Argument(..., help="프로젝트 id"),
    track: str = typer.Option(None, "--track", help="proposal|research|coding|document"),
) -> None:
    """프로젝트별 엔티티 통계."""
    from gstar.entity.linker import stats

    paths = Paths.load()
    if not paths.db.exists():
        typer.echo("먼저 `g init` 을 실행하세요.")
        raise typer.Exit(1)
    store = DuckStore(paths.db)
    try:
        data = stats(store, project_id, track)
    finally:
        store.close()

    typer.echo(f"project:        {project_id}  track={track or 'ALL'}")
    typer.echo(f"total canonical: {data['total_canonical']}")
    typer.echo(f"total aliases:   {data['total_aliases']}")
    if data["by_kind"]:
        typer.echo("by kind:")
        for k in sorted(data["by_kind"].keys()):
            typer.echo(f"  {k:20s}  {data['by_kind'][k]}")


@entity_app.command("list")
def entity_list_cmd(
    project_id: str = typer.Argument(..., help="프로젝트 id"),
    track: str = typer.Option(None, "--track"),
    kind: str = typer.Option(None, "--kind"),
    limit: int = typer.Option(50, "--limit"),
) -> None:
    """프로젝트 내 canonical entity 목록."""
    from gstar.entity.linker import list_canonicals
    from gstar.entity.types import EntityKind

    paths = Paths.load()
    store = DuckStore(paths.db)
    try:
        kind_enum = EntityKind(kind) if kind else None
        ents = list_canonicals(store, project_id, track, kind_enum)[:limit]
    finally:
        store.close()
    if not ents:
        typer.echo("(no entities)")
        return
    for e in ents:
        alias_suffix = f"  aliases={len(e.aliases)}" if e.aliases else ""
        typer.echo(
            f"{e.id[:10]}  [{e.kind.value:12s}]  {e.canonical_name}  "
            f"x{e.mentions}{alias_suffix}"
        )


@entity_app.command("neighbors")
def entity_neighbors_cmd(
    entity_id: str = typer.Argument(..., help="Entity canonical id 또는 node id"),
    kind: str = typer.Option(None, "--kind"),
    relation: list[str] = typer.Option(None, "--rel", help="relation_type 필터 (복수)"),
    max_hops: int = typer.Option(1, "--hops"),
) -> None:
    """엔티티의 k-hop 이웃."""
    from gstar.entity.graph import neighbors

    paths = Paths.load()
    store = DuckStore(paths.db)
    try:
        hits = neighbors(
            entity_id,
            store,
            kind=kind,
            relation_types=relation or None,
            max_hops=max_hops,
        )
    finally:
        store.close()
    if not hits:
        typer.echo("(no neighbors)")
        return
    for h in hits:
        txt = h.node_text[:50].replace("\n", " ")
        typer.echo(
            f"d={h.distance} [{h.node_kind[:5]:5s}] "
            f"{h.edge.relation_type.value:15s} w={h.edge.weight:.1f}  "
            f"{h.node_id[:10]}  {txt}"
        )


@entity_app.command("chain")
def entity_chain_cmd(
    start: str = typer.Argument(..., help="시작 엔티티 id"),
    target_kind: str = typer.Argument(..., help="목표 kind"),
    max_depth: int = typer.Option(4, "--depth"),
    relation: list[str] = typer.Option(None, "--rel"),
) -> None:
    """시작 → target_kind 로의 경로 탐색 (BFS)."""
    from gstar.entity.graph import trace_chain

    paths = Paths.load()
    store = DuckStore(paths.db)
    try:
        pths = trace_chain(
            start,
            target_kind,
            store,
            max_depth=max_depth,
            relation_types=relation or None,
        )
    finally:
        store.close()
    if not pths:
        typer.echo("(no path found)")
        return
    for i, p in enumerate(pths[:10]):
        segs = [start[:8]]
        for e in p.edges:
            other = e.dst if e.src in segs[-1:] + [p.nodes[len(segs) - 1]] else e.src
            segs.append(f"--{e.relation_type.value}-->{other[:8]}")
        typer.echo(f"#{i+1} ({len(p.edges)} hops)")
        for nid in p.nodes:
            typer.echo(f"  - {nid[:10]}")
        for e in p.edges:
            typer.echo(f"    [{e.relation_type.value}] {e.src[:8]} -> {e.dst[:8]}")


@entity_app.command("migrate")
def entity_migrate_cmd(
    project_id: str = typer.Argument(..., help="프로젝트 id (신규 canonical 작성 범위)"),
    track: str = typer.Option("proposal", "--track"),
    namespace: str = typer.Option(None, "--ns", help="대상 namespace"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """기존 node(kind='entity') 를 canonical 로 마이그레이션.

    Phase A 도입 이전 ingest 된 entity 노드들을 새 entity_canonical 테이블로 옮긴다.
    """
    from gstar.entity.classifier import classify
    from gstar.entity.linker import link

    paths = Paths.load()
    store = DuckStore(paths.db)
    try:
        ns = namespace or store.active_namespace()
        ents = store.list_nodes(kind="entity", namespace=ns)
        typer.echo(f"대상 entity 노드: {len(ents)} ({ns})")
        migrated = 0
        for e in ents:
            surface = e.text
            if not surface or not surface.strip():
                continue
            kind = classify(surface, track=track)
            if dry_run:
                typer.echo(f"  [DRY] {e.id[:10]} {surface} → {kind.value}")
                continue
            try:
                link(surface, project_id, track, store, kind_hint=kind)
                migrated += 1
            except ValueError:
                continue
        if dry_run:
            typer.echo("(dry run — no changes)")
        else:
            typer.echo(f"마이그레이션 완료: {migrated}")
    finally:
        store.close()


if __name__ == "__main__":
    app()

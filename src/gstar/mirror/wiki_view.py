"""Sprint B — G 의 entity/community/source 를 markdown 파생 뷰로 렌더.

원칙 (architecture v4.1 §4):
- Wiki 는 **독자 상태 없음**. G 가 Source-of-Truth, wiki 는 언제든 regenerate.
- 인간 편집 금지. 편집은 `_inbox/*.md` 에만 (Sprint C 범위).
- frontmatter 에 `g_entity_id` / `g_node_ids` provenance anchor 필수 (원칙 7).
- Obsidian graph view 는 md 내 [[링크]] 만으로 자동 구성.

derived_view 레코드: (g_node_id, view='wiki', external_id=파일 상대경로).

env:
  GSTAR_WIKI_OUT  (기본 /nas/workspace/wiki  — 맥북 Obsidian vault 경로)
  GSTAR_WIKI_MAX_FACTS_PER_ENTITY  (entity 페이지당 fact 인용 상한, 기본 20)
  GSTAR_WIKI_GIT_PUSH  (off | on. on 이면 emit 끝에 Gitea push. 초기 `git init`
                        + `remote add origin <token URL>` 은 1회 수동.)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from gstar.storage.duckdb_store import DuckStore


_SAFE_NAME = re.compile(r"[^\w\-가-힣()·,. ]", re.UNICODE)


def _slug(name: str, maxlen: int = 80) -> str:
    """filename-safe slug. 한글·영문·일부 기호 유지. 길이 제한."""
    s = _SAFE_NAME.sub("_", name.strip())
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_{2,}", "_", s)
    return s[:maxlen] or "unnamed"


def _out_dir() -> Path:
    return Path(os.environ.get("GSTAR_WIKI_OUT", "/nas/workspace/wiki"))


def _max_facts_per_entity() -> int:
    try:
        return int(os.environ.get("GSTAR_WIKI_MAX_FACTS_PER_ENTITY", "20"))
    except ValueError:
        return 20


@dataclass
class WikiResult:
    entities: int = 0
    topics: int = 0
    sources: int = 0
    index_written: bool = False
    git_pushed: bool = False
    errors: list[str] = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def _git_sync(out_dir: Path, result: WikiResult) -> None:
    """GSTAR_WIKI_GIT_PUSH=on 일 때 wiki 디렉터리의 변경을 Gitea 로 push.
    초기 설정(`git init` + `remote add origin`) 은 사용자가 1회 수동. 이후는 자동.
    변경 없으면 조용히 skip. 컨테이너 root uid 와 CIFS uid 1000 소유권이
    다르므로 safe.directory 를 인라인으로 지정 (container 전역 config 오염 방지)."""
    if os.environ.get("GSTAR_WIKI_GIT_PUSH", "off").lower() not in {"on", "1", "true"}:
        return
    base = ["git", "-c", f"safe.directory={out_dir}"]
    try:
        st = subprocess.run(
            base + ["status", "--porcelain"],
            cwd=str(out_dir), capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:
        result.errors.append(f"git status: {type(exc).__name__}: {exc}")
        return
    if st.returncode != 0:
        result.errors.append(f"git status rc={st.returncode}: {st.stderr.strip()[:200]}")
        return
    if not st.stdout.strip():
        return  # 변경 없음 — noise 감소 위해 empty commit 생성 안 함
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    msg = (
        f"wiki mirror {ts} "
        f"E={result.entities} T={result.topics} S={result.sources}"
    )
    for cmd in (
        base + ["add", "-A"],
        base + ["-c", "user.email=wiki-mirror@g", "-c", "user.name=wiki-mirror",
                "commit", "-m", msg],
        base + ["push", "origin", "HEAD"],
    ):
        try:
            r = subprocess.run(cmd, cwd=str(out_dir), capture_output=True,
                               text=True, timeout=180)
        except Exception as exc:
            result.errors.append(f"{cmd[3]}: {type(exc).__name__}: {exc}")
            return
        if r.returncode != 0:
            result.errors.append(
                f"git {cmd[3]} rc={r.returncode}: {r.stderr.strip()[:200]}"
            )
            return
    result.git_pushed = True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _frontmatter(kind: str, title: str, extra: dict) -> str:
    lines = [
        "---",
        f"view: wiki",
        f"type: {kind}",
        f"title: {title}",
        f"generated_at: {_now_iso()}",
        f"mirror_version: 1",
    ]
    for k, v in extra.items():
        if isinstance(v, list):
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        elif isinstance(v, (int, float, bool)):
            lines.append(f"{k}: {v}")
        else:
            lines.append(f"{k}: {json.dumps(str(v), ensure_ascii=False)}")
    lines.append("---\n")
    return "\n".join(lines)


def _fetch_entities(store: DuckStore) -> list[dict]:
    """entity_canonical 전체 + 상위 community/mentions."""
    rows = store._read_conn().execute(
        "SELECT id, project_id, track, canonical_name, kind, mentions, node_id, community_id "
        "FROM entity_canonical ORDER BY mentions DESC"
    ).fetchall()
    return [
        {
            "id": r[0], "project_id": r[1], "track": r[2],
            "canonical_name": r[3], "kind": r[4], "mentions": int(r[5] or 0),
            "node_id": r[6], "community_id": r[7],
        }
        for r in rows
    ]


def _fetch_communities(store: DuckStore) -> list[dict]:
    try:
        rows = store._read_conn().execute(
            "SELECT id, project_id, algorithm, members_json, size, label, created_at "
            "FROM community_canonical ORDER BY size DESC"
        ).fetchall()
    except Exception:
        return []
    out: list[dict] = []
    for r in rows:
        try:
            members = json.loads(r[3] or "[]")
        except Exception:
            members = []
        out.append({
            "id": r[0], "project_id": r[1], "algorithm": r[2],
            "members": members, "size": int(r[4] or 0), "label": r[5],
            "created_at": str(r[6]),
        })
    return out


def _skip_ns(name: str) -> bool:
    """벤치·임시 네임스페이스 (`_bench_`, `_tmp_` prefix) 는 wiki 렌더 제외.
    데이터는 DB 에 남아있지만 Obsidian graph 에 노이즈로 들어가지 않도록."""
    return name.startswith("_bench_") or name.startswith("_tmp_")


def _fetch_source_summary(store: DuckStore) -> list[dict]:
    rows = store._read_conn().execute(
        "SELECT source_namespace, kind, COUNT(*) FROM node GROUP BY source_namespace, kind"
    ).fetchall()
    ns_stats: dict[str, dict[str, int]] = {}
    for ns, kind, cnt in rows:
        ns = ns or "_unknown"
        if _skip_ns(ns):
            continue
        ns_stats.setdefault(ns, {})[kind or "_unknown"] = int(cnt)
    return [{"namespace": ns, "stats": s} for ns, s in sorted(ns_stats.items())]


def _resolve_entity_node_ids(store: DuckStore, canonical_name: str, fallback: str | None) -> list[str]:
    """entity_canonical.node_id 가 NULL 인 경우가 다수 (linker 가 NULL 로 INSERT 하고
    나중에 업데이트되지 않는 경로). canonical_name 으로 node(kind='entity').text
    매칭하여 보강. 여러 namespace 에서 같은 이름 entity 가 있을 수 있으니 list 로."""
    ids: list[str] = []
    if fallback:
        ids.append(fallback)
    if not canonical_name:
        return ids
    try:
        rows = store._read_conn().execute(
            "SELECT id FROM node WHERE kind = 'entity' AND text = ?",
            [canonical_name],
        ).fetchall()
    except Exception:
        rows = []
    for r in rows:
        nid = r[0]
        if nid and nid not in ids:
            ids.append(nid)
    return ids


def _entity_facts(store: DuckStore, node_ids: list[str], limit: int) -> list[dict]:
    """entity node 들에서 evidence_of edge 로 연결된 fact 상위 N개 (합집합)."""
    if not node_ids:
        return []
    placeholders = ",".join(["?"] * len(node_ids))
    try:
        rows = store._read_conn().execute(
            f"SELECT e.dst, n.text FROM edge e "
            "JOIN node n ON n.id = e.dst "
            f"WHERE e.src IN ({placeholders}) AND e.kind = 'evidence_of' "
            "AND n.kind = 'fact' LIMIT ?",
            list(node_ids) + [limit],
        ).fetchall()
    except Exception:
        return []
    return [{"fact_id": r[0], "text": r[1] or ""} for r in rows]


def _related_entities(store: DuckStore, node_ids: list[str], limit: int = 10) -> list[str]:
    """co_occurs edge 로 연결된 상위 entity (canonical_name)."""
    if not node_ids:
        return []
    placeholders = ",".join(["?"] * len(node_ids))
    # 2-step: 이웃 node id 목록 → canonical_name lookup (canonical 측의 node_id
    # 가 NULL 이면 node.text 로 최후 폴백).
    try:
        rows = store._read_conn().execute(
            f"SELECT CASE WHEN src IN ({placeholders}) THEN dst ELSE src END AS other_id, weight "
            f"FROM edge WHERE (src IN ({placeholders}) OR dst IN ({placeholders})) "
            "AND kind = 'co_occurs' ORDER BY weight DESC LIMIT ?",
            list(node_ids) * 3 + [limit * 4],
        ).fetchall()
    except Exception:
        return []
    seen: set[str] = set()
    out: list[str] = []
    own = set(node_ids)
    for other_id, _w in rows:
        if other_id in own or other_id in seen:
            continue
        seen.add(other_id)
        try:
            r2 = store._read_conn().execute(
                "SELECT COALESCE(ec.canonical_name, n.text) FROM node n "
                "LEFT JOIN entity_canonical ec ON ec.node_id = n.id "
                "WHERE n.id = ?",
                [other_id],
            ).fetchone()
        except Exception:
            continue
        if r2 and r2[0]:
            out.append(r2[0])
            if len(out) >= limit:
                break
    return out


def _render_entity(store: DuckStore, ent: dict, out_dir: Path) -> Path | None:
    name = ent["canonical_name"]
    if not name:
        return None
    fname = _slug(name) + ".md"
    path = out_dir / "entities" / fname
    path.parent.mkdir(parents=True, exist_ok=True)
    node_ids = _resolve_entity_node_ids(store, name, ent.get("node_id"))
    facts = _entity_facts(store, node_ids, _max_facts_per_entity())
    related = _related_entities(store, node_ids)
    fact_ids = [f["fact_id"] for f in facts]
    fm = _frontmatter("entity", name, {
        "g_entity_id": ent["id"],
        "g_node_ids": node_ids,
        "g_fact_sources": fact_ids,
        "kind": ent["kind"],
        "mentions": ent["mentions"],
        "project_id": ent["project_id"],
        "track": ent["track"],
        "community_id": ent["community_id"] or "",
    })
    body: list[str] = [fm, f"# {name}\n"]
    body.append(f"**kind**: `{ent['kind']}` · **mentions**: {ent['mentions']} · **project**: {ent['project_id']} · **track**: {ent['track']}\n")
    if ent["community_id"]:
        body.append(f"**topic**: [[topics/{_slug(ent['community_id'])}]]\n")
    if related:
        body.append("## 관련 entity\n")
        body.extend(f"- [[entities/{_slug(r)}]]" for r in related)
        body.append("")
    if facts:
        body.append("## 주요 근거 fact\n")
        for f in facts:
            txt = (f["text"] or "").strip().replace("\n", " ")[:300]
            body.append(f"- `{f['fact_id']}` {txt}")
        body.append("")
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def _render_topic(comm: dict, entity_by_id: dict[str, dict], out_dir: Path) -> Path | None:
    cid = comm["id"]
    label = comm.get("label") or cid
    fname = _slug(cid) + ".md"
    path = out_dir / "topics" / fname
    path.parent.mkdir(parents=True, exist_ok=True)
    member_names = []
    for mid in comm.get("members") or []:
        e = entity_by_id.get(mid)
        if e and e.get("canonical_name"):
            member_names.append(e["canonical_name"])
    fm = _frontmatter("topic", f"Topic: {label}", {
        "community_id": cid,
        "algorithm": comm.get("algorithm"),
        "size": comm.get("size", 0),
        "project_id": comm.get("project_id", ""),
    })
    body: list[str] = [fm, f"# Topic: {label}\n"]
    body.append(f"**algorithm**: `{comm.get('algorithm')}` · **size**: {comm.get('size')} · **project**: {comm.get('project_id')}\n")
    if member_names:
        body.append("## 소속 entity\n")
        body.extend(f"- [[entities/{_slug(n)}]]" for n in member_names)
        body.append("")
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def _render_source(store: DuckStore, src: dict, out_dir: Path) -> Path | None:
    ns = src["namespace"]
    fname = _slug(ns) + ".md"
    path = out_dir / "sources" / fname
    path.parent.mkdir(parents=True, exist_ok=True)
    stats = src["stats"]
    total = sum(stats.values())
    fm = _frontmatter("source", f"Source: {ns}", {
        "namespace": ns,
        "total_nodes": total,
    })
    body: list[str] = [fm, f"# Source: {ns}\n"]
    body.append(f"**total nodes**: {total}\n")
    body.append("## 노드 분포\n")
    for kind, cnt in sorted(stats.items(), key=lambda x: -x[1]):
        body.append(f"- `{kind}`: {cnt}")
    body.append("")
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def _render_index(entities: int, topics: int, sources: int, out_dir: Path) -> Path:
    path = out_dir / "index.md"
    fm = _frontmatter("index", "Wiki 인덱스", {
        "entities": entities,
        "topics": topics,
        "sources": sources,
    })
    body = [
        fm,
        "# Wiki 인덱스\n",
        f"- 생성: {_now_iso()}",
        f"- entity {entities}개 / topic {topics}개 / source {sources}개",
        "",
        "## 디렉터리\n",
        "- `entities/` — G entity_canonical 1 row = 1 페이지",
        "- `topics/` — Louvain community 1 group = 1 페이지",
        "- `sources/` — namespace 별 노드 통계",
        "",
        "> 이 vault 는 **G 로부터 자동 렌더링**. 직접 편집 금지 — `_inbox/` 에만 작성 (Sprint C).",
        "",
    ]
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def _record_derived(store: DuckStore, g_node_id: str | None, rel_path: str) -> None:
    if not g_node_id:
        return
    try:
        store.conn.execute(
            "INSERT INTO derived_view (g_node_id, view, external_id, collection, emitted_at, status) "
            "VALUES (?, 'wiki', ?, NULL, ?, 'ok') "
            "ON CONFLICT (g_node_id, view, external_id) DO UPDATE SET "
            "emitted_at = excluded.emitted_at, status = 'ok', error = NULL",
            [g_node_id, rel_path, datetime.now(timezone.utc)],
        )
    except Exception as exc:
        print(f"[wiki_view] derived_view record failed for {g_node_id}: {exc}", flush=True)


def emit_all(store: DuckStore, out_dir: Path | None = None) -> WikiResult:
    """전체 wiki regenerate. 규모 작아서 매번 full rebuild 가 단순·안전."""
    out = Path(out_dir) if out_dir else _out_dir()
    out.mkdir(parents=True, exist_ok=True)
    result = WikiResult()

    entities = _fetch_entities(store)
    communities = _fetch_communities(store)
    sources = _fetch_source_summary(store)

    entity_by_id = {e["id"]: e for e in entities}

    for ent in entities:
        try:
            p = _render_entity(store, ent, out)
            if p is None:
                continue
            rel = str(p.relative_to(out))
            # derived_view 는 anchor 가 1개여야 하므로 fallback 체인 첫 값 사용
            anchor = ent["node_id"] or (
                _resolve_entity_node_ids(store, ent["canonical_name"], None)[:1] or [None]
            )[0]
            _record_derived(store, anchor, rel)
            result.entities += 1
        except Exception as exc:
            result.errors.append(f"entity {ent.get('canonical_name')}: {exc}")

    for comm in communities:
        try:
            p = _render_topic(comm, entity_by_id, out)
            if p is None:
                continue
            result.topics += 1
        except Exception as exc:
            result.errors.append(f"topic {comm.get('id')}: {exc}")

    for src in sources:
        try:
            _render_source(store, src, out)
            result.sources += 1
        except Exception as exc:
            result.errors.append(f"source {src.get('namespace')}: {exc}")

    _render_index(result.entities, result.topics, result.sources, out)
    result.index_written = True

    _git_sync(out, result)
    return result

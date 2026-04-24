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
    citations: int = 0
    digests: int = 0
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
        f"E={result.entities} T={result.topics} S={result.sources} "
        f"C={result.citations} D={result.digests}"
    )
    # 2026-04-24 — push 전에 pull --rebase 로 맥북 측 push (예: Obsidian Git) 와
    # divergence 해결. mirror 는 idempotent 파생 뷰라 conflict 는 mirror 출력이
    # 최신. 하지만 인간이 `_inbox/` 수정한 게 있다면 그것만 tracked 되어야 함.
    # pull --rebase -X theirs 로 "맥북 쪽 _inbox 변경 존중, mirror 재생성은 local 최신" 구도.
    try:
        subprocess.run(
            base + ["pull", "--rebase", "-X", "theirs", "origin", "HEAD"],
            cwd=str(out_dir), capture_output=True, text=True, timeout=60,
        )
    except Exception as exc:
        result.errors.append(f"git pull: {type(exc).__name__}: {exc}")
        return

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
    # 콜론·특수문자 포함 문자열은 YAML 파서가 mapping 으로 오인하므로 전부
    # JSON double-quote 로 감싼다 (Quartz/Obsidian 모두 YAML 정합성 기대).
    lines = [
        "---",
        "view: wiki",
        f"type: {json.dumps(kind, ensure_ascii=False)}",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"generated_at: {_now_iso()}",
        "mirror_version: 1",
    ]
    for k, v in extra.items():
        if isinstance(v, list):
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
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
    seen_name: set[str] = set()
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
            nm = r2[0]
            # canonical_name level dedup — 여러 node_id 가 같은 entity 로
            # 정규화된 경우 동일 이름 중복 방지 (예: `AI` 가 3번 찍히는 버그).
            if nm in seen_name:
                continue
            seen_name.add(nm)
            out.append(nm)
            if len(out) >= limit:
                break
    return out


def _expand_2hop_entities(
    store: DuckStore,
    root_node_ids: list[str],
    one_hop_names: list[str],
    canonical_names: set[str] | None = None,
    limit: int = 10,
) -> list[str]:
    """1-hop 이웃을 거쳐 도달 가능한 2-hop entity 이름 (root·1-hop 제외).

    카파시 2nd-brain 스타일의 '개념 포함' 구조 — 5 가 1,2,3 과 6 을 포함하고
    6 이 7,8,9 와 연결되면, 5 의 본문에도 7,8,9 가 등장해 Obsidian graph 에
    더 두꺼운 연결망이 형성된다. canonical_names 로 dead wiki link 방지."""
    if not root_node_ids or not one_hop_names:
        return []
    neighbor_nids: list[str] = []
    seen_nid: set[str] = set()
    for name in one_hop_names:
        for nid in _resolve_entity_node_ids(store, name, None):
            if nid and nid not in seen_nid:
                seen_nid.add(nid)
                neighbor_nids.append(nid)
    if not neighbor_nids:
        return []
    excluded: set[str] = set(root_node_ids) | seen_nid
    placeholders = ",".join(["?"] * len(neighbor_nids))
    try:
        rows = store._read_conn().execute(
            f"SELECT CASE WHEN src IN ({placeholders}) THEN dst ELSE src END AS other_id, weight "
            f"FROM edge WHERE (src IN ({placeholders}) OR dst IN ({placeholders})) "
            "AND kind = 'co_occurs' ORDER BY weight DESC LIMIT ?",
            list(neighbor_nids) * 3 + [limit * 10],
        ).fetchall()
    except Exception:
        return []
    out: list[str] = []
    seen_name: set[str] = set()
    for other_id, _w in rows:
        if other_id in excluded or other_id in seen_name:
            continue
        seen_name.add(other_id)
        try:
            r2 = store._read_conn().execute(
                "SELECT COALESCE(ec.canonical_name, n.text) FROM node n "
                "LEFT JOIN entity_canonical ec ON ec.node_id = n.id "
                "WHERE n.id = ?",
                [other_id],
            ).fetchone()
        except Exception:
            continue
        if not r2 or not r2[0]:
            continue
        n = r2[0]
        if canonical_names is not None and n not in canonical_names:
            continue
        if n in out:
            continue
        out.append(n)
        if len(out) >= limit:
            break
    return out


def _entity_link(name: str) -> str:
    """Obsidian wikilink — path form 으로 folder collision 방지 + display 이름 유지."""
    return f"[[entities/{_slug(name)}|{name}]]"


def _render_entity(
    store: DuckStore,
    ent: dict,
    out_dir: Path,
    canonical_names: set[str] | None = None,
) -> Path | None:
    """Entity 1 row → narrative md 문서.

    카파시 "markdown wiki = 2nd brain" 원칙: 노드 자체가 읽을 수 있는 문서가
    되어야 한다. 연결된 1-hop·2-hop entity 이름을 본문에 embed 하여 (1) 인간이
    읽을 때 문맥이 잡히고 (2) Obsidian graph 의 [[link]] edge 수를 확보해
    orphan 문제를 해소한다."""
    name = ent["canonical_name"]
    if not name:
        return None
    fname = _slug(name) + ".md"
    path = out_dir / "entities" / fname
    path.parent.mkdir(parents=True, exist_ok=True)
    node_ids = _resolve_entity_node_ids(store, name, ent.get("node_id"))
    facts = _entity_facts(store, node_ids, _max_facts_per_entity())
    one_hop_raw = _related_entities(store, node_ids, limit=15)
    if canonical_names is not None:
        one_hop = [n for n in one_hop_raw if n in canonical_names and n != name]
    else:
        one_hop = [n for n in one_hop_raw if n != name]
    two_hop = _expand_2hop_entities(
        store, node_ids, one_hop, canonical_names=canonical_names, limit=10,
    )
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
        "one_hop_count": len(one_hop),
        "two_hop_count": len(two_hop),
    })

    topic_clause = ""
    if ent["community_id"]:
        topic_clause = (
            f", 주제 클러스터 [[topics/{_slug(ent['community_id'])}|"
            f"topic {ent['community_id']}]] 에 속한다"
        )
    intro: list[str] = [
        f"**{name}** 은(는) `{ent['kind']}` 유형의 엔티티로, "
        f"`{ent['project_id']}` · `{ent['track']}` 맥락에서 "
        f"{ent['mentions']}회 언급되었다{topic_clause}."
    ]
    if one_hop:
        links = ", ".join(_entity_link(n) for n in one_hop[:8])
        intro.append(
            f"직접 co-occurrence 로 연결된 1차 이웃에는 {links} 등이 있어 "
            f"**{name}** 과 함께 하나의 사건·맥락·주제를 구성한다."
        )
    if two_hop:
        far = ", ".join(_entity_link(n) for n in two_hop[:8])
        intro.append(
            f"이 1차 이웃들을 매개로 도달 가능한 2차 확장 맥락에는 {far} 등이 있으며, "
            f"**{name}** 은(는) 이 더 큰 개념 클러스터의 허브 역할을 한다."
        )
    if not one_hop and not two_hop:
        intro.append(
            f"현재 co-occurrence edge 로 확인되는 직접 이웃은 없으며, "
            f"아래 근거 문장이 **{name}** 의 맥락을 제공한다."
        )

    body: list[str] = [fm, f"# {name}\n", " ".join(intro), ""]
    body.append(
        f"**kind**: `{ent['kind']}` · **mentions**: {ent['mentions']} · "
        f"**project**: {ent['project_id']} · **track**: {ent['track']}"
    )
    if ent["community_id"]:
        body.append(f"**topic**: [[topics/{_slug(ent['community_id'])}]]")
    body.append("")

    if one_hop:
        body.append("## 직접 연결 엔티티 (1-hop co-occurrence)\n")
        body.extend(f"- {_entity_link(n)}" for n in one_hop)
        body.append("")

    if two_hop:
        body.append("## 확장 맥락 (2-hop 경유)\n")
        body.append(
            f"**{name}** 에 직접 연결되진 않았으나, 위 1차 이웃들을 거쳐 도달 가능한 개념 — "
            f"카파시 2nd-brain 의 '포함 관계' 에 해당 (5 가 6 을 포함하면 6 의 이웃 7·8·9 도 5 의 맥락):\n"
        )
        body.extend(f"- {_entity_link(n)}" for n in two_hop)
        body.append("")

    if facts:
        body.append("## 근거 문장 (evidence facts)\n")
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


def _fetch_citations(store: DuckStore) -> list[dict]:
    """citation_artifact 전체 (최신순)."""
    try:
        rows = store._read_conn().execute(
            """SELECT id, project, stage, version, title, clearance_token,
                      consistency, file_path, line_count,
                      wip_files_json, decisions_json, tags_json,
                      content, content_hash, source, created_at
               FROM citation_artifact ORDER BY created_at DESC"""
        ).fetchall()
    except Exception:
        return []
    out: list[dict] = []
    for r in rows:
        try:
            wip = json.loads(r[9] or "[]")
            dec = json.loads(r[10] or "[]")
            tags = json.loads(r[11] or "[]")
        except Exception:
            wip, dec, tags = [], [], []
        out.append({
            "id": r[0], "project": r[1], "stage": r[2], "version": r[3] or "",
            "title": r[4] or "", "clearance_token": r[5] or "",
            "consistency": r[6], "file_path": r[7] or "", "line_count": r[8],
            "wip_files": wip, "decisions": dec, "tags": tags,
            "content": r[12] or "", "content_hash": r[13] or "",
            "source": r[14] or "", "created_at": str(r[15] or ""),
        })
    return out


def _render_citation(cit: dict, out_dir: Path) -> Path | None:
    """citation_artifact row → wiki/citations/<project>/<stage>-<version>.md."""
    project = cit["project"]
    stage = cit["stage"]
    version = cit["version"] or "v1"
    if not project or not stage:
        return None
    fname = f"{_slug(stage)}-{_slug(version)}.md"
    path = out_dir / "citations" / _slug(project) / fname
    path.parent.mkdir(parents=True, exist_ok=True)

    title = cit["title"] or f"{project} · {stage} · {version}"
    fm = _frontmatter("citation", title, {
        "source_layer": "g",
        "citation_id": cit["id"],
        "project": project,
        "stage": stage,
        "version": version,
        "content_hash": cit["content_hash"],
        "clearance_token": cit["clearance_token"],
        "consistency": cit["consistency"] if cit["consistency"] is not None else 0,
        "file_path": cit["file_path"],
        "line_count": cit["line_count"] if cit["line_count"] is not None else 0,
        "tags": cit["tags"],
        "decisions": cit["decisions"],
        "wip_files": cit["wip_files"],
    })
    body: list[str] = [fm, f"# {title}\n"]
    meta_line = (
        f"**project**: `{project}` · **stage**: `{stage}` · **version**: `{version}`"
    )
    if cit["consistency"] is not None:
        meta_line += f" · **consistency**: {cit['consistency']}"
    if cit["line_count"]:
        meta_line += f" · **lines**: {cit['line_count']}"
    if cit["clearance_token"]:
        meta_line += f" · **clearance**: `{cit['clearance_token']}`"
    body.append(meta_line + "\n")

    if cit["decisions"]:
        body.append("## 주요 결정\n")
        body.extend(f"- {d}" for d in cit["decisions"])
        body.append("")
    if cit["wip_files"]:
        body.append("## WIP 파일 원본\n")
        body.extend(f"- `{w}`" for w in cit["wip_files"])
        body.append("")
    if cit["content"]:
        body.append("## 본문\n")
        body.append(cit["content"])
        body.append("")
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def _fetch_recent_notes(store: DuckStore, since_ts: datetime, until_ts: datetime) -> list[dict]:
    """기간 내 `personal_notes` namespace + source='skill' 의 fact/entity 수집."""
    try:
        rows = store._read_conn().execute(
            """SELECT id, text, source_namespace, kind, created_at
               FROM node
               WHERE created_at >= ? AND created_at < ?
                 AND (source_namespace = 'personal_notes' OR kind = 'goal')
               ORDER BY created_at DESC
               LIMIT 500""",
            [since_ts, until_ts],
        ).fetchall()
    except Exception:
        return []
    return [
        {"id": r[0], "text": r[1] or "", "namespace": r[2] or "", "kind": r[3] or "",
         "created_at": str(r[4])}
        for r in rows
    ]


def _fetch_citations_between(store: DuckStore, since_ts: datetime, until_ts: datetime) -> list[dict]:
    """기간 내 citation_artifact."""
    try:
        rows = store._read_conn().execute(
            """SELECT id, project, stage, version, title, consistency, line_count,
                      decisions_json, tags_json
               FROM citation_artifact
               WHERE created_at >= ? AND created_at < ?
               ORDER BY created_at ASC""",
            [since_ts, until_ts],
        ).fetchall()
    except Exception:
        return []
    out: list[dict] = []
    for r in rows:
        try:
            dec = json.loads(r[7] or "[]")
            tags = json.loads(r[8] or "[]")
        except Exception:
            dec, tags = [], []
        out.append({
            "id": r[0], "project": r[1], "stage": r[2], "version": r[3] or "",
            "title": r[4] or "", "consistency": r[5], "line_count": r[6],
            "decisions": dec, "tags": tags,
        })
    return out


def _call_projector_summarize(prompt: str, *, timeout_s: float = 180.0) -> str:
    """맥북 skill 과 동일 경로 — 4090 llama.cpp Qwen 14B 로 요약 생성.
    실패 시 폴백 프롬프트를 그대로 반환 (사용자가 수동 요약 가능)."""
    try:
        from gstar.selector.gemma_client import OllamaChatClient, projection_host, _default_api_schema
    except Exception:
        return ""
    try:
        model = os.environ.get("PROJECTOR_MODEL", "qwen2.5-14b")
        client = OllamaChatClient(
            host=projection_host(),
            model=model,
            timeout_s=timeout_s,
            num_predict=1500,
            temperature=0.3,
            api_schema=_default_api_schema(),
        )
        return client.judge(
            system="너는 주간/월간 지식 digest 요약가다. 한국어로 간결·정확하게.",
            prompt=prompt,
        ).strip()
    except Exception as exc:
        return f"(LLM 요약 실패: {type(exc).__name__}: {exc})"


def _build_digest_prompt(period_label: str, since: str, until: str,
                         citations: list[dict], notes: list[dict]) -> str:
    """digest 용 Qwen 프롬프트 구성. citation 메타 + note 본문 상위 N.

    prompt 총 크기 상한 ~6000자 (슬롯 8192 tokens 대략 여유분). 넘치면 note
    샘플링 축소. citation 없고 note 만 많은 경우는 'note 합성' 맥락으로 처리.
    """
    lines = [f"[기간] {period_label} ({since} ~ {until})"]

    if citations:
        lines.append(f"[산출물 citation {len(citations)}건]")
        for c in citations[:30]:
            d = "; ".join(c.get("decisions", [])[:3])
            lines.append(
                f"- [{c['project']}/{c['stage']} {c['version']}] "
                f"consistency={c['consistency']} lines={c['line_count']} · {d}"
            )
    else:
        lines.append("[산출물 citation 없음 — note 만으로 주요 활동 요약]")

    # notes sampling: 너무 많으면 앞부분만 (최근순 상위)
    note_cap = 20 if citations else 40
    if notes:
        lines.append(f"\n[요약 note {min(len(notes), note_cap)}건 / 총 {len(notes)}건 중]")
        for n in notes[:note_cap]:
            lines.append(f"- [{n['namespace']}] {(n['text'] or '')[:180]}")

    lines.append(
        "\n작업: 위 원본에서 다음 섹션으로 한국어 digest 작성. markdown 규약:\n"
        "## 📌 핵심 결정 (최대 7개 bullet, 출처 표기 [citation:project/stage])\n"
        "## 📄 완료 산출물 (citation 별 한 줄, `[[citations/<proj>/<stage>-<v>]]`)\n"
        "## 🔑 주요 테마 (3~5개, 명사구)\n"
        "## 📈 양적 변화 (숫자 있으면 그대로)\n"
        "각 섹션 150자 이내 간결. 과장·추측 금지. 원본 없는 내용 생성 금지."
    )
    prompt = "\n".join(lines)
    # 최종 안전망: 6000자 cap
    if len(prompt) > 6000:
        prompt = prompt[:6000] + "\n\n[prompt truncated at 6000]"
    return prompt


def _render_digest(period: str, since: datetime, until: datetime,
                   citations: list[dict], notes: list[dict],
                   projects: list[str], out_dir: Path) -> Path | None:
    """week|month digest 1개 파일 렌더."""
    if not citations and not notes:
        return None
    since_s = since.strftime("%Y-%m-%d")
    until_s = until.strftime("%Y-%m-%d")

    if period == "week":
        iso_year, iso_week, _ = since.isocalendar()
        stem = f"{iso_year}-w{iso_week:02d}"
        title = f"Week {iso_week} ({since_s} ~ {until_s})"
    else:
        stem = since.strftime("%Y-%m")
        title = f"{since.strftime('%Y-%m')} ({since_s} ~ {until_s})"

    fname = f"{stem}.md"
    path = out_dir / "digests" / fname
    path.parent.mkdir(parents=True, exist_ok=True)

    # LLM 요약 본문
    prompt = _build_digest_prompt(title, since_s, until_s, citations, notes)
    llm_body = _call_projector_summarize(prompt) or "(요약 미생성)"

    source_ids = [c["id"] for c in citations] + [n["id"] for n in notes]
    fm = _frontmatter("digest", title, {
        "source_layer": "g",
        "period": period,
        "period_start": since_s,
        "period_end": until_s,
        "projects": sorted(set(projects)) if projects else [],
        "citations_count": len(citations),
        "notes_count": len(notes),
        "source_ids": source_ids[:200],  # 너무 크면 잘림
        "generated_by": os.environ.get("PROJECTOR_MODEL", "qwen2.5-14b"),
    })
    body: list[str] = [fm, f"# {title}\n"]
    body.append(
        f"**period**: `{period}` · **citations**: {len(citations)} · **notes**: {len(notes)}\n"
    )
    body.append(llm_body)
    body.append("")
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def emit_citations(store: DuckStore, out_dir: Path | None = None) -> WikiResult:
    """citation_artifact 만 별도 emit (헤비한 entity 재생성 없이)."""
    out = Path(out_dir) if out_dir else _out_dir()
    out.mkdir(parents=True, exist_ok=True)
    result = WikiResult()
    for cit in _fetch_citations(store):
        try:
            p = _render_citation(cit, out)
            if p is None:
                continue
            result.citations += 1
        except Exception as exc:
            result.errors.append(f"citation {cit.get('project')}/{cit.get('stage')}: {exc}")
    return result


def _week_bounds(ref: datetime, offset_weeks: int = -1) -> tuple[datetime, datetime]:
    """기준 날짜 기반 ISO week 범위. offset=-1 이면 지난 주."""
    from datetime import timedelta
    # ISO weekday: Mon=1, Sun=7. 이번 주 월요일 00:00 기준으로 이동.
    wd = ref.isoweekday()
    this_mon = (ref - timedelta(days=wd - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    start = this_mon + timedelta(weeks=offset_weeks)
    end = start + timedelta(days=7)
    return start, end


def _month_bounds(ref: datetime, offset_months: int = -1) -> tuple[datetime, datetime]:
    """기준 날짜 기반 월 범위. offset=-1 이면 지난 달."""
    y, m = ref.year, ref.month
    # 이번 달 1일
    this_m1 = ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # offset 적용
    total = (y * 12 + (m - 1)) + offset_months
    ny, nm = divmod(total, 12)
    start = this_m1.replace(year=ny, month=nm + 1)
    # end = 다음 달 1일
    nend = (ny * 12 + nm) + 1
    ey, em = divmod(nend, 12)
    end = this_m1.replace(year=ey, month=em + 1)
    return start, end


def emit_digests(store: DuckStore, out_dir: Path | None = None, *,
                 now: datetime | None = None) -> WikiResult:
    """최신 주간·월간 digest 생성. 규칙:
      - 지난 주 (월~일) digest: 매 호출 시 regenerate (재호출해도 동일 파일 overwrite)
      - 지난 달 digest: 동일
      - 이번 주/달 digest 는 선택 (env `GSTAR_DIGEST_CURRENT=on`)

    호출 주기는 worker tick 이 결정 (일 1회면 충분).
    """
    out = Path(out_dir) if out_dir else _out_dir()
    out.mkdir(parents=True, exist_ok=True)
    result = WikiResult()
    ref = now or datetime.now(timezone.utc).replace(tzinfo=None)

    include_current = os.environ.get("GSTAR_DIGEST_CURRENT", "off").lower() in {"on", "1", "true"}

    targets: list[tuple[str, datetime, datetime]] = []
    # 지난 주
    s, e = _week_bounds(ref, offset_weeks=-1)
    targets.append(("week", s, e))
    # 지난 달
    s, e = _month_bounds(ref, offset_months=-1)
    targets.append(("month", s, e))
    if include_current:
        s, e = _week_bounds(ref, offset_weeks=0)
        targets.append(("week", s, e))
        s, e = _month_bounds(ref, offset_months=0)
        targets.append(("month", s, e))

    for period, since, until in targets:
        try:
            citations = _fetch_citations_between(store, since, until)
            notes = _fetch_recent_notes(store, since, until)
            projects = [c["project"] for c in citations if c.get("project")]
            p = _render_digest(period, since, until, citations, notes, projects, out)
            if p is not None:
                result.digests += 1
        except Exception as exc:
            result.errors.append(
                f"digest {period} {since.date()}: {type(exc).__name__}: {exc}"
            )
    return result


def _render_index(entities: int, topics: int, sources: int, out_dir: Path,
                  citations: int = 0, digests: int = 0) -> Path:
    path = out_dir / "index.md"
    fm = _frontmatter("index", "Wiki 인덱스", {
        "entities": entities,
        "topics": topics,
        "sources": sources,
        "citations": citations,
        "digests": digests,
        "mirror_version": 2,
    })
    body = [
        fm,
        "# Wiki 인덱스\n",
        f"- 생성: {_now_iso()}",
        f"- entity {entities} · topic {topics} · source {sources} · "
        f"citation {citations} · digest {digests}",
        "",
        "## 디렉터리\n",
        "- `entities/` — G entity_canonical 1 row = 1 페이지",
        "- `topics/` — Louvain community 1 group = 1 페이지",
        "- `sources/` — namespace 별 노드 통계",
        "- `citations/<project>/` — jw/re 산출물 전문 (v2 추가)",
        "- `digests/` — 주간·월간 LLM 요약 (v2 추가)",
        "- `_inbox/` — 인간 편집 진입점 (다음 tick 이 G 로 흡수)",
        "",
        "> 이 vault 는 **G 로부터 자동 렌더링**. 편집 금지 — `_inbox/` 에만 작성.",
        "> 스키마 v2 명세: [[docs/wiki-schema]] (repo `docs/wiki-schema.md`)",
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
    # dead wiki link 방지: 렌더 대상 entity 의 canonical_name set.
    # 1-hop/2-hop 이웃 중 이 set 에 없으면 [[link]] 에서 제외 (파일이 없어 Obsidian
    # graph 에 유령 노드로 찍힘).
    canonical_names: set[str] = {
        e["canonical_name"] for e in entities if e.get("canonical_name")
    }

    for ent in entities:
        try:
            p = _render_entity(store, ent, out, canonical_names=canonical_names)
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

    # v2: citations (toggle default on, 가볍고 매번 재생성 안전)
    if os.environ.get("GSTAR_WIKI_CITATIONS", "on").lower() in {"on", "1", "true"}:
        for cit in _fetch_citations(store):
            try:
                if _render_citation(cit, out) is not None:
                    result.citations += 1
            except Exception as exc:
                result.errors.append(
                    f"citation {cit.get('project')}/{cit.get('stage')}: {exc}"
                )

    # v2: digests (toggle default on, LLM 호출 비용 있으니 환경별 tune 가능)
    if os.environ.get("GSTAR_WIKI_DIGESTS", "on").lower() in {"on", "1", "true"}:
        try:
            dg = emit_digests(store, out)
            result.digests += dg.digests
            result.errors.extend(dg.errors)
        except Exception as exc:
            result.errors.append(f"digests: {type(exc).__name__}: {exc}")

    _render_index(result.entities, result.topics, result.sources, out,
                  citations=result.citations, digests=result.digests)
    result.index_written = True

    _git_sync(out, result)
    return result

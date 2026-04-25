# Wiki Schema — 공식 템플릿 정의 (v2, 2026-04-24)

이 문서는 `wiki/` vault 의 모든 markdown 파일이 따라야 하는 frontmatter·본문·링크 규약을 정의한다.
Andrej Karpathy 의 "LLM Wiki" 원칙을 G + Qdrant + Neo4j + 기타 저장소에 걸쳐 통일하기 위한 기반.

## 1. 원칙

1. **Wiki = 파생 뷰** (SoT 아님). G / Qdrant / Neo4j 어딘가에 있는 데이터를 markdown 으로 mirror.
2. **독자 상태 없음**. 항상 재생성 가능. 인간 편집은 `_inbox/` 에만.
3. **provenance anchor 필수**. 모든 파일 frontmatter 에 원본 저장소 식별자.
4. **LLM이 읽고 쓰는 공용 언어**. 구조 예측 가능 → context 효율.
5. **Obsidian graph view 친화**. `[[wikilink]]` 로만 연결 구성.

## 2. 디렉터리 구조

```
wiki/
├── index.md                              # 전체 index · 통계
├── entities/<slug>.md                    # G entity_canonical
├── topics/<community_id>.md              # G community (louvain)
├── sources/<namespace>.md                # G source_namespace 통계
├── citations/<project>/<stage>-<v>.md    # G citation_artifact (2026-04-24)
├── digests/
│   ├── 2026-w17.md                       # 주간 digest (ISO week)
│   └── 2026-04.md                        # 월간 digest
├── emergence/<event_id>.md               # G emergence_event (옵션)
├── forms/<form_id>.md                    # Qdrant forms collection (후속)
├── proposals/<project>.md                # Qdrant proposals (후속)
├── graphs/<domain>.md                    # Neo4j import (후속)
├── _queries/<date>-<q-slug>.md           # g-ask trace (후속)
└── _inbox/<slug>.md                      # 인간 편집 (유일한 인풋)
```

## 3. 공통 frontmatter

모든 파일이 **반드시** 포함:

```yaml
---
view: wiki                          # 고정
type: <enum>                        # 아래 4절 참조
title: "..."                        # JSON double-quote (콜론·특수문자 대응)
generated_at: 2026-04-24T05:00:00Z  # ISO8601 UTC
mirror_version: 2                   # 이 스키마 버전
source_layer: g | qdrant | neo4j | mcp
---
```

### provenance anchor (type 따라 필수)

| type | anchor 필드 |
|------|-----------|
| entity | `g_entity_id`, `g_node_ids[]`, `g_fact_sources[]` |
| topic  | `community_id`, `algorithm`, `project_id` |
| source | `namespace`, `total_nodes` |
| citation | `citation_id`, `project`, `stage`, `version`, `content_hash` |
| digest | `period`, `period_start`, `period_end`, `source_ids[]` |
| emergence | `event_id`, `trigger_node_id`, `connected_node_ids[]` |
| form | `qdrant_collection`, `point_id` |
| proposal | `qdrant_collection`, `project_id` |
| graph | `neo4j_database`, `label` |
| query | `query_text`, `expanded_terms[]`, `channels[]` |

## 4. 타입 명세

### 4.1 `type: entity`

**용도**: G `entity_canonical` row → 1 파일.

```yaml
---
view: wiki
type: "entity"
title: "농식품"
source_layer: g
g_entity_id: 01KP...
g_node_ids: [01KP...]            # entity node(kind='entity')
g_fact_sources: [01KP...]        # evidence_of 로 연결된 fact
kind: "organization|person|location|concept|other"
mentions: 27
project_id: "..."
track: "proposal|research|coding|document"
community_id: "01KP..."          # topic 링크용
aliases: ["농축산물", "농산품"]
---

# 농식품

**kind**: `concept` · **mentions**: 27 · **project**: _global · **track**: proposal

**topic**: [[topics/01KP...]]

## 관련 entity
- [[entities/농업기술실용화재단]]
- [[entities/스마트팜]]

## 주요 근거 fact
- `01KP...` 농식품부 2026 R&D 지원 120억 규모 확대...
```

### 4.2 `type: topic`

**용도**: Louvain community → 1 파일.

```yaml
---
view: wiki
type: "topic"
title: "Topic: 2026 농식품 스마트팩토리"
source_layer: g
community_id: "01KP..."
algorithm: "louvain"
size: 25
project_id: "_global"
---

# Topic: 2026 농식품 스마트팩토리

**algorithm**: `louvain` · **size**: 25 · **project**: _global

## 소속 entity
- [[entities/농식품부_R&D]]
- [[entities/스마트팩토리]]
...
```

### 4.3 `type: source`

**용도**: namespace 별 노드 통계.

```yaml
---
view: wiki
type: "source"
title: "Source: govsupport_smartfactory"
source_layer: g
namespace: "govsupport_smartfactory"
total_nodes: 2847
---

# Source: govsupport_smartfactory

**total nodes**: 2847

## 노드 분포
- `fact`: 2100
- `entity`: 523
- `goal`: 224
```

### 4.4 `type: citation`

**용도**: G `citation_artifact` → 산출물 전문 + 메타.

```yaml
---
view: wiki
type: "citation"
title: "deep-tect debate v2"
source_layer: g
citation_id: 01KP...
project: "deep-tect"
stage: "02-debate"
version: "v2"
content_hash: "da6fd4c9..."
clearance_token: "clr_6ce7ef98d00a"
consistency: 92
file_path: "02-debate/debate-result.md"
line_count: 490
tags: ["deep-tect", "debate", "v2"]
decisions: ["D8 Paper3 Tri-modal Adapter", "D11 IndustrialBench"]
wip_files: ["_wip/researcher-feasibility-v2.md", ...]
---

# deep-tect · 02-debate · v2

**project**: `deep-tect` · **stage**: `02-debate` · **version**: `v2`
**consistency**: 92 · **lines**: 490 · **clearance**: `clr_6ce7ef98d00a`

## 주요 결정
- D8 Paper3 Tri-modal Adapter Framework 재명명
- D11 IndustrialBench = Counterfactual + Continual 통합

## WIP 파일 원본
- `_wip/researcher-feasibility-v2-addendum.md`
- `_wip/senior-rigor-review-v2-addendum.md`

## 본문

<산출물 전문 (content 필드)>
```

### 4.5 `type: digest`

**용도**: 주간/월간 자동 요약.

```yaml
---
view: wiki
type: "digest"
title: "Week 17 (2026-04-20 ~ 04-26)"
source_layer: g
period: "week"                    # week | month
period_start: "2026-04-20"
period_end: "2026-04-26"
source_ids: [01KP..., 01KP...]    # 집계된 citation/note ids
projects: ["deep-tect", "agri-food-ai-gjw"]
generated_by: "qwen2.5-14b"
---

# Week 17 (2026-04-20 ~ 04-26)

## 📌 핵심 결정
- ...

## 📄 완료 산출물
- [[citations/deep-tect/02-debate-v2]]

## 🔑 새로운 entity
- [[entities/Tri-modal_Adapter_Framework]] +23 mentions

## 📈 메트릭 변화
- G nodes: 215,669 → 216,412 (+743)

## 🔍 관련 topic
- [[topics/Structure-CLIP_Lineage]]
```

### 4.6 `type: emergence` (옵션)

```yaml
---
view: wiki
type: "emergence"
title: "창발: 트럭 지식항성"
source_layer: g
event_id: 01KP...
trigger_node_id: 01KP...
connected_node_ids: [01KP..., ...]
goal_id: 01KP...
---
```

### 4.7 `type: form` (Qdrant, 후속)

```yaml
---
view: wiki
type: "form"
title: "2026 농식품 스마트팩토리 양식"
source_layer: qdrant
qdrant_collection: "forms"
point_id: "..."
---
```

### 4.8 `type: proposal` (Qdrant, 후속)

### 4.9 `type: graph` (Neo4j, 후속)

### 4.10 `type: query` (g-ask trace, 후속)

```yaml
---
view: wiki
type: "query"
title: "농산품"
source_layer: mcp
query_text: "농산품"
expanded_terms: ["농식품", "농축산물", ...]
channels: {"semantic": 23, "graph": 14, "ripgrep": 8}
final_selected: 7
generated_by: "gemma4:26b-a4b"
---
```

## 5. 본문 섹션 규약

LLM 이 Karpathy 원칙대로 compound 하는 표준 섹션 이름:

| 섹션 | 용도 | type |
|------|------|------|
| `## Summary` | LLM 요약 (매 regen 갱신) | digest, citation, emergence |
| `## 주요 결정` / `## Decisions` | 결정사항 bullet | citation, digest |
| `## 주요 근거 fact` / `## Evidence` | fact id + 본문 | entity, citation |
| `## 관련 entity` / `## Related` | `[[entities/...]]` 링크 | entity, topic |
| `## 소속 entity` / `## Members` | community member | topic |
| `## 본문` / `## Content` | 원문 (해당 시) | citation |
| `## 메트릭` / `## Metrics` | 수치 변화 | digest |
| `## 노드 분포` | 통계 | source |

## 6. 링크 규약

- **Wikilink 필수**: `[[entities/농식품]]` — 경로 없이 filename 만 쓰지 말 것 (Obsidian 자동 해석 충돌)
- **상대 경로 금지**: `[[../entities/X]]` 쓰지 말 것
- **Alias 표시**: `[[entities/농식품|농산품]]` 으로 다른 표시 가능

## 7. Mirror vs Inbox

| 경로 | 편집 주체 | 생명주기 |
|------|----------|----------|
| `entities/`, `topics/`, `sources/`, `citations/`, `digests/` | mirror (자동) | 매 tick regenerate |
| `_inbox/*.md` | 인간 또는 Claude Code | `/wiki/inbox/write` → 다음 tick 이 G 로 흡수 후 `_processed/` 로 이동 |
| `_processed/`, `_failed/` | watcher | inbox 처리 결과 |
| `_queries/` | g-ask (후속) | 검색 trace, 보관 |

## 8. 확장 지침

새 type 추가 시:
1. `source_layer` 선택 (g / qdrant / neo4j / mcp)
2. provenance anchor 필드 정의 (위 3-2절)
3. `wiki_view.py` 에 `_fetch_<type>()` + `_render_<type>()` 추가
4. `emit_all()` 루프에 편입
5. 이 문서 4절에 명세 추가

## 9. mirror_version 변경 이력

- **v1** (2026-04-23): entity / topic / source
- **v2** (2026-04-24): citation / digest / emergence 추가, common anchor 확장

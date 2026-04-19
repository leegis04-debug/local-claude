#!/bin/bash
# Phase A4 — G vs Gateway /search/hybrid 10 쿼리 비교.
# 사용: ASST_TOKEN=... bash scripts/a4_compare.sh <gateway_url> <g_url> <out_file>
#   gateway_url 기본 http://localhost:8000 (SSH 터널 또는 Meshnet)
#   g_url       기본 http://100.79.251.53:9999 (직접) 또는 tunnel 시 http://localhost:9999

set -u
GATEWAY="${1:-http://localhost:8000}"
G_URL="${2:-http://100.79.251.53:9999}"
OUT="${3:-/tmp/a4_compare.md}"
TOKEN="${ASST_TOKEN:-$(grep ASST_TOKEN ~/.config/asst/config 2>/dev/null | cut -d= -f2)}"
[ -z "$TOKEN" ] && { echo "ERROR: ASST_TOKEN 필요"; exit 2; }

QUERIES=(
  "agri-food AI 전략"
  "GraphRAG 구조 설계"
  "KAMIS 농산물 데이터"
  "정부 사업계획서 템플릿"
  "jw:idea 비전"
  "BM25 리트리벌"
  "회의록 요약"
  "이상값 탐지 알고리즘"
  "회사 프로젝트 stage"
  "사업계획 예산 항목"
)

echo "# Phase A4 — G vs Gateway 비교 ($(date))" > "$OUT"
echo "" >> "$OUT"
echo "Gateway: $GATEWAY" >> "$OUT"
echo "G: $G_URL" >> "$OUT"
echo "" >> "$OUT"

for q in "${QUERIES[@]}"; do
  echo "## Query: $q" >> "$OUT"
  echo "" >> "$OUT"

  # Gateway (Qdrant+Neo4j)
  echo "### Gateway /search/hybrid" >> "$OUT"
  echo '```' >> "$OUT"
  curl -sf -X POST "$GATEWAY/search/hybrid" \
    -H "X-Auth-Token: $TOKEN" -H "Content-Type: application/json" \
    -d "{\"query\":\"$q\",\"top_k\":5}" -m 30 \
    | python3 -c "
import sys,json
try:
  d=json.load(sys.stdin)
  results=d.get('results') or d.get('hits') or []
  for i,r in enumerate(results[:5],1):
    t=(r.get('text') or r.get('passage') or r.get('content') or '')[:120]
    s=r.get('score') or r.get('sim') or 0
    src=r.get('source') or r.get('collection') or r.get('namespace') or ''
    print(f'  [{i}] s={s:.3f} src={src} :: {t!r}')
except Exception as e:
  print('(parse err)', e)
  print(sys.stdin.read()[:500])
" 2>&1 >> "$OUT"
  echo '```' >> "$OUT"
  echo "" >> "$OUT"

  # G
  echo "### G /search/hybrid" >> "$OUT"
  echo '```' >> "$OUT"
  curl -sf -X POST "$G_URL/search/hybrid" \
    -H "Content-Type: application/json" \
    -d "{\"query\":\"$q\",\"top_k\":5}" -m 30 \
    | python3 -c "
import sys,json
try:
  d=json.load(sys.stdin)
  # G 응답 구조 확인
  results = d if isinstance(d,list) else (d.get('results') or d.get('hits') or [])
  for i,r in enumerate(results[:5],1):
    t=(r.get('text') or '')[:120]
    s=r.get('score') or r.get('gravity') or 0
    ns=r.get('source_namespace') or r.get('namespace') or ''
    print(f'  [{i}] s={s:.3f} ns={ns} :: {t!r}')
except Exception as e:
  print('(parse err)', e)
" 2>&1 >> "$OUT"
  echo '```' >> "$OUT"
  echo "" >> "$OUT"
done

echo "완료 → $OUT"

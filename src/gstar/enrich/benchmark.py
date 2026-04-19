"""웹 검색 백엔드 벤치마크 — 고반복 + Claude 비교용.

실행:
    python -m gstar.enrich.benchmark --backends ddg,tavily,brave,naver \
        --iters 3 --top-k 5 --out bench.json

지표:
- hit_rate: 반환된 결과 수 / top_k
- unique_url_ratio: 중복 제외 URL 비율
- avg_snippet_len: 평균 snippet 길이
- stddev_results: 반복 간 결과 수 표준편차 (안정성)
- korean_ratio: 한국어 문자 비율 (도메인 적합도)
- claude_overlap: Claude reference 와 URL 일치율 (reference 파일 제공 시)

Claude reference 제공 방법 (수동 또는 Claude API):
    bench-claude-ref.json = {"query": [{"url":..., "title":..., "snippet":...}, ...]}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_QUERIES: list[str] = [
    "KAMIS 농산물 유통 정보 API",
    "외식업 식자재비 비중 통계",
    "TRL 5 8 기술성숙도 사업화",
    "한국 스마트팜 AI 활용 사례",
    "POS 데이터 매출 분석 머신러닝",
    "LOEKAL 외식 프랜차이즈",
    "농식품 AI 응용제품 신속상용화 지원사업",
    "외식업 폐업률 2024",
    "Gemma4 a4b 모델 한국어",
    "Ollama 한국어 파인튜닝",
]


@dataclass
class BackendResult:
    backend: str
    query: str
    iter_idx: int
    hits: int
    urls: list[str]
    avg_snippet_len: float
    korean_ratio: float
    latency_ms: int
    errors: list[str] = field(default_factory=list)


@dataclass
class BenchSummary:
    backend: str
    query: str
    iters: int
    mean_hits: float
    stddev_hits: float
    mean_latency_ms: float
    unique_url_ratio: float
    avg_snippet_len: float
    korean_ratio: float
    claude_overlap: float | None = None


_HANGUL = re.compile(r"[\uac00-\ud7a3]")


def _korean_ratio(texts: list[str]) -> float:
    joined = "".join(texts)
    if not joined:
        return 0.0
    return len(_HANGUL.findall(joined)) / len(joined)


async def _one_run(backend: str, query: str, top_k: int, iter_idx: int) -> BackendResult:
    from gstar.enrich.web_search import web_search

    t0 = time.time()
    try:
        results = await web_search(query, backend=backend, top_k=top_k)
        errors: list[str] = []
    except Exception as e:
        results = []
        errors = [str(e)[:200]]
    latency_ms = int((time.time() - t0) * 1000)
    urls = [r.get("url", "") for r in results if r.get("url")]
    snippets = [r.get("snippet", "") for r in results]
    avg_len = (sum(len(s) for s in snippets) / len(snippets)) if snippets else 0.0
    return BackendResult(
        backend=backend,
        query=query,
        iter_idx=iter_idx,
        hits=len(results),
        urls=urls,
        avg_snippet_len=round(avg_len, 1),
        korean_ratio=round(_korean_ratio(snippets), 3),
        latency_ms=latency_ms,
        errors=errors,
    )


async def run_benchmark(
    backends: list[str],
    queries: list[str],
    *,
    iters: int = 3,
    top_k: int = 5,
    claude_ref: dict[str, list[dict]] | None = None,
) -> tuple[list[BackendResult], list[BenchSummary]]:
    raw: list[BackendResult] = []
    for backend in backends:
        for q in queries:
            for i in range(iters):
                r = await _one_run(backend, q, top_k, i)
                raw.append(r)

    summaries: list[BenchSummary] = []
    for backend in backends:
        for q in queries:
            runs = [r for r in raw if r.backend == backend and r.query == q]
            if not runs:
                continue
            hits = [r.hits for r in runs]
            all_urls = [u for r in runs for u in r.urls]
            unique = len(set(all_urls))
            total = len(all_urls) or 1
            mean_snippet = statistics.mean([r.avg_snippet_len for r in runs]) if runs else 0.0
            mean_korean = statistics.mean([r.korean_ratio for r in runs]) if runs else 0.0

            overlap: float | None = None
            if claude_ref and q in claude_ref:
                ref_urls = {x.get("url", "") for x in claude_ref[q]}
                ours = set(all_urls)
                if ref_urls:
                    overlap = len(ours & ref_urls) / len(ref_urls)

            summaries.append(
                BenchSummary(
                    backend=backend,
                    query=q,
                    iters=len(runs),
                    mean_hits=statistics.mean(hits) if hits else 0,
                    stddev_hits=statistics.stdev(hits) if len(hits) > 1 else 0.0,
                    mean_latency_ms=statistics.mean([r.latency_ms for r in runs]),
                    unique_url_ratio=unique / total,
                    avg_snippet_len=round(mean_snippet, 1),
                    korean_ratio=round(mean_korean, 3),
                    claude_overlap=overlap,
                )
            )
    return raw, summaries


def render_markdown_report(summaries: list[BenchSummary]) -> str:
    by_backend: dict[str, list[BenchSummary]] = {}
    for s in summaries:
        by_backend.setdefault(s.backend, []).append(s)

    lines = ["# 웹 검색 백엔드 벤치마크", ""]
    lines.append("## 백엔드별 요약 (쿼리 평균)")
    lines.append("")
    lines.append("| backend | 평균 hits | 평균 latency(ms) | unique URL | avg snippet | 한국어비율 | Claude overlap |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for backend, items in by_backend.items():
        mean_hits = statistics.mean([s.mean_hits for s in items])
        mean_lat = statistics.mean([s.mean_latency_ms for s in items])
        mean_uniq = statistics.mean([s.unique_url_ratio for s in items])
        mean_snip = statistics.mean([s.avg_snippet_len for s in items])
        mean_kor = statistics.mean([s.korean_ratio for s in items])
        overlaps = [s.claude_overlap for s in items if s.claude_overlap is not None]
        mean_overlap = statistics.mean(overlaps) if overlaps else None
        overlap_str = f"{mean_overlap:.2f}" if mean_overlap is not None else "—"
        lines.append(
            f"| {backend} | {mean_hits:.1f} | {int(mean_lat)} | {mean_uniq:.2f} | "
            f"{mean_snip:.0f} | {mean_kor:.2f} | {overlap_str} |"
        )
    lines.append("")
    lines.append("## 쿼리 × 백엔드 상세")
    lines.append("")
    lines.append("| 쿼리 | backend | iters | hits | σhits | latency | 한국어 | overlap |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for s in summaries:
        overlap_str = f"{s.claude_overlap:.2f}" if s.claude_overlap is not None else "—"
        q = s.query[:40]
        lines.append(
            f"| {q} | {s.backend} | {s.iters} | {s.mean_hits:.1f} | "
            f"{s.stddev_hits:.1f} | {int(s.mean_latency_ms)} | {s.korean_ratio:.2f} | {overlap_str} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--backends", default="ddg,mock",
                   help="쉼표 구분. 예: tavily,brave,ddg,naver")
    p.add_argument("--iters", type=int, default=3)
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--queries-file", default=None,
                   help="각 줄에 쿼리 하나. 미지정 시 DEFAULT_QUERIES")
    p.add_argument("--claude-ref", default=None,
                   help="Claude WebSearch 결과 JSON (비교 gold standard)")
    p.add_argument("--out", default="bench.json")
    p.add_argument("--md", default="bench.md")
    args = p.parse_args()

    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    if args.queries_file and Path(args.queries_file).exists():
        queries = [l.strip() for l in Path(args.queries_file).read_text(encoding="utf-8").splitlines() if l.strip()]
    else:
        queries = DEFAULT_QUERIES

    claude_ref = None
    if args.claude_ref and Path(args.claude_ref).exists():
        claude_ref = json.loads(Path(args.claude_ref).read_text(encoding="utf-8"))

    raw, summaries = asyncio.run(
        run_benchmark(backends, queries, iters=args.iters, top_k=args.top_k, claude_ref=claude_ref)
    )

    Path(args.out).write_text(
        json.dumps(
            {"raw": [asdict(r) for r in raw], "summaries": [asdict(s) for s in summaries]},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    Path(args.md).write_text(render_markdown_report(summaries), encoding="utf-8")
    print(f"raw: {len(raw)} runs  summaries: {len(summaries)}")
    print(f"out: {args.out}")
    print(f"md:  {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""P축 산출물 md 표준 frontmatter.

사용자 규칙 (2026-04-22): 모든 P축 생성 md 파일 상단에 YAML frontmatter
(작성 날짜·시간·버전·제목·키워드·재현 env) 필수.

사용 예:
    from gstar.projection.frontmatter import build, next_version
    fm = build(
        stage="structure", track="proposal",
        user_input="외식 원가·매출 통합",
        version=next_version(stage_dir, "structure"),
        keywords=["수요예측", "공급망"],
        mode="svrr",
        extra={"questions": "5/5 ok"},
    )
    main_path.write_text(fm + body, encoding="utf-8")
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def build(
    *,
    stage: str,
    track: str,
    user_input: str,
    version: int,
    keywords: list[str] | None = None,
    mode: str | None = None,
    extra: dict | None = None,
) -> str:
    """YAML frontmatter 문자열 반환 (끝에 `\\n\\n` 포함).

    title 은 `<stage> — <user_input 앞 80자>`. created 는 KST ISO8601.
    env 토글(panel / theme_group / emergence / rank / rank_rag) 자동 반영.
    """
    kst = timezone(timedelta(hours=9))
    now = datetime.now(tz=kst).isoformat(timespec="seconds")
    title_topic = (user_input or stage).strip().replace("\n", " ")
    title = f"{stage} — {title_topic[:80]}"
    kw = [k for k in (keywords or []) if k]
    kw_str = ", ".join(kw) if kw else "-"
    env_flags = {
        "panel": os.environ.get("GP_PERSONAS", "off").lower() in {"on", "1", "true"},
        "theme_group": os.environ.get("GP_THEME_GROUP", "off").lower() in {"on", "1", "true"},
        "emergence": os.environ.get("GP_EMERGENCE", "off").lower() in {"on", "1", "true"},
        "rank": os.environ.get("GP_QUESTION_RANK", "on").lower() in {"on", "1", "true"},
        "rank_rag": os.environ.get("GP_QUESTION_RANK_RAG", "off").lower() in {"on", "1", "true"},
    }
    lines = [
        "---",
        f"title: {title}",
        f"stage: {stage}",
        f"track: {track}",
        f"created: {now}",
        f"version: v{version}",
        f"keywords: [{kw_str}]",
        f"mode: {mode or 'classic'}",
        f"panel: {'on' if env_flags['panel'] else 'off'}",
        f"theme_group: {'on' if env_flags['theme_group'] else 'off'}",
        f"emergence: {'on' if env_flags['emergence'] else 'off'}",
        f"rank: {'on' if env_flags['rank'] else 'off'}"
        + (" (rag)" if env_flags["rank_rag"] else ""),
    ]
    if extra:
        for k, v in extra.items():
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def next_version(stage_dir: Path, stage: str) -> int:
    """`_versions/<stage>-*.md` 중 개수 + 1. 없으면 1."""
    ver_dir = Path(stage_dir) / "_versions"
    if not ver_dir.exists():
        return 1
    # 두 백업 패턴 모두 수용: `<stage>-v<N>.md` 와 `<stage>-<ts>.md`
    return len(list(ver_dir.glob(f"{stage}-*.md"))) + 1


def has_frontmatter(body: str) -> bool:
    """이미 frontmatter 로 시작하는지 (중복 prepend 방지)."""
    return body.lstrip().startswith("---\n") or body.lstrip().startswith("---\r\n")

"""JS-렌더링 공유 페이지를 headless Chromium 으로 가져와 마크다운으로 변환.

ChatGPT / Claude / Gemini share URL 처럼 정적 HTML 에 본문이 없는 페이지를
WebFetch 로 못 긁을 때 쓴다. 기본 동작은 ChatGPT share 전용 추출기지만
`--raw` 로 전체 렌더 HTML 을, `--text` 로 visible text 만 받을 수 있다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Iterable


CHATGPT_SHARE_RE = re.compile(r"^https?://chatgpt\.com/share/[0-9a-f-]+", re.IGNORECASE)


@dataclass
class Message:
    role: str
    content: str


@dataclass
class Conversation:
    title: str = ""
    url: str = ""
    messages: list[Message] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines: list[str] = []
        if self.title:
            lines.append(f"# {self.title}")
        if self.url:
            lines.append(f"> Source: {self.url}")
        if lines:
            lines.append("")
        for msg in self.messages:
            role_label = {"user": "User", "assistant": "Assistant", "system": "System"}.get(
                msg.role.lower(), msg.role.capitalize() or "Unknown"
            )
            lines.append(f"## {role_label}")
            lines.append("")
            lines.append(msg.content.rstrip())
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def to_json(self) -> str:
        return json.dumps(
            {
                "title": self.title,
                "url": self.url,
                "messages": [{"role": m.role, "content": m.content} for m in self.messages],
            },
            ensure_ascii=False,
            indent=2,
        )


def fetch_rendered(
    url: str,
    *,
    wait_selector: str | None = None,
    timeout_ms: int = 30000,
    extra_wait_ms: int = 1500,
) -> tuple[str, str]:
    """URL 을 headless Chromium 으로 로드, JS 렌더 후 (title, html) 반환.

    wait_selector 가 주어지면 그 셀렉터가 DOM 에 붙을 때까지 대기한다.
    없으면 networkidle 후 extra_wait_ms 만큼 추가 대기.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "playwright 가 설치돼 있지 않다. `pip install '.[fetch]'` 또는 "
            "`pip install playwright && python -m playwright install chromium` 필요."
        ) from e

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="ko-KR",
                viewport={"width": 1280, "height": 900},
            )
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except Exception:
                pass
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=timeout_ms)
                except Exception:
                    pass
            if extra_wait_ms:
                page.wait_for_timeout(extra_wait_ms)
            title = page.title() or ""
            html = page.content()
            return title, html
        finally:
            browser.close()


def extract_chatgpt_share(html: str, url: str = "", title: str = "") -> Conversation:
    """ChatGPT share 페이지 렌더된 HTML 에서 대화를 추출.

    우선 __NEXT_DATA__ JSON 에서 뽑고, 실패하면 DOM 의
    `[data-message-author-role]` 블록을 BeautifulSoup 으로 긁는다.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    next_data = soup.find("script", id="__NEXT_DATA__")
    if next_data and next_data.string:
        convo = _extract_from_next_data(next_data.string)
        if convo and convo.messages:
            convo.url = url or convo.url
            convo.title = convo.title or title
            return convo

    convo = Conversation(url=url, title=title)
    nodes = soup.select("[data-message-author-role]")
    for node in nodes:
        role = node.get("data-message-author-role") or "unknown"
        content = _render_node_text(node)
        if content.strip():
            convo.messages.append(Message(role=role, content=content))

    if not convo.title:
        h1 = soup.find("h1")
        if h1:
            convo.title = h1.get_text(strip=True)

    return convo


def _extract_from_next_data(raw: str) -> Conversation | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    mapping = _deep_get_mapping(data)
    if not mapping:
        return None

    nodes = [v for v in mapping.values() if isinstance(v, dict)]
    ordered = _order_by_linkedlist(mapping)
    if ordered:
        nodes = ordered

    convo = Conversation()
    title = _deep_get_key(data, "title")
    if isinstance(title, str):
        convo.title = title

    for node in nodes:
        msg = node.get("message") if isinstance(node, dict) else None
        if not isinstance(msg, dict):
            continue
        author = msg.get("author") or {}
        role = author.get("role") if isinstance(author, dict) else None
        if role in (None, "tool"):
            continue
        content = msg.get("content") or {}
        parts: Iterable = []
        if isinstance(content, dict):
            parts = content.get("parts") or []
        text_parts: list[str] = []
        for part in parts:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str):
                    text_parts.append(text)
        body = "\n\n".join(t for t in text_parts if t.strip())
        if body.strip():
            convo.messages.append(Message(role=role or "unknown", content=body))
    return convo


def _deep_get_mapping(data) -> dict | None:
    stack = [data]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if "mapping" in cur and isinstance(cur["mapping"], dict):
                return cur["mapping"]
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def _deep_get_key(data, key: str):
    stack = [data]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if key in cur:
                return cur[key]
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def _order_by_linkedlist(mapping: dict) -> list[dict]:
    root_id = None
    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            continue
        if node.get("parent") in (None, ""):
            root_id = node_id
            break
    if root_id is None:
        return []
    ordered: list[dict] = []
    cur_id = root_id
    visited: set[str] = set()
    while cur_id and cur_id not in visited:
        visited.add(cur_id)
        node = mapping.get(cur_id)
        if not isinstance(node, dict):
            break
        ordered.append(node)
        children = node.get("children") or []
        cur_id = children[0] if children else None
    return ordered


def _render_node_text(node) -> str:
    for br in node.find_all("br"):
        br.replace_with("\n")
    text = node.get_text("\n", strip=False)
    lines = [ln.rstrip() for ln in text.splitlines()]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if not ln.strip():
            blank += 1
            if blank <= 1:
                out.append("")
            continue
        blank = 0
        out.append(ln)
    return "\n".join(out).strip()


def fetch_chatgpt_share(url: str, *, timeout_ms: int = 30000) -> Conversation:
    title, html = fetch_rendered(
        url,
        wait_selector="[data-message-author-role], article, main",
        timeout_ms=timeout_ms,
    )
    return extract_chatgpt_share(html, url=url, title=title)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fetch-share",
        description="headless Chromium 으로 JS-렌더링 share 페이지를 가져온다.",
    )
    parser.add_argument("url", help="대상 URL (ChatGPT share 등)")
    parser.add_argument(
        "--format",
        choices=["markdown", "json", "text", "html"],
        default="markdown",
        help="출력 포맷 (기본 markdown)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30000,
        help="네트워크 타임아웃 ms (기본 30000)",
    )
    parser.add_argument(
        "--wait",
        default=None,
        help="이 CSS 셀렉터가 나타날 때까지 대기",
    )
    parser.add_argument(
        "--extra-wait",
        type=int,
        default=1500,
        help="렌더 안정화용 추가 대기 ms (기본 1500)",
    )
    args = parser.parse_args(argv)

    is_chatgpt = bool(CHATGPT_SHARE_RE.match(args.url))

    if args.format == "html":
        _, html = fetch_rendered(
            args.url,
            wait_selector=args.wait,
            timeout_ms=args.timeout,
            extra_wait_ms=args.extra_wait,
        )
        sys.stdout.write(html)
        return 0

    if args.format == "text":
        from bs4 import BeautifulSoup

        _, html = fetch_rendered(
            args.url,
            wait_selector=args.wait,
            timeout_ms=args.timeout,
            extra_wait_ms=args.extra_wait,
        )
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        body = soup.select_one("main") or soup.body or soup
        sys.stdout.write(_render_node_text(body) + "\n")
        return 0

    if is_chatgpt:
        convo = fetch_chatgpt_share(args.url, timeout_ms=args.timeout)
    else:
        title, html = fetch_rendered(
            args.url,
            wait_selector=args.wait,
            timeout_ms=args.timeout,
            extra_wait_ms=args.extra_wait,
        )
        convo = extract_chatgpt_share(html, url=args.url, title=title)
        if not convo.messages:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            body = soup.select_one("main") or soup.body or soup
            convo.messages = [Message(role="page", content=_render_node_text(body))]
            convo.title = convo.title or title

    if not convo.messages:
        print(
            f"fetch-share: 대화 내용을 추출하지 못함 ({args.url}).\n"
            "  - 공유 URL 이 만료됐거나 비공개일 수 있음\n"
            "  - `--format html` 로 원본 확인 또는 `--wait '셀렉터'` 지정",
            file=sys.stderr,
        )
        return 2

    if args.format == "json":
        sys.stdout.write(convo.to_json() + "\n")
    else:
        sys.stdout.write(convo.to_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

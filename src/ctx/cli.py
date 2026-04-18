"""ctx CLI — 위치/컨텍스트 스위치.

사용:
  ctx init                # 기본 templates (home, office-daegyeom, offline) 생성
  ctx current             # 활성 context 이름
  ctx list                # 등록된 context 목록
  ctx switch <name>       # 전환
  ctx get <dotted.key>    # 활성 context 의 값 조회 (예: gstar.namespace)
  ctx show [name]         # context TOML 내용 출력
  ctx history [--n N]     # 최근 전환 이력
  ctx device              # 맥북 fingerprint
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import typer

from ctx.config import Paths
from ctx.context import (
    ContextNotFound,
    current_context_name,
    get_key_path,
    list_context_names,
    load_context,
    set_current_context_name,
    write_context,
)
from ctx.device import get_or_init_device
from ctx.history import log_switch, read_history

app = typer.Typer(help="ctx — 통합 location/context 스위치")


_DEFAULT_CONTEXTS: dict[str, dict] = {
    "home": {
        "display": "🏠 집",
        "color": "#2b8a3e",
        "wifi": [],
        "gstar": {"namespace": "personal"},
        "jw": {"rag_enabled": True, "gateway_mode": "meshnet-direct"},
        "network": {
            "gateway_url": "http://100.79.251.53:8000",
            "prefer_lan": True,
        },
    },
    "office-daegyeom": {
        "display": "🏢 다겸",
        "color": "#0066cc",
        "wifi": [],
        "gstar": {"namespace": "daegyeom"},
        "jw": {"rag_enabled": True, "gateway_mode": "ssh-tunnel"},
        "network": {
            "gateway_url": "http://100.79.251.53:8000",
            "ssh_host": "ljw-op",
            "ssh_forward_ports": [8000, 8765, 8766],
        },
    },
    "offline": {
        "display": "✈️ 오프라인",
        "color": "#868e96",
        "wifi": [],
        "gstar": {"namespace": "personal"},
        "jw": {"rag_enabled": False, "gateway_mode": "disabled"},
        "network": {"gateway_url": ""},
    },
}


@app.command()
def init(
    force: bool = typer.Option(False, "--force", help="기존 파일 덮어쓰기"),
) -> None:
    """$CTX_HOME 초기화 + 기본 context 3개 템플릿 생성."""

    paths = Paths.load()
    paths.ensure()
    dev = get_or_init_device(paths)

    created: list[str] = []
    for name, data in _DEFAULT_CONTEXTS.items():
        target = paths.contexts_dir / f"{name}.toml"
        if target.exists() and not force:
            continue
        write_context(name, data, paths)
        created.append(name)

    if current_context_name(paths) is None:
        set_current_context_name("home", paths)
        log_switch(to="home", from_=None, trigger="init", paths=paths)

    typer.echo(f"home:   {paths.home}")
    typer.echo(f"device: {dev.fingerprint} ({dev.source})")
    typer.echo(f"created: {', '.join(created) if created else '(none)'}")
    typer.echo(f"current: {current_context_name(paths)}")


@app.command()
def current() -> None:
    name = current_context_name()
    if name is None:
        typer.echo("unset")
        raise typer.Exit(1)
    typer.echo(name)


@app.command("list")
def list_cmd(
    names_only: bool = typer.Option(
        False, "--names", help="이름만 한 줄에 하나씩 (스크립트 친화적)"
    ),
) -> None:
    paths = Paths.load()
    cur = current_context_name(paths)
    for name in list_context_names(paths):
        if names_only:
            typer.echo(name)
            continue
        try:
            data = load_context(name, paths)
            display = data.get("display", name)
        except Exception:
            display = name
        flag = "*" if name == cur else " "
        typer.echo(f"{flag} {name:25s}  {display}")


@app.command()
def switch(
    name: str = typer.Argument(..., help="전환할 context 이름"),
    trigger: str = typer.Option("manual", "--trigger", help="manual|auto-wifi|api"),
) -> None:
    paths = Paths.load()
    try:
        load_context(name, paths)
    except ContextNotFound:
        typer.echo(f"context not found: {name}")
        raise typer.Exit(1)

    prev = current_context_name(paths)
    if prev == name:
        typer.echo(f"already active: {name}")
        return
    set_current_context_name(name, paths)
    log_switch(to=name, from_=prev, trigger=trigger, paths=paths)
    typer.echo(f"{prev or '(none)'} -> {name}")


@app.command()
def get(
    key: str = typer.Argument(..., help="'gstar.namespace' 같은 도트 경로"),
    context: str = typer.Option(
        None, "--context", help="특정 context. 미지정 시 활성 context"
    ),
) -> None:
    """활성 context 에서 값 조회. 도구 스크립트들이 이걸 `ctx get gstar.namespace` 로 호출."""

    paths = Paths.load()
    name = context or current_context_name(paths)
    if not name:
        typer.echo("no active context", err=True)
        raise typer.Exit(1)
    try:
        data = load_context(name, paths)
        value = get_key_path(data, key)
    except ContextNotFound:
        typer.echo(f"context not found: {name}", err=True)
        raise typer.Exit(1)
    except KeyError:
        typer.echo(f"key not found: {key}", err=True)
        raise typer.Exit(1)

    if isinstance(value, (list, dict)):
        typer.echo(json.dumps(value, ensure_ascii=False))
    else:
        typer.echo(value)


@app.command("export-env")
def export_env_cmd(
    context: str = typer.Option(
        None, "--context", help="특정 context. 미지정 시 활성"
    ),
    prefix: str = typer.Option(
        "CTX_", "--prefix", help="환경변수 prefix"
    ),
) -> None:
    """활성 context 를 bash `export VAR=value` 형태로 덤프.

    사용: `eval "$(ctx export-env)"` 하면 각 도구가 읽을 환경변수로 주입.
    예: gstar.namespace → CTX_GSTAR_NAMESPACE=daegyeom
    """
    paths = Paths.load()
    name = context or current_context_name(paths)
    if not name:
        typer.echo("# no active context", err=True)
        raise typer.Exit(1)
    try:
        data = load_context(name, paths)
    except ContextNotFound:
        typer.echo(f"# context not found: {name}", err=True)
        raise typer.Exit(1)

    typer.echo(f"export {prefix}NAME={_sh_quote(name)}")
    for k, v in _flatten(data):
        env_key = prefix + k.upper().replace(".", "_").replace("-", "_")
        typer.echo(f"export {env_key}={_sh_quote(v)}")


def _flatten(obj, prefix: str = ""):
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            yield from _flatten(v, key)
        elif isinstance(v, list):
            yield (key, ",".join(str(x) for x in v))
        elif isinstance(v, bool):
            yield (key, "true" if v else "false")
        else:
            yield (key, str(v))


def _sh_quote(s: str) -> str:
    s = str(s)
    if s == "":
        return '""'
    # single-quote escape (POSIX safe)
    return "'" + s.replace("'", "'\\''") + "'"


@app.command()
def autodetect(
    apply: bool = typer.Option(False, "--apply", help="실제 전환 수행. 미지정 시 dry-run"),
    force: bool = typer.Option(False, "--force", help="쿨다운 무시"),
) -> None:
    """현재 SSID 를 매칭해 context 자동 전환.

    SwiftBar 플러그인이 매 tick 마다 `ctx autodetect --apply` 호출.
    """
    from ctx.autodetect import autodetect as _auto

    res = _auto(apply=apply, force=force)
    ssid = res.ssid or "(no wifi)"
    if res.action == "switched":
        if apply:
            typer.echo(f"{res.current or '(none)'} -> {res.matched}  (ssid={ssid})")
        else:
            typer.echo(f"would switch: {res.current or '(none)'} -> {res.matched}  (ssid={ssid}, dry-run)")
    elif res.action == "same":
        typer.echo(f"already active: {res.matched}  (ssid={ssid})")
    elif res.action == "no-match":
        typer.echo(f"no context matches ssid={ssid} — stay on {res.current or '(none)'}")
    elif res.action == "cooldown":
        typer.echo(f"skipped (cooldown): would be {res.matched}  (ssid={ssid})")
    else:
        typer.echo(f"no ssid detected — stay on {res.current or '(none)'}")


@app.command("wifi")
def wifi_cmd(
    action: str = typer.Argument(..., help="add|remove"),
    context: str = typer.Argument(..., help="대상 context 이름"),
    ssid: str = typer.Argument(..., help="WiFi SSID"),
) -> None:
    """context TOML 의 wifi 배열에 SSID 추가/제거."""
    paths = Paths.load()
    try:
        data = load_context(context, paths)
    except ContextNotFound:
        typer.echo(f"context not found: {context}", err=True)
        raise typer.Exit(1)
    ssids = list(data.get("wifi") or [])
    if action == "add":
        if ssid in ssids:
            typer.echo("already present")
            return
        ssids.append(ssid)
    elif action == "remove":
        if ssid not in ssids:
            typer.echo("not present")
            return
        ssids = [s for s in ssids if s != ssid]
    else:
        typer.echo(f"unknown action: {action} (use add|remove)", err=True)
        raise typer.Exit(1)
    data["wifi"] = ssids
    write_context(context, data, paths)
    typer.echo(f"{context}.wifi = {ssids}")


@app.command("add")
def add_cmd(
    name: str = typer.Argument(..., help="새 context 이름 (예: office-newcorp)"),
    from_template: str = typer.Option(
        None, "--from", help="복사할 기존 context. 미지정 시 offline 기반"
    ),
    display: str = typer.Option(
        None, "--display", help="표시 문자열 (예: '🏢 NewCorp')"
    ),
    color: str = typer.Option(None, "--color", help="SwiftBar 텍스트 색 (hex)"),
    description: str = typer.Option("", "--description"),
    namespace: str = typer.Option(
        None, "--namespace", help="gstar.namespace. 미지정 시 context name 재사용"
    ),
) -> None:
    """새 context TOML 생성. 이직·신규 프로젝트 대응."""
    paths = Paths.load()
    paths.ensure()
    existing = set(list_context_names(paths))
    if name in existing:
        typer.echo(f"이미 존재: {name}")
        raise typer.Exit(1)

    if from_template and from_template in existing:
        data = load_context(from_template, paths)
    else:
        data = {"display": name, "color": "#808080", "wifi": [],
                "gstar": {"namespace": name}, "jw": {}, "network": {}}

    if display:
        data["display"] = display
    if color:
        data["color"] = color
    data["wifi"] = []  # 템플릿에서 상속한 SSID 는 새 context 엔 무의미
    data.setdefault("gstar", {})["namespace"] = namespace or name
    if description:
        data["description"] = description

    write_context(name, data, paths)
    typer.echo(f"created: {name} ({data.get('display', name)})")


@app.command("edit")
def edit_cmd(
    name: str = typer.Argument(..., help="편집할 context"),
) -> None:
    """$EDITOR 로 context TOML 열기."""
    paths = Paths.load()
    target = paths.contexts_dir / f"{name}.toml"
    if not target.exists():
        typer.echo(f"context not found: {name}", err=True)
        raise typer.Exit(1)
    editor = os.environ.get("EDITOR") or "open -t"
    if editor == "open -t":
        subprocess.run(["open", "-t", str(target)])
    else:
        subprocess.run(editor.split() + [str(target)])


@app.command("rm")
def rm_cmd(
    name: str = typer.Argument(..., help="삭제할 context"),
    yes: bool = typer.Option(False, "--yes", help="확인 프롬프트 건너뛰기"),
) -> None:
    """context 삭제. 활성 context 는 거부. 이력(history.jsonl)은 보존."""
    paths = Paths.load()
    cur = current_context_name(paths)
    if name == cur:
        typer.echo(f"활성 context 는 삭제 불가: {name}. 먼저 다른 context 로 switch.",
                   err=True)
        raise typer.Exit(1)
    target = paths.contexts_dir / f"{name}.toml"
    if not target.exists():
        typer.echo(f"context not found: {name}", err=True)
        raise typer.Exit(1)
    if not yes:
        typer.echo(f"confirm delete: {name}? use --yes")
        raise typer.Exit(1)
    target.unlink()
    typer.echo(f"removed: {name}")


@app.command()
def show(
    context: str = typer.Argument(None, help="보여줄 context. 미지정 시 활성"),
) -> None:
    paths = Paths.load()
    name = context or current_context_name(paths)
    if not name:
        typer.echo("no active context")
        raise typer.Exit(1)
    try:
        data = load_context(name, paths)
    except ContextNotFound:
        typer.echo(f"context not found: {name}")
        raise typer.Exit(1)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


@app.command()
def history(
    n: int = typer.Option(10, "--n", help="최근 N 개"),
) -> None:
    entries = read_history()
    for ev in entries[-n:]:
        frm = ev.get("from") or "(none)"
        typer.echo(
            f"{ev['ts']}  {frm:20s} -> {ev['to']:20s}  "
            f"trigger={ev['trigger']}  fp={ev['device_fp']}"
        )


@app.command()
def device() -> None:
    dev = get_or_init_device()
    typer.echo(f"fingerprint: {dev.fingerprint}")
    typer.echo(f"source:      {dev.source}")
    typer.echo(f"hostname:    {dev.hostname}")
    typer.echo(f"platform:    {dev.platform}")


if __name__ == "__main__":
    app()

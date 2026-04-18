"""오프라인 미러 — 미니 PC G 정본을 맥북 ~/.gstar-mirror/ 로 read-only 복사.

rsync 기반. 실제 실행은 launchd agent + `g mirror pull` 커맨드.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MirrorResult:
    ok: bool
    bytes_copied: int
    error: str | None = None


def local_mirror_dir() -> Path:
    return Path.home() / ".gstar-mirror"


def pull_via_rsync(remote: str, local: Path | None = None) -> MirrorResult:
    """remote (예: `user@100.79.251.53:/volume1/docker/gstar-data/state/`) → local.

    rsync 미설치 시 fallback 은 없음 — 맥OS 는 기본 설치돼 있음.
    """
    local = local or local_mirror_dir()
    local.mkdir(parents=True, exist_ok=True)
    try:
        out = subprocess.run(
            ["rsync", "-av", "--delete", remote, str(local) + "/"],
            capture_output=True, timeout=300,
        )
    except Exception as e:
        return MirrorResult(ok=False, bytes_copied=0, error=str(e))
    if out.returncode != 0:
        return MirrorResult(
            ok=False, bytes_copied=0,
            error=out.stderr.decode("utf-8", errors="replace"),
        )
    size = sum(f.stat().st_size for f in local.rglob("*") if f.is_file())
    return MirrorResult(ok=True, bytes_copied=size)


def pull_via_local_copy(src: Path, local: Path | None = None) -> MirrorResult:
    """테스트·로컬용. rsync 대신 shutil.copytree."""
    local = local or local_mirror_dir()
    if local.exists():
        shutil.rmtree(local)
    shutil.copytree(src, local)
    size = sum(f.stat().st_size for f in local.rglob("*") if f.is_file())
    return MirrorResult(ok=True, bytes_copied=size)

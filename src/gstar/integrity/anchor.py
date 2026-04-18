"""외부 앵커링 어댑터 인터페이스.

Merkle root 를 외부 시스템(Bitcoin OP_RETURN, Ethereum, OpenTimestamps, git commit)
에 기록해 "이 시점에 이 지식 상태가 존재했다"는 외부 증인을 남긴다.

실제 구현은 사용자가 외부 공유를 시작할 때 선택.
"""

from __future__ import annotations

from typing import Protocol


class AnchorAdapter(Protocol):
    """auditor-free 외부 증인. 구현은 Week 3.5 범위 밖."""

    def anchor(self, merkle_root: str) -> str:
        """Merkle root 를 외부에 기록하고 추적 가능한 tx_ref 반환."""
        ...

    def verify(self, merkle_root: str, tx_ref: str) -> bool:
        """외부 레코드가 주어진 merkle_root 를 증명하는지 확인."""
        ...


class NullAnchor:
    """no-op. 기본값. 앵커링 비활성 상태."""

    def anchor(self, merkle_root: str) -> str:
        return ""

    def verify(self, merkle_root: str, tx_ref: str) -> bool:
        return False


class FilesystemSnapshotAnchor:
    """파일시스템 경로에 Merkle root 를 기록·검증하는 범용 anchor.

    지원 스토리지 예:
      - Dropbox 폴더 (`~/Dropbox/gstar-anchors/`)
      - iCloud Drive (`~/Library/Mobile Documents/.../gstar-anchors/`)
      - DS218 SMB 마운트 (`/Volumes/gstar-snapshots/`)
      - git 레포 (`~/gstar-anchors/`, 주기적으로 push)

    어떤 경로든 클라우드 동기화 도구가 알아서 다장치·시점별 보존.
    """

    def __init__(self, snapshot_dir):
        from pathlib import Path
        self.snapshot_dir = Path(snapshot_dir)
        self.anchors_dir = self.snapshot_dir / "anchors"

    def anchor(self, merkle_root: str) -> str:
        from datetime import datetime, timezone
        self.anchors_dir.mkdir(parents=True, exist_ok=True)
        p = self.anchors_dir / f"{merkle_root}.txt"
        ts = datetime.now(timezone.utc).isoformat()
        p.write_text(f"{ts}\n{merkle_root}\n", encoding="utf-8")
        return str(p)

    def verify(self, merkle_root: str, tx_ref: str) -> bool:
        from pathlib import Path
        p = Path(tx_ref)
        if not p.exists():
            return False
        content = p.read_text(encoding="utf-8")
        return merkle_root in content


# 하위 호환 alias (기존 import 보호)
DS218SnapshotAnchor = FilesystemSnapshotAnchor

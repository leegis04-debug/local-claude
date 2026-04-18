"""공통 fixture — User 메모리 경로를 tmp 로 격리한다.

LC_DATA_DIR 는 config import 시점에 캐시되므로 테스트 때는 config/user 모듈의
심볼을 직접 monkeypatch 해서 격리한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_user_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "lc-data" / "user" / "preferences.json"
    monkeypatch.setattr("local_claude.config.USER_MEMORY_PATH", path)
    return path


@pytest.fixture(autouse=True)
def _reset_session() -> None:
    from local_claude.memory import session

    session.clear()

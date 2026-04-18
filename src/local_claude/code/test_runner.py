"""프로젝트 유형 자동 감지 + 테스트 실행 + 재시도.

감지 우선순위:
  1. 명시적 --cmd override
  2. pyproject.toml 또는 pytest.ini 있음 → `python3 -m pytest`
  3. package.json 에 scripts.test 있음 → `npm test`
  4. Cargo.toml → `cargo test`
  5. go.mod → `go test ./...`
  6. Makefile 에 `test:` 타겟 → `make test`
  7. 어느 것도 없으면 ok=False + detected="none"
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

_OUTPUT_TAIL_CHARS = 2000


@dataclass
class TestResult:
    ok: bool
    command: list[str]
    detected: str  # "pytest" | "npm" | "cargo" | "go" | "make" | "override" | "none"
    exit_code: int = 0
    duration_ms: int = 0
    attempts: int = 0
    output: str = ""
    stderr: str = ""
    cwd: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "detected": self.detected,
            "command": self.command,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "attempts": self.attempts,
            "output": self.output,
            "stderr": self.stderr,
            "cwd": self.cwd,
        }


def detect_command(project_root: Path) -> tuple[list[str] | None, str]:
    """프로젝트 루트에서 테스트 실행 명령을 자동 감지."""
    if (project_root / "pyproject.toml").exists() or (project_root / "pytest.ini").exists():
        return ["python3", "-m", "pytest"], "pytest"
    pkg = project_root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            scripts = data.get("scripts") or {}
            if isinstance(scripts, dict) and scripts.get("test"):
                return ["npm", "test"], "npm"
        except (json.JSONDecodeError, OSError):
            pass
    if (project_root / "Cargo.toml").exists():
        return ["cargo", "test"], "cargo"
    if (project_root / "go.mod").exists():
        return ["go", "test", "./..."], "go"
    make = project_root / "Makefile"
    if make.exists():
        try:
            text = make.read_text(encoding="utf-8")
            if re.search(r"^\s*test\s*:", text, re.MULTILINE):
                return ["make", "test"], "make"
        except OSError:
            pass
    return None, "none"


def _tail(text: str, chars: int = _OUTPUT_TAIL_CHARS) -> str:
    if len(text) <= chars:
        return text
    return "...\n" + text[-chars:]


def run(
    project_root: Path | str = ".",
    *,
    cmd: str | list[str] | None = None,
    retries: int = 0,
    timeout_s: int = 600,
    env: dict[str, str] | None = None,
) -> TestResult:
    root = Path(project_root).resolve()
    if cmd is not None:
        if isinstance(cmd, str):
            command = shlex.split(cmd)
        else:
            command = list(cmd)
        detected = "override"
    else:
        command, detected = detect_command(root)
        if command is None:
            return TestResult(
                ok=False,
                command=[],
                detected="none",
                exit_code=127,
                cwd=str(root),
                output="테스트 러너를 감지하지 못함 — --cmd 로 명시하세요",
            )

    last_result: TestResult | None = None
    max_attempts = max(1, 1 + retries)
    for attempt in range(1, max_attempts + 1):
        start = time.monotonic()
        try:
            proc = subprocess.run(
                command,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
                env=env,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            result = TestResult(
                ok=proc.returncode == 0,
                command=command,
                detected=detected,
                exit_code=proc.returncode,
                duration_ms=duration_ms,
                attempts=attempt,
                output=_tail(proc.stdout or ""),
                stderr=_tail(proc.stderr or ""),
                cwd=str(root),
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - start) * 1000)
            result = TestResult(
                ok=False,
                command=command,
                detected=detected,
                exit_code=-1,
                duration_ms=duration_ms,
                attempts=attempt,
                output="",
                stderr=f"timeout after {timeout_s}s",
                cwd=str(root),
            )
        except FileNotFoundError as exc:
            return TestResult(
                ok=False,
                command=command,
                detected=detected,
                exit_code=127,
                attempts=attempt,
                output="",
                stderr=f"명령을 찾을 수 없음: {exc}",
                cwd=str(root),
            )

        last_result = result
        if result.ok:
            return result
        # 실패 시 재시도 — 마지막 시도라면 break.

    assert last_result is not None
    return last_result

"""Ed25519 서명 선택 사용 테스트."""

from __future__ import annotations

from pathlib import Path

from gstar.integrity.signing import (
    generate_keypair,
    load_private_key,
    sign_hash,
    verify_signature,
)


def test_sign_without_key_returns_none(tmp_path: Path):
    assert sign_hash(tmp_path, "personal", "a" * 64) is None


def test_generate_then_sign_then_verify(tmp_path: Path):
    fp = generate_keypair(tmp_path, "personal")
    assert len(fp) == 16

    assert load_private_key(tmp_path, "personal") is not None

    content = "c" * 64
    signed = sign_hash(tmp_path, "personal", content)
    assert signed is not None
    signer_id, sig_hex = signed
    assert signer_id == fp

    assert verify_signature(tmp_path, "personal", content, sig_hex)
    # 다른 content 에 대한 서명은 무효
    assert not verify_signature(tmp_path, "personal", "d" * 64, sig_hex)

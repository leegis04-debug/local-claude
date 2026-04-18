"""Ed25519 서명 슬롯.

키가 있으면 서명, 없으면 skip. 외부 공유가 실제로 시작될 때 활성화.
키 파일: $GSTAR_HOME/keys/<namespace>.key (PEM 형식의 private key)
"""

from __future__ import annotations

from pathlib import Path

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

    _CRYPTO_OK = True
except Exception:  # pragma: no cover
    _CRYPTO_OK = False


def _keys_dir(gstar_home: Path) -> Path:
    return gstar_home / "keys"


def generate_keypair(gstar_home: Path, namespace: str) -> str:
    """새 Ed25519 키 쌍을 생성하여 파일로 저장. fingerprint(hex 16자) 반환."""

    if not _CRYPTO_OK:
        raise RuntimeError("cryptography 패키지가 설치되지 않음")

    key_dir = _keys_dir(gstar_home)
    key_dir.mkdir(parents=True, exist_ok=True)
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    (key_dir / f"{namespace}.key").write_bytes(priv_pem)

    pub = priv.public_key()
    pub_raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    fingerprint = pub_raw.hex()[:16]
    (key_dir / f"{namespace}.pub").write_bytes(pub_raw)
    return fingerprint


def load_private_key(gstar_home: Path, namespace: str):
    key_path = _keys_dir(gstar_home) / f"{namespace}.key"
    if not key_path.exists() or not _CRYPTO_OK:
        return None
    return serialization.load_pem_private_key(key_path.read_bytes(), password=None)


def load_public_key_raw(gstar_home: Path, namespace: str) -> bytes | None:
    pub_path = _keys_dir(gstar_home) / f"{namespace}.pub"
    if not pub_path.exists():
        return None
    return pub_path.read_bytes()


def sign_hash(gstar_home: Path, namespace: str, content_hash: str) -> tuple[str, str] | None:
    """content_hash 에 서명. (signer_id, signature_hex) 또는 키 없으면 None."""

    priv = load_private_key(gstar_home, namespace)
    if priv is None:
        return None
    pub_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    fingerprint = pub_raw.hex()[:16]
    sig = priv.sign(bytes.fromhex(content_hash))
    return fingerprint, sig.hex()


def verify_signature(
    gstar_home: Path, namespace: str, content_hash: str, signature_hex: str
) -> bool:
    """서명 검증. 공개키 파일 기반. 키 없으면 False."""

    if not _CRYPTO_OK:
        return False
    pub_raw = load_public_key_raw(gstar_home, namespace)
    if pub_raw is None:
        return False
    pub = Ed25519PublicKey.from_public_bytes(pub_raw)
    try:
        pub.verify(bytes.fromhex(signature_hex), bytes.fromhex(content_hash))
        return True
    except Exception:
        return False

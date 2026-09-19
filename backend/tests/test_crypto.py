# TDD 红灯：BYO-key 加密存储（§C 客户自带 key：落库加密、主密钥在 env）
import pytest

from app.services.crypto import decrypt_secret, encrypt_secret


def test_roundtrip():
    token = encrypt_secret("sk-my-secret-key-123", "master-passphrase")
    assert decrypt_secret(token, "master-passphrase") == "sk-my-secret-key-123"


def test_ciphertext_differs_from_plaintext():
    token = encrypt_secret("sk-my-secret-key-123", "master-passphrase")
    assert "sk-my-secret-key" not in token
    # Fernet 每次加密带随机 IV：同一明文两次密文不同
    assert encrypt_secret("sk-my-secret-key-123", "master-passphrase") != token


def test_wrong_master_key_raises_value_error():
    token = encrypt_secret("sk-x", "right-key")
    with pytest.raises(ValueError):
        decrypt_secret(token, "wrong-key")

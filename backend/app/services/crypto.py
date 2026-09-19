# BYO-key 加密存储（§C）：主密钥放 env（GATEWAY_SECRET），明文 key 只存在于内存
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def _fernet(secret: str) -> Fernet:
    # 任意主密钥字符串 → Fernet 需要的 32 字节 urlsafe key
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt_secret(plaintext: str, secret: str) -> str:
    return _fernet(secret).encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str, secret: str) -> str:
    try:
        return _fernet(secret).decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise ValueError("解密失败：主密钥不匹配或密文损坏") from e

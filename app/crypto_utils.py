"""事件订阅 Encrypt Key 的 AES-256-CBC 加解密（飞书规范）。

key = sha256(encrypt_key) 的十六进制串取前 32 字符，作为 32 字节密钥；
iv  = 该 32 字节密钥的前 16 字节；
传输格式：base64( ciphertext )，PKCS7 填充。
明文事件包体置于 {"encrypt": "..."}；URL 验证的 challenge 也走同一套。
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def _cipher_params(encrypt_key: str) -> tuple[bytes, bytes]:
    digest = hashlib.sha256(encrypt_key.encode("utf-8")).hexdigest()
    key = digest[:32].encode("utf-8")          # 32 字节
    iv = key[:16]                              # 前 16 字节作为 IV
    return key, iv


def decrypt_event(encrypt_key: str, cipher_text: str) -> str:
    raw = base64.b64decode(cipher_text)
    key, iv = _cipher_params(encrypt_key)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(raw) + decryptor.finalize()
    pad_len = padded[-1]
    if pad_len < 1 or pad_len > 16 or padded[-pad_len:] != bytes([pad_len]) * pad_len:
        raise ValueError("AES 解密后填充校验失败，请检查 Encrypt Key 是否正确")
    return padded[:-pad_len].decode("utf-8")


def encrypt_event(encrypt_key: str, plain_text: str) -> str:
    """把明文加密成 {"encrypt": ...} 里所需的 base64 串（用于自测/回显）。"""
    data = plain_text.encode("utf-8")
    pad_len = 16 - (len(data) % 16)
    data += bytes([pad_len]) * pad_len
    key, iv = _cipher_params(encrypt_key)
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(encryptor.update(data) + encryptor.finalize()).decode("ascii")

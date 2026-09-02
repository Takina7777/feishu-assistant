"""事件订阅 Encrypt Key 的 AES-256-CBC 解密（飞书规范）。

key = sha256(encrypt_key) 的十六进制串取前 32 字符，作为 32 字节密钥；
iv  = 该 32 字节密钥的前 16 字节；
明文按 PKCS7 填充、base64 编码后置于 {"encrypt": "..."}。
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def decrypt_event(encrypt_key: str, cipher_text: str) -> str:
    raw = base64.b64decode(cipher_text)
    digest = hashlib.sha256(encrypt_key.encode("utf-8")).hexdigest()
    key = digest[:32].encode("utf-8")          # 32 字节
    iv = key[:16]                              # 前 16 字节作为 IV
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(raw) + decryptor.finalize()
    pad_len = padded[-1]
    if pad_len < 1 or pad_len > 16 or padded[-pad_len:] != bytes([pad_len]) * pad_len:
        raise ValueError("AES 解密后填充校验失败，请检查 Encrypt Key 是否正确")
    return padded[:-pad_len].decode("utf-8")

"""Encrypt Key 加解密往返测试（飞书 AES-256-CBC 规范）。"""
from __future__ import annotations

import json

import pytest

from app.crypto_utils import decrypt_event, encrypt_event

KEY = "test-encrypt-key-abc"


def test_roundtrip_url_verification_payload():
    payload = json.dumps({"challenge": "ajls384kdj1234", "token": "t-xxxx", "type": "url_verification"})
    enc = encrypt_event(KEY, payload)
    assert decrypt_event(KEY, enc) == payload


def test_roundtrip_event_message():
    payload = json.dumps({"schema": "2.0", "header": {"event_type": "im.message.receive_v1"}, "event": {}})
    enc = encrypt_event(KEY, payload)
    assert decrypt_event(KEY, enc) == payload


def test_roundtrip_multi_block_chinese():
    payload = json.dumps({"text": "开通 张三 13800138000 部门=od-测试部门名" * 5}, ensure_ascii=False)
    enc = encrypt_event(KEY, payload)
    assert decrypt_event(KEY, enc) == payload


def test_wrong_key_fails():
    enc = encrypt_event(KEY, json.dumps({"challenge": "x"}))
    with pytest.raises(Exception):
        decrypt_event("another-key-123", enc)

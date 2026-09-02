"""动作执行器测试（run_action 与审批摘要）。"""
from __future__ import annotations

import pytest

from app import actions, lifecycle
from app.feishu_client import FeishuError

from conftest import make_cfg, zh_user


def test_query_ok(fake_client):
    fake_client.add(zh_user("ou_target", mobile="13800138000", name="张三"))
    r = actions.run_action("query", {"ident": "13800138000", "identity": "mobile"}, make_cfg(), fake_client)
    assert r.ok is True
    assert "张三" in r.text
    assert "正常" in r.text


def test_freeze_ok(fake_client, monkeypatch):
    monkeypatch.setattr(lifecycle, "_pace_freeze", lambda: None)
    fake_client.add(zh_user("ou_t", mobile="13800138000"))
    r = actions.run_action("freeze", {"ident": "13800138000", "identity": "mobile"}, make_cfg(), fake_client)
    assert r.ok is True
    assert "已停用" in r.text
    assert fake_client.patch_calls[-1][1] == {"is_frozen": True}


def test_unfreeze_frozen_user(fake_client, monkeypatch):
    monkeypatch.setattr(lifecycle, "_pace_freeze", lambda: None)
    fake_client.add(zh_user("ou_t", mobile="13800138000", frozen=True))
    r = actions.run_action("unfreeze", {"ident": "13800138000", "identity": "mobile"}, make_cfg(), fake_client)
    assert r.ok is True
    assert "已启用" in r.text


def test_unfreeze_active_user_noop(fake_client, monkeypatch):
    monkeypatch.setattr(lifecycle, "_pace_freeze", lambda: None)
    fake_client.add(zh_user("ou_t", mobile="13800138000", frozen=False))
    r = actions.run_action("unfreeze", {"ident": "13800138000", "identity": "mobile"}, make_cfg(), fake_client)
    assert r.ok is False
    assert "未被停用" in r.text


def test_delete_ok(fake_client):
    cfg = make_cfg()
    fake_client.add(zh_user("ou_t", mobile="13800138000"))
    r = actions.run_action("delete", {"ident": "13800138000", "identity": "mobile"}, cfg, fake_client)
    assert r.ok is True
    assert "已彻底删除" in r.text
    assert fake_client.delete_calls == ["ou_t"]


def test_action_error_not_found(fake_client):
    r = actions.run_action("query", {"ident": "13700000000", "identity": "mobile"}, make_cfg(), fake_client)
    assert r.ok is False
    assert "未找到" in r.text


def test_provision_action_ok(fake_client, monkeypatch):
    monkeypatch.setattr(lifecycle, "_pace_freeze", lambda: None)
    cfg = make_cfg()
    params = {"name": "张三", "mobile": "13800138000", "dept_open_id": "od-dept-1",
              "client_token": "tok-x"}
    r = actions.run_action("provision", params, cfg, fake_client)
    assert r.ok is True
    assert "开通成功" in r.text
    assert fake_client.created


def test_approval_summary_provision():
    cfg = make_cfg(default_department_open_id="od-default-dept")
    s = actions.approval_summary("provision", {"name": "张三", "mobile": "13800138000"}, cfg, None)
    assert "张三" in s
    assert "138****8000" in s
    assert "13800138000" not in s


def test_param_summary_masks_mobile():
    s = actions.param_summary("provision", {"name": "张三", "mobile": "13800138000", "email": "a@b.com"})
    assert "138****8000" in s
    assert "13800138000" not in s

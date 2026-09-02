"""审批管理器测试。"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.approval import ApprovalManager
from app.storage import APPROVED, EXPIRED, PENDING, REJECTED

from conftest import make_cfg


def make_manager(tmp_path, **overrides):
    cfg = make_cfg(**overrides)
    st = __import__("app.storage", fromlist=["Storage"]).Storage(tmp_path / "data")
    return cfg, st, ApprovalManager(cfg, st)


def test_submit_and_approve_runs(tmp_path):
    cfg, st, mgr = make_manager(tmp_path)
    entry = mgr.submit(kind="freeze", summary="目标：张三", params={"ident": "13800138000", "identity": "mobile"},
                       requester_open_id="ou_req")
    assert entry["status"] == PENDING

    calls = []
    out = mgr.decide(entry["id"], "ou_approver", approve=True,
                     run=lambda params: calls.append(params) or {"ok": True, "text": "已停用"})
    assert out["ok"] is True
    assert out["ran"] is True
    assert calls == [{"ident": "13800138000", "identity": "mobile"}]
    assert st.get_entry(entry["id"])["status"] == APPROVED

    # 重复审批
    out2 = mgr.decide(entry["id"], "ou_approver", approve=True, run=lambda p: {"ok": True, "text": ""})
    assert out2["ok"] is False
    assert "已处理" in out2["text"]


def test_non_approver_cannot_decide(tmp_path):
    cfg, st, mgr = make_manager(tmp_path)
    entry = mgr.submit(kind="delete", summary="x", params={}, requester_open_id="ou_req")
    out = mgr.decide(entry["id"], "ou_other", approve=True, run=lambda p: {"ok": True, "text": ""})
    assert out["ok"] is False
    assert "审批人" in out["text"]
    assert st.get_entry(entry["id"])["status"] == PENDING


def test_reject_does_not_run(tmp_path):
    cfg, st, mgr = make_manager(tmp_path)
    entry = mgr.submit(kind="delete", summary="x", params={}, requester_open_id="ou_req")
    ran = []
    out = mgr.decide(entry["id"], "ou_approver", approve=False,
                     run=lambda p: ran.append(1) or {"ok": True, "text": "x"})
    assert out["ok"] is True
    assert out.get("ran") is False
    assert ran == []
    assert st.get_entry(entry["id"])["status"] == REJECTED


def test_run_failure_recorded(tmp_path):
    cfg, st, mgr = make_manager(tmp_path)
    entry = mgr.submit(kind="delete", summary="x", params={}, requester_open_id="ou_req")
    out = mgr.decide(entry["id"], "ou_approver", approve=True,
                     run=lambda p: {"ok": False, "text": "权限不足"})
    assert out["ok"] is True
    assert "执行失败" in out["text"]
    assert "权限不足" in st.get_entry(entry["id"])["reply"]


def test_expired_before_decide(tmp_path):
    cfg, st, mgr = make_manager(tmp_path)
    entry = mgr.submit(kind="freeze", summary="x", params={}, requester_open_id="ou_req")
    old = (datetime.now().astimezone() - timedelta(days=3)).isoformat(timespec="seconds")
    st.update_entry(entry["id"], created_at=old)
    out = mgr.decide(entry["id"], "ou_approver", approve=True, run=lambda p: {"ok": True, "text": ""})
    assert out["ok"] is False
    assert "有效期" in out["text"]
    assert st.get_entry(entry["id"])["status"] == EXPIRED

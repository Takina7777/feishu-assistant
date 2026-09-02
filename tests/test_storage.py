"""存储层测试：审批单状态机 + 审计日志。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from app.storage import APPROVED, EXPIRED, PENDING, REJECTED, Storage


def make_storage(tmp_path):
    return Storage(tmp_path / "data")


def test_audit_roundtrip(tmp_path):
    st = make_storage(tmp_path)
    st.audit({"action": "freeze", "ok": True, "operator": "ou_a", "detail": "13800138000"})
    st.audit({"action": "provision", "ok": False, "operator": "ou_b", "detail": "张三"})
    rows = st.recent_audit(10)
    assert len(rows) == 2
    assert rows[0]["action"] == "provision"  # 新在前
    assert rows[1]["action"] == "freeze"
    assert "ts" in rows[0]


def test_approval_cas_protects_single_decision(tmp_path):
    st = make_store(tmp_path)
    entry = st.create_entry(kind="freeze", summary="目标：张三", params={"ident": "13800138000", "identity": "mobile"},
                            requester_open_id="ou_req")
    assert entry["status"] == PENDING

    # 第一次处理成功（谁来处理由上层 ApprovalManager 校验，storage 只保证幂等）
    ok, entry2 = st.transition(entry["id"], PENDING, status=APPROVED, decided_by="ou_approver")
    assert ok and entry2["status"] == APPROVED
    # 重复处理被 CAS 拒绝
    ok2, _ = st.transition(entry["id"], PENDING, status=REJECTED, decided_by="ou_approver")
    assert not ok2
    # 状态仍是第一次的结果
    assert st.get_entry(entry["id"])["status"] == APPROVED


def test_expiry(tmp_path):
    st = make_store(tmp_path)
    entry = st.create_entry(kind="delete", summary="x", params={}, requester_open_id="ou_r")
    # 把创建时间改到 2 天前
    old = (datetime.now().astimezone() - timedelta(days=2)).isoformat(timespec="seconds")
    st.update_entry(entry["id"], created_at=old)
    assert st.is_expired(st.get_entry(entry["id"]), ttl_minutes=1440)
    n = st.mark_expired(ttl_minutes=1440)
    assert n == 1
    assert st.get_entry(entry["id"])["status"] == EXPIRED


def test_prune(tmp_path):
    st = make_store(tmp_path)
    e1 = st.create_entry(kind="provision", summary="a", params={}, requester_open_id="ou_r")
    st.update_entry(e1["id"], status=APPROVED)
    old = (datetime.now().astimezone() - timedelta(days=60)).isoformat(timespec="seconds")
    st.update_entry(e1["id"], created_at=old)
    st.prune(keep_days=30)
    assert st.get_entry(e1["id"]) is None


def make_store(tmp_path):
    return Storage(tmp_path / "data")

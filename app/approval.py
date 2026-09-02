"""审批流：提交审批单 -> 卡片审批 -> 通过后执行。

执行器由外部注入（run(kind, params) -> ActionResult），本模块只负责状态机，
保证审批单只能被审批人处理一次。
"""
from __future__ import annotations

from typing import Any, Callable

from .config import Config
from .messages import ACTION_ZH
from .storage import APPROVED, EXPIRED, PENDING, REJECTED, Storage


class ApprovalManager:
    def __init__(self, cfg: Config, storage: Storage):
        self.cfg = cfg
        self.storage = storage

    # ------------------------------------------------------------------ #
    def submit(self, *, kind: str, summary: str, params: dict[str, Any],
               requester_open_id: str, requester_note: str = "") -> dict[str, Any]:
        """创建待审批单，返回 entry。"""
        self.storage.mark_expired(ttl_minutes=self.cfg.approval_ttl_minutes)
        return self.storage.create_entry(
            kind=kind,
            summary=summary,
            params=params,
            requester_open_id=requester_open_id,
            requester_note=requester_note,
        )

    def can_decide(self, entry: dict[str, Any], operator_open_id: str) -> tuple[bool, str]:
        if operator_open_id not in self.cfg.approver_open_ids:
            return False, "您不是该审批单的审批人"
        return True, ""

    def decide(self, entry_id: str, approver_open_id: str, approve: bool,
               run: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
        """审批人表态。approve=True 时立即执行（run 负责具体动作与异常兜底）。"""
        entry = self.storage.get_entry(entry_id)
        if entry is None:
            return {"ok": False, "entry": None, "text": f"审批单 {entry_id} 不存在"}
        if entry["status"] != PENDING:
            return {"ok": False, "entry": entry, "text": f"该审批单已处理（状态：{entry['status']}），无需重复操作"}

        can, reason = self.can_decide(entry, approver_open_id)
        if not can:
            return {"ok": False, "entry": entry, "text": f"⛔ {reason}（open_id：{approver_open_id}）"}

        # 过期检查（提交到现在的时长）
        if self.storage.is_expired(entry, ttl_minutes=self.cfg.approval_ttl_minutes):
            self.storage.transition(entry["id"], PENDING, status=EXPIRED, decided_at=entry.get("created_at"))
            return {"ok": False, "entry": entry, "text": "该审批单已超过有效期，已自动作废，请重新提交"}

        ok, updated = self.storage.transition(
            entry["id"], PENDING,
            status=APPROVED if approve else REJECTED,
            decided_by=approver_open_id,
        )
        if not ok:
            cur = self.storage.get_entry(entry_id)
            return {"ok": False, "entry": cur, "text": f"该审批单已被其他审批人处理（状态：{cur.get('status') if cur else '未知'}）"}

        if not approve:
            return {"ok": True, "entry": updated, "text": "审批已拒绝，未执行任何操作", "ran": False}

        # 通过 -> 执行
        try:
            result = run(updated["params"])
            ran = True
        except Exception as exc:  # noqa: BLE001 —— 执行兜底，任何异常都转为可读文案
            result = {"ok": False, "text": f"执行异常：{exc}"}
            ran = True
        text = result.get("text", "")
        if not result.get("ok"):
            text = f"⚠️ 执行失败：{text}"
        self.storage.update_entry(entry["id"], reply=text)
        return {"ok": True, "entry": updated, "text": text, "ran": ran, "result": result}


def kind_zh(kind: str) -> str:
    return ACTION_ZH.get(kind, kind)

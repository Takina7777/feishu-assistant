"""本地状态存储：审批单 + 审计日志（JSON/JSONL，进程内线程安全）。"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# 审批单状态
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED = "expired"
CANCELLED = "cancelled"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Storage:
    """审批单与审计日志的落盘实现。"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.approvals_path = self.data_dir / "approvals.json"
        self.audit_path = self.data_dir / "audit.jsonl"
        self._lock = threading.Lock()
        if not self.approvals_path.exists():
            _atomic_write_json(self.approvals_path, [])

    # ------------------------------------------------------------------ #
    # 审计日志
    # ------------------------------------------------------------------ #
    def audit(self, record: dict[str, Any]) -> None:
        record = {"ts": now_iso(), **record}
        with self._lock:
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def recent_audit(self, count: int = 10) -> list[dict[str, Any]]:
        try:
            with self._lock:
                with open(self.audit_path, "r", encoding="utf-8") as f:
                    lines = [ln for ln in f if ln.strip()]
        except FileNotFoundError:
            return []
        rows = []
        for ln in reversed(lines[-200:]):
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
            if len(rows) >= count:
                break
        return rows

    # ------------------------------------------------------------------ #
    # 审批单
    # ------------------------------------------------------------------ #
    def _load(self) -> list[dict[str, Any]]:
        try:
            with open(self.approvals_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _save(self, rows: list[dict[str, Any]]) -> None:
        _atomic_write_json(self.approvals_path, rows)

    def new_id(self) -> str:
        return f"op-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    def create_entry(self, *, kind: str, summary: str, params: dict[str, Any],
                     requester_open_id: str, requester_note: str = "") -> dict[str, Any]:
        entry = {
            "id": self.new_id(),
            "kind": kind,                       # provision / freeze / unfreeze / delete
            "summary": summary,                 # 给审批人看的一句话摘要
            "params": params,                   # 执行所需的全部参数（含幂等 client_token）
            "requester_open_id": requester_open_id,
            "requester_note": requester_note,
            "status": PENDING,
            "created_at": now_iso(),
            "decided_at": None,
            "decided_by": None,
            "reply": None,
        }
        with self._lock:
            rows = self._load()
            rows.append(entry)
            self._save(rows)
        return entry

    def get_entry(self, entry_id: str) -> dict[str, Any] | None:
        with self._lock:
            for e in self._load():
                if e["id"] == entry_id:
                    return e
        return None

    def update_entry(self, entry_id: str, **fields: Any) -> dict[str, Any] | None:
        with self._lock:
            rows = self._load()
            for e in rows:
                if e["id"] == entry_id:
                    e.update(fields)
                    self._save(rows)
                    return e
        return None

    def transition(self, entry_id: str, expected_status: str, **fields: Any) -> tuple[bool, dict[str, Any] | None]:
        """CAS：仅当 entry 处于 expected_status 时更新（防重复审批）。"""
        with self._lock:
            rows = self._load()
            for e in rows:
                if e["id"] == entry_id:
                    if e["status"] != expected_status:
                        return False, e
                    e.update(fields)
                    self._save(rows)
                    return True, e
        return False, None

    def mark_expired(self, ttl_minutes: int = 1440) -> int:
        """把超过有效期的 pending 标记为 expired；返回数量。"""
        with self._lock:
            rows = self._load()
            changed = 0
            for e in rows:
                if e["status"] == PENDING and self.is_expired(e, ttl_minutes=ttl_minutes):
                    e["status"] = EXPIRED
                    e["decided_at"] = now_iso()
                    changed += 1
            if changed:
                self._save(rows)
        return changed

    def is_expired(self, entry: dict[str, Any], ttl_minutes: int = 1440) -> bool:
        try:
            created = datetime.fromisoformat(entry["created_at"])
            return (datetime.now().astimezone() - created).total_seconds() > ttl_minutes * 60
        except (ValueError, TypeError):
            return False

    def recent_entries(self, count: int = 10) -> list[dict[str, Any]]:
        rows = self._load()
        return list(reversed(rows[-max(count, 1):]))

    def prune(self, keep_days: int = 30) -> None:
        """清理超过 keep_days 且已终结的审批单。"""
        cutoff = time.time() - keep_days * 86400
        with self._lock:
            rows = self._load()
            kept = []
            for e in rows:
                try:
                    created = datetime.fromisoformat(e["created_at"]).timestamp()
                except (ValueError, TypeError):
                    created = time.time()
                if e["status"] == PENDING or created >= cutoff:
                    kept.append(e)
            if len(kept) != len(rows):
                self._save(kept)

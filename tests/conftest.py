"""共享测试基件：配置与假飞书客户端。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config import Config
from app.feishu_client import FeishuError


def make_cfg(**overrides: Any) -> Config:
    base = dict(
        app_id="cli_test",
        app_secret="secret_test",
        host="127.0.0.1",
        port=8000,
        data_dir=Path("data-test"),
        default_department_open_id="od-default-dept",
        default_employee_type=1,
        operator_open_ids=["ou_operator"],
        approver_open_ids=["ou_approver"],
        provision_mode="direct",
        freeze_mode="direct",
        unfreeze_mode="direct",
        delete_mode="approval",
        approval_ttl_minutes=1440,
    )
    base.update(overrides)
    return Config(**base)


def zh_user(open_id: str, *, name: str = "张三", mobile: str = "13800138000",
            email: str = "zs@corp.com", employee_no: str = "1001",
            frozen: bool = False, resigned: bool = False, activated: bool = True,
            unjoin: bool = False, exited: bool = False) -> dict[str, Any]:
    return {
        "open_id": open_id,
        "user_id": "u_" + open_id,
        "name": name,
        "mobile": mobile,
        "email": email,
        "employee_no": employee_no,
        "department_ids": ["od-dept-1"],
        "job_title": "",
        "status": {
            "is_frozen": frozen,
            "is_resigned": resigned,
            "is_activated": activated,
            "is_exited": exited,
            "is_unjoin": unjoin,
        },
    }


class FakeClient:
    """模拟 FeishuClient：按内存字典操作，记录调用。"""

    def __init__(self, users: dict[str, dict[str, Any]] | None = None):
        self.users: dict[str, dict[str, Any]] = users or {}
        self.created: list[dict[str, Any]] = []
        self.patch_calls: list[tuple[str, dict[str, Any]]] = []
        self.delete_calls: list[str] = []
        self.batch_queries: list[dict[str, Any]] = []
        self.fail_batch = False

    # ---- 辅助 ----
    def add(self, user: dict[str, Any]) -> None:
        self.users[user["open_id"]] = user

    def _match(self, user: dict[str, Any], key: str, value: str) -> bool:
        return str(user.get(key) or "").replace(" ", "") == value.replace(" ", "")

    # ---- FeishuClient 同构接口 ----
    def batch_get_user_ids(self, mobiles=None, emails=None, include_resigned=True):
        self.batch_queries.append({"mobiles": mobiles, "emails": emails, "include_resigned": include_resigned})
        if self.fail_batch:
            raise FeishuError(99991672, "permission denied")
        out = []
        wanted: list[tuple[str, str]] = []
        if mobiles:
            wanted += [("mobile", m) for m in mobiles]
        if emails:
            wanted += [("email", e) for e in emails]
        for key, value in wanted:
            for user in self.users.values():
                if self._match(user, key, value):
                    out.append({"user_id": user["open_id"], key: value, "status": user.get("status")})
                    break
        return out

    def get_user(self, open_id: str, department_id_type: str = "open_department_id"):
        user = self.users.get(open_id)
        if user is None:
            raise FeishuError(99991661, "user not found")
        return user

    def create_user(self, *, name, mobile=None, email=None, department_open_ids,
                    employee_type=1, employee_no=None, job_title=None, en_name=None,
                    client_token=None):
        open_id = "ou_new_" + str(len(self.created) + 1)
        user = zh_user(open_id, name=name, mobile=mobile or "", email=email or "",
                       employee_no=employee_no or "")
        user["department_ids"] = department_open_ids
        self.add(user)
        self.created.append(user)
        return dict(user)

    def patch_user(self, open_id: str, fields: dict[str, Any]):
        user = self.users.get(open_id)
        if user is None:
            raise FeishuError(99991661, "user not found")
        self.patch_calls.append((open_id, fields))
        if "is_frozen" in fields:
            user["status"]["is_frozen"] = fields["is_frozen"]
        user.update(fields)
        return dict(user)

    def delete_user(self, open_id: str, acceptor_open_id=None):
        user = self.users.get(open_id)
        if user is None:
            raise FeishuError(99991661, "user not found")
        self.delete_calls.append(open_id)
        user["status"]["is_resigned"] = True
        user["status"]["is_activated"] = False


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()

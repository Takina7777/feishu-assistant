"""飞书开放平台 API 客户端（通讯录 + 消息发送）。

仅依赖 requests。所有调用统一走 tenant_access_token。
约定：代码内部一律使用 open_id 作为用户标识，所有 contact 接口 user_id_type=open_id。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

import requests

# 常用通讯录错误码 -> 用户可读的中文提示
CONTACT_ERR_HINTS: dict[int, str] = {
    41001: "手机号已存在（租户内唯一），请核对是否已有人使用，或先“查询/启用”该账号",
    41002: "邮箱已存在（租户内唯一），请核对是否已有人使用",
    41003: "该手机号/邮箱关联了其他飞书账号，存在账号冲突，无法添加",
    41004: "手机号格式不合法",
    41005: "邮箱格式不合法",
    41006: "缺少用户姓名",
    41007: "未认证租户成员数已达上限",
    41008: "租户成员数已达席位上限，无法创建",
    41009: "邮箱和手机号不能都为空",
    41010: "创建用户必须提供手机号（如需仅邮箱创建，请先在飞书验证支持后调整代码）",
    41011: "自定义 user_id 重复",
    41014: "姓名疑似包含敏感信息，被拦截",
    41017: "必须提供所属部门",
    41053: "该用户已存在",
    41059: "员工类型取值无效",
    41060: "员工类型未在企业内启用",
    44010: "该成员尚未接受加入邀请（待加入状态），无法停用/更新。请等对方接受邀请后再试；如需取消该成员，请改用“删除”",
    44011: "禁止更新已主动退出企业的用户信息",
    44019: "未认证企业仅支持中国大陆手机号",
    44020: "已认证企业添加非大陆手机号时必须同时提供邮箱",
    44037: "租户管理员不允许被删除，请先在管理后台移除其管理员身份",
    44046: "当前租户为多许可证模式，创建用户必须分配席位（subscription_ids），请联系企业管理员配置",
    44050: "应用未开通“分配用户席位”权限",
    44051: "工号重复，请修改",
    44062: "根据租户配置，该成员只能通过生命周期引擎处理，无法用 API 删除",
    99991672: "应用无权限调用该接口，请检查权限管理中的 API 权限并发布版本",
    99991661: "访问令牌无效或已过期",
}


class FeishuError(Exception):
    """飞书 API 返回的业务错误。code=0 的响应视为成功。"""

    def __init__(self, code: int, msg: str, http: int = 200, request_id: str = ""):
        self.code = code
        self.msg = msg or ""
        self.http = http
        self.request_id = request_id
        super().__init__(f"[{code}] {self.msg} (http={http}, request_id={request_id})")

    def friendly(self) -> str:
        hint = CONTACT_ERR_HINTS.get(self.code)
        base = f"飞书返回错误码 {self.code}：{self.msg or '未知错误'}"
        if hint:
            base += f"\n提示：{hint}"
        if self.request_id:
            base += f"\nRequest ID：{self.request_id}（排查时可提供给管理员）"
        return base


class FeishuClient:
    """轻量飞书开放平台客户端。"""

    def __init__(self, app_id: str, app_secret: str, api_base: str = "https://open.feishu.cn/open-apis",
                 timeout: float = 15.0):
        self.app_id = app_id
        self.app_secret = app_secret
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._token: str = ""
        self._token_expire_at: float = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 令牌
    # ------------------------------------------------------------------ #
    def tenant_access_token(self, force: bool = False) -> str:
        with self._lock:
            if not force and self._token and time.time() < self._token_expire_at:
                return self._token
            resp = self._session.post(
                f"{self.api_base}/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=self.timeout,
            )
            try:
                data = resp.json()
            except ValueError:
                raise FeishuError(-1, f"获取 tenant_access_token 失败：HTTP {resp.status_code}", http=resp.status_code)
            if data.get("code") != 0 or not data.get("tenant_access_token"):
                raise FeishuError(data.get("code", -1), data.get("msg", "获取令牌失败"), http=resp.status_code)
            # 默认有效期 7200s，提前 300s 刷新
            expire = int(data.get("expire", 7200))
            self._token = data["tenant_access_token"]
            self._token_expire_at = time.time() + max(expire - 300, 60)
            return self._token

    # ------------------------------------------------------------------ #
    # 通用请求
    # ------------------------------------------------------------------ #
    def _request(self, method: str, path: str, *, query: dict[str, Any] | None = None,
                 body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.api_base}{path}"
        headers = {"Authorization": f"Bearer {self.tenant_access_token()}"}
        if body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        resp = self._session.request(
            method, url, params=query, json=body, headers=headers, timeout=self.timeout
        )
        try:
            data = resp.json()
        except ValueError:
            raise FeishuError(-1, f"响应非 JSON：HTTP {resp.status_code}", http=resp.status_code)

        code = data.get("code", -1)
        if code != 0:
            # 令牌失效时自动刷新重试一次
            if code == 99991663 and method in ("GET", "PUT", "PATCH", "DELETE", "POST"):
                self.tenant_access_token(force=True)
                return self._request(method, path, query=query, body=body)
            err = FeishuError(code, data.get("msg", ""), http=resp.status_code,
                              request_id=resp.headers.get("X-Request-Id", ""))
            raise err
        return data.get("data") or {}

    # ------------------------------------------------------------------ #
    # 通讯录：用户
    # ------------------------------------------------------------------ #
    def batch_get_user_ids(self, mobiles: list[str] | None = None, emails: list[str] | None = None,
                           include_resigned: bool = True) -> list[dict[str, Any]]:
        """通过手机号/邮箱批量获取用户 ID（user_id_type=open_id），返回 user_list。"""
        body: dict[str, Any] = {"include_resigned": include_resigned}
        if mobiles:
            body["mobiles"] = mobiles
        if emails:
            body["emails"] = emails
        data = self._request(
            "POST", "/contact/v3/users/batch_get_id",
            query={"user_id_type": "open_id"}, body=body,
        )
        return data.get("user_list", [])

    def get_user(self, open_id: str, department_id_type: str = "open_department_id") -> dict[str, Any]:
        """获取单个用户信息（user_id_type=open_id）。"""
        data = self._request(
            "GET", f"/contact/v3/users/{open_id}",
            query={"user_id_type": "open_id", "department_id_type": department_id_type},
        )
        return data.get("user") or {}

    def create_user(self, *, name: str, mobile: str | None = None, email: str | None = None,
                    department_open_ids: list[str], employee_type: int = 1,
                    employee_no: str | None = None, job_title: str | None = None,
                    en_name: str | None = None, client_token: str | None = None) -> dict[str, Any]:
        """创建成员（相当于入职开通）。返回创建的 user。"""
        body: dict[str, Any] = {
            "name": name,
            "department_ids": department_open_ids,
            "employee_type": employee_type,
        }
        if mobile:
            body["mobile"] = mobile
        if email:
            body["email"] = email
        if employee_no:
            body["employee_no"] = employee_no
        if job_title:
            body["job_title"] = job_title
        if en_name:
            body["en_name"] = en_name
        query: dict[str, Any] = {"user_id_type": "open_id", "department_id_type": "open_department_id"}
        if client_token:
            query["client_token"] = client_token
        data = self._request("POST", "/contact/v3/users", query=query, body=body)
        return data.get("user") or {}

    def patch_user(self, open_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """部分更新成员（含 is_frozen 冻结/解冻）。department_ids/is_frozen 限 1 QPS。"""
        data = self._request(
            "PATCH", f"/contact/v3/users/{open_id}",
            query={"user_id_type": "open_id", "department_id_type": "open_department_id"},
            body=fields,
        )
        return data.get("user") or {}

    def delete_user(self, open_id: str, acceptor_open_id: str | None = None) -> None:
        """删除成员（离职）。acceptor_open_id 为空时由飞书默认转交资源（直属上级）。"""
        body: dict[str, Any] = {}
        if acceptor_open_id:
            body = {
                "department_chat_acceptor_user_id": acceptor_open_id,
                "external_chat_acceptor_user_id": acceptor_open_id,
                "docs_acceptor_user_id": acceptor_open_id,
                "calendar_acceptor_user_id": acceptor_open_id,
                "application_acceptor_user_id": acceptor_open_id,
                "minutes_acceptor_user_id": acceptor_open_id,
            }
        self._request(
            "DELETE", f"/contact/v3/users/{open_id}",
            query={"user_id_type": "open_id"}, body=body,
        )

    # ------------------------------------------------------------------ #
    # 消息
    # ------------------------------------------------------------------ #
    def send_message(self, receive_id: str, msg_type: str, content: dict[str, Any],
                     receive_id_type: str = "open_id") -> dict[str, Any]:
        """发送消息。receive_id_type: open_id(单聊) / chat_id(群聊)。"""
        return self._request(
            "POST", "/im/v1/messages",
            query={"receive_id_type": receive_id_type},
            body={
                "receive_id": receive_id,
                "msg_type": msg_type,
                "content": json.dumps(content, ensure_ascii=False),
            },
        )

    def send_text(self, receive_id: str, text: str, receive_id_type: str = "open_id") -> dict[str, Any]:
        return self.send_message(receive_id, "text", {"text": text}, receive_id_type)

    def send_card(self, receive_id: str, card: dict[str, Any], receive_id_type: str = "open_id") -> dict[str, Any]:
        return self.send_message(receive_id, "interactive", card, receive_id_type)


def new_client() -> FeishuClient:
    from .config import load_config
    cfg = load_config()
    return FeishuClient(cfg.app_id, cfg.app_secret, api_base=cfg.api_base)


def gen_client_token() -> str:
    """生成幂等 client_token（防止重复创建）。"""
    return uuid.uuid4().hex

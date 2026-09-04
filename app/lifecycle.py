"""账号生命周期业务：解析身份、开通、停用/启用、彻底删除、查询。

所有对外函数返回供 UI 使用的结构化结果；错误以 LifecycleError/FeishuError 抛出。
本模块保持无状态（client 由调用方传入），便于单测与替换。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .feishu_client import FeishuClient, FeishuError, gen_client_token
from .parser import classify_identity

# is_frozen / department_ids 的 PATCH 限 1 QPS：全局串行 + 间隔限速
_FREEZE_LOCK = threading.Lock()
_FREEZE_INTERVAL = 1.0
_last_patch_monotonic: float = 0.0


def _pace_freeze() -> None:
    """保证两次冻结/解冻 PATCH 之间至少间隔 1 秒。"""
    global _last_patch_monotonic
    now = time.monotonic()
    wait = _last_patch_monotonic + _FREEZE_INTERVAL - now
    if wait > 0:
        time.sleep(wait)
    _last_patch_monotonic = time.monotonic()


class LifecycleError(Exception):
    """业务规则错误，message 可直接展示给用户。"""


@dataclass
class Person:
    """从飞书 user 对象抽取的核心摘要。"""
    open_id: str = ""
    user_id: str = ""
    name: str = ""
    en_name: str = ""
    mobile: str = ""
    email: str = ""
    employee_no: str = ""
    department_ids: list[str] = field(default_factory=list)
    job_title: str = ""
    # 状态
    is_frozen: bool | None = None
    is_resigned: bool | None = None
    is_activated: bool | None = None
    is_exited: bool | None = None
    is_unjoin: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_user(cls, user: dict[str, Any]) -> "Person":
        status = user.get("status") or {}
        # 兼容 status 嵌套 与 顶层 is_frozen（部分返回结构差异）
        def _st(*names: str) -> bool | None:
            for n in names:
                if n in status and status[n] is not None:
                    return bool(status[n])
                if n in user and user[n] is not None:
                    return bool(user[n])
            return None

        return cls(
            open_id=user.get("open_id", ""),
            user_id=user.get("user_id", ""),
            name=user.get("name", ""),
            en_name=user.get("en_name", ""),
            mobile=user.get("mobile", ""),
            email=user.get("email", ""),
            employee_no=user.get("employee_no", ""),
            department_ids=user.get("department_ids") or [],
            job_title=user.get("job_title", ""),
            is_frozen=_st("is_frozen"),
            is_resigned=_st("is_resigned"),
            is_activated=_st("is_activated"),
            is_exited=_st("is_exited"),
            is_unjoin=_st("is_unjoin"),
            raw=user,
        )

    @property
    def state_label(self) -> str:
        if self.is_resigned:
            return "已离职（账号已删除）"
        if self.is_exited:
            return "已主动退出企业"
        if self.is_unjoin:
            return "待加入（尚未接受邀请）"
        if self.is_frozen:
            return "已停用（冻结中，无法登录）"
        if self.is_activated is False:
            return "未激活"
        return "正常（在职可用）"

    def line(self) -> str:
        parts = [f"👤 {self.name or '-'}"]
        if self.mobile:
            parts.append(f"手机 {mask_mobile(self.mobile)}")
        if self.email:
            parts.append(f"邮箱 {self.email}")
        if self.employee_no:
            parts.append(f"工号 {self.employee_no}")
        parts.append(f"状态：{self.state_label}")
        return " ｜ ".join(parts)


def mask_mobile(m: str) -> str:
    if len(m) == 11:
        return m[:3] + "****" + m[-4:]
    if m.startswith("+") and len(m) > 6:
        return m[:3] + "****" + m[-3:]
    return m


def _describe_provision_params(p: dict[str, str], cfg: Config) -> str:
    dept = p.get("dept_open_id") or cfg.default_department_open_id or "（未配置默认部门！）"
    bits = [f"姓名：{p.get('name', '')}"]
    if p.get("mobile"):
        bits.append(f"手机：{mask_mobile(p['mobile'])}")
    if p.get("email"):
        bits.append(f"邮箱：{p['email']}")
    if p.get("employee_no"):
        bits.append(f"工号：{p['employee_no']}")
    bits.append(f"部门：{dept}")
    return "\n".join(bits)


# ---------------------------------------------------------------------- #
# 身份解析
# ---------------------------------------------------------------------- #
def resolve_person(client: FeishuClient, ident: str, ident_kind: str | None = None) -> Person:
    """把 手机号/邮箱/open_id 解析为成员摘要；找不到抛 LifecycleError。"""
    kind = ident_kind or classify_identity(ident)
    if kind in ("mobile", "email"):
        mobiles = [ident] if kind == "mobile" else None
        emails = [ident] if kind == "email" else None
        try:
            result = client.batch_get_user_ids(mobiles=mobiles, emails=emails, include_resigned=True)
        except FeishuError as e:
            raise LifecycleError(
                f"查询账号失败（需要应用开通“通过手机号或邮箱获取用户ID”权限）。{e.friendly()}"
            )
        if not result:
            raise LifecycleError(
                f"未找到使用 {kind_label(kind)} {ident} 的成员。请核对后重试，或先执行“开通”。"
            )
        open_id = result[0].get("user_id")
        if not open_id:
            raise LifecycleError("查询结果缺少 open_id，请确认应用已开通“读取通讯录”相关权限。")
    else:
        open_id = ident
    try:
        user = client.get_user(open_id)
    except FeishuError as e:
        raise LifecycleError(f"获取成员详情失败。{e.friendly()}")
    if not user.get("open_id") and not user.get("name"):
        raise LifecycleError("未获取到该成员的详情（可能不在应用的通讯录权限范围内）。")
    return Person.from_user(user)


def kind_label(kind: str) -> str:
    return {"mobile": "手机号", "email": "邮箱", "open_id": "open_id"}.get(kind, kind)


def resolve_from_batch(client: FeishuClient, mobiles: list[str] | None = None,
                       emails: list[str] | None = None) -> list[dict[str, Any]]:
    return client.batch_get_user_ids(mobiles=mobiles, emails=emails, include_resigned=True)


# ---------------------------------------------------------------------- #
# 开通（创建成员）
# ---------------------------------------------------------------------- #
def check_conflict(client: FeishuClient, *, mobile: str | None = None,
                   email: str | None = None) -> list[dict[str, Any]]:
    """开通前查重：返回命中的已存在用户列表（含离职）。"""
    mobiles = [mobile] if mobile else None
    emails = [email] if email else None
    if not mobiles and not emails:
        return []
    try:
        return resolve_from_batch(client, mobiles=mobiles, emails=emails)
    except FeishuError:
        return []


def provision(client: FeishuClient, params: dict[str, Any], cfg: Config,
              client_token: str | None = None) -> Person:
    """执行开通。params: name/mobile/email/dept_open_id/employee_no/job_title/en_name/employee_type"""
    mobile = params.get("mobile") or None
    email = params.get("email") or None
    name = params.get("name", "").strip()
    if not name:
        raise LifecycleError("缺少姓名。")

    conflicts = check_conflict(client, mobile=mobile, email=email)
    if conflicts:
        first = conflicts[0]
        open_id = first.get("user_id")
        person: Person | None = None
        if open_id:
            try:
                person = resolve_person(client, open_id, "open_id")
            except LifecycleError:
                person = None
        if person is None:
            # 查询不到详情（通常是离职/不可见用户），但联系方式已被占用
            raise LifecycleError(
                "该手机号/邮箱已绑定企业内其他成员（可能已离职），无法直接创建新账号。\n"
                "处理方式：请在 管理后台 > 成员与部门 > 离职成员/回收站 中处理原账号后重试，或核对联系方式是否正确。"
            )
        has_status = any(v is not None for v in (
            person.is_resigned, person.is_frozen,
            person.is_activated, person.is_exited, person.is_unjoin,
        ))
        if person.is_resigned:
            raise LifecycleError(
                f"手机号/邮箱已被离职成员 {person.name or ''} 占用，无法直接创建新账号。\n"
                "处理方式（二选一）：\n"
                "· 若此人要回归：请在 管理后台 → 成员与部门 → 离职成员 中点击“恢复”，原账号即可正常使用，无需重新开通；\n"
                "· 若要为其建立全新账号：需先彻底清理/注销离职记录以释放手机号，再执行“开通”。"
            )
        if person.is_unjoin:
            raise LifecycleError(
                f"{person.name or ''} 已存在但尚未接受加入邀请（待加入状态）。\n"
                "请等待其接受邀请后再使用，或先发送“删除”取消该邀请后重新开通。"
            )
        if person.is_frozen:
            raise LifecycleError(
                f"{person.name} 已存在但处于停用状态（手机号/邮箱一致）。如需复用请先发送：启用 {mobile or email}"
            )
        if has_status:
            raise LifecycleError(
                f"{person.name} 已是在职成员（手机号/邮箱一致），无需重复开通。可用“查询 {mobile or email}”查看详情。"
            )
        # 状态字段缺失（未开通“获取用户受雇信息”字段权限）时无法区分在职/离职
        raise LifecycleError(
            f"手机号/邮箱已关联成员 {person.name or ''}，但暂时无法读取其账号状态"
            "（应用缺少“获取用户受雇信息(contact:user.employee:readonly)”字段权限）。\n"
            "请先在 管理后台 → 成员与部门 核实该成员状态：\n"
            "· 已离职且要其回归 → 在“离职成员”中点击“恢复”，原账号即可使用，无需重新开通；\n"
            "· 要新建账号 → 需先释放其手机号/邮箱（注销或彻底清理离职记录）后再执行“开通”。\n"
            "建议为应用开通上述字段权限，机器人即可自动判断状态。"
        )

    dept = params.get("dept_open_id") or cfg.default_department_open_id
    if not dept:
        raise LifecycleError(
            "尚未配置默认部门：请在 .env 中设置 DEFAULT_DEPARTMENT_OPEN_ID（od- 开头），\n"
            "或在命令中传 部门=od-xxx。"
        )
    employee_type = int(params.get("employee_type") or cfg.default_employee_type)
    token = client_token or gen_client_token()

    try:
        user = client.create_user(
            name=name,
            mobile=mobile,
            email=email,
            department_open_ids=[dept],
            employee_type=employee_type,
            employee_no=params.get("employee_no") or None,
            job_title=params.get("job_title") or None,
            en_name=params.get("en_name") or None,
            client_token=token,
        )
    except FeishuError as e:
        raise LifecycleError(f"开通失败。{e.friendly()}")
    if not user:
        raise LifecycleError("开通接口未返回成员信息，请稍后使用“查询”确认结果。")
    return Person.from_user(user)


# ---------------------------------------------------------------------- #
# 停用 / 启用 / 彻底删除
# ---------------------------------------------------------------------- #
def _patch_frozen(client: FeishuClient, open_id: str, frozen: bool) -> Person:
    with _FREEZE_LOCK:
        try:
            client.patch_user(open_id, {"is_frozen": frozen})
            _pace_freeze()
        except FeishuError as e:
            if e.code == 44036:
                raise LifecycleError("不允许冻结租户创建者/超级管理员。")
            raise LifecycleError(f"{'停用' if frozen else '启用'}失败。{e.friendly()}")
    try:
        return Person.from_user(client.get_user(open_id))
    except FeishuError:
        return resolve_person(client, open_id, "open_id")


def freeze(client: FeishuClient, open_id: str) -> Person:
    return _patch_frozen(client, open_id, True)


def unfreeze(client: FeishuClient, open_id: str) -> Person:
    return _patch_frozen(client, open_id, False)


def deprovision(client: FeishuClient, open_id: str, cfg: Config) -> None:
    """彻底删除（离职）。资源转交给配置的接收人或由飞书默认处理。"""
    acceptor = cfg.delete_acceptor_open_id or None
    try:
        client.delete_user(open_id, acceptor_open_id=acceptor)
    except FeishuError as e:
        raise LifecycleError(f"删除失败。{e.friendly()}")


def provision_summary(params: dict[str, Any], cfg: Config) -> str:
    return _describe_provision_params(params, cfg)

"""动作执行器：把统一参数转成真实飞书调用，并产出用户可读结果文案。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import lifecycle
from .config import Config
from .feishu_client import FeishuClient
from .lifecycle import LifecycleError, Person
from .messages import status_text


@dataclass
class ActionResult:
    ok: bool
    text: str
    person: Person | None = None
    kind: str = ""
    detail: str = ""


def _err(e: Exception, kind: str) -> ActionResult:
    return ActionResult(ok=False, text=str(e) or e.__class__.__name__, kind=kind,
                        detail=str(e).splitlines()[0] if str(e) else "")


def run_action(kind: str, params: dict[str, Any], cfg: Config, client: FeishuClient) -> ActionResult:
    """统一入口：kind ∈ provision/freeze/unfreeze/delete/query。参数见各分支。"""
    if kind == "provision":
        return _do_provision(params, cfg, client)
    if kind in ("freeze", "unfreeze", "delete", "query"):
        return _do_identity_action(kind, params, cfg, client)
    return _err(ValueError(f"未知操作类型：{kind}"), kind)


# ---------------------------------------------------------------------- #
def _do_provision(params: dict[str, Any], cfg: Config, client: FeishuClient) -> ActionResult:
    try:
        person = lifecycle.provision(client, params, cfg, client_token=params.get("client_token"))
    except Exception as e:  # noqa: BLE001
        return _err(e, "provision")

    contact = params.get("mobile") or params.get("email") or "-"
    if not person.open_id:
        person.open_id = params.get("open_id", "")
    lines = [
        "✅ 开通成功，账号已创建",
        f"姓名：{person.name or params.get('name')}",
        f"联系方式：{params.get('mobile', '-')} / {params.get('email', '-')}",
        f"部门：{params.get('dept_open_id') or cfg.default_department_open_id}",
    ]
    if person.open_id:
        lines.append(f"open_id：{person.open_id}")
    lines.append(f"系统已向 {contact} 发送加入邀请，对方同意后即可使用飞书。")
    text = "\n".join(lines)
    return ActionResult(ok=True, text=text, person=person, kind="provision",
                        detail=f"{params.get('name', '')} {contact}")


def _do_identity_action(kind: str, params: dict[str, Any], cfg: Config, client: FeishuClient) -> ActionResult:
    ident = params.get("ident", "")
    identity = params.get("identity", "")
    try:
        person = lifecycle.resolve_person(client, ident, identity)
    except Exception as e:  # noqa: BLE001
        return _err(e, kind)

    open_id = person.open_id
    try:
        if kind == "query":
            text = f"📋 查询结果\n{status_text(person)}"
            return ActionResult(ok=True, text=text, person=person, kind=kind,
                                detail=f"{person.name or ident}")
        if kind == "freeze":
            if person.is_frozen:
                return ActionResult(ok=False, text=f"{person.name} 当前已是停用状态，无需重复操作。", person=person,
                                    kind=kind, detail=f"{person.name}")
            if person.is_unjoin:
                return ActionResult(
                    ok=False,
                    text=f"{person.name} 尚未接受加入邀请（待加入状态），无法停用。\n"
                         "请等待对方接受邀请后再发送：停用 <手机号/邮箱>；\n"
                         "如需取消该未入职成员，请改用：删除 <手机号/邮箱>（默认走审批）。",
                    person=person, kind=kind, detail=f"{person.name}（待加入）",
                )
            person = lifecycle.freeze(client, open_id)
            text = (f"✅ 已停用账号：{person.name}\n"
                    f"该成员现已无法登录飞书（账号保留，可随时恢复）。\n"
                    f"如需恢复，发送：启用 {ident}")
        elif kind == "unfreeze":
            if person.is_frozen is False and not person.is_resigned:
                return ActionResult(ok=False, text=f"{person.name} 当前未被停用，无需启用。", person=person,
                                    kind=kind, detail=f"{person.name}")
            if person.is_resigned:
                return ActionResult(
                    ok=False,
                    text=f"{person.name} 已离职（账号已删除），无法通过“启用”恢复。\n"
                         "请管理员在 管理后台 > 成员与部门 > 离职成员 中恢复，或重新“开通”。",
                    person=person, kind=kind, detail=f"{person.name}",
                )
            person = lifecycle.unfreeze(client, open_id)
            text = f"✅ 已启用账号：{person.name}\n该成员现已恢复正常使用。"
        elif kind == "delete":
            if person.is_resigned:
                return ActionResult(ok=False, text=f"{person.name} 已处于离职状态，无需重复删除。", person=person,
                                    kind=kind, detail=f"{person.name}")
            lifecycle.deprovision(client, open_id, cfg)
            acceptor = cfg.delete_acceptor_open_id or "其直属上级"
            text = (f"✅ 已彻底删除（离职）：{person.name}\n"
                    f"该成员账号已从通讯录移除，无法再登录。\n"
                    f"名下文档/日程/群主/应用等资源将转交给：{acceptor}。\n"
                    "如为误操作，请管理员尽快在管理后台 > 成员与部门 > 离职成员/回收站 中恢复。")
        else:  # pragma: no cover
            return _err(ValueError(f"未知操作：{kind}"), kind)
    except Exception as e:  # noqa: BLE001
        return _err(e, kind)
    return ActionResult(ok=True, text=text, person=person, kind=kind,
                        detail=f"{person.name or ident} ({ident})")


# ---------------------------------------------------------------------- #
# 审批摘要
# ---------------------------------------------------------------------- #
def approval_summary(kind: str, params: dict[str, Any], cfg: Config, client: FeishuClient) -> str:
    """生成审批卡片上给审批人看的内容摘要。"""
    if kind == "provision":
        return lifecycle.provision_summary(params, cfg)
    ident = params.get("ident", "")
    identity = params.get("identity", "")
    try:
        person = lifecycle.resolve_person(client, ident, identity)
        head = f"目标：{person.name}（当前状态：{person.state_label}）"
        extras = []
        if person.employee_no:
            extras.append(f"工号：{person.employee_no}")
        if person.mobile:
            extras.append(f"手机：{person.mobile[:3]}****{person.mobile[-4:] if len(person.mobile) == 11 else ''}")
        note = params.get("note")
        body = head + ("\n" + "｜".join(extras) if extras else "")
        if note:
            body += f"\n备注：{note}"
        return body
    except Exception:  # noqa: BLE001 —— 审批时解析不到也允许（执行时会再次解析）
        return f"目标：{ident}（{identity}）"


def param_summary(kind: str, params: dict[str, Any]) -> str:
    """审计日志用的简要目标描述（脱敏）。"""
    if kind == "provision":
        name = params.get("name", "")
        mobile = params.get("mobile", "")
        if mobile:
            mobile = mobile[:3] + "****" + mobile[-4:]
        email = params.get("email", "")
        return f"{name} {mobile} {email}".strip()
    return str(params.get("ident", ""))

"""消息/卡片构造：纯函数生成飞书文本消息与交互卡片 JSON。"""
from __future__ import annotations

from typing import Any

ACTION_ZH: dict[str, str] = {
    "provision": "开通账号",
    "freeze": "停用账号",
    "unfreeze": "启用账号",
    "restore": "恢复离职成员（重新在职）",
    "delete": "彻底删除账号（离职）",
    "query": "查询账号",
    "logs": "查看日志",
    "help": "帮助",
    "whoami": "获取我的ID",
}

HELP_TEXT = (
    "🤖 账号开通/回收机器人\n"
    "我是帮你操作飞书成员账号的机器人，请在单聊中向我发送指令：\n\n"
    "━━ 开通 ━━\n"
    "开通 姓名 手机号 [邮箱] [部门=od-xxx] [工号=xxx] [职务=xxx] [英文名=xxx]\n"
    "· 例：开通 张三 13800138000 zhangsan@corp.com 部门=od-xxxx 工号=1001\n"
    "· 部门不填则用 .env 中 DEFAULT_DEPARTMENT_OPEN_ID\n\n"
    "━━ 停用 / 启用（可恢复）━━\n"
    "停用 手机号/邮箱/open_id　例：停用 13800138000\n"
    "启用 手机号/邮箱/open_id　例：启用 13800138000\n\n"
    "━━ 恢复离职成员 ━━\n"
    "恢复 手机号/邮箱/open_id　例：恢复 13800138000\n"
    "· 将已离职(删除)成员恢复为在职；需企业商业专业版及以上、离职 30 天内\n"
    "· 需权限：恢复离职员工(directory:employee.resurrect:write)\n\n"
    "━━ 彻底删除（离职，默认需审批）━━\n"
    "删除 手机号/邮箱/open_id　例：删除 zhangsan@corp.com 离职交接\n"
    "· 会删除该成员账号并转交其文档/日程等资源（默认转给直属上级）\n\n"
    "━━ 其他 ━━\n"
    "查询 手机号/邮箱/open_id —— 查看成员与账号状态\n"
    "日志 —— 最近 10 条操作记录（日志 20 可看 20 条）\n"
    "我的ID —— 获取你本人的 open_id（用于配置 OPERATOR_OPEN_IDS）\n\n"
    "⚠️ 开通/删除/恢复等操作将真实调用飞书 API，请谨慎使用。"
)


def status_text(person: Any) -> str:
    """成员状态摘要文本。person 为 lifecycle.Person。"""
    from .lifecycle import mask_mobile

    lines = [f"👤 {person.name or '未知'}", f"状态：{person.state_label}"]
    if person.mobile:
        lines.append(f"手机号：{mask_mobile(person.mobile)}")
    if person.email:
        lines.append(f"邮箱：{person.email}")
    if person.employee_no:
        lines.append(f"工号：{person.employee_no}")
    if person.department_ids:
        lines.append(f"所属部门数：{len(person.department_ids)}")
    if person.open_id:
        lines.append(f"open_id：{person.open_id}")
    if person.user_id:
        lines.append(f"user_id：{person.user_id}")
    return "\n".join(lines)


def simple_card(title: str, body: str, template: str = "blue") -> dict[str, Any]:
    """生成一张文本卡片（msg_type=interactive 的 content）。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": template, "title": {"tag": "plain_text", "content": title}},
        "elements": [{"tag": "markdown", "content": body}],
    }


def approval_card(entry: dict[str, Any], approver_names_hint: str = "") -> dict[str, Any]:
    """审批卡片：同意 / 拒绝 按钮，value 携带 op 与审批单 id。"""
    kind_zh = ACTION_ZH.get(entry.get("kind", ""), entry.get("kind", ""))
    body = (
        f"**操作类型：**{kind_zh}\n"
        f"**审批单号：**{entry['id']}\n"
        f"**申请人：**<at id={entry.get('requester_open_id', '')}></at>\n"
        f"**申请内容：**\n{entry.get('summary', '')}"
    )
    if entry.get("requester_note"):
        body += f"\n**备注：**{entry['requester_note']}"
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": body},
        {"tag": "hr"},
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button", "text": {"tag": "plain_text", "content": "✅ 同意并执行"},
                    "type": "primary",
                    "value": {"op": "approve", "entry_id": entry["id"]},
                },
                {
                    "tag": "button", "text": {"tag": "plain_text", "content": "❌ 拒绝"},
                    "type": "danger",
                    "value": {"op": "reject", "entry_id": entry["id"]},
                },
            ],
        },
    ]
    if approver_names_hint:
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": approver_names_hint}]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": f"【审批】{kind_zh}"},
        },
        "elements": elements,
    }


def audit_lines(records: list[dict[str, Any]]) -> str:
    if not records:
        return "暂无操作记录。"
    lines = ["📋 最近操作记录："]
    for r in records:
        ok = "✅" if r.get("ok") else "❌"
        lines.append(
            f"{r.get('ts', '')} {ok} {ACTION_ZH.get(r.get('action', ''), r.get('action', ''))}"
            f"｜操作人 {r.get('operator', '')}｜{r.get('detail', '')}"
        )
    return "\n".join(lines)

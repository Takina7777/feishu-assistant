"""命令解析：把用户发给机器人的中文指令解析为结构化操作。

支持（别名已做归一化，大小写不敏感）：
  开通 / 创建 / 新增 / onboard         —— 开通账号（创建飞书成员）
  停用 / 冻结 / 回收                   —— 停用账号（is_frozen=true，可恢复）
  启用 / 解冻 / 恢复账号                —— 恢复被停用账号
  删除 / 彻底删除 / 回收-彻底删除 / 注销 —— 彻底删除账号（离职，默认走审批）
  查询 / 查 / status                   —— 查询账号状态
  我的ID / whoami                       —— 获取本人 open_id（用于配置白名单）
  日志 / audit / log                    —— 查看最近审计日志
  帮助 / help                           —— 命令帮助

“开通”可携带字段：姓名(第一个无关键字参数或 name=)、手机号、邮箱、
  部门=od-xxx、工号=、职务=、英文名=。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ACTION_ALIASES: dict[str, list[str]] = {
    "provision": ["开通", "创建", "新增", "添加", "onboard", "开账号", "开通账号"],
    "freeze": ["停用", "冻结", "回收", "回收账号", "suspend", "停用账号"],
    "unfreeze": ["启用", "解冻", "恢复账号", "unfreeze", "activate", "启账号", "启用账号"],
    "delete": ["删除", "彻底删除", "注销", "回收-彻底删除", "delete", "离职", "删除账号", "回收-删除"],
    "query": ["查询", "查", "查找", "status", "状态", "看看"],
    "whoami": ["我的id", "我的ID", "whoami", "我是谁"],
    "logs": ["日志", "audit", "log", "记录", "最近操作"],
    "help": ["帮助", "help", "菜单", "?", "？", "help me"],
}

KEY_ALIASES: dict[str, list[str]] = {
    "name": ["姓名", "名字", "name"],
    "mobile": ["手机", "手机号", "电话", "mobile"],
    "email": ["邮箱", "邮件", "email"],
    "dept": ["部门", "dept", "部门id", "department"],
    "employee_no": ["工号", "员工号", "employee_no", "员工编号"],
    "job_title": ["职务", "职位", "岗位", "job_title"],
    "en_name": ["英文名", "en", "en_name"],
    "employee_type": ["类型", "员工类型", "employee_type"],
}

TYPE_ZH: dict[str, int] = {
    "正式": 1, "正式员工": 1,
    "实习": 2, "实习生": 2,
    "外包": 3,
    "劳务": 4,
    "顾问": 5,
}

_OPEN_ID_RE = re.compile(r"^ou_[A-Za-z0-9]+$")
_UNION_ID_RE = re.compile(r"^on_[A-Za-z0-9]+$")
_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
# 大陆手机号（11位，1[3-9]开头）或带国家码的国际号（+ 开头）
_CN_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
_INTL_MOBILE_RE = re.compile(r"^\+\d{7,15}$")


class CommandError(Exception):
    """解析/参数错误，message 为可直接发给用户的使用提示。"""


@dataclass
class ParsedCommand:
    action: str
    raw: str
    target: str = ""                       # 冻结/启用/删除/查询 的身份（手机号/邮箱/open_id）
    params: dict[str, str] = field(default_factory=dict)  # provision 字段
    identity: str = ""                     # 规范化后的身份

    @property
    def kind(self) -> str:
        return self.action


def classify_identity(text: str) -> str:
    """识别身份类型：mobile / email / open_id / unknown。"""
    s = normalize_identity(text)
    if _OPEN_ID_RE.match(s) or s.startswith("ou_"):
        return "open_id"
    if _UNION_ID_RE.match(s):
        return "union_id"
    if _CN_MOBILE_RE.match(s) or _INTL_MOBILE_RE.match(s):
        return "mobile"
    if _EMAIL_RE.match(s):
        return "email"
    return "unknown"


def normalize_identity(text: str) -> str:
    """清洗手机号中的空格/连字符，去掉引号等装饰。"""
    s = (text or "").strip().strip("`\"'“”‘’【】[]()（）")
    # 形如 “+86 130 0000 0000” -> “+8613000000000”
    if s.startswith("+"):
        return "+" + re.sub(r"[\s\-]", "", s[1:])
    return re.sub(r"[\s\-]", "", s)


_POLITE_PREFIXES = ("麻烦你", "麻烦一下", "麻烦", "帮我一下", "帮帮忙", "帮我", "我想要", "我要", "我想",
                    "你好", "您好", "哈喽", "嗨", "hello", "hi", "请")


def _strip_mention(text: str) -> str:
    """去掉群聊 @机器人 富文本尾巴、客套前缀/后缀。"""
    text = re.sub(r"@_user_\d+", "", text).strip().lstrip("：: ")
    changed = True
    rounds = 0
    while changed and rounds < 4:
        changed = False
        rounds += 1
        for w in _POLITE_PREFIXES:
            if text.lower().startswith(w.lower()):
                text = text[len(w):].lstrip(" \u3000,，:：")
                changed = True
                break
    text = re.sub(r"[\s]*([，,]?\s*(谢谢|多谢|感谢|辛苦了|拜托了|谢谢啦|麻烦啦))$", "", text)
    return text.strip()


def _merge_mobile_parts(tokens: list[str]) -> list[str]:
    """把“+86 138 0013 8000”这类被空格拆开的国际手机号合并为一个 token。"""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.startswith("+") and len(t) <= 4 and t[1:].isdigit():
            merged = t
            j = i + 1
            while j < len(tokens) and tokens[j].isdigit():
                merged += tokens[j]
                j += 1
            out.append(merged)
            i = j
            continue
        out.append(t)
        i += 1
    return out


def _split_args(text: str) -> list[str]:
    return [t for t in re.split(r"[\s,，;；]+", text) if t]


def _first_action(text: str) -> tuple[str, str] | None:
    """返回 (action, 剩余文本)。带匹配 action 关键词；‘回收-彻底删除’优先于‘回收’。"""
    head = text
    # 先试最长别名（避免“回收-彻底删除”被“回收”吃掉）
    for action, aliases in ACTION_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            if head == alias:
                return action, ""
            if head.startswith(alias + " ") or head.startswith(alias + "\u3000") \
                    or head.startswith(alias + ":") or head.startswith(alias + "："):
                return action, head[len(alias):].lstrip(" \u3000:：")
            # “回收-彻底删除 xxx”
            if head.startswith(alias) and head[len(alias):].startswith(" "):
                continue
    return None


def _classify_field(tok: str) -> tuple[str, str]:
    """把单个参数 token 分类为 (key, value)。free=裸字段值。"""
    m = re.match(r"^([^=：:]+)[=：:](.+)$", tok, flags=re.S)
    if m:
        k, v = m.group(1).strip(), m.group(2).strip()
        for canon, aliases in KEY_ALIASES.items():
            if k in aliases:
                return canon, v
        return "unknown_key", f"{k}={v}"
    return "free", tok


def _norm_employee_type(v: str) -> int | None:
    if v.isdigit():
        return int(v)
    return TYPE_ZH.get(v.strip())


def parse_command(raw_text: str) -> ParsedCommand:
    """解析完整指令。参数错误抛 CommandError（message 为使用提示）。"""
    text = _strip_mention((raw_text or "").strip())
    if not text:
        raise CommandError("请输入指令，例如：开通 张三 13800138000；发送“帮助”查看全部指令。")

    hit = _first_action(text)
    if not hit:
        # 不以动作开头：若第一个 token 恰为动作别名（如“开通，张三”在split后失真）——此处兜底按帮助处理
        raise CommandError(f"无法识别指令“{text[:20]}…”。发送“帮助”查看支持的指令。")

    action, rest = hit
    tokens = _merge_mobile_parts(_split_args(rest))
    cmd = ParsedCommand(action=action, raw=raw_text)

    if action in ("help",):
        return cmd
    if action == "whoami":
        return cmd
    if action == "logs":
        if tokens:
            cmd.params["count"] = tokens[0] if tokens[0].isdigit() else ""
        return cmd

    if action == "provision":
        return _parse_provision(cmd, tokens, rest)

    # 冻结 / 启用 / 删除 / 查询 —— 都需要一个身份目标
    target_raw = rest.strip().strip(":： ")
    if not target_raw:
        usage = {
            "freeze": "停用 手机号/邮箱/open_id（例：停用 13800138000）",
            "unfreeze": "启用 手机号/邮箱/open_id（例：启用 13800138000）",
            "delete": "删除 手机号/邮箱/open_id（例：删除 13800138000；默认需审批）",
            "query": "查询 手机号/邮箱/open_id（例：查询 zhangsan@corp.com）",
        }[action]
        raise CommandError(f"缺少目标账号。用法：{usage}")
    # 目标取第一个 token，多余内容视为备注（如删除原因）
    target = tokens[0]
    note = " ".join(tokens[1:]).strip()
    identity = classify_identity(target)
    if identity == "unknown":
        raise CommandError(
            f"无法识别账号“{target}”。请使用以下任一标识：\n"
            "· 手机号（如 13800138000 或 +8613800138000）\n"
            "· 邮箱（如 zhangsan@corp.com）\n"
            "· open_id（如 ou_xxxxxxxx，可先对某人执行“查询”后从卡片复制）"
        )
    if identity == "union_id":
        raise CommandError("暂不支持 union_id，请使用手机号/邮箱或 open_id。")
    cmd.target = normalize_identity(target)
    cmd.identity = identity
    if note:
        cmd.params["note"] = note
    return cmd


def _parse_provision(cmd: ParsedCommand, tokens: list[str], rest: str) -> ParsedCommand:
    free: list[str] = []
    for tok in tokens:
        key, val = _classify_field(tok)
        if key == "free":
            free.append(val)
        elif key == "unknown_key":
            raise CommandError(f"无法识别的字段“{val}”，可用字段：姓名= 手机号= 邮箱= 部门= 工号= 职务= 英文名=")
        else:
            cmd.params[key] = val

    # 裸字段归类：邮箱 / 手机号 / 其余为姓名候选
    for f in free:
        c = classify_identity(f)
        if c == "email":
            cmd.params.setdefault("email", f)
        elif c == "mobile":
            cmd.params.setdefault("mobile", normalize_identity(f))
        else:
            cmd.params.setdefault("name", f)

    if not cmd.params.get("name"):
        raise CommandError("缺少姓名。用法：开通 姓名 手机号 [邮箱] [部门=od-xxx] [工号=xxx]")
    if not (cmd.params.get("mobile") or cmd.params.get("email")):
        raise CommandError("缺少联系方式。开通账号至少需要 手机号 或 邮箱（飞书要求手机号必填，强烈建议提供）。\n"
                           "用法：开通 张三 13800138000 zhangsan@corp.com [部门=od-xxx]")
    # 规范手机号
    if cmd.params.get("mobile"):
        cmd.params["mobile"] = normalize_identity(cmd.params["mobile"])
        if classify_identity(cmd.params["mobile"]) != "mobile":
            raise CommandError(f"手机号“{cmd.params['mobile']}”格式不合法，请检查（大陆号如 13800138000，国际号需 + 国家码）。")
    if cmd.params.get("email") and classify_identity(cmd.params["email"]) != "email":
        raise CommandError(f"邮箱“{cmd.params['email']}”格式不合法。")
    dept = cmd.params.get("dept", "")
    if dept and not dept.startswith("od-"):
        raise CommandError(
            "部门需提供 open_department_id（od- 开头）。\n"
            f"您填的是“{dept}”。可在 .env 配置 DEFAULT_DEPARTMENT_OPEN_ID 作为默认部门，或在命令中传 部门=od-xxxx。"
        )
    if cmd.params.get("employee_type"):
        et = _norm_employee_type(cmd.params["employee_type"])
        if et is None:
            raise CommandError(f"员工类型“{cmd.params['employee_type']}”无法识别，可用：正式/实习/外包/劳务/顾问 或数字 1-5。")
        cmd.params["employee_type"] = str(et)
    if dept:
        cmd.params["dept_open_id"] = dept
    cmd.params.pop("dept", None)
    return cmd

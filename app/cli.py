"""运维小工具：python -m app.cli <命令>

用途（配合 .env 填写）：
  check        验证 .env 里的 App ID / App Secret 能否拿到 tenant_access_token
  departments  列出你通讯录权限范围内的部门树（name -> open_department_id），用于填 DEFAULT_DEPARTMENT_OPEN_ID
  whoami <手机号|邮箱>  通过本人手机号/邮箱取回自己的 open_id（用于填 OPERATOR_OPEN_IDS / APPROVER_OPEN_IDS）

需要先开通只读权限（见 README 权限清单），命令内部用 tenant_access_token 调用。
"""
from __future__ import annotations

import sys

from .config import load_config
from .feishu_client import FeishuClient, FeishuError


def _client() -> tuple[FeishuClient, "object"]:
    cfg = load_config()
    problems = cfg.validate()
    if not cfg.app_id or not cfg.app_secret:
        print("请先在 .env 中填写 FEISHU_APP_ID / FEISHU_APP_SECRET（复制 .env.example 为 .env）。")
        sys.exit(1)
    return FeishuClient(cfg.app_id, cfg.app_secret, api_base=cfg.api_base), cfg


def cmd_check() -> int:
    client, cfg = _client()
    print("配置检查：")
    for p in cfg.validate():
        print(f"  - {p}")
    try:
        token = client.tenant_access_token()
    except FeishuError as e:
        print(f"  ✗ 获取 tenant_access_token 失败：{e}")
        print("    请核对 App ID / App Secret 是否一致、应用是否已发布启用。")
        return 1
    print(f"  ✓ App 凭证有效（token 前缀：{token[:12]}…）")
    print("提示：还需在开发者后台开通通讯录/消息相关权限并发布版本，见 README 第 3.2 节。")
    return 0


def cmd_departments() -> int:
    client, cfg = _client()
    try:
        rows = _walk_departments(client)
    except FeishuError as e:
        print(f"列出部门失败：{e}")
        print("常见原因：未开通『读取通讯录/获取部门基础信息』等只读权限，或通讯录权限范围不含目标部门。")
        return 1
    if not rows:
        print("通讯录权限范围内暂无可见部门。请检查开发者后台 -> 权限管理 -> 数据权限 的通讯录权限范围。")
        return 1
    print("部门树（name -> open_department_id）：")
    for name, oid in rows:
        print(f"  {oid}   {name}")
    print("\n把目标部门的 od-xxx 填入 .env 的 DEFAULT_DEPARTMENT_OPEN_ID（或用命令里的 部门=od-xxx 覆盖）。")
    return 0


def cmd_whoami(ident: str) -> int:
    client, cfg = _client()
    from .parser import classify_identity

    kind = classify_identity(ident)
    if kind == "mobile":
        mobiles, emails = [ident], None
    elif kind == "email":
        mobiles, emails = None, [ident]
    else:
        print("参数需为手机号或邮箱，例如：python -m app.cli whoami 13800138000")
        return 1
    try:
        result = client.batch_get_user_ids(mobiles=mobiles, emails=emails, include_resigned=False)
    except FeishuError as e:
        print(f"查询失败：{e.friendly()}")
        return 1
    if not result:
        print("未找到该成员，或应用无权限查看（需开通『通过手机号或邮箱获取用户 ID』并配置权限范围）。")
        return 1
    for row in result:
        print(f"open_id：{row.get('user_id')}   {kind}：{row.get(kind)}")
        print("把它填入 .env 的 OPERATOR_OPEN_IDS（审批人则填 APPROVER_OPEN_IDS）。")
    return 0


def _walk_departments(client: FeishuClient, max_depth: int = 10) -> list[tuple[str, str]]:
    """从根部门开始递归列出 open_department_id（深度优先，带缩进语义的平铺结果）。"""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def walk(parent: str, depth: int) -> None:
        if depth > max_depth:
            return
        page_token = ""
        while True:
            query = {
                "department_id_type": "open_department_id",
                "parent_department_id": parent,
                "page_size": 50,
            }
            if page_token:
                query["page_token"] = page_token
            data = client._request("GET", "/contact/v3/departments", query=query)
            items = data.get("items") or []
            for d in items:
                oid = d.get("open_department_id") or ""
                name = d.get("name") or "(未命名)"
                if oid and oid not in seen:
                    seen.add(oid)
                    out.append((name, oid))
                    walk(oid, depth + 1)
            if data.get("has_more") and data.get("page_token"):
                page_token = data["page_token"]
            else:
                break

    walk("0", 1)
    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "check":
        return cmd_check()
    if cmd == "departments":
        return cmd_departments()
    if cmd == "whoami" and len(argv) >= 2:
        return cmd_whoami(argv[1])
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())

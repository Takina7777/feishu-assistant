"""HTTP 服务：接收飞书事件订阅与卡片回调，编排账号生命周期操作。"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import actions
from .actions import ActionResult, run_action
from .approval import ApprovalManager
from .config import Config, load_config
from .crypto_utils import decrypt_event
from .feishu_client import FeishuClient, FeishuError
from .messages import ACTION_ZH, HELP_TEXT, approval_card, audit_lines
from .parser import CommandError, ParsedCommand, parse_command
from .storage import Storage

log = logging.getLogger("feishu-bot")


class Bot:
    """机器人运行时：持有配置、客户端、存储与审批管理器。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = FeishuClient(cfg.app_id, cfg.app_secret, api_base=cfg.api_base)
        self.storage = Storage(cfg.data_dir)
        self.approvals = ApprovalManager(cfg, self.storage)
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="bot")

    # ------------------------------------------------------------------ #
    # 发送
    # ------------------------------------------------------------------ #
    def reply(self, receive_id: str, text: str, receive_id_type: str = "open_id") -> None:
        try:
            self.client.send_text(receive_id, text, receive_id_type=receive_id_type)
        except FeishuError as e:
            log.warning("回复消息失败 receive=%s: %s", receive_id, e)

    def reply_card(self, receive_id: str, card: dict[str, Any], receive_id_type: str = "open_id") -> None:
        try:
            self.client.send_card(receive_id, card, receive_id_type=receive_id_type)
        except FeishuError as e:
            log.warning("发送卡片失败 receive=%s: %s", receive_id, e)

    def send_result(self, receive_id: str, result: ActionResult, receive_id_type: str = "open_id") -> None:
        self.reply(receive_id, result.text, receive_id_type=receive_id_type)

    # ------------------------------------------------------------------ #
    # 审计
    # ------------------------------------------------------------------ #
    def audit(self, *, action: str, ok: bool, operator: str, detail: str,
              entry_id: str = "", extra: dict[str, Any] | None = None) -> None:
        rec: dict[str, Any] = {
            "action": action,
            "ok": bool(ok),
            "operator": operator,
            "detail": detail,
        }
        if entry_id:
            rec["entry_id"] = entry_id
        if extra:
            rec.update(extra)
        self.storage.audit(rec)

    # ------------------------------------------------------------------ #
    # 消息处理
    # ------------------------------------------------------------------ #
    def handle_message_event(self, event: dict[str, Any]) -> None:
        sender = event.get("sender") or {}
        message = event.get("message") or {}
        chat_type = message.get("chat_type", "p2p")
        sender_open_id = ((sender.get("sender_id") or {}).get("open_id")) or ""
        chat_id = message.get("chat_id", "")
        if message.get("message_type") != "text":
            return
        try:
            content = json.loads(message.get("content") or "{}")
        except json.JSONDecodeError:
            return
        text = (content.get("text") or "").strip()
        if not text:
            return
        if chat_type == "p2p":
            receive_id, rid_type = sender_open_id, "open_id"
        else:
            receive_id, rid_type = chat_id, "chat_id"

        try:
            cmd = parse_command(text)
        except CommandError as e:
            self.reply(receive_id, str(e), rid_type)
            return
        try:
            self.route_command(cmd, sender_open_id, receive_id, rid_type)
        except Exception:  # noqa: BLE001
            log.exception("指令处理异常 text=%r", text)
            self.reply(receive_id, "⚠️ 处理指令时发生内部错误，请稍后重试或联系管理员查看日志。", rid_type)

    # ------------------------------------------------------------------ #
    def route_command(self, cmd: ParsedCommand, operator: str, receive_id: str,
                      rid_type: str) -> None:
        cfg = self.cfg
        action = cmd.action

        # 无需授权的指令
        if action == "help":
            self.reply(receive_id, HELP_TEXT, rid_type)
            return
        if action == "whoami":
            self.reply(
                receive_id,
                f"你的 open_id：`{operator}`\n\n"
                "请管理员把它填入 .env 的 OPERATOR_OPEN_IDS 后重启机器人，即可获得操作权限。\n"
                "若用于审批，则填入 APPROVER_OPEN_IDS。",
                rid_type,
            )
            return

        # 其余指令需要操作员权限
        if operator not in cfg.operator_open_ids:
            self.reply(
                receive_id,
                "⛔ 您不是本机器人的授权操作员。\n"
                "请向管理员发送“我的ID”获取自己的 open_id，并请管理员加入 .env 的 OPERATOR_OPEN_IDS。",
                rid_type,
            )
            return

        if action == "logs":
            try:
                count = min(max(int(cmd.params.get("count") or 10), 1), 50)
            except ValueError:
                count = 10
            records = self.storage.recent_audit(count)
            self.reply(receive_id, audit_lines(records), rid_type)
            return

        # 账号操作（provision/freeze/unfreeze/delete/query）
        self.handle_account_action(cmd, operator, receive_id, rid_type)

    # ------------------------------------------------------------------ #
    def handle_account_action(self, cmd: ParsedCommand, operator: str, receive_id: str,
                              rid_type: str) -> None:
        kind = cmd.action
        cfg = self.cfg
        params = self._build_params(kind, cmd)

        if kind == "query" or not cfg.is_approval(kind):
            result = self._execute(kind, params, operator)
            self.send_result(receive_id, result, rid_type)
            return

        # 审批模式：先解析目标给出摘要，再提交审批
        summary = actions.approval_summary(kind, params, cfg, self.client)
        note = cmd.params.get("note", "")
        entry = self.approvals.submit(
            kind=kind, summary=summary, params=params,
            requester_open_id=operator, requester_note=note,
        )
        self.audit(action=kind, ok=True, operator=operator,
                   detail=f"提交审批：{summary}", entry_id=entry["id"])
        self.reply(
            receive_id,
            f"📨 已提交审批（单号 {entry['id']}）\n"
            f"内容：{ACTION_ZH.get(kind, kind)}\n{summary}\n"
            "审批结果将通过私聊通知你。",
            rid_type,
        )
        for approver in cfg.approver_open_ids:
            self.reply_card(approver, approval_card(entry, approver_names_hint=""), "open_id")

    # ------------------------------------------------------------------ #
    def _build_params(self, kind: str, cmd: ParsedCommand) -> dict[str, Any]:
        if kind == "provision":
            from .feishu_client import gen_client_token

            p = dict(cmd.params)
            p.setdefault("client_token", gen_client_token())
            return p
        return {"ident": cmd.target, "identity": cmd.identity, "note": cmd.params.get("note", "")}

    def _execute(self, kind: str, params: dict[str, Any], operator: str) -> ActionResult:
        try:
            result = run_action(kind, params, self.cfg, self.client)
        except Exception as e:  # noqa: BLE001
            result = ActionResult(ok=False, text=f"内部错误：{e}", kind=kind, detail=str(e))
        self.audit(action=kind, ok=result.ok, operator=operator,
                   detail=actions.param_summary(kind, params),
                   extra={"brief": result.text.splitlines()[0] if result.text else ""})
        return result

    # ------------------------------------------------------------------ #
    # 卡片审批回调
    # ------------------------------------------------------------------ #
    def handle_card_action(self, value: dict[str, Any], operator: str) -> None:
        op = (value or {}).get("op", "")
        entry_id = (value or {}).get("entry_id", "")
        if op not in ("approve", "reject") or not entry_id:
            log.info("忽略未知卡片回调 value=%s operator=%s", value, operator)
            return
        entry = self.storage.get_entry(entry_id)
        if entry is None:
            self.reply(operator, f"审批单 {entry_id} 不存在或已被清理。", "open_id")
            return
        kind = entry.get("kind", "")

        def runner(params: dict[str, Any]) -> dict[str, Any]:
            try:
                r = run_action(kind, params, self.cfg, self.client)
                return {"ok": r.ok, "text": r.text, "person": r.person}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "text": f"内部错误：{e}"}

        out = self.approvals.decide(entry_id, operator, approve=(op == "approve"), run=runner)
        decided = entry.get("status") or out.get("entry", {}).get("status", "?")
        ok = bool(out.get("ok"))
        text = out.get("text", "")
        self.audit(
            action=f"{kind}-{'同意' if op == 'approve' else '拒绝'}",
            ok=ok, operator=operator,
            detail=actions.param_summary(kind, entry.get("params", {})),
            entry_id=entry_id,
            extra={"status": decided},
        )
        # 通知审批人与申请人
        head = f"📋 审批结果（{ACTION_ZH.get(kind, kind)}｜单号 {entry_id}）\n"
        self.reply(operator, head + text, "open_id")
        requester = entry.get("requester_open_id", "")
        if requester and requester != operator:
            self.reply(requester, head + text, "open_id")

    # ------------------------------------------------------------------ #
    # 入口（线程池）
    # ------------------------------------------------------------------ #
    def dispatch(self, body: dict[str, Any]) -> None:
        self._pool.submit(self._safe_handle, body)

    def _safe_handle(self, body: dict[str, Any]) -> None:
        try:
            header = body.get("header") or {}
            event = body.get("event") or body
            etype = header.get("event_type") or event.get("type") or body.get("type") or ""
            if etype == "im.message.receive_v1":
                self.handle_message_event(event)
            elif etype == "card.action.trigger" or self._is_legacy_card_action(body):
                operator = self._extract_card_operator(body)
                action_src = body.get("action") or event.get("action") or {}
                value = (action_src or {}).get("value") or {}
                self.handle_card_action(value, operator)
            else:
                log.info("忽略未处理回调 type=%s", etype)
        except Exception:  # noqa: BLE001
            log.exception("处理回调异常")

    @staticmethod
    def _is_legacy_card_action(body: dict[str, Any]) -> bool:
        action = body.get("action")
        return isinstance(action, dict) and ("value" in action or "tag" in action)

    @staticmethod
    def _extract_card_operator(body: dict[str, Any]) -> str:
        event = body.get("event") or {}
        operator = event.get("operator") or body.get("operator") or {}
        return (
            operator.get("open_id")
            or operator.get("user_id")
            or body.get("open_id")
            or event.get("open_id")
            or ""
        )


# ---------------------------------------------------------------------- #
# FastAPI 应用
# ---------------------------------------------------------------------- #
_bot: Bot | None = None
_bot_lock = threading.Lock()


def get_bot() -> Bot:
    global _bot
    with _bot_lock:
        if _bot is None:
            _bot = Bot(load_config())
        return _bot


def create_app() -> FastAPI:
    app = FastAPI(title="飞书成员账号开通/回收机器人", version="1.0.0")

    @app.get("/")
    def root() -> dict[str, Any]:
        return {
            "name": "飞书成员账号开通/回收机器人",
            "status": "running",
            "endpoints": {
                "healthz": "/healthz",
                "webhook": "/webhook（飞书事件订阅与卡片回调的 POST 入口）",
            },
            "tip": "开发者后台请求地址请配置为 https://<你的公网域名>/webhook",
        }

    @app.get("/webhook")
    def webhook_get() -> dict[str, Any]:
        return {"code": 0, "msg": "OK：本地址接收飞书 POST 回调；GET 仅用于连通性检查"}

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        cfg = load_config()
        problems = cfg.validate()
        return {"status": "ok" if not problems else "misconfigured", "problems": problems}

    @app.post("/webhook")
    async def webhook(request: Request) -> JSONResponse:
        bot = get_bot()
        cfg = bot.cfg
        try:
            raw = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"code": -1, "msg": "bad json"})

        # 解密（Encrypt Key）
        if raw.get("encrypt"):
            if not cfg.encrypt_key:
                log.warning(
                    "收到飞书【加密】回调，但 .env 未配置 FEISHU_ENCRYPT_KEY。\n"
                    "请到 开发者后台 -> 事件与回调 -> 加密策略 复制 Encrypt Key，"
                    "填入 .env 的 FEISHU_ENCRYPT_KEY 后重启 run.py。"
                )
                return JSONResponse(
                    {"code": -1, "msg": "encrypt not configured: 请在 .env 配置 FEISHU_ENCRYPT_KEY（见服务端日志）"},
                    status_code=400,
                )
            try:
                raw = json.loads(decrypt_event(cfg.encrypt_key, raw["encrypt"]))
            except Exception as e:  # noqa: BLE001
                log.warning("事件解密失败，请核对 FEISHU_ENCRYPT_KEY 是否与后台 Encrypt Key 完全一致: %s", e)
                return JSONResponse({"code": -1, "msg": "decrypt failed"}, status_code=400)

        # 校验 token（配置了才校验）
        token = (raw.get("header") or {}).get("token") or raw.get("token") or ""
        if cfg.verification_token and token and token != cfg.verification_token:
            log.warning(
                "Verification Token 校验失败：请求携带 token=%s… 长度%d，与 .env 配置不一致。\n"
                "请打开 开发者后台 -> 事件与回调 -> 加密策略，把页面显示的 Verification Token "
                "原样复制覆盖到 .env 的 FEISHU_VERIFICATION_TOKEN 后重启 run.py。",
                token[:6], len(token),
            )
            return JSONResponse({"code": -1, "msg": "invalid token"}, status_code=403)

        # URL 验证握手
        if raw.get("type") == "url_verification":
            return JSONResponse({"challenge": raw.get("challenge", ""), "token": token})
        if "challenge" in raw:
            return JSONResponse({"challenge": raw["challenge"]})

        bot.dispatch(raw)
        # 立即确认，业务在后台线程执行
        return JSONResponse({"code": 0, "msg": "ok"})

    return app


app = create_app()

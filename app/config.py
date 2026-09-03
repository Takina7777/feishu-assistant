"""集中配置：从 .env / 环境变量加载，并提供类型安全的访问。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv 未安装时静默跳过，直接读环境变量
    load_dotenv = None


def _load() -> None:
    # 从当前目录或上级目录找 .env
    if load_dotenv is not None:
        for p in (Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"):
            if p.exists():
                load_dotenv(p, override=False)
                return


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _bool_env(key: str, default: bool) -> bool:
    v = _env(key)
    if not v:
        return default
    return v.lower() in ("1", "true", "yes", "on")


def _split_env(key: str) -> list[str]:
    raw = _env(key)
    if not raw:
        return []
    return [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]


@dataclass(frozen=True)
class Config:
    # 飞书应用
    app_id: str
    app_secret: str
    verification_token: str = ""
    encrypt_key: str = ""
    # 服务
    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: Path = field(default_factory=lambda: Path("data"))
    # 通讯录
    default_department_open_id: str = ""
    default_employee_type: int = 1
    delete_acceptor_open_id: str = ""
    # 权限
    operator_open_ids: list[str] = field(default_factory=list)
    approver_open_ids: list[str] = field(default_factory=list)
    # 模式
    provision_mode: str = "direct"   # direct | approval
    freeze_mode: str = "direct"
    unfreeze_mode: str = "direct"
    delete_mode: str = "approval"
    approval_ttl_minutes: int = 1440
    # 令牌可访问性开关
    api_base: str = "https://open.feishu.cn/open-apis"

    def is_approval(self, action: str) -> bool:
        """某类操作是否走审批（action: provision/freeze/unfreeze/delete）。"""
        mode = {
            "provision": self.provision_mode,
            "freeze": self.freeze_mode,
            "unfreeze": self.unfreeze_mode,
            "delete": self.delete_mode,
        }.get(action, "direct")
        return mode.lower() == "approval"

    def validate(self) -> list[str]:
        """返回配置问题清单；为空表示可以启动。"""
        problems: list[str] = []
        if not self.app_id:
            problems.append("缺少 FEISHU_APP_ID")
        if not self.app_secret:
            problems.append("缺少 FEISHU_APP_SECRET")
        if not self.operator_open_ids:
            problems.append("OPERATOR_OPEN_IDS 为空：请先让本人给机器人发“我的ID”，再把返回的 open_id 填入 .env")
        return problems


_config: Config | None = None


def load_config() -> Config:
    """加载配置（进程内单例）。"""
    global _config
    if _config is not None:
        return _config
    _load()
    data_dir = Path(_env("DATA_DIR", "data"))
    cfg = Config(
        app_id=_env("FEISHU_APP_ID"),
        app_secret=_env("FEISHU_APP_SECRET"),
        verification_token=_env("FEISHU_VERIFICATION_TOKEN"),
        encrypt_key=_env("FEISHU_ENCRYPT_KEY"),
        host=_env("HOST", "0.0.0.0"),
        port=int(_env("PORT", "8000") or 8000),
        data_dir=data_dir,
        default_department_open_id=_env("DEFAULT_DEPARTMENT_OPEN_ID"),
        default_employee_type=int(_env("DEFAULT_EMPLOYEE_TYPE", "1") or 1),
        delete_acceptor_open_id=_env("DELETE_RESOURCE_ACCEPTOR_OPEN_ID"),
        operator_open_ids=_split_env("OPERATOR_OPEN_IDS"),
        approver_open_ids=_split_env("APPROVER_OPEN_IDS"),
        provision_mode=_env("PROVISION_MODE", "direct") or "direct",
        freeze_mode=_env("FREEZE_MODE", "direct") or "direct",
        unfreeze_mode=_env("UNFREEZE_MODE", "direct") or "direct",
        delete_mode=_env("DELETE_MODE", "approval") or "approval",
        approval_ttl_minutes=int(_env("APPROVAL_TTL_MINUTES", "1440") or 1440),
    )
    _config = cfg
    return cfg


def reset_config() -> None:
    """仅测试用：清空单例。"""
    global _config
    _config = None

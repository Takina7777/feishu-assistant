"""启动入口：python run.py"""
from __future__ import annotations

import logging
import sys

import uvicorn

from app.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main() -> None:
    cfg = load_config()
    problems = cfg.validate()
    if problems:
        print("=" * 60)
        print("⚠️  启动前请先完善配置：")
        for p in problems:
            print(f"  - {p}")
        print("=" * 60)
        if not cfg.app_id or not cfg.app_secret:
            sys.exit(1)
    print(
        f"启动机器人服务 http://{cfg.host}:{cfg.port}\n"
        f"回调地址请填： https://<你的公网域名>:{cfg.port}/webhook\n"
        "本地联调可用内网穿透工具将公网流量转发到该端口。"
    )
    uvicorn.run(
        "app.server:app",
        host=cfg.host,
        port=cfg.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()

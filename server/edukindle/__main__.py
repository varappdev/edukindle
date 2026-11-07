"""`python -m edukindle` / `edukindle-server`: run the API + dashboard."""

from __future__ import annotations

import logging
import socket

import uvicorn

from . import config
from .api import create_app


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s",
                        datefmt="%H:%M:%S")
    cfg = config.load()
    print(f"EduKindle server: dashboard http://{_lan_ip()}:{cfg.port}/  "
          f"(Kindle edukindle.conf: server={_lan_ip()}:{cfg.port})")
    if not cfg.llm_enabled:
        print("No OPENAI_API_KEY set: running in offline mode with heuristic agents.")
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="warning",
                timeout_keep_alive=30)


if __name__ == "__main__":
    main()

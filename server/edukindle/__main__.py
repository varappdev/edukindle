"""`python -m edukindle` / `edukindle-server`: run the API + dashboard."""

from __future__ import annotations

import ipaddress
import logging
import socket
from pathlib import Path

import uvicorn

from . import config
from .api import create_app


def _lan_ip() -> str:
    """The address other devices on this network reach us at."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # picks the outgoing interface; sends nothing
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _ufw_enabled() -> bool:
    """ufw's state file is world-readable even though its rules aren't."""
    try:
        return "ENABLED=yes" in Path("/etc/ufw/ufw.conf").read_text()
    except OSError:
        return False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s",
                        datefmt="%H:%M:%S")
    cfg = config.load()
    ip = _lan_ip()
    print(f"\nEduKindle server on http://{ip}:{cfg.port}/  (dashboard)")
    print(f"  On the Kindle, connect to:  {ip}:{cfg.port}")
    print("  (the app finds it automatically when both are on the same Wi-Fi or hotspot)")
    if cfg.llm_enabled:
        print(f"  AI agents: {cfg.provider_name} · {cfg.model}")
    else:
        print("  No OPENAI_API_KEY or OPENROUTER_API_KEY set: offline heuristic agents.")
    if _ufw_enabled() and not ipaddress.ip_address(ip).is_loopback:
        subnet = ipaddress.ip_network(f"{ip}/24", strict=False)
        print("  The ufw firewall is on. If the Kindle can't connect, allow this network:")
        print(f"    sudo ufw allow from {subnet} to any port {cfg.port} proto tcp")
    print()
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="warning",
                timeout_keep_alive=30)


if __name__ == "__main__":
    main()

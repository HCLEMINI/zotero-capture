"""Configuration loaded from environment variables."""
import logging
import os
import pathlib
import platform
from dataclasses import dataclass

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 24731
# Default Zotero data dir on this machine (zotero.sqlite + storage/).
# PDF ground-truth verification (Problem 3) reads this read-only.
DEFAULT_ZOTERO_DATA_DIR = None  # set ZCB_ZOTERO_DATA_DIR to your Zotero data dir (contains zotero.sqlite + storage/)


@dataclass
class Config:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    log_level: str = "INFO"
    zotero_data_dir: str = DEFAULT_ZOTERO_DATA_DIR

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            host=os.environ.get("ZCB_HOST", DEFAULT_HOST),
            port=int(os.environ.get("ZCB_PORT", str(DEFAULT_PORT))),
            log_level=os.environ.get("ZCB_LOG_LEVEL", "INFO").upper(),
            zotero_data_dir=os.environ.get("ZCB_ZOTERO_DATA_DIR", DEFAULT_ZOTERO_DATA_DIR),
        )


def setup_logging(level: str) -> logging.Logger:
    """Configure logging. MCP uses stdio for the protocol, so logs MUST go to
    stderr or a file — never stdout."""
    logger = logging.getLogger("zotero_claude_bridge")
    logger.setLevel(level)
    if logger.handlers:
        return logger
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    sh = logging.StreamHandler()  # defaults to stderr
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    try:
        if platform.system() == "Windows":
            base = pathlib.Path(
                os.environ.get("LOCALAPPDATA", str(pathlib.Path.home()))
            ) / "zotero-claude-bridge"
        else:
            base = pathlib.Path.home() / ".local" / "share" / "zotero-claude-bridge"
        base.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(base / "bridge.log", encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass
    return logger

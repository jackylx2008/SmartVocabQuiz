"""统一加载项目配置和本机环境变量。"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml


VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def load_config(root: Path) -> dict[str, Any]:
    """读取 common.env 和 config.yaml；进程环境变量优先。"""
    _load_env(root / "common.env")
    config_path = root / "config.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"缺少配置文件：{config_path}")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config.yaml 顶层必须是对象")
    return _expand(data)


def resolve_path(root: Path, value: str) -> Path:
    """在统一入口解析相对路径和用户目录。"""
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def _load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _cloudstation_root() -> str | None:
    if os.environ.get("CLOUDSTATION_ROOT"):
        return os.environ["CLOUDSTATION_ROOT"]
    suffix = {"win32": "WINDOWS", "darwin": "MACOS"}.get(sys.platform, "LINUX")
    return os.environ.get(f"CLOUDSTATION_ROOT_{suffix}")


def _expand(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        found = _cloudstation_root() if name == "CLOUDSTATION_ROOT" else os.environ.get(name)
        if found is None:
            if default is None:
                raise ValueError(f"缺少环境变量：{name}")
            return default
        return found

    return VARIABLE.sub(replace, value)

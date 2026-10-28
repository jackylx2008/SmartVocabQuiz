"""缓存单个图块并原子写入最终 JSON。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def cache_path(cache_dir: Path, pdf: Path, page: int, tile: str, image: bytes, model: str) -> Path:
    digest = hashlib.sha256(image + model.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{pdf.stem}_p{page}_{tile}_{digest}.json"


def read_cache(path: Path) -> list[dict[str, str]] | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"缓存格式错误：{path}")
    return data


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)

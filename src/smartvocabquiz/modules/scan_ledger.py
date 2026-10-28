"""按 PDF 内容哈希记录已完成的识别结果。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartvocabquiz.modules.pdf_images import iter_page_images
from smartvocabquiz.modules.result_store import cache_path, write_json


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_ledger(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 1, "documents": {}, "last_output_files": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("documents"), dict):
        raise ValueError(f"扫描记录格式不受支持：{path}")
    data.setdefault("last_output_files", {})
    return data


def migrate_existing_output(input_dir: Path, output_file: Path, ledger_file: Path, *,
                            model: str, scale: float, quality: int) -> dict[str, Any]:
    """首次建账：仅在每个现有 PDF 的图块缓存匹配时登记已有词库。"""
    if ledger_file.is_file():
        return load_ledger(ledger_file)
    ledger = load_ledger(ledger_file)
    if not output_file.is_file():
        return ledger
    vocabulary = json.loads(output_file.read_text(encoding="utf-8"))
    if vocabulary.get("schema_version") != 1 or not isinstance(vocabulary.get("entries"), list):
        raise ValueError(f"现有词库格式不受支持：{output_file}")
    by_file: dict[str, list[dict[str, Any]]] = {}
    for entry in vocabulary["entries"]:
        for source in entry.get("sources", []):
            by_file.setdefault(source["file"], []).append({
                "english": entry["english"], "chinese": entry["chinese"],
                "page": source["page"], "tile": source["tile"],
            })
    for name, entries in by_file.items():
        pdf = input_dir / name
        if not pdf.is_file():
            raise FileNotFoundError(f"已有词库引用的 PDF 不存在，无法安全登记：{pdf}")
        tiles = list(iter_page_images(pdf, scale, quality))
        for page, tile, image in tiles:
            if not cache_path(output_file.parent / ".cache", pdf, page, tile, image, model).is_file():
                raise ValueError(f"现有 PDF 与图块缓存不匹配，不能确认已扫描：{pdf.name}")
        pdf_id = sha256_file(pdf)
        ledger["documents"][pdf_id] = {
            "id": pdf_id,
            "scanned_at": vocabulary.get("generated_at") or datetime.now(timezone.utc).isoformat(),
            "original_file": name,
            "page_count": max((page for page, _, _ in tiles), default=0),
            "tile_count": len(tiles),
            "entries": entries,
            "migrated_from_vocabulary": True,
        }
        ledger["last_output_files"][name] = pdf_id
    ledger["output_sha256"] = sha256_file(output_file)
    write_json(ledger_file, ledger)
    return ledger


def build_vocabulary(ledger: dict[str, Any], files: dict[str, str], input_dir: Path) -> dict[str, Any]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    tile_count = 0
    for name, pdf_id in sorted(files.items(), key=lambda pair: pair[0].casefold()):
        document = ledger["documents"][pdf_id]
        tile_count += document["tile_count"]
        for entry in document["entries"]:
            english = " ".join(entry["english"].split())
            chinese = " ".join(entry["chinese"].split())
            key = (english.casefold(), chinese)
            source = {"file": name, "page": entry["page"], "tile": entry["tile"]}
            if key not in merged:
                merged[key] = {"english": english, "chinese": chinese, "sources": [source]}
            elif source not in merged[key]["sources"]:
                merged[key]["sources"].append(source)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_directory": input_dir.name,
        "pdf_count": len(files),
        "tile_count": tile_count,
        "entry_count": len(merged),
        "entries": list(merged.values()),
    }

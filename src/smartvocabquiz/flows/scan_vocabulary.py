"""仅识别新 PDF 内容，并由扫描记录重建词库。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from logging_config import get_logger
from smartvocabquiz.modules.local_ai import LocalAIClient
from smartvocabquiz.modules.pdf_images import iter_page_images
from smartvocabquiz.modules.result_store import cache_path, read_cache, write_json
from smartvocabquiz.modules.scan_ledger import (
    build_vocabulary, migrate_existing_output, sha256_file,
)


logger = get_logger(__name__)
Progress = Callable[[str], None]


def run(input_dir: Path, output_file: Path, ai: LocalAIClient, *, scale: float, quality: int,
        retries: int, ledger_file: Path | None = None, progress: Progress | None = None) -> dict[str, Any]:
    ledger_file = ledger_file or output_file.parent / "scan_manifest.json"
    ledger = migrate_existing_output(input_dir, output_file, ledger_file, model=ai.model,
                                     scale=scale, quality=quality)
    pdfs = sorted(input_dir.glob("*.pdf"), key=lambda path: path.name.casefold())
    files = {pdf.name: sha256_file(pdf) for pdf in pdfs}
    missing = {pdf_id for pdf_id in files.values() if pdf_id not in ledger["documents"]}
    if not missing and files == ledger["last_output_files"] and output_file.is_file():
        return {"new_pdf_count": 0, "vocabulary": json.loads(output_file.read_text(encoding="utf-8"))}
    if output_file.is_file() and ledger.get("output_sha256"):
        if sha256_file(output_file) != ledger["output_sha256"]:
            raise ValueError("vocabulary.json 自上次扫描后被修改；为避免覆盖人工修改，请先处理词库与扫描记录的差异")
    checked_ai = False
    newly_scanned = 0
    processed_ids: set[str] = set()
    vocabulary = None
    for pdf in pdfs:
        pdf_id = files[pdf.name]
        if pdf_id not in missing or pdf_id in processed_ids:
            continue
        processed_ids.add(pdf_id)
        _report(progress, f"正在识别 {pdf.name}")
        logger.info("开始识别 %s (SHA-256 %s)", pdf.name, pdf_id[:12])
        entries = []
        tile_count = 0
        page_count = 0
        for page, tile, image in iter_page_images(pdf, scale, quality):
            page_count = max(page_count, page)
            tile_count += 1
            cache = cache_path(output_file.parent / ".cache", pdf, page, tile, image, ai.model)
            recognized = read_cache(cache)
            if recognized is None:
                if not checked_ai:
                    ai.check_available()
                    checked_ai = True
                for attempt in range(retries + 1):
                    try:
                        recognized = ai.recognize(image)
                        write_json(cache, recognized)
                        break
                    except (ValueError, KeyError, OSError) as exc:
                        if attempt >= retries:
                            raise RuntimeError(f"识别失败：{pdf.name} 第 {page} 页 {tile}") from exc
                        logger.warning("重试 %s 第 %s 页 %s：%s", pdf.name, page, tile, exc)
            for item in recognized:
                entries.append({"english": item["english"], "chinese": item["chinese"],
                                "page": page, "tile": tile})
            _report(progress, f"{pdf.name}：第 {page} 页 {tile}，识别 {len(recognized)} 项")
        if sha256_file(pdf) != pdf_id:
            raise RuntimeError(f"扫描期间 PDF 内容发生变化，请重试：{pdf.name}")
        ledger["documents"][pdf_id] = {
            "id": pdf_id, "scanned_at": datetime.now(timezone.utc).isoformat(),
            "original_file": pdf.name, "page_count": page_count,
            "tile_count": tile_count, "entries": entries,
        }
        write_json(ledger_file, ledger)
        available_files = {name: item_id for name, item_id in files.items()
                           if item_id in ledger["documents"]}
        vocabulary = _persist_vocabulary(ledger, available_files, input_dir, output_file, ledger_file)
        newly_scanned += 1
        logger.info("完成 %s：%s 项", pdf.name, len(entries))
    if vocabulary is None or ledger["last_output_files"] != files:
        vocabulary = _persist_vocabulary(ledger, files, input_dir, output_file, ledger_file)
    _report(progress, f"完成：新增识别 {newly_scanned} 份 PDF，词库共 {vocabulary['entry_count']} 项")
    return {"new_pdf_count": newly_scanned, "vocabulary": vocabulary}


def _report(callback: Progress | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _persist_vocabulary(ledger: dict[str, Any], files: dict[str, str], input_dir: Path,
                        output_file: Path, ledger_file: Path) -> dict[str, Any]:
    vocabulary = build_vocabulary(ledger, files, input_dir)
    write_json(output_file, vocabulary)
    ledger["last_output_files"] = files
    ledger["output_sha256"] = sha256_file(output_file)
    write_json(ledger_file, ledger)
    return vocabulary

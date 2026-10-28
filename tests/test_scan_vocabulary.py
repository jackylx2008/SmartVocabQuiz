"""用临时 PDF 验证哈希去重、已有词库迁移与网页接口。"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

import fitz


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from smartvocabquiz.flows.scan_vocabulary import run
from smartvocabquiz.modules.scan_ledger import load_ledger, migrate_existing_output, sha256_file
from smartvocabquiz.web_server import AppState, make_server


class FakeAI:
    model = "test-model"

    def __init__(self):
        self.calls = 0
        self.checks = 0

    def check_available(self):
        self.checks += 1

    def recognize(self, image):
        self.calls += 1
        return [{"english": "apple", "chinese": "苹果"}]


class FailSecondAI(FakeAI):
    def recognize(self, image):
        if self.calls >= 2:
            raise OSError("simulated model failure")
        return super().recognize(image)


def make_pdf(path: Path, text: str = "") -> None:
    document = fitz.open()
    page = document.new_page(width=300, height=200)
    if text:
        page.insert_text((30, 40), text)
    document.save(path)
    document.close()


class ScanVocabularyTest(unittest.TestCase):
    def test_incremental_scan_uses_pdf_hash_and_keeps_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_dir = root / "BookScan"
            input_dir.mkdir()
            first_pdf = input_dir / "one.pdf"
            second_pdf = input_dir / "two.pdf"
            make_pdf(first_pdf, "first")
            make_pdf(second_pdf, "second")
            output = root / "output" / "vocabulary.json"
            ledger_file = output.parent / "scan_manifest.json"
            ai = FakeAI()
            first = run(input_dir, output, ai, scale=1, quality=80, retries=0)
            self.assertEqual(first["new_pdf_count"], 2)
            self.assertEqual(first["vocabulary"]["entry_count"], 1)
            self.assertEqual(len(first["vocabulary"]["entries"][0]["sources"]), 4)
            self.assertEqual(ai.calls, 4)
            self.assertEqual(ai.checks, 1)
            self.assertEqual(set(load_ledger(ledger_file)["documents"]),
                             {sha256_file(first_pdf), sha256_file(second_pdf)})

            duplicate = input_dir / "copy.pdf"
            shutil.copyfile(first_pdf, duplicate)
            again = run(input_dir, output, ai, scale=1, quality=80, retries=0)
            self.assertEqual(again["new_pdf_count"], 0)
            self.assertEqual(ai.calls, 4)
            self.assertEqual(len(again["vocabulary"]["entries"][0]["sources"]), 6)

            old_id = sha256_file(second_pdf)
            second_pdf.unlink()
            make_pdf(second_pdf, "changed")
            changed = run(input_dir, output, ai, scale=1, quality=80, retries=0)
            self.assertEqual(changed["new_pdf_count"], 1)
            self.assertEqual(ai.calls, 5)
            self.assertIn(old_id, load_ledger(ledger_file)["documents"])
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["pdf_count"], 3)

    def test_migrates_existing_vocabulary_without_ai_or_rewriting_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_dir = root / "BookScan"
            input_dir.mkdir()
            make_pdf(input_dir / "one.pdf", "sample")
            output = root / "output" / "vocabulary.json"
            ai = FakeAI()
            run(input_dir, output, ai, scale=1, quality=80, retries=0)
            before = output.read_bytes()
            ledger_file = output.parent / "scan_manifest.json"
            ledger_file.unlink()
            ledger = migrate_existing_output(input_dir, output, ledger_file, model=ai.model, scale=1, quality=80)
            self.assertEqual(len(ledger["documents"]), 1)
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual(run(input_dir, output, ai, scale=1, quality=80, retries=0)["new_pdf_count"], 0)
            self.assertEqual(ai.calls, 2)

    def test_completed_pdf_is_saved_when_later_pdf_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_dir = root / "BookScan"
            input_dir.mkdir()
            make_pdf(input_dir / "one.pdf", "first")
            make_pdf(input_dir / "two.pdf", "second")
            output = root / "output" / "vocabulary.json"
            with self.assertRaises(RuntimeError):
                run(input_dir, output, FailSecondAI(), scale=1, quality=80, retries=0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["pdf_count"], 1)
            self.assertEqual(len(load_ledger(output.parent / "scan_manifest.json")["documents"]), 1)
            recovered = run(input_dir, output, FakeAI(), scale=1, quality=80, retries=0)
            self.assertEqual(recovered["new_pdf_count"], 1)
            self.assertEqual(recovered["vocabulary"]["pdf_count"], 2)

    def test_web_exposes_status_and_starts_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_dir = root / "BookScan"
            input_dir.mkdir()
            make_pdf(input_dir / "one.pdf", "sample")
            ai = FakeAI()
            state = AppState(input_dir, root / "output" / "vocabulary.json",
                             root / "output" / "scan_manifest.json", ROOT / "web", ai,
                             scale=1, quality=80, retries=0)
            server = make_server(state)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                status = json.load(urlopen(base + "/api/status"))
                self.assertEqual(status["counts"]["pending"], 1)
                home = urlopen(base + "/").read().decode("utf-8")
                self.assertIn("扫描词汇", home)
                self.assertIn("生成试题", home)
                self.assertIn("需要启动本地 AI", home)
                ai_status = json.load(urlopen(base + "/api/ai-status"))
                self.assertTrue(ai_status["available"])
                self.assertEqual(ai.checks, 1)
                self.assertTrue(json.load(urlopen(base + "/api/ai-status"))["available"])
                self.assertEqual(ai.checks, 1)
                page = urlopen(base + "/scan").read().decode("utf-8")
                self.assertIn("扫描新增 PDF", page)
                quiz = urlopen(base + "/quiz").read().decode("utf-8")
                self.assertIn("试题功能尚未实现", quiz)
                self.assertEqual(ai.calls, 0)
                original_pdf = (input_dir / "one.pdf").read_bytes()
                upload = Request(base + "/api/upload", data=original_pdf, method="POST",
                                 headers={"X-SmartVocabQuiz": "1", "X-Filename": "copy.pdf",
                                          "Content-Type": "application/pdf"})
                self.assertEqual(json.load(urlopen(upload))["name"], "copy.pdf")
                self.assertEqual(json.load(urlopen(base + "/api/status"))["counts"]["pending"], 2)
                request = Request(base + "/api/scan", data=b"", method="POST",
                                  headers={"X-SmartVocabQuiz": "1"})
                self.assertEqual(urlopen(request).status, 202)
                for _ in range(100):
                    status = json.load(urlopen(base + "/api/status"))
                    if status["job"]["state"] != "running":
                        break
                    time.sleep(0.02)
                self.assertEqual(status["job"]["state"], "done")
                self.assertEqual(status["counts"]["pending"], 0)
                self.assertEqual(json.load(urlopen(base + "/api/vocabulary"))["entry_count"], 1)
                self.assertEqual(ai.calls, 2)
            finally:
                server.shutdown()
                server.server_close()

    def test_ai_status_reports_unavailable_without_starting_service(self):
        class OfflineAI(FakeAI):
            def check_available(self):
                self.checks += 1
                raise OSError("not running")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_dir = root / "BookScan"
            input_dir.mkdir()
            ai = OfflineAI()
            state = AppState(input_dir, root / "output" / "vocabulary.json",
                             root / "output" / "scan_manifest.json", ROOT / "web", ai,
                             scale=1, quality=80, retries=0)
            server = make_server(state)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                self.assertFalse(json.load(urlopen(base + "/api/ai-status"))["available"])
                self.assertEqual(ai.checks, 1)
                self.assertFalse(json.load(urlopen(base + "/api/ai-status"))["available"])
                self.assertEqual(ai.checks, 1)
                self.assertFalse(json.load(urlopen(base + "/api/ai-status?refresh=1"))["available"])
                self.assertEqual(ai.checks, 2)
            finally:
                server.shutdown()
                server.server_close()

    def test_upload_keeps_existing_file_when_name_is_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_dir = root / "BookScan"
            input_dir.mkdir()
            original = input_dir / "sample.pdf"
            make_pdf(original, "first")
            other = root / "other.pdf"
            make_pdf(other, "second")
            original_bytes = original.read_bytes()
            new_bytes = other.read_bytes()
            state = AppState(input_dir, root / "output" / "vocabulary.json",
                             root / "output" / "scan_manifest.json", ROOT / "web", FakeAI(),
                             scale=1, quality=80, retries=0)
            result = state.save_upload("sample.pdf", BytesIO(new_bytes), len(new_bytes))
            self.assertNotEqual(result["name"], "sample.pdf")
            self.assertEqual(original.read_bytes(), original_bytes)
            self.assertEqual((input_dir / result["name"]).read_bytes(), new_bytes)
            with self.assertRaises(ValueError):
                state.save_upload("../bad.pdf", BytesIO(new_bytes), len(new_bytes))


if __name__ == "__main__":
    unittest.main()

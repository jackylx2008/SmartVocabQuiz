"""本机网页与扫描任务的 HTTP 接口。"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import fitz

from logging_config import get_logger
from smartvocabquiz.flows.scan_vocabulary import run
from smartvocabquiz.modules.scan_ledger import load_ledger, migrate_existing_output, sha256_file


logger = get_logger(__name__)


class AppState:
    def __init__(self, input_dir: Path, output_file: Path, ledger_file: Path, web_dir: Path,
                 ai: Any, *, scale: float, quality: int, retries: int):
        self.input_dir = input_dir
        self.output_file = output_file
        self.ledger_file = ledger_file
        self.web_dir = web_dir
        self.ai = ai
        self.scale = scale
        self.quality = quality
        self.retries = retries
        self.lock = threading.Lock()
        self._ai_status_lock = threading.Lock()
        self._ai_status_cache: dict[str, Any] | None = None
        self._ai_status_checked_at = 0.0
        self.job: dict[str, Any] = {"state": "idle", "message": "准备就绪", "new_pdf_count": 0}
        self._hash_cache: dict[Path, tuple[int, int, str]] = {}
        try:
            migrate_existing_output(input_dir, output_file, ledger_file, model=ai.model,
                                    scale=scale, quality=quality)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            self.job = {"state": "error", "message": str(exc), "new_pdf_count": 0}
            logger.exception("迁移现有扫描记录失败")

    def status(self) -> dict[str, Any]:
        ledger = load_ledger(self.ledger_file)
        pdfs = []
        for pdf in sorted(self.input_dir.glob("*.pdf"), key=lambda path: path.name.casefold()):
            pdf_id = self._file_hash(pdf)
            record = ledger["documents"].get(pdf_id)
            pdfs.append({
                "name": pdf.name,
                "id": pdf_id,
                "status": "scanned" if record else "pending",
                "entry_count": len(record["entries"]) if record else 0,
            })
        entry_count = 0
        if self.output_file.is_file():
            entry_count = json.loads(self.output_file.read_text(encoding="utf-8")).get("entry_count", 0)
        with self.lock:
            job = dict(self.job)
        return {
            "job": job,
            "pdfs": pdfs,
            "counts": {
                "pdf_total": len(pdfs),
                "scanned": sum(item["status"] == "scanned" for item in pdfs),
                "pending": sum(item["status"] == "pending" for item in pdfs),
                "entry_count": entry_count,
            },
        }

    def ai_status(self, *, refresh: bool = False) -> dict[str, Any]:
        """只读探测服务和配置模型；短暂缓存，避免页面轮询频繁请求 AI。"""
        with self._ai_status_lock:
            if not refresh and self._ai_status_cache is not None:
                if time.monotonic() - self._ai_status_checked_at < 5:
                    return dict(self._ai_status_cache)
            try:
                self.ai.check_available()
                available = True
            except Exception as exc:
                logger.debug("本地 AI 可用性检查未通过：%s", type(exc).__name__)
                available = False
            result = {"available": available,
                      "label": "本地 AI 已启动" if available else "本地 AI 未启动或不可用"}
            self._ai_status_cache = result
            self._ai_status_checked_at = time.monotonic()
            return dict(result)

    def start_scan(self) -> bool:
        with self.lock:
            if self.job["state"] == "running":
                return False
            self.job = {"state": "running", "message": "正在检查 PDF 哈希…", "new_pdf_count": 0}
        threading.Thread(target=self._scan_worker, daemon=True, name="vocabulary-scan").start()
        return True

    def save_upload(self, filename: str, stream: Any, length: int) -> dict[str, Any]:
        """把浏览器选中的 PDF 存入 BookScan，绝不覆盖已有文件。"""
        with self.lock:
            if self.job["state"] == "running":
                raise ValueError("扫描期间暂不能添加 PDF")
        if not filename or filename != Path(filename).name or not filename.lower().endswith(".pdf"):
            raise ValueError("只能上传带 .pdf 后缀的文件名")
        if any(char in filename for char in ("/", "\\", ":", "\x00")):
            raise ValueError("PDF 文件名包含不允许的字符")
        if length <= 0 or length > 100 * 1024 * 1024:
            raise ValueError("PDF 大小必须在 1 字节到 100 MB 之间")
        self.input_dir.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.input_dir, prefix=".upload-", suffix=".tmp",
                                             delete=False) as target:
                temporary = Path(target.name)
                remaining = length
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("PDF 上传未完成")
                    target.write(chunk)
                    remaining -= len(chunk)
            with temporary.open("rb") as check:
                if check.read(5) != b"%PDF-":
                    raise ValueError("文件内容不是 PDF")
            try:
                with fitz.open(temporary) as document:
                    if len(document) == 0:
                        raise ValueError("PDF 没有页面")
            except fitz.FileDataError as exc:
                raise ValueError("PDF 文件无法读取") from exc
            pdf_id = sha256_file(temporary)
            target_path = self.input_dir / filename
            if target_path.exists() and sha256_file(target_path) == pdf_id:
                return {"name": target_path.name, "id": pdf_id, "already_present": True}
            if target_path.exists():
                stem = Path(filename).stem
                target_path = self.input_dir / f"{stem}-{pdf_id[:12]}.pdf"
            suffix = 2
            while target_path.exists():
                if sha256_file(target_path) == pdf_id:
                    return {"name": target_path.name, "id": pdf_id, "already_present": True}
                target_path = self.input_dir / f"{Path(filename).stem}-{pdf_id[:12]}-{suffix}.pdf"
                suffix += 1
            try:
                with temporary.open("rb") as source, target_path.open("xb") as destination:
                    shutil.copyfileobj(source, destination, 1024 * 1024)
            except Exception:
                target_path.unlink(missing_ok=True)
                raise
            return {"name": target_path.name, "id": pdf_id, "already_present": False}
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _scan_worker(self) -> None:
        try:
            result = run(self.input_dir, self.output_file, self.ai, scale=self.scale,
                         quality=self.quality, retries=self.retries, ledger_file=self.ledger_file,
                         progress=self._progress)
            count = result["new_pdf_count"]
            message = f"扫描完成：新增 {count} 份 PDF，词库共 {result['vocabulary']['entry_count']} 项"
            with self.lock:
                self.job = {"state": "done", "message": message, "new_pdf_count": count}
        except Exception as exc:
            logger.exception("扫描失败")
            with self.lock:
                self.job = {"state": "error", "message": str(exc), "new_pdf_count": 0}

    def _progress(self, message: str) -> None:
        with self.lock:
            self.job["message"] = message

    def _file_hash(self, path: Path) -> str:
        stat = path.stat()
        with self.lock:
            cached = self._hash_cache.get(path)
        if cached and cached[:2] == (stat.st_size, stat.st_mtime_ns):
            return cached[2]
        digest = sha256_file(path)
        with self.lock:
            self._hash_cache[path] = (stat.st_size, stat.st_mtime_ns, digest)
        return digest


def make_server(state: AppState, port: int = 0) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if not self._local_host():
                self._json({"error": "请求地址不匹配"}, 403)
                return
            route = urlparse(self.path).path
            try:
                if route == "/api/status":
                    self._json(state.status())
                elif route == "/api/ai-status":
                    refresh = parse_qs(urlparse(self.path).query).get("refresh") == ["1"]
                    self._json(state.ai_status(refresh=refresh))
                elif route == "/api/vocabulary":
                    if state.output_file.is_file():
                        self._json(json.loads(state.output_file.read_text(encoding="utf-8")))
                    else:
                        self._json({"entries": [], "entry_count": 0})
                elif route == "/download/vocabulary.json":
                    if not state.output_file.is_file():
                        self._json({"error": "词库尚未生成"}, 404)
                    else:
                        self._bytes(state.output_file.read_bytes(), "application/json; charset=utf-8",
                                    attachment="vocabulary.json")
                elif route in ("/", "/index.html", "/scan", "/scan.html", "/quiz", "/quiz.html",
                               "/app.js", "/ai-status.js", "/styles.css"):
                    name = {"/": "index.html", "/scan": "scan.html", "/quiz": "quiz.html"}.get(
                        route, route.lstrip("/"))
                    mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}[
                        Path(name).suffix]
                    self._bytes((state.web_dir / name).read_bytes(), f"{mime}; charset=utf-8")
                else:
                    self._json({"error": "未找到页面"}, 404)
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                logger.exception("网页请求失败")
                self._json({"error": str(exc)}, 500)

        def do_POST(self) -> None:
            if not self._local_host():
                self._json({"error": "请求地址不匹配"}, 403)
                return
            route = urlparse(self.path).path
            if route not in ("/api/scan", "/api/upload"):
                self._json({"error": "未找到接口"}, 404)
                return
            if self.headers.get("X-SmartVocabQuiz") != "1":
                self._json({"error": "请求未获授权"}, 403)
                return
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                self._json({"error": "请求来源不匹配"}, 403)
                return
            if route == "/api/upload":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    filename = unquote(self.headers.get("X-Filename", ""))
                    self._json(state.save_upload(filename, self.rfile, length), 201)
                except (OSError, ValueError) as exc:
                    self._json({"error": str(exc)}, 400)
                return
            if not state.start_scan():
                self._json({"error": "扫描任务正在运行"}, 409)
                return
            self._json({"started": True}, 202)

        def _local_host(self) -> bool:
            host = self.headers.get("Host", "")
            port_text = str(self.server.server_port)
            return host in (f"127.0.0.1:{port_text}", f"localhost:{port_text}")

        def _json(self, value: Any, status: int = 200) -> None:
            self._bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8", status)

        def _bytes(self, value: bytes, content_type: str, status: int = 200,
                   attachment: str | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(value)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'")
            if attachment:
                self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
            self.end_headers()
            self.wfile.write(value)

        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("HTTP %s", format % args)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)

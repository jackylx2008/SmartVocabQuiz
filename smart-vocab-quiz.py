"""本地词汇扫描网页入口。

用途：
  启动仅在本机可访问的功能选择页。扫描词汇页可查看词库和识别新增 PDF；
  生成试题页当前仅保留入口。

配置文件：
  config.yaml 保存目录、服务及网页参数；common.env 保存本机 AI 凭据。

可选参数：
  --port        网页端口；0 表示自动分配，默认取 config.yaml。
  --no-browser  只启动服务，不自动打开浏览器。

示例：
  python smart-vocab-quiz.py

输出：
  词库保存到 output/vocabulary.json；扫描记录保存到 output/scan_manifest.json。
  按 Ctrl+C 停止本地网页服务。
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

from logging_config import configure_utf8_stdio, get_logger, setup_logger


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from smartvocabquiz.config_loader import load_config, resolve_path
from smartvocabquiz.modules.local_ai import LocalAIClient
from smartvocabquiz.web_server import AppState, make_server


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, help="网页端口，0 表示自动分配")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()
    try:
        config = load_config(ROOT)
        setup_logger(config["app"].get("log_level", "INFO"))
        logger = get_logger(__name__)
        app = config["app"]
        ai_config = config["llamacpp"]
        ai = LocalAIClient(
            ai_config["base_url"], ai_config["model"], ai_config.get("api_key", ""),
            int(ai_config["timeout_seconds"]), int(ai_config["max_tokens"]),
        )
        flow = config["flows"]["scan_vocabulary"]
        state = AppState(
            resolve_path(ROOT, app["input_dir"]),
            resolve_path(ROOT, app["output_file"]),
            resolve_path(ROOT, app["scan_record_file"]),
            ROOT / "web", ai,
            scale=float(flow["render_scale"]), quality=int(flow["image_quality"]),
            retries=int(flow["retries"]),
        )
        port = args.port if args.port is not None else int(config["web"]["port"])
        server = make_server(state, port)
        url = f"http://127.0.0.1:{server.server_port}/"
        logger.info("本地网页已启动：%s", url)
        print(f"打开 {url}；按 Ctrl+C 退出")
        server_thread = threading.Thread(target=server.serve_forever, daemon=True, name="vocabulary-web")
        server_thread.start()
        try:
            if not args.no_browser:
                webbrowser.open(url)
            while server_thread.is_alive():
                server_thread.join(timeout=0.5)
        except KeyboardInterrupt:
            logger.info("正在关闭本地网页")
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)
        return 0
    except Exception:
        get_logger(__name__).exception("启动失败")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

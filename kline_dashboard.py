from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import sys
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse


NOTE_FIELD_PATTERN = re.compile(r"^-\s*([^：:]+)[：:]\s*(.+?)\s*$", re.MULTILINE)
IMAGE_PATTERN = re.compile(r"!\[\[([^\]]+)\]\]")
PROFIT_PATTERN = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*(?:%|pct)", re.IGNORECASE)
CODE_PATTERN = re.compile(r"(?:^|\s)(\d{6})$")
DATE_RANGE_PATTERN = re.compile(r"(\d{8})\s*[-—–_]\s*(\d{8})")


def resource_path(*parts: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root.joinpath(*parts)


def _parse_datetime(value: str, fallback: float) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d_%H-%M-%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return datetime.fromtimestamp(fallback)


def _safe_relative_path(root: Path, value: str) -> Path | None:
    candidate = (root / value.replace("/", "\\")).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


@dataclass(frozen=True)
class ReviewRecord:
    record_id: str
    stock: str
    code: str
    profit: float | None
    profit_text: str
    range_start: str
    range_end: str
    recorded_at: datetime
    note_name: str
    chart_image: str
    result_image: str

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.record_id,
            "stock": self.stock,
            "code": self.code,
            "profit": self.profit,
            "profitText": self.profit_text,
            "rangeStart": self.range_start,
            "rangeEnd": self.range_end,
            "recordedAt": self.recorded_at.isoformat(timespec="seconds"),
            "noteName": self.note_name,
            "chartImage": self._media_url(self.chart_image),
            "resultImage": self._media_url(self.result_image),
        }

    @staticmethod
    def _media_url(path: str) -> str:
        return f"/media?path={quote(path, safe='')}" if path else ""


def parse_review_note(note_path: Path, root: Path) -> ReviewRecord | None:
    try:
        text = note_path.read_text(encoding="utf-8-sig")
        stat = note_path.stat()
    except (OSError, UnicodeError):
        return None

    fields = {key.strip(): value.strip() for key, value in NOTE_FIELD_PATTERN.findall(text)}
    title_match = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else note_path.stem

    stock_field = fields.get("股票", "")
    code_match = CODE_PATTERN.search(stock_field)
    code = code_match.group(1) if code_match else ""
    stock = stock_field[: code_match.start()].strip() if code_match else stock_field.strip()
    if not stock:
        title_profit = PROFIT_PATTERN.search(title)
        stock = title[: title_profit.start()].strip() if title_profit else title.strip()
    stock = stock or "未知股票"

    profit_text = fields.get("本局收益", "")
    profit_match = PROFIT_PATTERN.search(profit_text or title)
    profit = float(profit_match.group(1)) if profit_match else None
    if not profit_text:
        profit_text = f"{profit:.2f}%" if profit is not None else "未识别"

    range_match = DATE_RANGE_PATTERN.search(fields.get("训练区间", ""))
    range_start = range_match.group(1) if range_match else ""
    range_end = range_match.group(2) if range_match else ""
    recorded_at = _parse_datetime(fields.get("记录时间", ""), stat.st_mtime)

    images = [value.strip().replace("\\", "/") for value in IMAGE_PATTERN.findall(text)]
    chart_image = images[0] if images else ""
    result_image = images[1] if len(images) > 1 else ""
    relative_note = note_path.resolve().relative_to(root.resolve()).as_posix()
    record_id = hashlib.sha1(relative_note.encode("utf-8")).hexdigest()[:16]

    return ReviewRecord(
        record_id=record_id,
        stock=stock,
        code=code,
        profit=profit,
        profit_text=profit_text,
        range_start=range_start,
        range_end=range_end,
        recorded_at=recorded_at,
        note_name=note_path.name,
        chart_image=chart_image,
        result_image=result_image,
    )


class ReviewRepository:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._lock = threading.Lock()
        self._fingerprint: tuple[tuple[str, int, int], ...] = ()
        self._payload: dict[str, object] = self._build_payload([])

    def snapshot(self) -> dict[str, object]:
        self.root.mkdir(parents=True, exist_ok=True)
        notes = sorted(self.root.glob("*.md"))
        fingerprint = tuple(
            (note.name, note.stat().st_mtime_ns, note.stat().st_size)
            for note in notes
            if note.is_file()
        )
        with self._lock:
            if fingerprint != self._fingerprint:
                records = [record for note in notes if (record := parse_review_note(note, self.root))]
                records.sort(key=lambda item: item.recorded_at, reverse=True)
                self._payload = self._build_payload(records)
                self._fingerprint = fingerprint
            return self._payload

    def resolve_media(self, relative_path: str) -> Path | None:
        candidate = _safe_relative_path(self.root, relative_path)
        if candidate is None or not candidate.is_file():
            return None
        return candidate

    @staticmethod
    def _build_payload(records: list[ReviewRecord]) -> dict[str, object]:
        valid = [record for record in records if record.profit is not None]
        profits = [record.profit for record in valid if record.profit is not None]
        wins = [profit for profit in profits if profit > 0]
        losses = [profit for profit in profits if profit < 0]

        return {
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "summary": {
                "total": len(records),
                "valid": len(valid),
                "average": round(sum(profits) / len(profits), 2) if profits else 0.0,
                "winRate": round(len(wins) * 100 / len(profits), 1) if profits else 0.0,
                "best": round(max(profits), 2) if profits else 0.0,
                "worst": round(min(profits), 2) if profits else 0.0,
                "positive": len(wins),
                "negative": len(losses),
                "flat": len(profits) - len(wins) - len(losses),
            },
            "records": [record.to_json() for record in records],
        }


class DashboardServer:
    def __init__(self, review_dir: Path, port: int = 0) -> None:
        self.repository = ReviewRepository(review_dir)
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self._server is None:
            return ""
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/"

    def start(self, open_browser: bool = True) -> str:
        if self._server is None:
            handler = self._make_handler()
            self._server = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
        if open_browser:
            webbrowser.open(self.url)
        return self.url

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        repository = self.repository
        static_root = resource_path("webui").resolve()

        class DashboardHandler(BaseHTTPRequestHandler):
            server_version = "KlineReviewDashboard/1.0"

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/api/dashboard":
                    self._send_json(repository.snapshot())
                    return
                if parsed.path == "/api/health":
                    self._send_json({"ok": True})
                    return
                if parsed.path == "/media":
                    relative_path = unquote(parse_qs(parsed.query).get("path", [""])[0])
                    media_path = repository.resolve_media(relative_path)
                    if media_path is None:
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    self._send_file(media_path, cache=False)
                    return

                requested = "index.html" if parsed.path in ("", "/") else parsed.path.lstrip("/")
                static_path = _safe_relative_path(static_root, requested)
                if static_path is None or not static_path.is_file():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._send_file(static_path, cache=True)

            def _send_json(self, payload: dict[str, object]) -> None:
                data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _send_file(self, path: Path, cache: bool) -> None:
                try:
                    data = path.read_bytes()
                except OSError:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=3600" if cache else "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        return DashboardHandler


def main() -> int:
    parser = argparse.ArgumentParser(description="同花顺 K线训练复盘本地数据看板")
    parser.add_argument("--dir", type=Path, help="复盘 Markdown 所在目录")
    parser.add_argument("--port", type=int, default=8765, help="本地监听端口")
    parser.add_argument("--no-browser", action="store_true", help="启动时不打开浏览器")
    args = parser.parse_args()

    review_dir = args.dir
    if review_dir is None:
        from kline_recorder import load_config

        review_dir = Path(load_config()["paths"]["obsidian_dir"])
    server = DashboardServer(review_dir, port=args.port)
    print(server.start(open_browser=not args.no_browser), flush=True)
    try:
        if server._server is not None:
            server._thread.join()
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

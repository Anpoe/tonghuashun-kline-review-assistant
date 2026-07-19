from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.request import urlopen

from kline_dashboard import DashboardServer, ReviewRepository, parse_review_note


class DashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "images").mkdir()
        (self.root / "images" / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\nmock")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_note(self, name: str, stock: str, profit: str) -> Path:
        note = self.root / name
        note.write_text(
            f"# {stock} {profit}\n\n"
            f"- 股票：{stock} 002758\n"
            "- 训练区间：20251212 - 20260403\n"
            f"- 本局收益：{profit}\n"
            "- 记录时间：2026-06-20 14:03:14\n\n"
            "![[images/chart.png]]\n",
            encoding="utf-8",
        )
        return note

    def test_parses_current_note_format(self) -> None:
        note = self.write_note("current.md", "浙农股份", "2.11%")
        record = parse_review_note(note, self.root)
        self.assertIsNotNone(record)
        self.assertEqual(record.stock, "浙农股份")
        self.assertEqual(record.code, "002758")
        self.assertEqual(record.profit, 2.11)
        self.assertEqual(record.range_start, "20251212")

    def test_parses_legacy_pct_profit(self) -> None:
        note = self.write_note("legacy.md", "信科移动", "6.54pct")
        record = parse_review_note(note, self.root)
        self.assertIsNotNone(record)
        self.assertEqual(record.stock, "信科移动")
        self.assertEqual(record.profit, 6.54)

    def test_repository_summary_and_media_guard(self) -> None:
        self.write_note("winner.md", "浙农股份", "2.00%")
        self.write_note("loser.md", "双汇发展", "-1.00%")
        repository = ReviewRepository(self.root)
        payload = repository.snapshot()
        self.assertEqual(payload["summary"]["total"], 2)
        self.assertEqual(payload["summary"]["average"], 0.5)
        self.assertEqual(payload["summary"]["winRate"], 50.0)
        self.assertIsNotNone(repository.resolve_media("images/chart.png"))
        self.assertIsNone(repository.resolve_media("../outside.png"))

    def test_local_http_api(self) -> None:
        self.write_note("record.md", "浙农股份", "2.11%")
        server = DashboardServer(self.root)
        url = server.start(open_browser=False)
        try:
            with urlopen(url + "api/dashboard", timeout=3) as response:
                payload = json.load(response)
            with urlopen(url, timeout=3) as response:
                index = response.read()
            self.assertEqual(payload["summary"]["total"], 1)
            self.assertIn(b"<!doctype html>", index)
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()

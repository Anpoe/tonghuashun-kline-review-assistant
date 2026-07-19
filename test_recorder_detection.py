from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from kline_recorder import (
    OcrReader,
    Rect,
    active_config_path,
    has_result_anchor,
    is_session_start,
    normalize_tags,
    ocr_item_rect,
    requires_initial_setup,
    write_obsidian_note,
)


class StubOcrReader(OcrReader):
    def __init__(self) -> None:
        self.enabled = True
        self.engine = object()
        self.initialization_error = ""
        self.last_error = ""

    def read_items(self, _image: Image.Image) -> list[object]:
        return [[[[20, 40], [120, 40], [120, 80], [20, 80]], "30/30", 0.99]]


class RecorderDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "session": {
                "require_start_30_30": True,
                "start_keywords": ["30/30", "3030"],
            }
        }

    def test_session_start_accepts_common_ocr_forms(self) -> None:
        self.assertTrue(is_session_start("结算\n30/30", self.config))
        self.assertTrue(is_session_start("结算 30／30", self.config))
        self.assertTrue(is_session_start("结算3030", self.config))
        self.assertFalse(is_session_start("结算 6/30", self.config))

    def test_result_anchor_accepts_split_ocr_text(self) -> None:
        self.assertTrue(has_result_anchor("南极电商\n股票区间涨幅\n-8.03%"))
        self.assertTrue(has_result_anchor("股票区间\n涨幅 -8.03%"))

    def test_focused_ocr_maps_boxes_back_to_window_coordinates(self) -> None:
        image = Image.new("RGB", (656, 1348), "black")
        items = StubOcrReader().read_region_items(image, Rect(100, 900, 300, 1100), scale=2.0)
        self.assertEqual(len(items), 1)
        rect = ocr_item_rect(items[0])
        self.assertEqual(rect, Rect(110, 920, 160, 940))
        self.assertEqual(items[0][1], "30/30")

    def test_fresh_source_checkout_uses_template_and_requires_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            template = root / "config.default.yaml"
            template.write_text("paths:\n  obsidian_dir: ''\n", encoding="utf-8")
            with (
                patch("kline_recorder.IS_FROZEN", False),
                patch("kline_recorder.SOURCE_CONFIG_PATH", root / "config.yaml"),
                patch("kline_recorder.USER_CONFIG_PATH", root / "user" / "config.yaml"),
                patch("kline_recorder.BUNDLED_CONFIG_PATH", template),
            ):
                self.assertEqual(active_config_path(), template)
                self.assertTrue(requires_initial_setup())

    def test_source_checkout_reuses_existing_user_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            template = root / "config.default.yaml"
            user_config = root / "user" / "config.yaml"
            template.write_text("paths:\n  obsidian_dir: ''\n", encoding="utf-8")
            user_config.parent.mkdir()
            user_config.write_text("paths:\n  obsidian_dir: C:/Reviews\n", encoding="utf-8")
            with (
                patch("kline_recorder.IS_FROZEN", False),
                patch("kline_recorder.SOURCE_CONFIG_PATH", root / "config.yaml"),
                patch("kline_recorder.USER_CONFIG_PATH", user_config),
                patch("kline_recorder.BUNDLED_CONFIG_PATH", template),
            ):
                self.assertEqual(active_config_path(), user_config)
                self.assertFalse(requires_initial_setup())

    def test_writes_normalized_tags_to_markdown(self) -> None:
        tags = normalize_tags(
            (
                {"name": "突破", "color": "#EF4444"},
                {"name": "低吸", "color": "not-a-color"},
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("kline_recorder.crop_result_card", return_value=Image.new("RGB", (40, 20), "black")):
                note_path = write_obsidian_note(
                    Path(temp_dir),
                    "images",
                    Image.new("RGB", (80, 60), "black"),
                    Image.new("RGB", (80, 60), "black"),
                    {"stock": "测试股份", "code": "002758", "profit": "2.11%", "date_range": "20251212 - 20260403"},
                    {},
                    tags=tags,
                )
            text = note_path.read_text(encoding="utf-8")
        self.assertIn("- 标签：突破, 低吸", text)
        self.assertIn('- 标签颜色：{"突破":"#ef4444","低吸":"#6b7280"}', text)


if __name__ == "__main__":
    unittest.main()

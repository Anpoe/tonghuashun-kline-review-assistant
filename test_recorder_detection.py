from __future__ import annotations

import unittest

from PIL import Image

from kline_recorder import OcrReader, Rect, has_result_anchor, is_session_start, ocr_item_rect


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


if __name__ == "__main__":
    unittest.main()

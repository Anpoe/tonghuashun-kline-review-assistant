from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from kline_recorder import (
    _BLUE_BORDER_CAPTURE_CACHE,
    OcrReader,
    Rect,
    active_config_path,
    capture_window,
    chart_paint_change_ratio,
    find_duplicate_note,
    has_training_controls,
    has_result_anchor,
    image_pixel_change_ratio,
    is_home_page_visual,
    is_result_page_visual_ready,
    is_training_page,
    is_session_start,
    moving_average_lines_loaded,
    normalize_tags,
    ocr_item_rect,
    parse_metadata,
    parse_result_metadata,
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


class StubResultCardOcr(StubOcrReader):
    def read_text(self, _image: Image.Image) -> str:
        return "海辰药业\n300584\n20260401-20260717\n股票区间涨幅 -4.28%"


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
        self.assertFalse(is_session_start("今日剩余次数 3/3 OCR误读30/30", self.config))
        self.assertTrue(has_training_controls("买入  观望", self.config))

    def test_training_page_requires_center_settlement_ring(self) -> None:
        image = Image.new("RGB", (656, 1348), "black")
        for x in range(0, 300):
            for y in range(1138, 1160):
                image.putpixel((x, y), (255, 128, 0))
        for x in range(300, 656):
            for y in range(1138, 1160):
                image.putpixel((x, y), (0, 100, 255))
        config = {
            "capture": {
                "training_control_region": {"left": 0, "top": 1138, "right": 656, "bottom": 1348},
                "training_center_ring_region": {"left": 240, "top": 1180, "right": 416, "bottom": 1348},
                "training_orange_pixels": 100,
                "training_blue_pixels": 100,
                "training_center_ring_pixels": 80,
            },
            "window": {"preferred_width": 656, "preferred_height": 1348},
        }
        self.assertFalse(is_training_page(image, config))
        for x in range(280, 300):
            for y in range(1200, 1220):
                image.putpixel((x, y), (255, 0, 80))
        self.assertTrue(is_training_page(image, config))

    def test_home_page_visual_uses_dense_orange_layout(self) -> None:
        config = {
            "capture": {"home_orange_pixels": 80000},
            "window": {"preferred_width": 656, "preferred_height": 1348},
        }
        image = Image.new("RGB", (656, 1348), "black")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 199, 199), fill=(255, 120, 0))
        self.assertFalse(is_home_page_visual(image, config))
        draw.rectangle((0, 200, 399, 399), fill=(255, 120, 0))
        self.assertTrue(is_home_page_visual(image, config))

    def test_result_page_waits_for_final_blue_action_button(self) -> None:
        config = {
            "capture": {
                "result_final_controls_region": {"left": 60, "top": 430, "right": 596, "bottom": 700},
                "result_final_blue_pixels": 20000,
            },
            "window": {"preferred_width": 656, "preferred_height": 1348},
        }
        image = Image.new("RGB", (656, 1348), "black")
        ImageDraw.Draw(image).rectangle((100, 540, 555, 590), fill=(45, 110, 235))
        self.assertTrue(is_result_page_visual_ready(image, config))

        image = Image.new("RGB", (656, 1348), "black")
        ImageDraw.Draw(image).rectangle((100, 540, 180, 590), fill=(45, 110, 235))
        self.assertFalse(is_result_page_visual_ready(image, config))

    def test_result_frame_change_ratio_detects_stability(self) -> None:
        previous = Image.new("RGB", (100, 100), "black")
        current = previous.copy()
        self.assertEqual(image_pixel_change_ratio(previous, current), 0.0)
        ImageDraw.Draw(current).rectangle((0, 0, 49, 49), fill="white")
        self.assertGreater(image_pixel_change_ratio(previous, current), 20.0)

    def test_metadata_accepts_long_dash_date_range(self) -> None:
        metadata = parse_metadata("海辰药业 300584\n20260401—20260717\n本局收益 1.65%")
        self.assertEqual(metadata["date_range"], "20260401 - 20260717")

    def test_metadata_ignores_stray_ocr_character_between_dates(self) -> None:
        metadata = parse_metadata(
            "康拓医疗\n688314\n股票区间涨幅 -16.35%\n20260306\n5.20260623"
        )
        self.assertEqual(metadata["date_range"], "20260306 - 20260623")

    def test_result_metadata_recovers_missing_fields_from_result_card(self) -> None:
        result_image = Image.new("RGB", (656, 1348), "black")
        config = {"capture": {}}
        with patch(
            "kline_recorder.crop_result_card",
            return_value=Image.new("RGB", (606, 198), "black"),
        ) as crop:
            metadata = parse_result_metadata(
                "本局收益 1.65%",
                result_image,
                [],
                config,
                StubResultCardOcr(),
            )
        crop.assert_called_once_with(result_image, config, [])
        self.assertEqual(metadata["stock"], "海辰药业")
        self.assertEqual(metadata["code"], "300584")
        self.assertEqual(metadata["profit"], "1.65%")
        self.assertEqual(metadata["date_range"], "20260401 - 20260717")

    def test_chart_requires_all_three_moving_average_lines(self) -> None:
        image = Image.new("RGB", (652, 415), (18, 18, 38))
        draw = ImageDraw.Draw(image)
        draw.line((40, 100, 600, 180), fill=(240, 25, 175), width=3)
        draw.line((80, 190, 600, 230), fill=(240, 175, 25), width=3)
        config = {
            "session": {
                "loaded_ma_required_lines": 3,
                "loaded_ma_min_pixels": 180,
                "loaded_ma_min_columns": 120,
            }
        }
        self.assertFalse(moving_average_lines_loaded(image, config))
        draw.line((180, 260, 600, 290), fill=(20, 125, 245), width=3)
        self.assertTrue(moving_average_lines_loaded(image, config))

    def test_chart_paint_stability_detects_newly_drawn_content(self) -> None:
        config = {"session": {"loaded_main_top_px": 55, "loaded_main_bottom_px": 470}}
        previous = Image.new("RGB", (652, 998), (18, 18, 38))
        current = previous.copy()
        self.assertEqual(chart_paint_change_ratio(previous, current, config), 0.0)
        ImageDraw.Draw(current).rectangle((100, 100, 250, 180), fill=(240, 25, 50))
        self.assertGreater(chart_paint_change_ratio(previous, current, config), 0.02)

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

    def test_blue_border_capture_reuses_recent_location(self) -> None:
        hwnd = 987654
        window_rect = Rect(100, 100, 756, 1448)
        capture_rect = Rect(120, 140, 776, 1488)
        located_image = Image.new("RGB", (656, 1348), "navy")
        cached_image = Image.new("RGB", (656, 1348), "black")
        _BLUE_BORDER_CAPTURE_CACHE.pop(hwnd, None)
        try:
            with (
                patch(
                    "kline_recorder.capture_training_window_by_blue_border",
                    return_value=(located_image, capture_rect),
                ) as locate,
                patch("kline_recorder.ImageGrab.grab", return_value=cached_image) as grab,
                patch("kline_recorder.time.monotonic", side_effect=(10.0, 10.1)),
            ):
                first = capture_window(
                    hwnd,
                    window_rect,
                    {"direct_window_capture": False, "locate_by_blue_border": True},
                )
                second = capture_window(
                    hwnd,
                    window_rect,
                    {"direct_window_capture": False, "locate_by_blue_border": True},
                )
            self.assertEqual(first.getpixel((0, 0)), (0, 0, 128))
            self.assertEqual(second.getpixel((0, 0)), (0, 0, 0))
            self.assertEqual(locate.call_count, 1)
            self.assertEqual(grab.call_count, 1)
        finally:
            _BLUE_BORDER_CAPTURE_CACHE.pop(hwnd, None)

    def test_direct_window_capture_never_uses_covering_desktop_pixels(self) -> None:
        hwnd = 123456
        window_rect = Rect(100, 100, 756, 1448)
        training_image = Image.new("RGB", (656, 1348), "navy")
        game_image = Image.new("RGB", (656, 1348), "red")
        with (
            patch("kline_recorder.capture_window_direct", return_value=training_image),
            patch("kline_recorder.ImageGrab.grab", return_value=game_image) as desktop_grab,
        ):
            captured = capture_window(hwnd, window_rect, {"direct_window_capture": True})
        self.assertEqual(captured.getpixel((0, 0)), (0, 0, 128))
        desktop_grab.assert_not_called()

    def test_direct_window_capture_failure_does_not_fall_back_to_desktop(self) -> None:
        hwnd = 123456
        window_rect = Rect(100, 100, 756, 1448)
        with (
            patch("kline_recorder.capture_window_direct", return_value=None),
            patch("kline_recorder.ImageGrab.grab") as desktop_grab,
        ):
            with self.assertRaises(RuntimeError):
                capture_window(hwnd, window_rect, {"direct_window_capture": True})
        desktop_grab.assert_not_called()

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


    def test_duplicate_note_matches_recent_training_identity(self) -> None:
        metadata = {
            "stock": "康拓医疗",
            "code": "688314",
            "profit": "7.58%",
            "date_range": "20260306 - 20260623",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            note_path = root / "2026-07-19 康拓医疗 7.58pct.md"
            note_path.write_text(
                "# 康拓医疗 7.58%\n\n"
                "- 股票：康拓医疗 688314\n"
                "- 训练区间：20260306 - 20260623\n"
                "- 本局收益：7.58%\n",
                encoding="utf-8",
            )
            duplicate = find_duplicate_note(root, Image.new("RGB", (20, 20), "black"), metadata)
        self.assertEqual(duplicate, note_path)


if __name__ == "__main__":
    unittest.main()

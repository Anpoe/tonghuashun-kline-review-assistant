from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import cv2
import numpy as np
import yaml
from PIL import Image, ImageChops, ImageGrab

try:
    import win32api
    import win32con
    import win32gui
except ImportError:
    win32api = None
    win32con = None
    win32gui = None


APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
IS_FROZEN = bool(getattr(sys, "frozen", False))
SOURCE_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
BUNDLED_CONFIG_PATH = Path(getattr(sys, "_MEIPASS", APP_DIR)) / "config.default.yaml"
USER_CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "KlineReviewAssistant"
USER_CONFIG_PATH = USER_CONFIG_DIR / "config.yaml"

StatusCallback = Callable[[str, str, str], None]
TagProvider = Callable[[], Iterable[object]]

DEFAULT_TAG_COLOR = "#6b7280"
TAG_COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{6}")

DEFAULT_FOCUSED_OCR_REGIONS = {
    "training_control_region": {"left": 0, "top": 1138, "right": 656, "bottom": 1348},
    "result_scan_region": {"left": 20, "top": 680, "right": 636, "bottom": 980},
}

def emit_status(
    callback: StatusCallback | None,
    state: str,
    message: str,
    detail: str = "",
) -> None:
    if callback is None:
        return
    try:
        callback(state, message, detail)
    except Exception:
        pass


def normalize_tags(values: Iterable[object]) -> list[dict[str, str]]:
    tags: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, str):
            raw_name = value
            raw_color = DEFAULT_TAG_COLOR
        elif isinstance(value, dict):
            raw_name = str(value.get("name", ""))
            raw_color = str(value.get("color", DEFAULT_TAG_COLOR))
        else:
            continue

        name = re.sub(r"[\r\n,，;；#]+", " ", raw_name).strip()
        name = " ".join(name.split())[:20]
        key = name.casefold()
        if not name or key in seen:
            continue
        color = raw_color.lower() if TAG_COLOR_PATTERN.fullmatch(raw_color) else DEFAULT_TAG_COLOR
        tags.append({"name": name, "color": color})
        seen.add(key)
    return tags


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def clamp(self, width: int, height: int) -> "Rect":
        return Rect(
            max(0, min(self.left, width)),
            max(0, min(self.top, height)),
            max(0, min(self.right, width)),
            max(0, min(self.bottom, height)),
        )


_BLUE_BORDER_CAPTURE_CACHE: dict[int, tuple[Rect, Rect, float]] = {}


def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def active_config_path() -> Path:
    if IS_FROZEN:
        return USER_CONFIG_PATH if USER_CONFIG_PATH.is_file() else BUNDLED_CONFIG_PATH
    if SOURCE_CONFIG_PATH.is_file():
        return SOURCE_CONFIG_PATH
    return USER_CONFIG_PATH if USER_CONFIG_PATH.is_file() else BUNDLED_CONFIG_PATH


def load_config(path: Path | None = None) -> dict:
    config_path = path or active_config_path()
    with config_path.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}

    # User settings survive upgrades. New options from the bundled template are
    # filled in without overwriting values the user already chose.
    if config_path != BUNDLED_CONFIG_PATH and BUNDLED_CONFIG_PATH.is_file():
        with BUNDLED_CONFIG_PATH.open("r", encoding="utf-8") as f:
            defaults = yaml.safe_load(f) or {}
        loaded = merge_config(defaults, loaded)
    return loaded


def merge_config(defaults: dict, overrides: dict) -> dict:
    merged = dict(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def save_user_config(config: dict) -> Path:
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    temporary = USER_CONFIG_PATH.with_suffix(".yaml.tmp")
    temporary.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    temporary.replace(USER_CONFIG_PATH)
    return USER_CONFIG_PATH


def save_runtime_config(config: dict) -> Path:
    if IS_FROZEN or not SOURCE_CONFIG_PATH.is_file():
        return save_user_config(config)
    SOURCE_CONFIG_PATH.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return SOURCE_CONFIG_PATH


def initialize_user_config() -> dict:
    if USER_CONFIG_PATH.is_file():
        return load_config(USER_CONFIG_PATH)
    if not BUNDLED_CONFIG_PATH.is_file():
        raise FileNotFoundError(f"Default config not found: {BUNDLED_CONFIG_PATH}")
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BUNDLED_CONFIG_PATH, USER_CONFIG_PATH)
    return load_config(USER_CONFIG_PATH)


def requires_initial_setup() -> bool:
    config_path = active_config_path()
    if config_path == BUNDLED_CONFIG_PATH:
        return True
    try:
        config = load_config(config_path)
    except Exception:
        return True
    output_dir = str(config.get("paths", {}).get("obsidian_dir", "")).strip()
    return not output_dir


def rect_from_config(value: dict) -> Rect:
    return Rect(
        int(value["left"]),
        int(value["top"]),
        int(value["right"]),
        int(value["bottom"]),
    )


def scale_rect(rect: Rect, source_width: int, source_height: int, target_width: int, target_height: int) -> Rect:
    return Rect(
        round(rect.left * target_width / source_width),
        round(rect.top * target_height / source_height),
        round(rect.right * target_width / source_width),
        round(rect.bottom * target_height / source_height),
    )


def rect_from_config_scaled(value: dict, image: Image.Image, config: dict) -> Rect:
    rect = rect_from_config(value)
    window_cfg = config.get("window", {})
    source_width = int(window_cfg.get("preferred_width", image.width))
    source_height = int(window_cfg.get("preferred_height", image.height))
    if image.width == source_width and image.height == source_height:
        return rect
    return scale_rect(rect, source_width, source_height, image.width, image.height)


def list_visible_windows() -> list[tuple[int, str, str, Rect]]:
    if win32gui is None:
        raise RuntimeError("pywin32 is required on Windows.")

    windows: list[tuple[int, str, str, Rect]] = []

    def callback(hwnd: int, _extra: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        rect = Rect(left, top, right, bottom)
        if rect.width > 100 and rect.height > 100:
            windows.append((hwnd, title, win32gui.GetClassName(hwnd), rect))
        return True

    win32gui.EnumWindows(callback, None)
    return windows


def title_needles(title_contains: str | Iterable[str]) -> list[str]:
    values = [title_contains] if isinstance(title_contains, str) else list(title_contains)
    return [str(value).casefold() for value in values if str(value).strip()]


def find_window(title_contains: str | Iterable[str], window_config: dict) -> tuple[int, Rect] | None:
    needles = title_needles(title_contains)
    min_width = int(window_config.get("min_width", 100))
    min_height = int(window_config.get("min_height", 100))
    preferred_width = int(window_config.get("preferred_width", min_width))
    preferred_height = int(window_config.get("preferred_height", min_height))

    candidates: list[tuple[float, int, Rect]] = []
    for hwnd, title, _class_name, rect in list_visible_windows():
        if not any(needle in title.casefold() for needle in needles):
            continue
        if rect.width < min_width or rect.height < min_height:
            continue
        width_score = abs(rect.width - preferred_width) / preferred_width
        height_score = abs(rect.height - preferred_height) / preferred_height
        aspect_score = abs((rect.width / rect.height) - (preferred_width / preferred_height))
        candidates.append((width_score + height_score + aspect_score, hwnd, rect))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1], candidates[0][2]


def print_window_diagnostics(filter_text: str | Iterable[str] = "") -> None:
    needles = title_needles(filter_text)
    print("Visible windows:")
    for hwnd, title, class_name, rect in list_visible_windows():
        if needles and not any(needle in title.casefold() for needle in needles):
            continue
        print(
            f"- hwnd={hwnd} size={rect.width}x{rect.height} "
            f"pos=({rect.left},{rect.top}) class={class_name} title={title}"
        )


def capture_window(hwnd: int, rect: Rect, window_config: dict | None = None) -> Image.Image:
    window_config = window_config or {}
    if bool(window_config.get("locate_by_blue_border", False)):
        now = time.monotonic()
        refresh_seconds = max(0.1, float(window_config.get("blue_border_refresh_seconds", 0.5)))
        cached = _BLUE_BORDER_CAPTURE_CACHE.get(hwnd)
        if cached is not None:
            cached_window_rect, capture_rect, located_at = cached
            if cached_window_rect == rect and now - located_at < refresh_seconds:
                try:
                    return ImageGrab.grab(
                        bbox=(capture_rect.left, capture_rect.top, capture_rect.right, capture_rect.bottom),
                        all_screens=True,
                    ).convert("RGB")
                except Exception:
                    _BLUE_BORDER_CAPTURE_CACHE.pop(hwnd, None)
        located = capture_training_window_by_blue_border(window_config, rect)
        if located is not None:
            image, capture_rect = located
            _BLUE_BORDER_CAPTURE_CACHE[hwnd] = (rect, capture_rect, now)
            return image

    if win32gui is not None and win32con is not None:
        if bool(window_config.get("bring_to_front", False)):
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetWindowPos(
                    hwnd,
                    win32con.HWND_TOPMOST,
                    rect.left,
                    rect.top,
                    rect.width,
                    rect.height,
                    win32con.SWP_SHOWWINDOW,
                )
                time.sleep(0.05)
                win32gui.SetWindowPos(
                    hwnd,
                    win32con.HWND_NOTOPMOST,
                    rect.left,
                    rect.top,
                    rect.width,
                    rect.height,
                    win32con.SWP_SHOWWINDOW,
                )
            except Exception:
                pass
    return ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom)).convert("RGB")


def capture_training_window_by_blue_border(
    window_config: dict,
    expected_rect: Rect | None = None,
) -> tuple[Image.Image, Rect] | None:
    full = ImageGrab.grab(all_screens=True).convert("RGB")
    arr = np.asarray(full)
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, np.array([95, 80, 80], dtype=np.uint8), np.array([115, 255, 255], dtype=np.uint8))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(closed, 8)

    preferred_width = int(window_config.get("preferred_width", 656))
    preferred_height = int(window_config.get("preferred_height", 1348))
    min_width = max(220, preferred_width * 0.42)
    min_height = max(480, preferred_height * 0.42)
    max_width = preferred_width * 2.5
    max_height = preferred_height * 2.5

    virtual_left = 0
    virtual_top = 0
    if win32api is not None and win32con is not None:
        try:
            virtual_left = int(win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN))
            virtual_top = int(win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN))
        except Exception:
            pass

    candidates: list[tuple[float, Rect, Rect]] = []
    for index in range(1, count):
        x, y, width, height, area = stats[index]
        if area < 1000:
            continue
        if not (min_width <= width <= max_width and min_height <= height <= max_height):
            continue
        aspect_score = abs((width / height) - (preferred_width / preferred_height))
        size_score = abs(width - preferred_width) / preferred_width + abs(height - preferred_height) / preferred_height
        local_rect = Rect(int(x), int(y), int(x + width), int(y + height))
        screen_rect = Rect(
            local_rect.left + virtual_left,
            local_rect.top + virtual_top,
            local_rect.right + virtual_left,
            local_rect.bottom + virtual_top,
        )
        position_score = 0.0
        if expected_rect is not None:
            center_dx = abs(
                (screen_rect.left + screen_rect.right) / 2
                - (expected_rect.left + expected_rect.right) / 2
            )
            center_dy = abs(
                (screen_rect.top + screen_rect.bottom) / 2
                - (expected_rect.top + expected_rect.bottom) / 2
            )
            position_score = center_dx / max(1, expected_rect.width) + center_dy / max(1, expected_rect.height)
        candidates.append((aspect_score + size_score + position_score * 3.0, local_rect, screen_rect))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    local_rect = candidates[0][1]
    screen_rect = candidates[0][2]
    return full.crop((local_rect.left, local_rect.top, local_rect.right, local_rect.bottom)), screen_rect


def crop_relative(image: Image.Image, rect: Rect) -> Image.Image:
    rect = rect.clamp(*image.size)
    return image.crop((rect.left, rect.top, rect.right, rect.bottom))


def find_result_card_rect(image: Image.Image) -> Rect | None:
    arr = np.asarray(image.convert("RGB"))
    height, width = arr.shape[:2]
    red = arr[:, :, 0].astype(np.int16)
    green = arr[:, :, 1].astype(np.int16)
    blue = arr[:, :, 2].astype(np.int16)

    info_bar_mask = (
        (blue >= 55)
        & (green >= 35)
        & (red <= 90)
        & (blue >= red + 10)
        & (blue >= green + 4)
    )

    search_top = int(height * 0.35)
    search_bottom = int(height * 0.9)
    row_counts = info_bar_mask[search_top:search_bottom].sum(axis=1)

    def find_runs(threshold: int) -> list[tuple[int, int, int]]:
        runs: list[tuple[int, int, int]] = []
        start: int | None = None
        for index, count in enumerate(row_counts):
            if count >= threshold:
                if start is None:
                    start = index
            elif start is not None:
                if index - start >= 20:
                    runs.append((start, index, int(row_counts[start:index].sum())))
                start = None
        if start is not None and len(row_counts) - start >= 20:
            runs.append((start, len(row_counts), int(row_counts[start:].sum())))
        return runs

    threshold = max(80, int(width * 0.28))
    runs = find_runs(threshold)
    if not runs:
        runs = find_runs(max(50, int(width * 0.18)))
    if not runs:
        return None

    run_start, run_end, _score = max(runs, key=lambda item: item[2])
    band_top = search_top + run_start
    band_bottom = search_top + run_end
    strip_top = max(0, band_top - 4)
    strip_bottom = min(height, band_bottom + 4)
    strip = info_bar_mask[strip_top:strip_bottom, :]
    ys, xs = np.where(strip)
    if len(xs) == 0:
        return None

    left = max(0, int(xs.min()) - 10)
    right = min(width, int(xs.max()) + 11)
    band_height = max(1, band_bottom - band_top)
    card_width = max(1, right - left)
    chart_extra = max(int(band_height * 1.65), int(card_width * 0.18))
    top = max(0, band_top - chart_extra)
    bottom = min(height, band_bottom + max(8, int(band_height * 0.08)))

    if right - left < width * 0.45 or bottom - top < height * 0.08:
        return None
    return Rect(left, top, right, bottom)


def ocr_item_rect(item: object) -> Rect | None:
    if not isinstance(item, (list, tuple)) or len(item) < 2:
        return None
    try:
        points = np.asarray(item[0], dtype=np.float32).reshape(-1, 2)
    except (TypeError, ValueError):
        return None
    if len(points) < 2:
        return None
    return Rect(
        int(np.floor(points[:, 0].min())),
        int(np.floor(points[:, 1].min())),
        int(np.ceil(points[:, 0].max())),
        int(np.ceil(points[:, 1].max())),
    )


def normalized_ocr_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def has_result_anchor(text: str, anchor: str = "股票区间涨幅") -> bool:
    compact = normalized_ocr_text(text)
    return anchor in compact or ("股票区间" in compact and "涨幅" in compact)


def find_result_anchor_rect(ocr_items: Iterable[object], anchor: str = "股票区间涨幅") -> Rect | None:
    parsed: list[tuple[str, Rect]] = []
    for item in ocr_items:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        rect = ocr_item_rect(item)
        if rect is None:
            continue
        text = normalized_ocr_text(str(item[1]))
        parsed.append((text, rect))
        if anchor in text:
            return rect

    interval_items = [(text, rect) for text, rect in parsed if "股票区间" in text]
    gain_items = [(text, rect) for text, rect in parsed if "涨幅" in text]
    for _interval_text, interval_rect in interval_items:
        for _gain_text, gain_rect in gain_items:
            interval_y = (interval_rect.top + interval_rect.bottom) / 2
            gain_y = (gain_rect.top + gain_rect.bottom) / 2
            line_height = max(interval_rect.height, gain_rect.height, 1)
            if abs(interval_y - gain_y) <= line_height:
                return Rect(
                    min(interval_rect.left, gain_rect.left),
                    min(interval_rect.top, gain_rect.top),
                    max(interval_rect.right, gain_rect.right),
                    max(interval_rect.bottom, gain_rect.bottom),
                )
    return None


def find_result_card_rect_from_ocr(
    image: Image.Image,
    ocr_items: Iterable[object],
    anchor: str = "股票区间涨幅",
) -> Rect | None:
    anchor_rect = find_result_anchor_rect(ocr_items, anchor)
    if anchor_rect is None:
        return None

    width, height = image.size
    text_height = max(anchor_rect.height, int(height * 0.015), 1)
    left = int(width * 0.045)
    right = int(width * 0.955)
    top = anchor_rect.top - int(text_height * 6.6)
    bottom = anchor_rect.bottom + int(text_height * 2.3)
    return Rect(left, top, right, bottom).clamp(width, height)


def crop_result_card(
    result_image: Image.Image,
    config: dict,
    ocr_items: Iterable[object] = (),
) -> Image.Image:
    anchor = str(config.get("ocr", {}).get("result_anchor_keyword", "股票区间涨幅"))
    anchored = find_result_card_rect_from_ocr(result_image, ocr_items, anchor)
    if anchored is not None:
        print(
            "[RESULT] OCR-anchored result card: "
            f"left={anchored.left} top={anchored.top} right={anchored.right} bottom={anchored.bottom}"
        )
        return crop_relative(result_image, anchored)

    detected = find_result_card_rect(result_image)
    if detected is not None:
        print(
            "[RESULT] Auto-cropped result card: "
            f"left={detected.left} top={detected.top} right={detected.right} bottom={detected.bottom}"
        )
        return crop_relative(result_image, detected)

    result_region_cfg = config.get("capture", {}).get("result_crop_region")
    if result_region_cfg:
        print("[RESULT] Auto-crop failed; using configured fallback result_crop_region.")
        result_rect = rect_from_config_scaled(result_region_cfg, result_image, config)
        return crop_relative(result_image, result_rect)

    print("[RESULT] Auto-crop failed; saving full result page.")
    return result_image


def image_change_score(a: Image.Image, b: Image.Image) -> float:
    if a.size != b.size:
        return 999.0
    diff = ImageChops.difference(a.convert("L"), b.convert("L"))
    return float(np.asarray(diff, dtype=np.float32).mean())


def image_pixel_change_ratio(previous: Image.Image, current: Image.Image) -> float:
    if previous.size != current.size:
        return 100.0
    previous_array = np.asarray(previous.convert("RGB"), dtype=np.int16)
    current_array = np.asarray(current.convert("RGB"), dtype=np.int16)
    if previous_array.size == 0:
        return 100.0
    changed = np.max(np.abs(previous_array - current_array), axis=2) >= 12
    return float(np.count_nonzero(changed) * 100.0 / changed.size)


def color_pixel_count(image: Image.Image, lower: tuple[int, int, int], upper: tuple[int, int, int]) -> int:
    hsv = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, np.array(lower, dtype=np.uint8), np.array(upper, dtype=np.uint8))
    return int(cv2.countNonZero(mask))


def red_pink_pixel_count(image: Image.Image) -> int:
    hsv = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2HSV)
    red = cv2.inRange(hsv, np.array([0, 90, 90], dtype=np.uint8), np.array([8, 255, 255], dtype=np.uint8))
    pink = cv2.inRange(hsv, np.array([145, 70, 80], dtype=np.uint8), np.array([179, 255, 255], dtype=np.uint8))
    return int(cv2.countNonZero(cv2.bitwise_or(red, pink)))


def red_green_mask(image: Image.Image) -> np.ndarray:
    hsv = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2HSV)
    red_low = cv2.inRange(hsv, np.array([0, 80, 80], dtype=np.uint8), np.array([10, 255, 255], dtype=np.uint8))
    red_high = cv2.inRange(hsv, np.array([170, 80, 80], dtype=np.uint8), np.array([179, 255, 255], dtype=np.uint8))
    green = cv2.inRange(hsv, np.array([45, 50, 60], dtype=np.uint8), np.array([90, 255, 255], dtype=np.uint8))
    return cv2.bitwise_or(cv2.bitwise_or(red_low, red_high), green)


def moving_average_line_masks(image: Image.Image) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    hsv = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2HSV)
    magenta = cv2.inRange(
        hsv,
        np.array([140, 90, 100], dtype=np.uint8),
        np.array([179, 255, 255], dtype=np.uint8),
    )
    yellow = cv2.inRange(
        hsv,
        np.array([12, 100, 110], dtype=np.uint8),
        np.array([38, 255, 255], dtype=np.uint8),
    )
    blue = cv2.inRange(
        hsv,
        np.array([95, 90, 100], dtype=np.uint8),
        np.array([125, 255, 255], dtype=np.uint8),
    )
    return magenta, yellow, blue


def moving_average_lines_loaded(main_chart: Image.Image, config: dict) -> bool:
    session_cfg = config.get("session", {})
    width = max(main_chart.width, 1)
    width_scale = width / 652.0
    min_pixels = max(1, int(float(session_cfg.get("loaded_ma_min_pixels", 180)) * width_scale))
    min_columns = max(1, int(float(session_cfg.get("loaded_ma_min_columns", 120)) * width_scale))
    required_lines = int(session_cfg.get("loaded_ma_required_lines", 3))

    ready_lines = 0
    for mask in moving_average_line_masks(main_chart):
        pixel_count = cv2.countNonZero(mask)
        occupied_columns = int(np.count_nonzero(np.any(mask > 0, axis=0)))
        if pixel_count >= min_pixels and occupied_columns >= min_columns:
            ready_lines += 1
    return ready_lines >= required_lines


def chart_paint_change_ratio(previous: Image.Image, current: Image.Image, config: dict) -> float:
    if previous.size != current.size:
        return 100.0
    session_cfg = config.get("session", {})
    top = max(0, min(int(session_cfg.get("loaded_main_top_px", 55)), current.height))
    bottom = max(top, min(int(session_cfg.get("loaded_main_bottom_px", 470)), current.height))
    previous_array = np.asarray(previous.crop((0, top, previous.width, bottom)), dtype=np.int16)
    current_array = np.asarray(current.crop((0, top, current.width, bottom)), dtype=np.int16)
    if previous_array.size == 0:
        return 100.0
    changed = np.max(np.abs(previous_array - current_array), axis=2) >= 12
    return float(np.count_nonzero(changed) * 100.0 / changed.size)


def is_chart_loaded(viewport: Image.Image, config: dict) -> bool:
    mask = red_green_mask(viewport)
    height, width = mask.shape
    total_count = cv2.countNonZero(mask)
    left_count = cv2.countNonZero(mask[:, : width // 2])
    session_cfg = config.get("session", {})
    main_top = max(0, min(int(session_cfg.get("loaded_main_top_px", 55)), height))
    main_bottom = max(main_top, min(int(session_cfg.get("loaded_main_bottom_px", 470)), height))
    main_mask = mask[main_top:main_bottom, :]
    main_count = cv2.countNonZero(main_mask)
    main_chart = viewport.crop((0, main_top, width, main_bottom))

    component_count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(main_mask, 8)
    vertical_components = 0
    for index in range(1, component_count):
        _x, _y, _w, component_height, area = stats[index]
        if area >= 4 and component_height >= 8:
            vertical_components += 1

    return (
        total_count >= int(session_cfg.get("loaded_total_color_pixels", 5000))
        and left_count >= int(session_cfg.get("loaded_left_color_pixels", 1200))
        and main_count >= int(session_cfg.get("loaded_main_color_pixels", 2500))
        and vertical_components >= int(session_cfg.get("loaded_main_vertical_components", 15))
        and moving_average_lines_loaded(main_chart, config)
    )


def is_training_page(screenshot: Image.Image, config: dict) -> bool:
    region_cfg = config["capture"].get("training_control_region")
    if not region_cfg:
        return True
    region = crop_relative(screenshot, rect_from_config_scaled(region_cfg, screenshot, config))
    orange_count = color_pixel_count(region, (0, 80, 120), (28, 255, 255))
    blue_count = color_pixel_count(region, (95, 70, 100), (125, 255, 255))
    ring_cfg = config["capture"].get("training_center_ring_region")
    ring_count = 999999
    if ring_cfg:
        ring = crop_relative(screenshot, rect_from_config_scaled(ring_cfg, screenshot, config))
        ring_count = red_pink_pixel_count(ring)
    return (
        orange_count >= int(config["capture"].get("training_orange_pixels", 1200))
        and blue_count >= int(config["capture"].get("training_blue_pixels", 1200))
        and ring_count >= int(config["capture"].get("training_center_ring_pixels", 80))
    )


def has_training_controls(text: str, config: dict) -> bool:
    compact = re.sub(r"\s+", "", text)
    keywords = config.get("session", {}).get("start_control_keywords", ["结算", "买入", "观望"])
    return any(str(keyword) in compact for keyword in keywords)


def is_session_start(text: str, config: dict) -> bool:
    if not bool(config.get("session", {}).get("require_start_30_30", False)):
        return True
    compact = re.sub(r"\s+", "", text)
    compact = compact.replace("\uff0f", "/")
    keywords = config.get("session", {}).get("start_keywords", [])
    counter_detected = any(keyword.replace("\uff0f", "/") in compact for keyword in keywords)
    return counter_detected and has_training_controls(text, config)


def edge_gray(image: Image.Image) -> np.ndarray:
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.Canny(gray, 40, 120)


def find_horizontal_shift(previous: Image.Image, current: Image.Image, min_shift: int, max_shift: int) -> tuple[int, float]:
    prev = edge_gray(previous)
    curr = edge_gray(current)
    _height, width = prev.shape
    max_shift = min(max_shift, width // 3)

    best_shift = min_shift
    best_score = float("inf")
    for shift in range(max(1, min_shift), max_shift + 1):
        overlap_width = width - shift
        if overlap_width < 120:
            continue
        score = float(np.mean(cv2.absdiff(prev[:, shift:], curr[:, :overlap_width])))
        if score < best_score:
            best_score = score
            best_shift = shift

    return best_shift, best_score


def append_strip(canvas: Image.Image, viewport: Image.Image, width: int) -> Image.Image:
    width = max(1, min(width, viewport.width))
    strip = viewport.crop((viewport.width - width, 0, viewport.width, viewport.height))
    expanded = Image.new("RGB", (canvas.width + width, canvas.height), (18, 18, 32))
    expanded.paste(canvas, (0, 0))
    expanded.paste(strip, (canvas.width, 0))
    return expanded


def column_signature(image: Image.Image) -> np.ndarray:
    arr = np.asarray(image)
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    red_low = cv2.inRange(hsv, np.array([0, 80, 80], dtype=np.uint8), np.array([10, 255, 255], dtype=np.uint8))
    red_high = cv2.inRange(hsv, np.array([170, 80, 80], dtype=np.uint8), np.array([179, 255, 255], dtype=np.uint8))
    red = cv2.bitwise_or(red_low, red_high)
    green = cv2.inRange(hsv, np.array([45, 50, 60], dtype=np.uint8), np.array([90, 255, 255], dtype=np.uint8))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 40, 120)
    sig = np.vstack(
        [
            red.sum(axis=0).astype(np.float32),
            green.sum(axis=0).astype(np.float32),
            edges.sum(axis=0).astype(np.float32) * 0.25,
        ]
    )
    kernel = np.ones(5, dtype=np.float32) / 5.0
    for index in range(sig.shape[0]):
        sig[index] = np.convolve(sig[index], kernel, mode="same")
        std = float(sig[index].std())
        if std > 1e-6:
            sig[index] = (sig[index] - float(sig[index].mean())) / std
        else:
            sig[index] = 0
    return sig


def resize_signature(signature: np.ndarray, width: int) -> np.ndarray:
    resized = cv2.resize(signature.T, (signature.shape[0], width), interpolation=cv2.INTER_LINEAR)
    return resized.T.astype(np.float32)


def find_two_snapshot_overlap(first: Image.Image, last: Image.Image, config: dict) -> tuple[int, int, float]:
    stitch_cfg = config["stitching"]
    first_sig = column_signature(first)
    last_sig = column_signature(last)
    width = min(first.width, last.width)
    min_overlap = int(stitch_cfg.get("overlap_min_px", 160))
    max_overlap = min(int(stitch_cfg.get("overlap_max_px", 560)), width - 20)
    step = int(stitch_cfg.get("overlap_step_px", 4))
    scale_min = float(stitch_cfg.get("overlap_scale_min", 0.9))
    scale_max = float(stitch_cfg.get("overlap_scale_max", 1.1))
    scale_step = float(stitch_cfg.get("overlap_scale_step", 0.02))

    best_first_overlap = min_overlap
    best_last_overlap = min_overlap
    best_score = float("inf")

    scale_count = int(round((scale_max - scale_min) / scale_step)) + 1
    scales = [scale_min + i * scale_step for i in range(scale_count)]

    for first_overlap in range(min_overlap, max_overlap + 1, step):
        first_part = first_sig[:, first.width - first_overlap :]
        for scale in scales:
            last_overlap = int(round(first_overlap * scale))
            if last_overlap < min_overlap or last_overlap > max_overlap or last_overlap >= last.width:
                continue
            last_part = last_sig[:, :last_overlap]
            if last_overlap != first_overlap:
                last_part = resize_signature(last_part, first_overlap)
            score = float(np.mean(np.abs(first_part - last_part)))
            # Prefer a larger overlap when scores are close; it avoids duplicating
            # shared candles and gives a cleaner two-screen panorama.
            score -= first_overlap * 0.0005
            if score < best_score:
                best_score = score
                best_first_overlap = first_overlap
                best_last_overlap = last_overlap

    return best_first_overlap, best_last_overlap, best_score


def stitch_two_snapshots(first: Image.Image, last: Image.Image, config: dict) -> Image.Image:
    if config["stitching"].get("mode") == "fixed_two_snapshots":
        overlap = int(config["stitching"].get("fixed_overlap_px", 0))
        gap = max(0, int(config["stitching"].get("gap_px", 0)))
        duplicate_hint_width = max(0, int(config["stitching"].get("duplicate_hint_width_px", 0)))
        duplicate_hint_alpha = max(0, min(255, int(config["stitching"].get("duplicate_hint_alpha", 28))))
        duplicate_border_alpha = max(0, min(255, int(config["stitching"].get("duplicate_hint_border_alpha", 120))))
        overlap = max(0, min(overlap, last.width - 1))
        right_tail = last.crop((overlap, 0, last.width, last.height))
        canvas = Image.new("RGB", (first.width + gap + right_tail.width, first.height), (18, 18, 32))
        canvas.paste(first, (0, 0))
        canvas.paste(right_tail, (first.width + gap, 0))
        if duplicate_hint_width > 0:
            hint_width = min(duplicate_hint_width, right_tail.width)
            hint_x = first.width + gap
            overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            hint_color = (76, 140, 255, duplicate_hint_alpha)
            border_color = (76, 140, 255, duplicate_border_alpha)
            from PIL import ImageDraw

            draw = ImageDraw.Draw(overlay)
            draw.rectangle(
                (hint_x, 0, hint_x + hint_width - 1, canvas.height - 1),
                fill=hint_color,
                outline=border_color,
                width=2,
            )
            canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
        print(
            "[STITCH] fixed two snapshots: "
            f"overlap={overlap}px gap={gap}px duplicate_hint={duplicate_hint_width}px"
        )
        return canvas

    first_overlap, last_overlap, score = find_two_snapshot_overlap(first, last, config)
    right_tail = last.crop((last_overlap, 0, last.width, last.height))
    canvas = Image.new("RGB", (first.width + right_tail.width, first.height), (18, 18, 32))
    canvas.paste(first, (0, 0))
    canvas.paste(right_tail, (first.width, 0))
    print(
        "[STITCH] two snapshots: "
        f"first_overlap={first_overlap}px last_overlap={last_overlap}px score={score:.3f}"
    )
    return canvas


def stitch_frames(frames: list[Image.Image], config: dict) -> Image.Image:
    if not frames:
        raise ValueError("No training frames were captured.")
    if config["stitching"].get("mode") in {"two_snapshots", "fixed_two_snapshots"} and len(frames) >= 2:
        return stitch_two_snapshots(frames[0], frames[-1], config)

    stitch_cfg = config["stitching"]
    min_shift = int(stitch_cfg["min_shift_px"])
    max_shift = int(stitch_cfg["max_shift_px"])
    accept_score = float(stitch_cfg["accept_overlap_score"])
    fallback_append = int(stitch_cfg["fallback_append_px"])
    min_change = float(stitch_cfg["min_change_score"])

    canvas = frames[0].copy()
    previous = frames[0]

    for current in frames[1:]:
        shift, overlap_score = find_horizontal_shift(previous, current, min_shift, max_shift)
        viewport_change = image_change_score(previous, current)

        if overlap_score <= accept_score:
            append_width = shift
        elif viewport_change >= min_change:
            append_width = fallback_append
        else:
            previous = current
            continue

        canvas = append_strip(canvas, current, append_width)
        previous = current

    return canvas


class OcrReader:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.engine = None
        self.initialization_error = ""
        self.last_error = ""
        if enabled:
            try:
                from rapidocr_onnxruntime import RapidOCR

                self.engine = RapidOCR()
            except Exception as exc:
                self.initialization_error = str(exc)
                print(f"[WARN] OCR disabled: {exc}")
                self.enabled = False

    def read_items(self, image: Image.Image) -> list[object]:
        if not self.enabled or self.engine is None:
            return []
        arr = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
        try:
            result, _elapsed = self.engine(arr)
        except Exception as exc:
            self.last_error = str(exc)
            print(f"[WARN] OCR failed: {exc}")
            return []
        if not result:
            return []
        return list(result)

    def read_region_items(self, image: Image.Image, rect: Rect, scale: float = 2.0) -> list[object]:
        rect = rect.clamp(image.width, image.height)
        if rect.width <= 0 or rect.height <= 0:
            return []
        crop = crop_relative(image, rect)
        scale = max(1.0, float(scale))
        if scale != 1.0:
            crop = crop.resize(
                (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
                Image.Resampling.LANCZOS,
            )

        remapped: list[object] = []
        for item in self.read_items(crop):
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            try:
                points = np.asarray(item[0], dtype=np.float32).reshape(-1, 2)
            except (TypeError, ValueError):
                continue
            points[:, 0] = points[:, 0] / scale + rect.left
            points[:, 1] = points[:, 1] / scale + rect.top
            remapped.append([points.tolist(), *item[1:]])
        return remapped

    @staticmethod
    def text_from_items(items: Iterable[object]) -> str:
        return "\n".join(
            str(item[1])
            for item in items
            if isinstance(item, (list, tuple)) and len(item) >= 2
        )

    def read_text(self, image: Image.Image) -> str:
        return self.text_from_items(self.read_items(image))


def is_result_page(text: str, anchor: str = "股票区间涨幅") -> bool:
    return bool(text) and has_result_anchor(text, anchor)


def is_home_page(text: str, keywords: Iterable[str]) -> bool:
    return bool(text) and sum(1 for keyword in keywords if keyword in text) >= 2


def is_home_page_visual(screenshot: Image.Image, config: dict) -> bool:
    capture_cfg = config.get("capture", {})
    orange_count = color_pixel_count(screenshot, (0, 80, 120), (28, 255, 255))
    preferred_width = max(1, int(config.get("window", {}).get("preferred_width", 656)))
    preferred_height = max(1, int(config.get("window", {}).get("preferred_height", 1348)))
    area_scale = screenshot.width * screenshot.height / (preferred_width * preferred_height)
    threshold = float(capture_cfg.get("home_orange_pixels", 80000)) * area_scale
    return orange_count >= threshold


def is_result_page_visual_ready(screenshot: Image.Image, config: dict) -> bool:
    capture_cfg = config.get("capture", {})
    region_cfg = capture_cfg.get(
        "result_final_controls_region",
        {"left": 60, "top": 430, "right": 596, "bottom": 700},
    )
    region = crop_relative(screenshot, rect_from_config_scaled(region_cfg, screenshot, config))
    blue_count = color_pixel_count(region, (95, 70, 100), (125, 255, 255))
    preferred_width = max(1, int(config.get("window", {}).get("preferred_width", 656)))
    preferred_height = max(1, int(config.get("window", {}).get("preferred_height", 1348)))
    area_scale = screenshot.width * screenshot.height / (preferred_width * preferred_height)
    threshold = float(capture_cfg.get("result_final_blue_pixels", 20000)) * area_scale
    return blue_count >= threshold


def parse_metadata(text: str) -> dict[str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    compact = re.sub(r"\s+", " ", text)

    profit = ""
    profit_match = re.search("\u672c\u5c40\u6536\u76ca\\s*([+-]?\\d+(?:\\.\\d+)?%)", compact)
    if not profit_match:
        profit_match = re.search(r"([+-]?\d+(?:\.\d+)?%)", compact)
    if profit_match:
        profit = profit_match.group(1)

    code = ""
    code_index = -1
    for index, line in enumerate(lines):
        code_match = re.search(r"\b(\d{6})\b", line)
        if code_match:
            code = code_match.group(1)
            code_index = index
            break

    stock = ""
    if code_index >= 0:
        line = lines[code_index]
        stock_match = re.search("([\u4e00-\u9fff]{2,8})\\s*" + re.escape(code), line)
        if stock_match:
            stock = stock_match.group(1)
        else:
            ignored = {
                "\u80a1\u7968\u533a\u95f4\u6da8\u5e45",
                "\u672c\u5c40\u6536\u76ca",
                "\u518d\u6765\u4e00\u5c40",
                "\u8fd4\u56de\u9996\u9875",
                "\u7ebf\u8bad\u7ec3\u8425",
            }
            for candidate_line in reversed(lines[:code_index]):
                candidate = re.sub("[^\u4e00-\u9fff]", "", candidate_line)
                if 2 <= len(candidate) <= 8 and candidate not in ignored:
                    stock = candidate
                    break

    date_range = ""
    date_match = re.search(r"(\d{8})\D{0,16}(\d{8})", compact)
    if date_match:
        date_range = f"{date_match.group(1)} - {date_match.group(2)}"
    else:
        date_values = re.findall(r"(?<!\d)(20\d{6})(?!\d)", compact)
        if len(date_values) >= 2:
            date_range = f"{date_values[0]} - {date_values[1]}"

    return {
        "stock": stock or "\u672a\u77e5\u80a1\u7968",
        "code": code,
        "profit": profit or "\u672a\u77e5\u6536\u76ca",
        "date_range": date_range,
        "ocr_text": text.strip(),
    }


def parse_result_metadata(
    result_text: str,
    result_image: Image.Image,
    result_items: list[object],
    config: dict,
    ocr: OcrReader,
) -> dict[str, str]:
    metadata = parse_metadata(result_text)
    metadata_incomplete = (
        metadata["stock"] == "\u672a\u77e5\u80a1\u7968"
        or not metadata["code"]
        or not metadata["date_range"]
    )
    if not metadata_incomplete:
        return metadata

    result_card = crop_result_card(result_image, config, result_items)
    result_card_text = ocr.read_text(result_card)
    if not result_card_text:
        return metadata
    return parse_metadata(f"{result_text}\n{result_card_text}")


def safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\r\n]+', "_", value)
    value = value.replace("%", "pct")
    return value.strip(" ._") or "kline-review"


def image_content_digest(image: Image.Image) -> str:
    normalized = image.convert("RGB")
    digest = hashlib.sha256()
    digest.update(f"{normalized.width}x{normalized.height}".encode("ascii"))
    digest.update(normalized.tobytes())
    return digest.hexdigest()


def note_identity(metadata: dict[str, str]) -> tuple[str, str, str]:
    stock_key = metadata.get("code", "").strip() or metadata.get("stock", "").strip()
    date_key = "".join(re.findall(r"\d", metadata.get("date_range", "")))
    profit_match = re.search(r"[+-]?\d+(?:\.\d+)?%", metadata.get("profit", ""))
    profit_key = profit_match.group(0) if profit_match else metadata.get("profit", "").strip()
    return stock_key, date_key, profit_key


def note_identity_from_text(note_text: str) -> tuple[str, str, str]:
    stock_line = re.search(r"^-\s*股票[：:]\s*(.+)$", note_text, re.MULTILINE)
    interval_line = re.search(r"^-\s*训练区间[：:]\s*(.+)$", note_text, re.MULTILINE)
    profit_line = re.search(r"^-\s*本局收益[：:]\s*(.+)$", note_text, re.MULTILINE)
    stock_value = stock_line.group(1).strip() if stock_line else ""
    code_match = re.search(r"\b\d{6}\b", stock_value)
    return note_identity(
        {
            "stock": re.sub(r"\s*\d{6}\s*$", "", stock_value).strip(),
            "code": code_match.group(0) if code_match else "",
            "date_range": interval_line.group(1).strip() if interval_line else "",
            "profit": profit_line.group(1).strip() if profit_line else "",
        }
    )


def find_duplicate_note(
    obsidian_dir: Path,
    stitched: Image.Image,
    metadata: dict[str, str] | None = None,
    now: datetime | None = None,
) -> Path | None:
    if not obsidian_dir.is_dir():
        return None
    current_time = now or datetime.now()
    expected_identity = note_identity(metadata or {})
    expected_digest = image_content_digest(stitched)
    image_pattern = re.compile(r"!\[\[([^\]]+\.png)\]\]", re.IGNORECASE)
    note_paths = sorted(
        obsidian_dir.glob("*.md"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
        reverse=True,
    )
    for note_path in note_paths:
        try:
            note_text = note_path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        if all(expected_identity) and note_identity_from_text(note_text) == expected_identity:
            try:
                note_age = abs(current_time.timestamp() - note_path.stat().st_mtime)
            except OSError:
                note_age = float("inf")
            if note_age <= 600:
                return note_path
        image_match = image_pattern.search(note_text)
        if image_match is None:
            continue
        image_path = (obsidian_dir / image_match.group(1).replace("/", os.sep)).resolve()
        try:
            image_path.relative_to(obsidian_dir.resolve())
            with Image.open(image_path) as existing_image:
                if image_content_digest(existing_image) == expected_digest:
                    return note_path
        except (OSError, ValueError):
            continue
    return None


def write_obsidian_note(
    obsidian_dir: Path,
    image_subdir: str,
    stitched: Image.Image,
    result_image: Image.Image,
    metadata: dict[str, str],
    config: dict,
    result_ocr_items: Iterable[object] = (),
    tags: Iterable[object] = (),
) -> Path:
    now = datetime.now()
    obsidian_dir.mkdir(parents=True, exist_ok=True)
    image_dir = obsidian_dir / image_subdir
    image_dir.mkdir(parents=True, exist_ok=True)

    title = f"{metadata['stock']} {metadata['profit']}"
    stem = safe_name(f"{now:%Y-%m-%d_%H-%M-%S}_{title}")
    stitched_name = f"{stem}.png"
    result_card_name = f"{stem}_result_card.png"
    stitched.save(image_dir / stitched_name)
    result_card = crop_result_card(result_image, config, result_ocr_items)
    result_card.save(image_dir / result_card_name)

    code_part = f" {metadata['code']}" if metadata["code"] else ""
    note_stem = safe_name(f"{now:%Y-%m-%d} {title}")
    note_path = obsidian_dir / f"{note_stem}.md"
    suffix = 2
    while note_path.exists():
        note_path = obsidian_dir / f"{note_stem}_{suffix}.md"
        suffix += 1

    normalized_tags = normalize_tags(tags)
    tag_lines = ""
    if normalized_tags:
        tag_names = ", ".join(tag["name"] for tag in normalized_tags)
        tag_colors = json.dumps(
            {tag["name"]: tag["color"] for tag in normalized_tags},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        tag_lines = f"- 标签：{tag_names}\n- 标签颜色：{tag_colors}\n"

    note = (
        f"# {title}\n\n"
        f"- \u80a1\u7968\uff1a{metadata['stock']}{code_part}\n"
        f"- \u8bad\u7ec3\u533a\u95f4\uff1a{metadata['date_range'] or '\u672a\u8bc6\u522b'}\n"
        f"- \u672c\u5c40\u6536\u76ca\uff1a{metadata['profit']}\n"
        f"- \u8bb0\u5f55\u65f6\u95f4\uff1a{now:%Y-%m-%d %H:%M:%S}\n"
        f"{tag_lines}\n"
        f"![[{image_subdir}/{stitched_name}]]\n\n"
        f"![[{image_subdir}/{result_card_name}]]\n"
    )
    note_path.write_text(note, encoding="utf-8")
    return note_path


def save_raw_frames(obsidian_dir: Path, subdir: str, frames: list[Image.Image]) -> None:
    frame_dir = obsidian_dir / subdir / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    frame_dir.mkdir(parents=True, exist_ok=True)
    for index, frame in enumerate(frames, start=1):
        name = f"{index:04d}.png"
        if len(frames) == 2:
            name = "0001_start.png" if index == 1 else "0002_last.png"
        frame.save(frame_dir / name)


def main(
    status_callback: StatusCallback | None = None,
    stop_event: object | None = None,
    tag_provider: TagProvider | None = None,
    manual_start_event: object | None = None,
) -> int:
    enable_dpi_awareness()
    config = load_config()
    if "--list-windows" in sys.argv:
        print_window_diagnostics(config["window"]["title_contains"])
        return 0

    title_contains = config["window"]["title_contains"]
    title_description = " / ".join(
        [title_contains] if isinstance(title_contains, str) else [str(value) for value in title_contains]
    )
    poll_seconds = float(config["window"]["poll_seconds"])
    ocr_seconds = float(config["window"].get("state_ocr_seconds", config["window"]["ocr_seconds"]))
    home_ocr_seconds = max(ocr_seconds, float(config["window"].get("home_ocr_seconds", 0.6)))
    missing_window_poll_seconds = max(0.1, float(config["window"].get("missing_window_poll_seconds", 0.2)))
    output_value = str(config.get("paths", {}).get("obsidian_dir", "")).strip()
    if not output_value:
        emit_status(status_callback, "error", "尚未设置保存目录", "请打开设置完成首次配置")
        return 1
    obsidian_dir = Path(output_value)
    image_subdir = str(config["paths"]["image_subdir"])
    raw_frame_subdir = str(config["paths"]["raw_frame_subdir"])
    result_anchor = str(config["ocr"].get("result_anchor_keyword", "股票区间涨幅"))
    home_keywords = config["ocr"].get("home_keywords", [])
    ocr = OcrReader(bool(config["ocr"]["enabled"]))

    last_status_signature: tuple[str, str, str] | None = None

    def report_status(state: str, message: str, detail: str = "") -> None:
        nonlocal last_status_signature
        signature = (state, message, detail)
        if signature == last_status_signature:
            return
        last_status_signature = signature
        emit_status(status_callback, state, message, detail)

    print("Kline recorder started.")
    print(f"Watching window title: {title_description}")
    print("Session starts only after 30/30 is detected.")
    report_status("waiting", "正在等待 K 线训练营窗口", title_description)

    start_frame: Image.Image | None = None
    latest_frame: Image.Image | None = None
    last_latest_change_crop: Image.Image | None = None
    last_loading_log_at = 0.0
    last_ocr_at = 0.0
    last_text = ""
    last_ocr_items: list[object] = []
    last_ocr_screenshot: Image.Image | None = None
    quote_ready_since: float | None = None
    start_chart_previous: Image.Image | None = None
    start_chart_stable_since: float | None = None
    result_ready_since: float | None = None
    result_previous_frame: Image.Image | None = None
    result_stable_since: float | None = None
    result_page_handled = False
    pending_result_image: Image.Image | None = None
    pending_result_items: list[object] = []
    pending_result_text = ""
    recovery_start_frame: Image.Image | None = None
    recovery_latest_frame: Image.Image | None = None
    recovery_change_crop: Image.Image | None = None
    last_missing_window_log = 0.0
    last_state_log_at = 0.0
    last_window_log_at = 0.0
    window_connected = False
    last_training_page_detected: bool | None = None
    last_home_ocr_at = 0.0
    nontraining_since: float | None = None
    session_start_detected = False
    quote_ready_detected = False
    result_detected = False
    home_detected = False
    training_controls_detected = False

    while True:
        if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
            report_status("stopped", "复盘助手已停止")
            return 0

        found = find_window(title_contains, config["window"])
        if not found:
            window_connected = False
            if time.time() - last_missing_window_log > 5.0:
                print("[WAIT] Training window not found. Try: python kline_recorder.py --list-windows")
                last_missing_window_log = time.time()
            report_status("waiting", "未找到训练营窗口", "请打开新K线训练营")
            time.sleep(missing_window_poll_seconds)
            continue

        hwnd, window_rect = found
        if not window_connected:
            window_connected = True
            report_status("connected", "已连接训练营窗口")
        if now := time.time():
            if now - last_window_log_at >= 10.0:
                print(
                    "[WINDOW] "
                    f"hwnd={hwnd} pos=({window_rect.left},{window_rect.top}) "
                    f"size={window_rect.width}x{window_rect.height}"
                )
                last_window_log_at = now
        screenshot = capture_window(hwnd, window_rect, config["window"])
        now = time.time()
        training_page_detected = is_training_page(screenshot, config)
        visual_home_detected = not training_page_detected and is_home_page_visual(screenshot, config)
        result_visual_ready_detected = (
            not training_page_detected and is_result_page_visual_ready(screenshot, config)
        )
        has_snapshots = (
            start_frame is not None
            or recovery_start_frame is not None
            or recovery_latest_frame is not None
        )
        chart_loaded_detected = False
        if training_page_detected:
            chart_region = rect_from_config_scaled(config["capture"]["stitch_region"], screenshot, config)
            chart_loaded_detected = is_chart_loaded(crop_relative(screenshot, chart_region), config)
        page_changed = last_training_page_detected is None or training_page_detected != last_training_page_detected
        last_training_page_detected = training_page_detected
        if page_changed:
            last_ocr_at = 0.0

        if training_page_detected:
            nontraining_since = None
            result_detected = False
            home_detected = False
            result_ready_since = None
            result_previous_frame = None
            result_stable_since = None
            if start_frame is not None:
                session_start_detected = True
                quote_ready_detected = True
            elif session_start_detected or training_controls_detected:
                quote_ready_detected = chart_loaded_detected
        else:
            if nontraining_since is None:
                nontraining_since = now
            session_start_detected = False
            quote_ready_detected = False
            if page_changed:
                result_detected = False
                home_detected = False
                training_controls_detected = False
            if result_visual_ready_detected and has_snapshots:
                result_detected = True
                home_detected = False
            if visual_home_detected or (not has_snapshots and not result_page_handled):
                home_detected = True

        if pending_result_image is None and now - last_ocr_at >= ocr_seconds:
            focused_scale = float(config.get("ocr", {}).get("focused_scale", 1.5))
            ocr_performed = False

            if training_page_detected and start_frame is None and not session_start_detected:
                control_cfg = config.get("capture", {}).get(
                    "start_counter_region"
                ) or config.get("capture", {}).get(
                    "training_control_region"
                ) or DEFAULT_FOCUSED_OCR_REGIONS["training_control_region"]
                control_region = rect_from_config_scaled(control_cfg, screenshot, config)
                start_scale = float(config.get("ocr", {}).get("start_counter_scale", 2.0))
                control_items = ocr.read_region_items(screenshot, control_region, start_scale)
                last_ocr_items = control_items
                last_text = ocr.text_from_items(last_ocr_items)
                last_ocr_screenshot = screenshot.copy()
                training_controls_detected = has_training_controls(last_text, config)
                session_start_detected = is_session_start(last_text, config)
                quote_ready_detected = training_controls_detected and chart_loaded_detected
                ocr_performed = True

            elif not training_page_detected and not home_detected and not result_detected:
                focused_items: list[object] = []
                if has_snapshots:
                    result_region_cfg = config.get("capture", {}).get(
                        "result_anchor_scan_region"
                    ) or config.get("capture", {}).get(
                        "result_scan_region"
                    ) or DEFAULT_FOCUSED_OCR_REGIONS["result_scan_region"]
                    result_region = rect_from_config_scaled(result_region_cfg, screenshot, config)
                    result_scale = float(config.get("ocr", {}).get("result_anchor_scale", focused_scale))
                    focused_items = ocr.read_region_items(screenshot, result_region, result_scale)
                    last_ocr_items = focused_items
                    last_text = ocr.text_from_items(focused_items)
                    result_detected = is_result_page(last_text, result_anchor)
                    home_detected = False
                    ocr_performed = True

                should_scan_home = (
                    not result_detected
                    and now - last_home_ocr_at >= home_ocr_seconds
                    and (not has_snapshots or (nontraining_since is not None and now - nontraining_since >= 0.2))
                )
                if should_scan_home:
                    full_items = ocr.read_items(screenshot)
                    last_ocr_items = [*full_items, *focused_items]
                    last_text = ocr.text_from_items(last_ocr_items)
                    result_detected = is_result_page(last_text, result_anchor)
                    home_detected = is_home_page(last_text, home_keywords)
                    last_home_ocr_at = time.time()
                    ocr_performed = True

                if ocr_performed:
                    last_ocr_screenshot = screenshot.copy()

            if ocr_performed:
                last_ocr_at = time.time()

        if result_detected and pending_result_image is None and not result_page_handled:
            if not result_visual_ready_detected:
                result_ready_since = None
                result_previous_frame = None
                result_stable_since = None
                report_status("result", "已识别结果页", "等待最终结果画面")
                time.sleep(poll_seconds)
                continue

            if result_ready_since is None:
                result_ready_since = now
                result_previous_frame = screenshot.copy()
                result_stable_since = None
                report_status("result", "最终结果已出现", "正在确认画面稳定")
                time.sleep(poll_seconds)
                continue

            stable_seconds = float(config.get("session", {}).get("result_capture_stable_seconds", 0.12))
            max_wait_seconds = float(config.get("session", {}).get("result_capture_max_wait_seconds", 0.8))
            max_change_ratio = float(config.get("session", {}).get("result_capture_max_change_ratio", 0.03))
            result_change = (
                image_pixel_change_ratio(result_previous_frame, screenshot)
                if result_previous_frame is not None
                else 100.0
            )
            result_previous_frame = screenshot.copy()
            if result_change <= max_change_ratio:
                if result_stable_since is None:
                    result_stable_since = now
            else:
                result_stable_since = None

            stable_ready = result_stable_since is not None and now - result_stable_since >= stable_seconds
            timeout_ready = now - result_ready_since >= max_wait_seconds
            if not stable_ready and not timeout_ready:
                time.sleep(poll_seconds)
                continue

            pending_result_image = screenshot.copy()
            pending_result_items = []
            pending_result_text = ""
            result_previous_frame = None
            result_stable_since = None
            report_status("result", "已锁定最终结果", "正在读取结果卡")

        result_detected = pending_result_image is not None or result_detected
        home_detected = home_detected and not result_detected
        if home_detected:
            if start_frame is not None:
                print("[RESET] Home page detected. Cached snapshots cleared.")
                start_frame = None
                latest_frame = None
                last_latest_change_crop = None
                quote_ready_since = None
                start_chart_previous = None
                start_chart_stable_since = None
                result_ready_since = None
                result_previous_frame = None
                result_stable_since = None
                result_page_handled = False
                recovery_start_frame = None
                recovery_latest_frame = None
                recovery_change_crop = None
                pending_result_image = None
                pending_result_items = []
                pending_result_text = ""
            if now - last_state_log_at >= 5.0:
                print("[WAIT] Home page detected. Waiting for a 30/30 training chart...")
                last_state_log_at = now
            report_status("home", "当前在主菜单", "等待开始一局 K 线训练")
            time.sleep(poll_seconds)
            continue

        if not result_detected:
            result_ready_since = None
            result_previous_frame = None
            result_stable_since = None
            result_page_handled = False

        if result_detected:
            if result_page_handled:
                time.sleep(poll_seconds)
                continue
            result_page_handled = True
            recovered = start_frame is None or latest_frame is None
            frames_ready = start_frame is not None and latest_frame is not None
            if not frames_ready and recovery_start_frame is not None and recovery_latest_frame is not None:
                start_frame = recovery_start_frame
                latest_frame = recovery_latest_frame
                frames_ready = True

            if frames_ready:
                result_image = pending_result_image or last_ocr_screenshot or screenshot
                result_items = list(pending_result_items)
                result_text = pending_result_text
                result_region_cfg = config.get("capture", {}).get(
                    "result_anchor_scan_region"
                ) or config.get("capture", {}).get(
                    "result_scan_region"
                ) or DEFAULT_FOCUSED_OCR_REGIONS["result_scan_region"]
                result_region = rect_from_config_scaled(result_region_cfg, result_image, config)
                current_result_items = ocr.read_region_items(
                    result_image,
                    result_region,
                    float(config.get("ocr", {}).get("result_anchor_scale", 1.5)),
                )
                current_result_text = ocr.text_from_items(current_result_items)
                result_items = current_result_items
                result_text = current_result_text

                profit_cfg = config.get("capture", {}).get("result_profit_region")
                profit_items: list[object] = []
                if profit_cfg:
                    profit_region = rect_from_config_scaled(profit_cfg, result_image, config)
                    profit_items = ocr.read_region_items(result_image, profit_region, 2.0)
                profit_text = ocr.text_from_items(profit_items)
                if re.search(r"[+-]?\d+(?:\.\d+)?%", profit_text):
                    result_items = [*profit_items, *result_items]
                    result_text = ocr.text_from_items(result_items)
                detail = "30/30 曾漏检，正在使用暂存盘面补录" if recovered else "识别到股票区间涨幅"
                report_status("result", "已锁定结果页", detail)
                print("[DONE] Result page detected. Writing review image...")
                report_status("saving", "正在生成总结", "拼接截图并写入 Obsidian")
                metadata = parse_result_metadata(result_text, result_image, result_items, config, ocr)
                try:
                    selected_tags = normalize_tags(tag_provider() if tag_provider is not None else ())
                except Exception:
                    selected_tags = []
                frames = [start_frame, latest_frame]
                stitched = stitch_frames(frames, config)
                duplicate_note = find_duplicate_note(obsidian_dir, stitched, metadata)
                if duplicate_note is not None:
                    print(f"[SKIP] Duplicate chart already saved: {duplicate_note}")
                    report_status("saved", "重复总结已忽略", str(duplicate_note))
                else:
                    note_path = write_obsidian_note(
                        obsidian_dir,
                        image_subdir,
                        stitched,
                        result_image,
                        metadata,
                        config,
                        result_items,
                        selected_tags,
                    )
                    if bool(config["stitching"]["save_raw_frames"]):
                        save_raw_frames(obsidian_dir, raw_frame_subdir, frames)
                    print(f"[DONE] Wrote: {note_path}")
                    report_status("saved", "总结已保存", str(note_path))
                start_frame = None
                latest_frame = None
                last_latest_change_crop = None
                quote_ready_since = None
                start_chart_previous = None
                start_chart_stable_since = None
                result_ready_since = None
                result_previous_frame = None
                result_stable_since = None
                recovery_start_frame = None
                recovery_latest_frame = None
                recovery_change_crop = None
                last_text = ""
                last_ocr_items = []
                last_ocr_screenshot = None
                pending_result_image = None
                pending_result_items = []
                pending_result_text = ""
            else:
                print("[WARN] Result page detected, but no chart snapshots were available.")
                report_status(
                    "error",
                    "结果页已出现，但没有可用盘面",
                    "本局无法生成截图；下一局会继续自动记录",
                )
                pending_result_image = None
                pending_result_items = []
                pending_result_text = ""
                result_ready_since = None
                result_previous_frame = None
                result_stable_since = None
            time.sleep(poll_seconds)
            continue

        if not training_page_detected:
            time.sleep(poll_seconds)
            continue

        stitch_rect = rect_from_config_scaled(config["capture"]["stitch_region"], screenshot, config)
        change_rect = rect_from_config_scaled(config["capture"]["change_region"], screenshot, config)
        viewport = crop_relative(screenshot, stitch_rect)
        change_crop = crop_relative(screenshot, change_rect)

        manual_start_requested = (
            manual_start_event is not None
            and getattr(manual_start_event, "is_set", lambda: False)()
        )
        if manual_start_requested:
            getattr(manual_start_event, "clear", lambda: None)()
            if start_frame is None:
                start_frame = viewport.copy()
                latest_frame = viewport.copy()
                last_latest_change_crop = change_crop.copy()
                recovery_start_frame = None
                recovery_latest_frame = None
                recovery_change_crop = None
                quote_ready_since = None
                start_chart_previous = None
                start_chart_stable_since = None
                session_start_detected = True
                quote_ready_detected = True
                print(f"[START] Manually captured initial snapshot. Window={screenshot.width}x{screenshot.height}")
                report_status("captured", "已手动截取开始盘面", "正在记录本局训练")
                time.sleep(poll_seconds)
                continue
            report_status("captured", "本局已经开始记录", "无需重复截取开始盘面")

        if start_frame is None:
            if not session_start_detected:
                quote_ready_since = None
                start_chart_previous = None
                start_chart_stable_since = None
                result_ready_since = None
                if quote_ready_detected:
                    if recovery_start_frame is None:
                        recovery_start_frame = viewport.copy()
                        recovery_latest_frame = viewport.copy()
                        recovery_change_crop = change_crop.copy()
                        print("[RECOVERY] Cached first visible chart in case 30/30 was missed.")
                    else:
                        recovery_change = (
                            image_change_score(recovery_change_crop, change_crop)
                            if recovery_change_crop is not None
                            else 999.0
                        )
                        if recovery_change >= float(config["stitching"]["min_change_score"]):
                            recovery_latest_frame = viewport.copy()
                            recovery_change_crop = change_crop.copy()
                if now - last_state_log_at >= 5.0:
                    print("[WAIT] Training window visible, but 30/30 has not been detected yet.")
                    last_state_log_at = now
                detail = "等待 30/30；已准备漏检补录" if recovery_start_frame is not None else "等待出现结算 30/30"
                report_status("training", "训练页面已打开", detail)
                time.sleep(poll_seconds)
                continue
            if not quote_ready_detected:
                quote_ready_since = None
                start_chart_previous = None
                start_chart_stable_since = None
                if now - last_loading_log_at >= 1.0:
                    print("[WAIT] 30/30 detected, waiting for the chart and moving averages...")
                    last_loading_log_at = now
                report_status("loading", "检测到 30/30", "等待 K 线和均线完整加载")
                time.sleep(poll_seconds)
                continue
            if quote_ready_since is None:
                quote_ready_since = now
                start_chart_previous = viewport.copy()
                start_chart_stable_since = None
                print("[WAIT] Complete chart detected. Confirming that paint is stable...")
                report_status("loading", "K 线已完整加载", "正在确认画面稳定")
                time.sleep(poll_seconds)
                continue

            stable_seconds = float(config.get("session", {}).get("start_capture_stable_seconds", 0.18))
            max_wait_seconds = float(config.get("session", {}).get("start_capture_max_wait_seconds", 1.5))
            max_change_ratio = float(config.get("session", {}).get("start_capture_max_change_ratio", 0.02))
            paint_change = (
                chart_paint_change_ratio(start_chart_previous, viewport, config)
                if start_chart_previous is not None
                else 100.0
            )
            start_chart_previous = viewport.copy()
            if paint_change <= max_change_ratio:
                if start_chart_stable_since is None:
                    start_chart_stable_since = now
            else:
                start_chart_stable_since = None

            stable_ready = (
                start_chart_stable_since is not None
                and now - start_chart_stable_since >= stable_seconds
            )
            timeout_ready = now - quote_ready_since >= max_wait_seconds
            if not stable_ready and not timeout_ready:
                time.sleep(poll_seconds)
                continue
            start_frame = viewport
            latest_frame = viewport
            last_latest_change_crop = change_crop
            recovery_start_frame = None
            recovery_latest_frame = None
            recovery_change_crop = None
            quote_ready_since = None
            start_chart_previous = None
            start_chart_stable_since = None
            print(f"[START] Captured initial 30/30 snapshot. Window={screenshot.width}x{screenshot.height}")
            report_status("captured", "已截取开始盘面", "正在记录本局训练")
            time.sleep(poll_seconds)
            continue

        if not quote_ready_detected:
            time.sleep(poll_seconds)
            continue

        min_change = float(config["stitching"]["min_change_score"])
        latest_change = (
            image_change_score(last_latest_change_crop, change_crop) if last_latest_change_crop is not None else 999.0
        )
        if latest_change >= min_change:
            latest_frame = viewport
            last_latest_change_crop = change_crop
            print(f"[LATEST] Updated final snapshot candidate, change={latest_change:.2f}")
            report_status("captured", "已更新结束盘面", "等待本局结算")

        time.sleep(poll_seconds)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped.")
        raise SystemExit(0)

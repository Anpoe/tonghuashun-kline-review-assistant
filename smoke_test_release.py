from __future__ import annotations

import copy
import ctypes
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import win32gui
import win32process
import yaml


ROOT = Path(__file__).resolve().parent
EXE = ROOT / "dist" / "KlineReviewAssistant" / "KlineReviewAssistant.exe"
DEFAULT_CONFIG = ROOT / "config.default.yaml"
INSTANCE_MUTEX_NAME = "Local\\KlineReviewAssistant-4D4964F4-33F8-49AB-8FF1-C5EE8B7CF27A"


def application_already_running() -> bool:
    create_mutex = ctypes.windll.kernel32.CreateMutexW
    create_mutex.restype = ctypes.c_void_p
    handle = create_mutex(None, False, INSTANCE_MUTEX_NAME)
    if not handle:
        return False
    already_running = ctypes.windll.kernel32.GetLastError() == 183
    ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
    return already_running


def window_titles(pid: int) -> list[str]:
    titles: list[str] = []

    def callback(hwnd: int, _extra: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        _thread_id, window_pid = win32process.GetWindowThreadProcessId(hwnd)
        if window_pid == pid:
            title = win32gui.GetWindowText(hwnd)
            if title:
                titles.append(title)
        return True

    win32gui.EnumWindows(callback, None)
    return titles


def wait_for_title(process: subprocess.Popen[bytes], expected: str, timeout: float = 25.0) -> list[str]:
    deadline = time.monotonic() + timeout
    last_titles: list[str] = []
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Application exited early with code {process.returncode}.")
        last_titles = window_titles(process.pid)
        if any(expected in title for title in last_titles):
            return last_titles
        time.sleep(0.15)
    raise TimeoutError(f"Window containing {expected!r} was not shown. Last titles: {last_titles}")


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def launch(appdata: Path) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env["APPDATA"] = str(appdata)
    return subprocess.Popen([str(EXE)], cwd=EXE.parent, env=env)


def main() -> int:
    if not EXE.is_file():
        raise FileNotFoundError(f"Release executable not found: {EXE}")

    ocr_test = subprocess.run([str(EXE), "--self-test"], cwd=EXE.parent, timeout=45)
    if ocr_test.returncode != 0:
        raise RuntimeError(f"Packaged OCR self-test failed with code {ocr_test.returncode}.")
    print("packaged-ocr=ok")

    dashboard_test = subprocess.run([str(EXE), "--dashboard-self-test"], cwd=EXE.parent, timeout=15)
    if dashboard_test.returncode != 0:
        raise RuntimeError(f"Packaged dashboard self-test failed with code {dashboard_test.returncode}.")
    print("packaged-dashboard=ok")

    if application_already_running():
        print("window-tests=skipped (another application instance is running)")
        return 0

    test_root = Path(tempfile.mkdtemp(prefix="KlineReleaseTest-"))
    try:
        appdata = test_root / "AppData"
        first_run = launch(appdata)
        try:
            titles = wait_for_title(first_run, "首次设置")
            print("first-run-window=ok", titles)
            assert not (appdata / "KlineReviewAssistant" / "config.yaml").exists()
        finally:
            stop_process(first_run)

        config = copy.deepcopy(yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8")))
        config["launcher"]["auto_start"] = False
        config["paths"]["obsidian_dir"] = str(test_root / "Reviews")
        config_dir = appdata / "KlineReviewAssistant"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.yaml").write_text(
            yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        configured = launch(appdata)
        try:
            titles = wait_for_title(configured, "K线复盘助手")
            print("configured-window=ok", titles)
        finally:
            stop_process(configured)
    finally:
        shutil.rmtree(test_root, ignore_errors=True)

    print("release-smoke-test=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import ctypes
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

import win32api
import win32con
import win32gui
import win32process

from kline_recorder import (
    OcrReader,
    enable_dpi_awareness,
    find_window,
    list_visible_windows,
    load_config,
    main as run_recorder,
    requires_initial_setup,
)
from kline_settings import find_happ_executable, show_settings


PANEL_WIDTH = 318
PANEL_HEIGHT = 334
PANEL_GAP = 10

COLORS = {
    "background": "#15171c",
    "surface": "#1d2027",
    "surface_alt": "#242832",
    "border": "#343a46",
    "text": "#f3f4f6",
    "muted": "#9ca3af",
    "accent": "#ff7a1a",
    "waiting": "#f5b942",
    "connected": "#38bdf8",
    "home": "#a3a3a3",
    "training": "#38bdf8",
    "loading": "#f5b942",
    "captured": "#22c55e",
    "result": "#f97316",
    "saving": "#f97316",
    "saved": "#22c55e",
    "error": "#ef4444",
    "stopped": "#a3a3a3",
}

INSTANCE_MUTEX_NAME = "Local\\KlineReviewAssistant-4D4964F4-33F8-49AB-8FF1-C5EE8B7CF27A"
INSTANCE_MUTEX_HANDLE: int | None = None


def acquire_single_instance() -> bool:
    global INSTANCE_MUTEX_HANDLE
    create_mutex = ctypes.windll.kernel32.CreateMutexW
    create_mutex.restype = ctypes.c_void_p
    handle = create_mutex(None, False, INSTANCE_MUTEX_NAME)
    if not handle:
        return True
    INSTANCE_MUTEX_HANDLE = int(handle)
    if ctypes.windll.kernel32.GetLastError() != 183:
        return True

    existing = win32gui.FindWindow(None, "K线复盘助手")
    if existing:
        win32gui.ShowWindow(existing, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(existing)
    return False


class RecorderPanel:
    def __init__(self) -> None:
        enable_dpi_awareness()
        self.config = load_config()
        self.title_contains = self.config["window"]["title_contains"]
        self.obsidian_dir = Path(self.config["paths"]["obsidian_dir"])
        self.events: queue.Queue[tuple[str, str, str]] = queue.Queue()
        self.stop_event = threading.Event()
        self.history: list[str] = []
        self.collapsed = False
        self.panel_topmost = False

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("K线复盘助手")
        self.root.configure(bg=COLORS["border"])
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", False)
        try:
            self.root.attributes("-toolwindow", True)
        except tk.TclError:
            pass
        self.root.geometry(f"{PANEL_WIDTH}x{PANEL_HEIGHT}+30+80")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._build_ui()
        self.root.deiconify()
        self.root.after(80, self._drain_events)
        self.root.after(150, self._follow_training_window)
        self.root.after(200, self._start_services)

    def _build_ui(self) -> None:
        self.shell = tk.Frame(self.root, bg=COLORS["background"], bd=0)
        self.shell.pack(fill="both", expand=True, padx=1, pady=1)

        header = tk.Frame(self.shell, bg=COLORS["surface"], height=42)
        header.pack(fill="x")
        header.pack_propagate(False)

        mark = tk.Frame(header, bg=COLORS["accent"], width=4, height=22)
        mark.pack(side="left", padx=(12, 9))
        mark.pack_propagate(False)

        title = tk.Label(
            header,
            text="K线复盘助手",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        title.pack(side="left")

        close_button = tk.Button(
            header,
            text="×",
            command=self.close,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            activebackground="#3a2023",
            activeforeground="#ffffff",
            bd=0,
            width=3,
            font=("Segoe UI", 13),
            cursor="hand2",
        )
        close_button.pack(side="right", fill="y")

        self.collapse_button = tk.Button(
            header,
            text="−",
            command=self.toggle_collapsed,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            activebackground=COLORS["surface_alt"],
            activeforeground="#ffffff",
            bd=0,
            width=3,
            font=("Segoe UI", 12),
            cursor="hand2",
        )
        self.collapse_button.pack(side="right", fill="y")

        self.body = tk.Frame(self.shell, bg=COLORS["background"])
        self.body.pack(fill="both", expand=True)

        current = tk.Frame(self.body, bg=COLORS["surface_alt"])
        current.pack(fill="x", padx=12, pady=(12, 8))

        self.status_dot = tk.Canvas(
            current,
            width=14,
            height=14,
            bg=COLORS["surface_alt"],
            highlightthickness=0,
        )
        self.status_dot.pack(side="left", padx=(11, 8), pady=13)
        self.dot_id = self.status_dot.create_oval(3, 3, 11, 11, fill=COLORS["waiting"], outline="")

        status_text = tk.Frame(current, bg=COLORS["surface_alt"])
        status_text.pack(side="left", fill="x", expand=True, pady=8)
        self.status_label = tk.Label(
            status_text,
            text="正在启动",
            anchor="w",
            bg=COLORS["surface_alt"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.status_label.pack(fill="x")
        self.detail_label = tk.Label(
            status_text,
            text="准备录制服务",
            anchor="w",
            justify="left",
            wraplength=235,
            bg=COLORS["surface_alt"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        )
        self.detail_label.pack(fill="x", pady=(2, 0))

        tk.Label(
            self.body,
            text="最近进度",
            anchor="w",
            bg=COLORS["background"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(fill="x", padx=14)

        footer = tk.Frame(self.body, bg=COLORS["surface"], height=42)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)
        tk.Button(
            footer,
            text="打开笔记目录",
            command=self.open_output_dir,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            activebackground=COLORS["surface_alt"],
            activeforeground="#ffffff",
            bd=0,
            padx=12,
            pady=6,
            font=("Microsoft YaHei UI", 8),
            cursor="hand2",
        ).pack(side="left")
        tk.Button(
            footer,
            text="设置",
            command=self.open_settings,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            activebackground=COLORS["surface_alt"],
            activeforeground="#ffffff",
            bd=0,
            padx=8,
            pady=6,
            font=("Microsoft YaHei UI", 8),
            cursor="hand2",
        ).pack(side="left")
        self.attach_label = tk.Label(
            footer,
            text="等待吸附",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        )
        self.attach_label.pack(side="right", padx=12)

        self.history_label = tk.Label(
            self.body,
            text="",
            anchor="nw",
            justify="left",
            bg=COLORS["background"],
            fg="#d1d5db",
            font=("Microsoft YaHei UI", 8),
            height=4,
        )
        self.history_label.pack(fill="both", expand=True, padx=14, pady=(4, 5))

    def post_status(self, state: str, message: str, detail: str = "") -> None:
        self.events.put((state, message, detail))

    def _drain_events(self) -> None:
        try:
            while True:
                state, message, detail = self.events.get_nowait()
                self.status_label.configure(text=message)
                self.detail_label.configure(text=detail or " ")
                color = COLORS.get(state, COLORS["connected"])
                self.status_dot.itemconfigure(self.dot_id, fill=color)
                entry = f"{datetime.now():%H:%M:%S}  {message}"
                if not self.history or self.history[-1] != entry:
                    self.history.append(entry)
                    self.history = self.history[-4:]
                    self.history_label.configure(text="\n".join(self.history))
        except queue.Empty:
            pass
        if not self.stop_event.is_set():
            self.root.after(100, self._drain_events)

    def _start_services(self) -> None:
        threading.Thread(target=self._launch_ths, daemon=True).start()
        threading.Thread(target=self._run_recorder, daemon=True).start()

    def _run_recorder(self) -> None:
        try:
            run_recorder(self.post_status, self.stop_event)
        except Exception as exc:
            self.post_status("error", "录制服务发生错误", str(exc))

    def _launch_ths(self) -> None:
        try:
            tasklist = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq happ.exe", "/NH"],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
            if "happ.exe" in tasklist.stdout.casefold():
                self.post_status("connected", "同花顺远航版已运行", "正在寻找新K线训练营")
                return

            if not bool(self.config.get("launcher", {}).get("auto_start", True)):
                self.post_status("waiting", "等待同花顺启动", "已在设置中关闭自动启动")
                return

            for _hwnd, title, _class_name, _rect in list_visible_windows():
                if "同花顺远航版" in title:
                    self.post_status("connected", "同花顺远航版已运行", "正在寻找新K线训练营")
                    return

            configured = str(self.config.get("launcher", {}).get("executable", "")).strip()
            executable = find_happ_executable(configured)
            if executable is None:
                self.post_status("error", "未找到同花顺远航版", "请打开设置并选择 happ.exe")
                return

            subprocess.Popen([str(executable)], cwd=str(executable.parent))
            self.post_status("connected", "已启动同花顺远航版", "等待客户端和训练营窗口")
        except Exception as exc:
            self.post_status("error", "启动同花顺失败", str(exc))

    def _follow_training_window(self) -> None:
        if self.stop_event.is_set():
            return
        try:
            found = find_window(self.title_contains, self.config["window"])
            if found:
                hwnd, rect = found
                panel_height = 42 if self.collapsed else PANEL_HEIGHT
                monitor = win32api.MonitorFromRect(
                    (rect.left, rect.top, rect.right, rect.bottom),
                    win32con.MONITOR_DEFAULTTONEAREST,
                )
                work_left, work_top, work_right, work_bottom = win32api.GetMonitorInfo(monitor)["Work"]
                right_x = rect.right + PANEL_GAP
                left_x = rect.left - PANEL_WIDTH - PANEL_GAP
                x = right_x if right_x + PANEL_WIDTH <= work_right else max(work_left, left_x)
                y = min(max(rect.top, work_top), max(work_top, work_bottom - panel_height))
                geometry = f"{PANEL_WIDTH}x{panel_height}+{x}+{y}"
                if self.root.geometry() != geometry:
                    self.root.geometry(geometry)

                panel_hwnd = win32gui.FindWindow(None, "K线复盘助手")
                foreground = win32gui.GetForegroundWindow()
                _target_thread, target_pid = win32process.GetWindowThreadProcessId(hwnd)
                _foreground_thread, foreground_pid = win32process.GetWindowThreadProcessId(foreground)
                same_app_active = foreground in (hwnd, panel_hwnd) or foreground_pid == target_pid
                if not same_app_active:
                    for app_hwnd, title, _class_name, _app_rect in list_visible_windows():
                        if "同花顺远航版" not in title:
                            continue
                        _app_thread, app_pid = win32process.GetWindowThreadProcessId(app_hwnd)
                        if foreground == app_hwnd or foreground_pid == app_pid:
                            same_app_active = True
                            break

                if same_app_active != self.panel_topmost:
                    self.panel_topmost = same_app_active
                    self.root.attributes("-topmost", same_app_active)

                if panel_hwnd and same_app_active:
                    win32gui.SetWindowPos(
                        panel_hwnd,
                        win32con.HWND_TOPMOST,
                        x,
                        y,
                        PANEL_WIDTH,
                        panel_height,
                        win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
                    )
                self.attach_label.configure(text="已吸附")
            else:
                if self.panel_topmost:
                    self.panel_topmost = False
                    self.root.attributes("-topmost", False)
                self.attach_label.configure(text="等待吸附")
        except Exception:
            self.attach_label.configure(text="等待吸附")
        self.root.after(120, self._follow_training_window)

    def toggle_collapsed(self) -> None:
        self.collapsed = not self.collapsed
        if self.collapsed:
            self.body.pack_forget()
            self.collapse_button.configure(text="+")
            self.root.geometry(f"{PANEL_WIDTH}x42")
        else:
            self.body.pack(fill="both", expand=True)
            self.collapse_button.configure(text="−")
            self.root.geometry(f"{PANEL_WIDTH}x{PANEL_HEIGHT}")

    def open_output_dir(self) -> None:
        try:
            self.obsidian_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(self.obsidian_dir)
        except Exception as exc:
            messagebox.showerror("无法打开目录", str(exc), parent=self.root)

    def open_settings(self) -> None:
        if not show_settings(self.root, first_run=False):
            return
        self.stop_event.set()
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable], cwd=str(Path(sys.executable).resolve().parent))
        else:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve())], cwd=str(Path(__file__).resolve().parent))
        self.root.destroy()

    def close(self) -> None:
        self.stop_event.set()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    try:
        enable_dpi_awareness()
        if "--self-test" in sys.argv:
            from PIL import Image

            reader = OcrReader(True)
            if not reader.enabled or reader.engine is None:
                return 10
            reader.read_items(Image.new("RGB", (320, 120), "white"))
            return 11 if reader.last_error else 0
        if not acquire_single_instance():
            return 0
        if requires_initial_setup():
            setup_root = tk.Tk()
            setup_root.withdraw()
            completed = show_settings(setup_root, first_run=True)
            setup_root.destroy()
            if not completed:
                return 0
        RecorderPanel().run()
        return 0
    except Exception as exc:
        messagebox.showerror("K线复盘助手", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

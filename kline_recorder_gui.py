from __future__ import annotations

import ctypes
import os
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import colorchooser, messagebox
from urllib.request import urlopen

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
    normalize_tags,
    requires_initial_setup,
    save_runtime_config,
)
from kline_settings import find_happ_executable, show_settings
from kline_dashboard import DashboardServer


PANEL_WIDTH = 318
PANEL_HEIGHT = 374
PANEL_GAP = 10
TAG_COLORS = ("#ef4444", "#f97316", "#eab308", "#22c55e", "#06b6d4", "#3b82f6", "#8b5cf6", "#ec4899")

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


def blend_color(foreground: str, background: str, amount: float) -> str:
    foreground_rgb = tuple(int(foreground[index : index + 2], 16) for index in (1, 3, 5))
    background_rgb = tuple(int(background[index : index + 2], 16) for index in (1, 3, 5))
    blended = tuple(
        round(background_value + (foreground_value - background_value) * amount)
        for foreground_value, background_value in zip(foreground_rgb, background_rgb)
    )
    return "#" + "".join(f"{value:02x}" for value in blended)


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
        try:
            win32gui.ShowWindow(existing, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(existing)
        except win32gui.error:
            pass
    return False


class RecorderPanel:
    def __init__(self) -> None:
        enable_dpi_awareness()
        self.config = load_config()
        self.title_contains = self.config["window"]["title_contains"]
        self.obsidian_dir = Path(self.config["paths"]["obsidian_dir"])
        self.events: queue.Queue[tuple[str, str, str]] = queue.Queue()
        self.stop_event = threading.Event()
        self.manual_start_event = threading.Event()
        self.history: list[str] = []
        self.collapsed = False
        self.panel_topmost = False
        self.session_active = False
        self.tag_lock = threading.Lock()
        self.tag_presets = normalize_tags(self.config.get("tags", {}).get("presets", []))
        self.selected_tag_names: set[str] = set()
        self.tag_window: tk.Toplevel | None = None
        self.tag_options_frame: tk.Frame | None = None
        self.tag_canvas: tk.Canvas | None = None
        self.tag_list_frame: tk.Frame | None = None
        self.tag_summary_label: tk.Label | None = None
        self.tag_editor_frame: tk.Frame | None = None
        self.tag_footer: tk.Frame | None = None
        self.tag_editor_toggle: tk.Button | None = None
        self.tag_editor_expanded = False
        self.tag_name_entry: tk.Entry | None = None
        self.tag_color = TAG_COLORS[5]
        self.tag_color_swatch: tk.Button | None = None
        self.tag_color_buttons: list[tuple[str, tk.Button]] = []
        self.dashboard = DashboardServer(self.obsidian_dir)

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

        self.manual_start_button = tk.Button(
            self.body,
            text="手动截取开始盘面",
            command=self.request_manual_start,
            bg=COLORS["accent"],
            fg="#ffffff",
            activebackground="#e8640c",
            activeforeground="#ffffff",
            disabledforeground="#7f8794",
            bd=0,
            pady=6,
            font=("Microsoft YaHei UI", 8, "bold"),
            cursor="hand2",
        )
        self.manual_start_button.pack(fill="x", padx=12, pady=(0, 8))

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
            text="数据看板",
            command=self.open_dashboard,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            activebackground=COLORS["surface_alt"],
            activeforeground="#ffffff",
            bd=0,
            padx=2,
            pady=6,
            font=("Microsoft YaHei UI", 8, "bold"),
            cursor="hand2",
        ).pack(side="left")
        tk.Button(
            footer,
            text="笔记目录",
            command=self.open_output_dir,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            activebackground=COLORS["surface_alt"],
            activeforeground="#ffffff",
            bd=0,
            padx=2,
            pady=6,
            font=("Microsoft YaHei UI", 8),
            cursor="hand2",
        ).pack(side="left")
        self.tag_button = tk.Button(
            footer,
            text="标签⌄",
            command=self.open_tags,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            activebackground=COLORS["surface_alt"],
            activeforeground="#ffffff",
            bd=0,
            padx=2,
            pady=6,
            font=("Microsoft YaHei UI", 8),
            cursor="hand2",
        )
        self.tag_button.pack(side="left")
        tk.Button(
            footer,
            text="设置",
            command=self.open_settings,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            activebackground=COLORS["surface_alt"],
            activeforeground="#ffffff",
            bd=0,
            padx=2,
            pady=6,
            font=("Microsoft YaHei UI", 8),
            cursor="hand2",
        ).pack(side="left")
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
                if state in {"training", "loading", "captured", "result", "saving"}:
                    self.session_active = True
                elif state == "saved":
                    self.session_active = False
                    self._clear_selected_tags()
                elif state == "home" and self.session_active:
                    self.session_active = False
                    self._clear_selected_tags()
                if state == "captured":
                    self.manual_start_button.configure(
                        text="本局正在记录",
                        state="disabled",
                        cursor="arrow",
                    )
                elif state in {"result", "saving"}:
                    self.manual_start_button.configure(
                        text="正在处理本局结果",
                        state="disabled",
                        cursor="arrow",
                    )
                elif state in {"saved", "home", "waiting", "connected", "error"}:
                    self.manual_start_event.clear()
                    self.manual_start_button.configure(
                        text="手动截取开始盘面",
                        state="normal",
                        cursor="hand2",
                    )
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
            run_recorder(
                self.post_status,
                self.stop_event,
                self.get_selected_tags,
                self.manual_start_event,
            )
        except Exception as exc:
            self.post_status("error", "录制服务发生错误", str(exc))

    def request_manual_start(self) -> None:
        if self.manual_start_event.is_set():
            return
        self.manual_start_event.set()
        self.manual_start_button.configure(
            text="正在截取当前盘面…",
            state="disabled",
            cursor="arrow",
        )
        self.post_status("loading", "已请求手动开始", "正在截取当前训练盘面")

    def get_selected_tags(self) -> list[dict[str, str]]:
        with self.tag_lock:
            selected = set(self.selected_tag_names)
            return [dict(tag) for tag in self.tag_presets if tag["name"] in selected]

    def _clear_selected_tags(self) -> None:
        with self.tag_lock:
            self.selected_tag_names.clear()
        self._update_tag_button()
        self._refresh_tag_options()

    def _update_tag_button(self) -> None:
        with self.tag_lock:
            count = len(self.selected_tag_names)
        expanded = False
        if self.tag_window is not None:
            try:
                expanded = bool(self.tag_window.winfo_exists())
            except tk.TclError:
                pass
        suffix = "⌃" if expanded else "⌄"
        count_text = str(count) if count < 10 else "+"
        self.tag_button.configure(
            text=f"标签{count_text}{suffix}" if count else f"标签{suffix}",
            fg=COLORS["accent"] if count else COLORS["muted"],
        )

    def open_tags(self) -> None:
        if self.tag_window is not None:
            try:
                if self.tag_window.winfo_exists():
                    self._close_tag_panel()
                    return
            except tk.TclError:
                pass

        window = tk.Toplevel(self.root)
        self.tag_window = window
        self._update_tag_button()
        window.title("本局标签")
        window.geometry("360x360")
        window.resizable(False, False)
        window.overrideredirect(True)
        window.configure(bg=COLORS["border"])
        window.transient(self.root)
        window.attributes("-topmost", self.panel_topmost)

        shell = tk.Frame(window, bg=COLORS["background"])
        shell.pack(fill="both", expand=True, padx=1, pady=1)
        header = tk.Frame(shell, bg=COLORS["surface"], height=86)
        header.pack(fill="x")
        header.pack_propagate(False)
        mark = tk.Frame(header, bg=COLORS["accent"], width=4, height=24)
        mark.pack(side="left", padx=(14, 10))
        mark.pack_propagate(False)
        title_group = tk.Frame(header, bg=COLORS["surface"])
        title_group.pack(side="left", fill="x", expand=True, pady=10)
        tk.Label(
            title_group,
            text="本局标签",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w")
        self.tag_summary_label = tk.Label(
            title_group,
            text="未选择标签",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        )
        self.tag_summary_label.pack(anchor="w", pady=(2, 0))
        tk.Button(
            header,
            text="×",
            command=self._close_tag_panel,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            activebackground="#3a2023",
            activeforeground="#ffffff",
            bd=0,
            width=3,
            font=("Segoe UI", 13),
            cursor="hand2",
        ).pack(side="right", fill="y")

        section_header = tk.Frame(shell, bg=COLORS["background"])
        section_header.pack(fill="x", padx=14, pady=(12, 7))
        tk.Label(
            section_header,
            text="选择标签",
            bg=COLORS["background"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(side="left")

        list_frame = tk.Frame(shell, bg=COLORS["background"])
        self.tag_list_frame = list_frame
        list_frame.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        scroll_indicator = tk.Canvas(
            list_frame,
            width=6,
            bg=COLORS["background"],
            highlightthickness=0,
        )
        scroll_indicator.pack(side="right", fill="y", padx=(4, 0))
        scroll_state = [0.0, 1.0]

        def draw_scroll_indicator() -> None:
            scroll_indicator.delete("all")
            first, last = scroll_state
            if last - first >= 0.999:
                return
            height = max(1, scroll_indicator.winfo_height())
            top = round(first * height)
            bottom = max(top + 20, round(last * height))
            scroll_indicator.create_rectangle(
                1,
                top,
                5,
                min(height, bottom),
                fill=COLORS["muted"],
                outline="",
            )

        def update_scroll_indicator(first: str, last: str) -> None:
            scroll_state[:] = [float(first), float(last)]
            draw_scroll_indicator()

        scroll_indicator.bind("<Configure>", lambda _event: draw_scroll_indicator())
        self.tag_canvas = tk.Canvas(
            list_frame,
            bg=COLORS["background"],
            width=1,
            height=1,
            highlightthickness=0,
            yscrollcommand=update_scroll_indicator,
        )
        self.tag_canvas.pack(side="left", fill="both", expand=True)
        self.tag_options_frame = tk.Frame(self.tag_canvas, bg=COLORS["background"])
        canvas_window = self.tag_canvas.create_window((0, 0), window=self.tag_options_frame, anchor="nw")
        self.tag_options_frame.bind(
            "<Configure>",
            lambda _event: self.tag_canvas.configure(scrollregion=self.tag_canvas.bbox("all")),
        )
        self.tag_canvas.bind(
            "<Configure>",
            lambda event: self.tag_canvas.itemconfigure(canvas_window, width=event.width),
        )
        window.bind(
            "<MouseWheel>",
            lambda event: self.tag_canvas.yview_scroll(int(-event.delta / 120), "units")
            if self.tag_canvas is not None
            else None,
        )
        self._refresh_tag_options()

        self.tag_editor_frame = tk.Frame(shell, bg=COLORS["surface_alt"])
        tk.Label(
            self.tag_editor_frame,
            text="新建标签",
            bg=COLORS["surface_alt"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(anchor="w", padx=12, pady=(10, 7))
        editor = tk.Frame(self.tag_editor_frame, bg=COLORS["surface_alt"])
        editor.pack(fill="x", padx=12)
        self.tag_name_entry = tk.Entry(
            editor,
            bg=COLORS["background"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            font=("Microsoft YaHei UI", 9),
            width=8,
        )
        self.tag_name_entry.insert(0, "新标签名称")
        self.tag_name_entry.bind("<FocusIn>", self._clear_tag_placeholder)
        self.tag_color_swatch = tk.Button(
            editor,
            text="⋯",
            width=2,
            bg=self.tag_color,
            activebackground=self.tag_color,
            fg="#ffffff",
            activeforeground="#ffffff",
            bd=0,
            command=self._choose_tag_color,
            cursor="hand2",
        )
        add_button = tk.Button(
            editor,
            text="添加",
            command=self._add_tag_preset,
            bg=COLORS["accent"],
            fg="#17191f",
            activebackground="#ff9147",
            activeforeground="#17191f",
            bd=0,
            padx=12,
            font=("Microsoft YaHei UI", 8, "bold"),
            cursor="hand2",
        )
        add_button.pack(side="right", fill="y")
        self.tag_color_swatch.pack(side="right", fill="y", padx=(8, 8))
        self.tag_name_entry.pack(side="left", fill="x", expand=True, ipady=7)

        palette = tk.Frame(self.tag_editor_frame, bg=COLORS["surface_alt"])
        palette.pack(fill="x", padx=9, pady=(8, 10))
        self.tag_color_buttons = []
        for column in range(4):
            palette.columnconfigure(column, weight=1)
        for index, color in enumerate(TAG_COLORS):
            color_button = tk.Button(
                palette,
                text="",
                width=2,
                height=1,
                bg=color,
                activebackground=color,
                bd=2,
                relief="solid",
                highlightthickness=1,
                highlightbackground=COLORS["surface_alt"],
                command=lambda value=color: self._set_tag_color(value),
                cursor="hand2",
            )
            color_button.grid(row=index // 4, column=index % 4, sticky="ew", padx=3, pady=3, ipady=3)
            self.tag_color_buttons.append((color, color_button))

        self.tag_footer = tk.Frame(shell, bg=COLORS["surface"])
        self.tag_footer.pack(fill="x", side="bottom", before=list_frame)
        self.tag_editor_toggle = tk.Button(
            self.tag_footer,
            text="＋ 新建标签",
            command=self._toggle_tag_editor,
            bg=COLORS["surface"],
            fg=COLORS["accent"],
            activebackground=COLORS["surface_alt"],
            activeforeground="#ffad73",
            bd=0,
            padx=12,
            pady=9,
            font=("Microsoft YaHei UI", 8, "bold"),
            cursor="hand2",
        )
        self.tag_editor_toggle.pack(side="left")
        tk.Button(
            self.tag_footer,
            text="收起",
            command=self._close_tag_panel,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            activebackground=COLORS["surface_alt"],
            activeforeground="#ffffff",
            bd=0,
            padx=14,
            pady=9,
            font=("Microsoft YaHei UI", 8),
            cursor="hand2",
        ).pack(side="right")

        self._set_tag_color(self.tag_color)
        self.tag_editor_expanded = False
        window.bind("<Escape>", lambda _event: self._close_tag_panel())
        window.lift()
        window.update_idletasks()
        self._position_tag_window()
        window.focus_force()

    def _close_tag_panel(self) -> None:
        window = self.tag_window
        self.tag_window = None
        self.tag_options_frame = None
        self.tag_canvas = None
        self.tag_list_frame = None
        self.tag_summary_label = None
        self.tag_editor_frame = None
        self.tag_footer = None
        self.tag_editor_toggle = None
        self.tag_name_entry = None
        self.tag_color_swatch = None
        self.tag_color_buttons = []
        self.tag_editor_expanded = False
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass
        self._update_tag_button()

    def _position_tag_window(self) -> None:
        window = self.tag_window
        if window is None:
            return
        try:
            if not window.winfo_exists():
                return
            width = 360
            height = 580 if self.tag_editor_expanded else 360
            self.root.update_idletasks()
            monitor = win32api.MonitorFromWindow(self.root.winfo_id(), win32con.MONITOR_DEFAULTTONEAREST)
            work_left, work_top, work_right, work_bottom = win32api.GetMonitorInfo(monitor)["Work"]
            root_x = self.root.winfo_rootx()
            root_y = self.root.winfo_rooty()
            right_x = root_x + self.root.winfo_width() + 8
            left_x = root_x - width - 8
            x = right_x if right_x + width <= work_right else max(work_left, left_x)
            y = min(max(root_y, work_top), max(work_top, work_bottom - height))
            window.geometry(f"{width}x{height}+{x}+{y}")
            window.attributes("-topmost", self.panel_topmost)
        except (tk.TclError, win32gui.error):
            pass

    def _toggle_tag_editor(self) -> None:
        if (
            self.tag_editor_frame is None
            or self.tag_footer is None
            or self.tag_editor_toggle is None
            or self.tag_list_frame is None
        ):
            return
        self.tag_editor_expanded = not self.tag_editor_expanded
        if self.tag_editor_expanded:
            self.tag_editor_frame.pack(
                fill="x",
                side="bottom",
                padx=14,
                pady=(0, 10),
                before=self.tag_list_frame,
            )
            self.tag_editor_toggle.configure(text="− 收起新建")
        else:
            self.tag_editor_frame.pack_forget()
            self.tag_editor_toggle.configure(text="＋ 新建标签")
        self._position_tag_window()

    def _clear_tag_placeholder(self, _event: object) -> None:
        if self.tag_name_entry is not None and self.tag_name_entry.get() == "新标签名称":
            self.tag_name_entry.delete(0, "end")

    def _set_tag_color(self, color: str) -> None:
        self.tag_color = color
        if self.tag_color_swatch is not None:
            self.tag_color_swatch.configure(bg=color, activebackground=color)
        for value, button in self.tag_color_buttons:
            button.configure(
                text="✓" if value == color else "",
                fg="#ffffff",
                activeforeground="#ffffff",
                highlightbackground="#ffffff" if value == color else COLORS["surface_alt"],
                highlightcolor="#ffffff" if value == color else COLORS["surface_alt"],
            )

    def _choose_tag_color(self) -> None:
        _rgb, color = colorchooser.askcolor(self.tag_color, title="选择标签颜色", parent=self.tag_window)
        if color:
            self._set_tag_color(color.lower())

    def _toggle_tag(self, name: str) -> None:
        with self.tag_lock:
            if name in self.selected_tag_names:
                self.selected_tag_names.remove(name)
            else:
                self.selected_tag_names.add(name)
        self._update_tag_button()
        self._refresh_tag_options()

    def _refresh_tag_options(self) -> None:
        frame = self.tag_options_frame
        if frame is None:
            return
        try:
            if not frame.winfo_exists():
                return
        except tk.TclError:
            return

        for child in frame.winfo_children():
            child.destroy()
        with self.tag_lock:
            presets = [dict(tag) for tag in self.tag_presets]
            selected_names = set(self.selected_tag_names)

        if self.tag_summary_label is not None:
            count = len(selected_names)
            self.tag_summary_label.configure(
                text=f"已选择 {count} 个 · 保存后自动清空" if count else "未选择标签",
                fg=COLORS["accent"] if count else COLORS["muted"],
            )

        if not presets:
            tk.Label(
                frame,
                text="暂无标签",
                bg=COLORS["background"],
                fg=COLORS["muted"],
                font=("Microsoft YaHei UI", 9),
            ).pack(fill="x", pady=24)
            return

        for tag in presets:
            name = tag["name"]
            color = tag["color"]
            selected = name in selected_names
            row_bg = blend_color(color, COLORS["surface_alt"], 0.20) if selected else COLORS["surface_alt"]
            border = tk.Frame(frame, bg=color if selected else COLORS["border"], padx=1, pady=1)
            border.pack(fill="x", pady=(0, 6))
            row = tk.Frame(border, bg=row_bg)
            row.pack(fill="x")
            tk.Button(
                row,
                text="×",
                command=lambda value=name: self._delete_tag_preset(value),
                bg=row_bg,
                fg=COLORS["muted"],
                activebackground="#3a2023",
                activeforeground="#ffffff",
                bd=0,
                width=3,
                font=("Segoe UI", 11),
                cursor="hand2",
            ).pack(side="right", fill="y")
            tk.Button(
                row,
                text=f"✓  {name}" if selected else f"●  {name}",
                command=lambda value=name: self._toggle_tag(value),
                anchor="w",
                justify="left",
                wraplength=230,
                bg=row_bg,
                fg="#ffffff" if selected else color,
                activebackground=blend_color(color, COLORS["surface_alt"], 0.28),
                activeforeground="#ffffff",
                bd=0,
                padx=12,
                pady=8,
                font=("Microsoft YaHei UI", 9, "bold" if selected else "normal"),
                cursor="hand2",
            ).pack(side="left", fill="both", expand=True)

        if self.tag_canvas is not None:
            self.tag_canvas.after_idle(
                lambda: self.tag_canvas.configure(scrollregion=self.tag_canvas.bbox("all"))
                if self.tag_canvas is not None
                else None
            )

    def _add_tag_preset(self) -> None:
        if self.tag_name_entry is None:
            return
        value = self.tag_name_entry.get().strip()
        candidate = normalize_tags(({"name": value, "color": self.tag_color},))
        if not candidate or value == "新标签名称":
            messagebox.showwarning("标签名称无效", "请输入 1 到 20 个字符的标签名称。", parent=self.tag_window)
            return
        tag = candidate[0]
        with self.tag_lock:
            existing = next(
                (item for item in self.tag_presets if item["name"].casefold() == tag["name"].casefold()),
                None,
            )
            if existing is None:
                self.tag_presets.append(tag)
            else:
                existing["color"] = tag["color"]
                tag = existing
            self.selected_tag_names.add(tag["name"])
        self._persist_tag_presets()
        self.tag_name_entry.delete(0, "end")
        self._refresh_tag_options()
        self._update_tag_button()

    def _delete_tag_preset(self, name: str) -> None:
        if not messagebox.askyesno(
            "删除标签预设",
            f"确定删除“{name}”吗？\n已保存记录中的标签不会受影响。",
            parent=self.tag_window,
        ):
            return
        with self.tag_lock:
            self.tag_presets = [tag for tag in self.tag_presets if tag["name"] != name]
            self.selected_tag_names.discard(name)
        self._persist_tag_presets()
        self._refresh_tag_options()
        self._update_tag_button()

    def _persist_tag_presets(self) -> None:
        try:
            with self.tag_lock:
                presets = [dict(tag) for tag in self.tag_presets]
            self.config.setdefault("tags", {})["presets"] = presets
            save_runtime_config(self.config)
        except Exception as exc:
            messagebox.showerror("无法保存标签", str(exc), parent=self.tag_window or self.root)

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
                same_app_active = (
                    foreground in (hwnd, panel_hwnd)
                    or foreground_pid in (target_pid, os.getpid())
                )
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
            else:
                if self.panel_topmost:
                    self.panel_topmost = False
                    self.root.attributes("-topmost", False)
            if self.tag_window is not None:
                self._position_tag_window()
        except Exception:
            pass
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
        if self.tag_window is not None:
            self._position_tag_window()

    def open_output_dir(self) -> None:
        try:
            self.obsidian_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(self.obsidian_dir)
        except Exception as exc:
            messagebox.showerror("无法打开目录", str(exc), parent=self.root)

    def open_dashboard(self) -> None:
        try:
            self.dashboard.start(open_browser=True)
            self.post_status("connected", "已打开数据看板", "浏览器正在读取本地复盘记录")
        except Exception as exc:
            messagebox.showerror("无法打开数据看板", str(exc), parent=self.root)

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
        self.dashboard.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    try:
        enable_dpi_awareness()
        if "--dashboard-self-test" in sys.argv:
            with tempfile.TemporaryDirectory(prefix="KlineDashboardTest-") as temp_dir:
                dashboard = DashboardServer(Path(temp_dir))
                url = dashboard.start(open_browser=False)
                try:
                    with urlopen(url, timeout=5) as response:
                        index = response.read()
                    with urlopen(url + "api/dashboard", timeout=5) as response:
                        api = response.read()
                    return 0 if b"<!doctype html>" in index and b'"total":0' in api else 12
                finally:
                    dashboard.stop()
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

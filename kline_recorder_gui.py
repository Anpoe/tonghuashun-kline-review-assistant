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
PANEL_HEIGHT = 334
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
        self.history: list[str] = []
        self.collapsed = False
        self.panel_topmost = False
        self.session_active = False
        self.tag_lock = threading.Lock()
        self.tag_presets = normalize_tags(self.config.get("tags", {}).get("presets", []))
        self.selected_tag_names: set[str] = set()
        self.tag_window: tk.Toplevel | None = None
        self.tag_listbox: tk.Listbox | None = None
        self.tag_name_entry: tk.Entry | None = None
        self.tag_color = TAG_COLORS[5]
        self.tag_color_swatch: tk.Button | None = None
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
            padx=4,
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
            padx=4,
            pady=6,
            font=("Microsoft YaHei UI", 8),
            cursor="hand2",
        ).pack(side="left")
        self.tag_button = tk.Button(
            footer,
            text="标签",
            command=self.open_tags,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            activebackground=COLORS["surface_alt"],
            activeforeground="#ffffff",
            bd=0,
            padx=4,
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
            padx=4,
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
            run_recorder(self.post_status, self.stop_event, self.get_selected_tags)
        except Exception as exc:
            self.post_status("error", "录制服务发生错误", str(exc))

    def get_selected_tags(self) -> list[dict[str, str]]:
        with self.tag_lock:
            selected = set(self.selected_tag_names)
            return [dict(tag) for tag in self.tag_presets if tag["name"] in selected]

    def _clear_selected_tags(self) -> None:
        with self.tag_lock:
            self.selected_tag_names.clear()
        self._update_tag_button()
        self._refresh_tag_listbox()

    def _update_tag_button(self) -> None:
        count = len(self.selected_tag_names)
        self.tag_button.configure(
            text=f"标签 {count}" if count else "标签",
            fg=COLORS["accent"] if count else COLORS["muted"],
        )

    def open_tags(self) -> None:
        if self.tag_window is not None and self.tag_window.winfo_exists():
            self.tag_window.lift()
            self.tag_window.focus_force()
            return

        window = tk.Toplevel(self.root)
        self.tag_window = window
        window.title("本局标签")
        window.geometry("420x540")
        window.minsize(400, 430)
        window.resizable(True, True)
        window.configure(bg=COLORS["background"])
        window.transient(self.root)

        header = tk.Frame(window, bg=COLORS["surface"])
        header.pack(fill="x")
        tk.Label(
            header,
            text="本局标签",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w", padx=18, pady=(14, 2))
        tk.Label(
            header,
            text="选中的标签会写入当前或下一局，保存后自动清空",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
            justify="left",
            wraplength=330,
        ).pack(anchor="w", padx=18, pady=(0, 13))

        list_frame = tk.Frame(window, bg=COLORS["background"])
        list_frame.pack(fill="both", expand=True, padx=18, pady=(14, 8))
        scrollbar = tk.Scrollbar(list_frame, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        self.tag_listbox = tk.Listbox(
            list_frame,
            height=6,
            selectmode=tk.MULTIPLE,
            exportselection=False,
            activestyle="none",
            bg=COLORS["surface_alt"],
            fg=COLORS["text"],
            selectbackground="#384154",
            selectforeground="#ffffff",
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            relief="flat",
            font=("Microsoft YaHei UI", 9),
            yscrollcommand=scrollbar.set,
        )
        self.tag_listbox.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self.tag_listbox.yview)
        self.tag_listbox.bind("<<ListboxSelect>>", self._on_tag_selection)
        self._refresh_tag_listbox()

        editor = tk.Frame(window, bg=COLORS["background"])
        editor.pack(fill="x", padx=18, pady=(4, 0))
        self.tag_name_entry = tk.Entry(
            editor,
            bg=COLORS["surface_alt"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            font=("Microsoft YaHei UI", 9),
        )
        self.tag_name_entry.pack(side="left", fill="x", expand=True, ipady=7)
        self.tag_name_entry.insert(0, "新标签名称")
        self.tag_name_entry.bind("<FocusIn>", self._clear_tag_placeholder)
        self.tag_color_swatch = tk.Button(
            editor,
            text="",
            width=3,
            bg=self.tag_color,
            activebackground=self.tag_color,
            bd=0,
            command=self._choose_tag_color,
            cursor="hand2",
        )
        self.tag_color_swatch.pack(side="left", fill="y", padx=(8, 0))
        tk.Button(
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
        ).pack(side="left", fill="y", padx=(8, 0))

        palette = tk.Frame(window, bg=COLORS["background"])
        palette.pack(fill="x", padx=18, pady=10)
        for column in range(4):
            palette.columnconfigure(column, weight=1)
        for index, color in enumerate(TAG_COLORS):
            tk.Button(
                palette,
                text="",
                width=2,
                height=1,
                bg=color,
                activebackground=color,
                bd=0,
                command=lambda value=color: self._set_tag_color(value),
                cursor="hand2",
            ).grid(row=index // 4, column=index % 4, sticky="ew", padx=3, pady=3, ipady=3)

        actions = tk.Frame(window, bg=COLORS["background"])
        actions.pack(fill="x", padx=18, pady=(2, 14))
        tk.Button(
            actions,
            text="删除选中预设",
            command=self._delete_tag_presets,
            bg=COLORS["background"],
            fg=COLORS["error"],
            activebackground=COLORS["surface_alt"],
            activeforeground="#ff8b8b",
            bd=0,
            font=("Microsoft YaHei UI", 8),
            cursor="hand2",
        ).pack(side="left")
        tk.Button(
            actions,
            text="完成",
            command=window.destroy,
            bg=COLORS["surface_alt"],
            fg=COLORS["text"],
            activebackground="#343946",
            activeforeground="#ffffff",
            bd=0,
            padx=16,
            pady=6,
            font=("Microsoft YaHei UI", 8, "bold"),
            cursor="hand2",
        ).pack(side="right")

        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.lift()
        window.focus_force()

    def _clear_tag_placeholder(self, _event: object) -> None:
        if self.tag_name_entry is not None and self.tag_name_entry.get() == "新标签名称":
            self.tag_name_entry.delete(0, "end")

    def _set_tag_color(self, color: str) -> None:
        self.tag_color = color
        if self.tag_color_swatch is not None:
            self.tag_color_swatch.configure(bg=color, activebackground=color)

    def _choose_tag_color(self) -> None:
        _rgb, color = colorchooser.askcolor(self.tag_color, title="选择标签颜色", parent=self.tag_window)
        if color:
            self._set_tag_color(color.lower())

    def _on_tag_selection(self, _event: object | None = None) -> None:
        if self.tag_listbox is None:
            return
        selected = {self.tag_presets[index]["name"] for index in self.tag_listbox.curselection()}
        with self.tag_lock:
            self.selected_tag_names = selected
        self._update_tag_button()

    def _refresh_tag_listbox(self) -> None:
        if self.tag_listbox is None:
            return
        try:
            if not self.tag_listbox.winfo_exists():
                return
        except tk.TclError:
            return
        self.tag_listbox.delete(0, "end")
        for index, tag in enumerate(self.tag_presets):
            self.tag_listbox.insert("end", tag["name"])
            self.tag_listbox.itemconfigure(index, fg=tag["color"])
            if tag["name"] in self.selected_tag_names:
                self.tag_listbox.selection_set(index)

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
        self._refresh_tag_listbox()
        self._update_tag_button()

    def _delete_tag_presets(self) -> None:
        if self.tag_listbox is None:
            return
        indices = set(self.tag_listbox.curselection())
        if not indices:
            return
        removed = {self.tag_presets[index]["name"] for index in indices}
        names = "、".join(sorted(removed))
        if not messagebox.askyesno("删除标签预设", f"确定删除“{names}”吗？\n已保存记录中的标签不会受影响。", parent=self.tag_window):
            return
        with self.tag_lock:
            self.tag_presets = [tag for index, tag in enumerate(self.tag_presets) if index not in indices]
            self.selected_tag_names.difference_update(removed)
        self._persist_tag_presets()
        self._refresh_tag_listbox()
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
            else:
                if self.panel_topmost:
                    self.panel_topmost = False
                    self.root.attributes("-topmost", False)
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

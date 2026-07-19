from __future__ import annotations

import copy
import os
import tkinter as tk
import winreg
from pathlib import Path
from tkinter import filedialog, messagebox

import win32api
import win32con
import win32process

from kline_recorder import (
    BUNDLED_CONFIG_PATH,
    IS_FROZEN,
    SOURCE_CONFIG_PATH,
    USER_CONFIG_PATH,
    load_config,
    save_runtime_config,
)


BG = "#15171c"
SURFACE = "#20232b"
BORDER = "#3b414d"
TEXT = "#f3f4f6"
MUTED = "#a5abb5"
ACCENT = "#ff7a1a"


def _running_happ_path() -> Path | None:
    for pid in win32process.EnumProcesses():
        handle = None
        try:
            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
                False,
                pid,
            )
            executable = Path(win32process.GetModuleFileNameEx(handle, 0))
            if executable.name.casefold() == "happ.exe" and executable.is_file():
                return executable
        except Exception:
            pass
        finally:
            if handle is not None:
                try:
                    handle.Close()
                except Exception:
                    pass
    return None


def _registry_candidates() -> list[Path]:
    candidates: list[Path] = []
    app_path_keys = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\happ.exe",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\happ.exe",
    ]
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for key_path in app_path_keys:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    value = str(winreg.QueryValue(key, None)).strip(' "')
                    if value:
                        candidates.append(Path(value))
            except OSError:
                pass

    uninstall_roots = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for root_path in uninstall_roots:
            try:
                root = winreg.OpenKey(hive, root_path)
            except OSError:
                continue
            with root:
                index = 0
                while True:
                    try:
                        child_name = winreg.EnumKey(root, index)
                        index += 1
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(root, child_name) as child:
                            display_name = str(winreg.QueryValueEx(child, "DisplayName")[0])
                            if "同花顺" not in display_name and "远航" not in display_name:
                                continue
                            try:
                                install_location = Path(str(winreg.QueryValueEx(child, "InstallLocation")[0]))
                                candidates.extend(
                                    [install_location / "bin" / "happ.exe", install_location / "happ.exe"]
                                )
                            except OSError:
                                pass
                            try:
                                icon_value = str(winreg.QueryValueEx(child, "DisplayIcon")[0])
                                candidates.append(Path(icon_value.split(",", 1)[0].strip(' "')))
                            except OSError:
                                pass
                    except OSError:
                        continue
    return candidates


def _shortcut_candidates() -> list[Path]:
    try:
        from win32com.client import Dispatch
    except Exception:
        return []

    roots = [
        Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path.home() / "Desktop",
        Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop",
    ]
    shell = Dispatch("WScript.Shell")
    candidates: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            shortcuts = root.rglob("*.lnk")
            for shortcut in shortcuts:
                if "同花顺" not in shortcut.stem and "远航" not in shortcut.stem:
                    continue
                try:
                    target = str(shell.CreateShortcut(str(shortcut)).TargetPath).strip()
                    if target:
                        candidates.append(Path(target))
                except Exception:
                    pass
        except OSError:
            pass
    return candidates


def find_happ_executable(configured: str = "") -> Path | None:
    running = _running_happ_path()
    if running is not None:
        return running

    candidates: list[Path] = []
    if configured.strip():
        candidates.append(Path(configured.strip()))
    candidates.extend(_shortcut_candidates())
    candidates.extend(_registry_candidates())

    program_roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("LOCALAPPDATA", "")),
    ]
    for drive in ("C", "D", "E", "F"):
        candidates.append(Path(f"{drive}:\\同花顺远航版\\bin\\happ.exe"))
    for root in program_roots:
        candidates.extend(
            [
                root / "同花顺远航版" / "bin" / "happ.exe",
                root / "同花顺" / "bin" / "happ.exe",
                root / "hexin" / "bin" / "happ.exe",
            ]
        )

    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate).casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        if candidate.is_file() and candidate.name.casefold() == "happ.exe":
            return candidate.resolve()
    return None


def default_output_dir() -> Path:
    documents = Path.home() / "Documents"
    base = documents if documents.is_dir() else Path.home()
    return base / "K线训练营复盘"


def _base_config() -> dict:
    if USER_CONFIG_PATH.is_file():
        try:
            return load_config(USER_CONFIG_PATH)
        except Exception:
            pass
    template = BUNDLED_CONFIG_PATH if IS_FROZEN else SOURCE_CONFIG_PATH
    return load_config(template)


class SettingsDialog:
    def __init__(self, parent: tk.Misc, first_run: bool) -> None:
        self.parent = parent
        self.first_run = first_run
        self.saved = False
        self.config = copy.deepcopy(_base_config())

        launcher = self.config.setdefault("launcher", {})
        paths = self.config.setdefault("paths", {})
        detected = find_happ_executable(str(launcher.get("executable", "")))
        executable = str(detected or launcher.get("executable", ""))
        output_dir = str(paths.get("obsidian_dir", "")).strip() or str(default_output_dir())

        self.window = tk.Toplevel(parent)
        self.window.title("首次设置 - K线复盘助手" if first_run else "设置 - K线复盘助手")
        self.window.configure(bg=BG)
        self.window.resizable(True, True)
        screen_width = parent.winfo_screenwidth()
        screen_height = parent.winfo_screenheight()
        window_width = min(680, max(520, screen_width - 40))
        window_height = min(540, max(420, screen_height - 80))
        self.window.geometry(f"{window_width}x{window_height}")
        self.window.minsize(min(560, window_width), min(420, window_height))
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)
        if not first_run:
            self.window.transient(parent)
        self.window.grab_set()

        self.executable_var = tk.StringVar(value=executable)
        self.output_var = tk.StringVar(value=output_dir)
        self.auto_start_var = tk.BooleanVar(value=bool(launcher.get("auto_start", True)))
        self.detected_var = tk.StringVar(
            value="已自动找到同花顺远航版" if detected else "未自动找到，请点击浏览选择 happ.exe"
        )

        self._build()
        self.window.update_idletasks()
        x = parent.winfo_screenwidth() // 2 - self.window.winfo_width() // 2
        y = parent.winfo_screenheight() // 2 - self.window.winfo_height() // 2
        self.window.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()
        if first_run:
            self.window.attributes("-topmost", True)
            self.window.after(600, lambda: self.window.attributes("-topmost", False))

    def _build(self) -> None:
        self.window.grid_columnconfigure(0, weight=1)
        self.window.grid_rowconfigure(1, weight=1)

        header = tk.Frame(self.window, bg=SURFACE, height=64)
        header.grid(row=0, column=0, sticky="ew")
        header.pack_propagate(False)
        tk.Frame(header, bg=ACCENT, width=5).pack(side="left", fill="y")
        tk.Label(
            header,
            text="配置你的复盘助手",
            bg=SURFACE,
            fg=TEXT,
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(side="left", padx=18)

        content_shell = tk.Frame(self.window, bg=BG)
        content_shell.grid(row=1, column=0, sticky="nsew")
        content_shell.grid_columnconfigure(0, weight=1)
        content_shell.grid_rowconfigure(0, weight=1)

        canvas = tk.Canvas(content_shell, bg=BG, bd=0, highlightthickness=0)
        scrollbar = tk.Scrollbar(content_shell, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        content = tk.Frame(canvas, bg=BG)
        content_window = canvas.create_window((24, 18), window=content, anchor="nw")

        def update_scroll_region(_event: tk.Event[tk.Misc] | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def resize_content(event: tk.Event[tk.Misc]) -> None:
            canvas.itemconfigure(content_window, width=max(1, event.width - 48))

        def scroll_content(event: tk.Event[tk.Misc]) -> str:
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"

        content.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", resize_content)
        canvas.bind("<MouseWheel>", scroll_content)
        content.bind("<MouseWheel>", scroll_content)
        self.window.bind("<MouseWheel>", scroll_content)
        self.settings_canvas = canvas

        self._path_field(
            content,
            "同花顺远航版程序",
            self.executable_var,
            self.browse_executable,
        )
        tk.Label(
            content,
            textvariable=self.detected_var,
            bg=BG,
            fg="#4ade80" if self.executable_var.get() else "#f5b942",
            anchor="w",
            font=("Microsoft YaHei UI", 8),
        ).pack(fill="x", pady=(4, 14))

        self._path_field(
            content,
            "复盘笔记保存位置",
            self.output_var,
            self.browse_output,
        )
        tk.Label(
            content,
            text="可以选择 Obsidian 仓库中的文件夹，也可以使用普通文件夹。",
            bg=BG,
            fg=MUTED,
            anchor="w",
            font=("Microsoft YaHei UI", 8),
        ).pack(fill="x", pady=(4, 12))

        tk.Checkbutton(
            content,
            text="启动复盘助手时自动启动同花顺远航版",
            variable=self.auto_start_var,
            bg=BG,
            fg=TEXT,
            activebackground=BG,
            activeforeground=TEXT,
            selectcolor=SURFACE,
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w")

        tk.Frame(content, bg=BG, height=18).pack(fill="x")

        actions = tk.Frame(self.window, bg=SURFACE, height=58)
        actions.grid(row=2, column=0, sticky="ew")
        actions.pack_propagate(False)
        self.actions = actions
        tk.Button(
            actions,
            text="保存并开始" if self.first_run else "保存并重启",
            command=self.save,
            bg=ACCENT,
            fg="#ffffff",
            activebackground="#e9660f",
            activeforeground="#ffffff",
            bd=0,
            padx=20,
            pady=7,
            font=("Microsoft YaHei UI", 9, "bold"),
            cursor="hand2",
        ).pack(side="right", padx=(8, 18), pady=12)
        tk.Button(
            actions,
            text="取消",
            command=self.cancel,
            bg=SURFACE,
            fg=MUTED,
            activebackground="#30343e",
            activeforeground="#ffffff",
            bd=0,
            padx=15,
            pady=7,
            font=("Microsoft YaHei UI", 9),
            cursor="hand2",
        ).pack(side="right", pady=12)

    def _path_field(
        self,
        parent: tk.Misc,
        label: str,
        variable: tk.StringVar,
        browse_command: object,
    ) -> None:
        tk.Label(
            parent,
            text=label,
            bg=BG,
            fg=TEXT,
            anchor="w",
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(fill="x", pady=(0, 5))
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x")
        tk.Entry(
            row,
            textvariable=variable,
            bg=SURFACE,
            fg=TEXT,
            insertbackground=TEXT,
            relief="solid",
            bd=1,
            highlightthickness=0,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", fill="x", expand=True, ipady=6)
        tk.Button(
            row,
            text="浏览...",
            command=browse_command,
            bg="#30343e",
            fg=TEXT,
            activebackground="#414652",
            activeforeground="#ffffff",
            bd=0,
            padx=13,
            pady=7,
            font=("Microsoft YaHei UI", 9),
            cursor="hand2",
        ).pack(side="right", padx=(8, 0))

    def browse_executable(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self.window,
            title="选择同花顺远航版 happ.exe",
            filetypes=[("同花顺程序", "happ.exe"), ("Windows 程序", "*.exe")],
        )
        if selected:
            self.executable_var.set(selected)
            self.detected_var.set("已选择同花顺远航版程序")

    def browse_output(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.window,
            title="选择复盘笔记保存位置",
            initialdir=self.output_var.get() or str(Path.home()),
        )
        if selected:
            self.output_var.set(selected)

    def save(self) -> None:
        executable_text = self.executable_var.get().strip()
        output_text = self.output_var.get().strip()
        auto_start = bool(self.auto_start_var.get())

        if auto_start and (not executable_text or not Path(executable_text).is_file()):
            messagebox.showerror("路径无效", "请选择有效的同花顺远航版 happ.exe。", parent=self.window)
            return
        if not output_text:
            messagebox.showerror("路径无效", "请选择复盘笔记保存位置。", parent=self.window)
            return
        output_path = Path(output_text).expanduser()
        try:
            output_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("无法使用保存目录", str(exc), parent=self.window)
            return

        self.config.setdefault("launcher", {})["executable"] = executable_text
        self.config["launcher"]["auto_start"] = auto_start
        self.config.setdefault("paths", {})["obsidian_dir"] = str(output_path.resolve())
        save_runtime_config(self.config)
        self.saved = True
        self.window.destroy()

    def cancel(self) -> None:
        if self.first_run:
            confirmed = messagebox.askyesno(
                "退出设置",
                "尚未完成首次设置，退出后不会启动复盘助手。",
                parent=self.window,
            )
            if not confirmed:
                return
        self.window.destroy()

    def show(self) -> bool:
        self.parent.wait_window(self.window)
        return self.saved


def show_settings(parent: tk.Misc, first_run: bool = False) -> bool:
    return SettingsDialog(parent, first_run).show()

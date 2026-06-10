# app.py

import os
import time
import shutil
import logging
import re
import subprocess
import tempfile
from pathlib import Path
import sys
import threading
import webbrowser
from queue import Queue
from tkinter import filedialog
from xml.sax.saxutils import escape

from tkinterdnd2 import DND_FILES, TkinterDnD
import customtkinter as ctk
from CTkMessagebox import CTkMessagebox
from PIL import Image
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from core_logic import (
    cleanup_author_string,
    get_basic_paper_details,
    get_paper_details,
    sanitize_filename_part,
    unique_path,
    validate_pdf_filename,
)
from settings import AppSettings, SettingsManager

# --- NEW: DnD-enabled CTk root to keep CTk overlays/alpha in sync with main window ---
class DnDCTk(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self, *args, **kwargs):
        ctk.CTk.__init__(self, *args, **kwargs)
        # Initialize TkDnD on this CTk window
        self.TkdndVersion = TkinterDnD._require(self)
        TkinterDnD.DnDWrapper.__init__(self)

class TextboxRedirector:
    MOVED_PATH_RE = re.compile(r"MOVED: '.*' -> '([^']+)'")

    def __init__(self, textbox: ctk.CTkTextbox):
        self.textbox = textbox
        self.link_count = 0
        self.textbox.tag_config("log_link", foreground="#4da3ff", underline=True)
        self.textbox.tag_bind("log_link", "<Enter>", lambda _event: self.textbox.configure(cursor="hand2"))
        self.textbox.tag_bind("log_link", "<Leave>", lambda _event: self.textbox.configure(cursor=""))

    def write(self, text):
        self.textbox.after(0, self._write_on_main_thread, text)

    def _write_on_main_thread(self, text):
        for line in text.splitlines(keepends=True):
            has_newline = line.endswith(("\n", "\r"))
            line_text = line.rstrip("\r\n")
            self.textbox.insert("end", line_text)
            self._append_move_links(line_text)
            if has_newline:
                self.textbox.insert("end", "\n")
        self.textbox.see("end")

    def _append_move_links(self, line_text: str):
        match = self.MOVED_PATH_RE.search(line_text)
        if not match:
            return
        paper_path = Path(match.group(1))
        self.textbox.insert("end", "  ")
        self._append_link("View Location", lambda path=paper_path: self._open_location(path))
        self.textbox.insert("end", "  ")
        self._append_link("View Paper", lambda path=paper_path: self._open_paper(path))

    def _append_link(self, label: str, callback):
        self.link_count += 1
        tag = f"log_link_{self.link_count}"
        self.textbox.insert("end", label, ("log_link", tag))
        self.textbox.tag_bind(tag, "<Button-1>", lambda _event: callback())

    def _open_location(self, paper_path: Path):
        try:
            os.startfile(str(paper_path.parent))
        except Exception as e:
            logging.error(f"Failed to open location for '{paper_path}': {e}")

    def _open_paper(self, paper_path: Path):
        try:
            os.startfile(str(paper_path))
        except Exception as e:
            logging.error(f"Failed to open paper '{paper_path}': {e}")

    def flush(self): pass

class ToolTip:
    def __init__(self, widget, text: str, delay_ms=450):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id = None
        self._window = None
        self.widget.bind("<Enter>", self._schedule, add="+")
        self.widget.bind("<Leave>", self._hide, add="+")
        self.widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _show(self):
        if self._window or not self.text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self._window = ctk.CTkToplevel(self.widget)
        self._window.wm_overrideredirect(True)
        self._window.wm_geometry(f"+{x}+{y}")
        label = ctk.CTkLabel(
            self._window,
            text=self.text,
            justify="left",
            wraplength=320,
            fg_color="#242526",
            text_color="#f2f2f2",
            corner_radius=6,
            padx=10,
            pady=6,
        )
        label.pack()

    def _hide(self, _event=None):
        self._cancel()
        if self._window:
            self._window.destroy()
            self._window = None

    def _cancel(self):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

def add_tooltip(widget, text: str):
    ToolTip(widget, text)
    return widget

# --- NEW: Custom Dialog for Editing Filenames ---
class FilenameEditorDialog(ctk.CTkToplevel):
    def __init__(self, master, original_name: str, ai_title: str, proposed_name: str):
        super().__init__(master)
        self.title("Propose & Edit Filename")
        self.geometry("600x250")
        self.transient(master)
        self.grab_set()

        self.result = None  # This will store the final filename or None if skipped

        # Layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Info Frame
        info_frame = ctk.CTkFrame(self, fg_color="transparent")
        info_frame.grid(row=0, column=0, padx=15, pady=15, sticky="ew")
        info_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(info_frame, text="Original File:", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(info_frame, text=original_name, wraplength=450).grid(row=0, column=1, sticky="w", padx=5)
        
        ctk.CTkLabel(info_frame, text="Detected Title:", font=ctk.CTkFont(weight="bold")).grid(row=1, column=0, sticky="w", pady=(5,0))
        ctk.CTkLabel(info_frame, text=ai_title, wraplength=450).grid(row=1, column=1, sticky="w", padx=5, pady=(5,0))

        # Entry Frame
        entry_frame = ctk.CTkFrame(self)
        entry_frame.grid(row=1, column=0, padx=15, pady=10, sticky="ew")
        entry_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(entry_frame, text="Proposed Filename (Editable):").pack(side="top", anchor="w", padx=10, pady=(5,2))
        self.filename_entry = ctk.CTkEntry(entry_frame, width=550)
        self.filename_entry.pack(side="top", fill="x", expand=True, padx=10, pady=(0,10))
        self.filename_entry.insert(0, proposed_name)

        # Button Frame
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=2, column=0, padx=15, pady=15, sticky="sew")
        button_frame.grid_columnconfigure((0, 1), weight=1)

        self.skip_button = ctk.CTkButton(button_frame, text="Skip File", command=self._on_skip)
        self.skip_button.grid(row=0, column=0, padx=5, pady=5, sticky="ew")
        add_tooltip(self.skip_button, "Leave this PDF where it is and move on without sorting it.")
        self.continue_button = ctk.CTkButton(button_frame, text="Continue", command=self._on_continue)
        self.continue_button.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        add_tooltip(self.continue_button, "Accept the filename shown here and continue to folder selection.")

    def _on_continue(self):
        try:
            self.result = validate_pdf_filename(self.filename_entry.get())
        except ValueError as e:
            CTkMessagebox(master=self, title="Invalid Filename", message=str(e), icon="warning")
            return
        self.destroy()

    def _on_skip(self):
        self.result = None
        self.destroy()

class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master, settings: AppSettings):
        super().__init__(master)
        self.title("Settings")
        self.geometry("760x410")
        self.transient(master)
        self.grab_set()
        self.result = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        frame = ctk.CTkFrame(self)
        frame.grid(row=0, column=0, padx=16, pady=16, sticky="nsew")
        frame.grid_columnconfigure(1, weight=1)

        self.watch_var = ctk.StringVar(value=str(settings.watch_folder or ""))
        self.sorted_var = ctk.StringVar(value=str(settings.sorted_folder or ""))
        self.api_key_var = ctk.StringVar(value=settings.api_key or "")
        self.naming_mode_var = ctk.StringVar(value=settings.clean_naming_mode())
        self.watch_launch_var = ctk.BooleanVar(value=settings.watch_and_launch_enabled)

        ctk.CTkLabel(frame, text="To Sort folder").grid(row=0, column=0, padx=10, pady=(14, 6), sticky="w")
        ctk.CTkEntry(frame, textvariable=self.watch_var).grid(row=0, column=1, padx=10, pady=(14, 6), sticky="ew")
        self.watch_browse_button = ctk.CTkButton(frame, text="Browse", width=90, command=self._browse_watch)
        self.watch_browse_button.grid(row=0, column=2, padx=10, pady=(14, 6))
        add_tooltip(self.watch_browse_button, "Choose the folder the app watches and copies new PDFs into.")

        ctk.CTkLabel(frame, text="Sorted papers root").grid(row=1, column=0, padx=10, pady=6, sticky="w")
        ctk.CTkEntry(frame, textvariable=self.sorted_var).grid(row=1, column=1, padx=10, pady=6, sticky="ew")
        self.sorted_browse_button = ctk.CTkButton(frame, text="Browse", width=90, command=self._browse_sorted)
        self.sorted_browse_button.grid(row=1, column=2, padx=10, pady=6)
        add_tooltip(self.sorted_browse_button, "Choose the root folder where sorted and renamed papers are stored.")

        ctk.CTkLabel(frame, text="Naming mode").grid(row=2, column=0, padx=10, pady=6, sticky="w")
        self.naming_mode_menu = ctk.CTkOptionMenu(frame, variable=self.naming_mode_var, values=["Automatic", "AI", "Basic"])
        self.naming_mode_menu.grid(row=2, column=1, padx=10, pady=6, sticky="w")
        add_tooltip(self.naming_mode_menu, "Automatic uses AI when a key exists, otherwise Basic naming.")

        ctk.CTkLabel(frame, text="Gemini API key").grid(row=3, column=0, padx=10, pady=6, sticky="w")
        self.api_key_entry = ctk.CTkEntry(frame, textvariable=self.api_key_var, show="*")
        self.api_key_entry.grid(row=3, column=1, padx=10, pady=6, sticky="ew")
        add_tooltip(self.api_key_entry, "Optional. Add a Gemini key to enable AI naming on this PC.")
        self.api_help_button = ctk.CTkButton(frame, text="Get Key", width=90, command=self._show_api_help)
        self.api_help_button.grid(row=3, column=2, padx=10, pady=6)
        add_tooltip(self.api_help_button, "Show concise setup instructions for Gemini AI naming.")

        self.watch_launch_check = ctk.CTkCheckBox(
            frame,
            text="Start Watch and Launch at Windows login/unlock",
            variable=self.watch_launch_var,
            command=self._on_watch_launch_toggle,
        )
        self.watch_launch_check.grid(row=4, column=1, padx=10, pady=(10, 6), sticky="w")
        add_tooltip(self.watch_launch_check, "Run the lightweight watcher in the background so PDFs added later can open the sorter app.")

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=1, column=0, padx=16, pady=(0, 16), sticky="ew")
        button_frame.grid_columnconfigure((0, 1), weight=1)
        self.cancel_button = ctk.CTkButton(button_frame, text="Cancel", command=self._cancel)
        self.cancel_button.grid(row=0, column=0, padx=6, sticky="ew")
        add_tooltip(self.cancel_button, "Close Settings without saving folder changes.")
        self.save_button = ctk.CTkButton(button_frame, text="Save", command=self._save)
        self.save_button.grid(row=0, column=1, padx=6, sticky="ew")
        add_tooltip(self.save_button, "Save these folder choices for this PC and restart folder watching.")

    def _browse_watch(self):
        initial = self.watch_var.get() or str(Path.home())
        selected = filedialog.askdirectory(parent=self, title="Choose To Sort folder", initialdir=initial)
        if selected:
            self.watch_var.set(selected)

    def _browse_sorted(self):
        initial = self.sorted_var.get() or str(Path.home())
        selected = filedialog.askdirectory(parent=self, title="Choose Sorted papers root", initialdir=initial)
        if selected:
            self.sorted_var.set(selected)

    def _on_watch_launch_toggle(self):
        if self.watch_launch_var.get() and not self.watch_var.get().strip():
            self._browse_watch()
            if not self.watch_var.get().strip():
                self.watch_launch_var.set(False)

    def _save(self):
        watch = self.watch_var.get().strip()
        sorted_folder = self.sorted_var.get().strip()
        if self.watch_launch_var.get() and not watch:
            self._browse_watch()
            watch = self.watch_var.get().strip()
        if not watch or not sorted_folder:
            CTkMessagebox(master=self, title="Missing Folders", message="Choose both folders before saving.", icon="warning")
            return
        self.result = AppSettings(
            watch_folder=Path(watch).expanduser(),
            sorted_folder=Path(sorted_folder).expanduser(),
            api_key=self.api_key_var.get().strip(),
            naming_mode=self.naming_mode_var.get(),
            watch_and_launch_enabled=self.watch_launch_var.get(),
        )
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

    def _show_api_help(self):
        webbrowser.open("https://aistudio.google.com/app/apikey")
        CTkMessagebox(
            master=self,
            title="Gemini API Key",
            message=(
                "AI naming is optional.\n\n"
                "1. Create a Gemini API key in Google AI Studio.\n"
                "2. Paste it into the Gemini API key field.\n"
                "3. Set Naming mode to Automatic or AI.\n\n"
                "Without a key, Basic naming still works."
            ),
            icon="info",
        )

class App:
    WATCH_LAUNCH_TASK_NAME = "AI Paper Sorter Watch and Launch"

    def _asset_path(self, *parts):
        if getattr(sys, 'frozen', False):
            return self.SCRIPT_DIRECTORY / "assets" / Path(*parts)
        return self.SCRIPT_DIRECTORY.parent / "assets" / Path(*parts)

    def _toolbar_icon(self, filename):
        image = Image.open(self._asset_path("icons", filename))
        return ctk.CTkImage(light_image=image, dark_image=image, size=(22, 22))

    def _watch_launcher_command(self):
        candidates = [
            self.SCRIPT_DIRECTORY.parent / "Watch and Launch" / "Watch and Launch.exe",
            self.SCRIPT_DIRECTORY / "Watch and Launch.exe",
            Path(r"C:\Paper Sorter\dist\Watch and Launch.exe"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate), "", str(candidate.parent)

        pythonw = Path(sys.executable).with_name("pythonw.exe")
        python_exe = pythonw if pythonw.exists() else Path(sys.executable)
        script = self.SCRIPT_DIRECTORY / "watch_and_launch.py"
        return str(python_exe), f'"{script}"', str(self.SCRIPT_DIRECTORY)

    def _watch_launch_task_xml(self):
        command, arguments, working_directory = self._watch_launcher_command()
        arguments_xml = f"<Arguments>{escape(arguments)}</Arguments>" if arguments else ""
        return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Starts the AI Paper Sorter watcher at login and unlock.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
    <SessionStateChangeTrigger>
      <Enabled>true</Enabled>
      <StateChange>SessionUnlock</StateChange>
    </SessionStateChangeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(command)}</Command>
      {arguments_xml}
      <WorkingDirectory>{escape(working_directory)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""

    def _run_hidden(self, command):
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            creationflags=0x08000000,
            timeout=20,
        )

    def _install_watch_launch_task(self):
        xml = self._watch_launch_task_xml()
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as task_file:
            task_file.write(xml)
            task_path = task_file.name
        try:
            result = self._run_hidden(["schtasks", "/Create", "/TN", self.WATCH_LAUNCH_TASK_NAME, "/XML", task_path, "/F"])
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "Task Scheduler did not accept the task.").strip())
            self._run_hidden(["schtasks", "/Run", "/TN", self.WATCH_LAUNCH_TASK_NAME])
        finally:
            try:
                Path(task_path).unlink(missing_ok=True)
            except Exception:
                pass

    def _remove_watch_launch_task(self):
        self._run_hidden(["schtasks", "/Delete", "/TN", self.WATCH_LAUNCH_TASK_NAME, "/F"])
        self._stop_watch_launcher_process()

    def _stop_watch_launcher_process(self):
        powershell = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -eq 'Watch and Launch.exe' -or $_.CommandLine -like '*watch_and_launch.py*' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        self._run_hidden(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", powershell])

    def _sync_watch_launch_setting(self):
        if self.settings.watch_and_launch_enabled:
            self._install_watch_launch_task()
            logging.info("Watch and Launch enabled for Windows login/unlock.")
        else:
            self._remove_watch_launch_task()
            logging.info("Watch and Launch disabled.")

    def __init__(self, root):
        self.root = root
        self.root.title("AI Paper Sorter"); self.root.geometry("900x640")
        ctk.set_appearance_mode("dark")
        self.root.grid_columnconfigure(0, weight=1); self.root.grid_rowconfigure(0, weight=1)

        if getattr(sys, 'frozen', False): self.SCRIPT_DIRECTORY = Path(sys.executable).parent
        else: self.SCRIPT_DIRECTORY = Path(__file__).parent
        self.ENV_API_KEY = os.getenv('GEMINI_API_KEY', '')

        self.settings_manager = SettingsManager(self.SCRIPT_DIRECTORY)
        self.settings = self.settings_manager.load()
        self.WATCH_FOLDER = self.settings.watch_folder
        self.SORTED_FOLDER = self.settings.sorted_folder

        # Unified log for both sorting and naming
        self.LOG_FILE = self._default_log_file()
        if self.settings.is_complete():
            self.WATCH_FOLDER.mkdir(parents=True, exist_ok=True)
            self.SORTED_FOLDER.mkdir(parents=True, exist_ok=True)
        
        self.main_frame = ctk.CTkFrame(self.root)
        self.main_frame.grid(row=0, column=0, sticky="nsew")
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(0, weight=0)
        self.main_frame.grid_rowconfigure(1, weight=1)
        self.main_frame.grid_rowconfigure(2, weight=3)

        self.toolbar_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.toolbar_frame.grid(row=0, column=0, padx=10, pady=(10, 0), sticky="ew")
        self.toolbar_frame.grid_columnconfigure(0, weight=1)
        self.toolbar_icons = {
            "name": self._toolbar_icon("pen.png"),
            "refresh": self._toolbar_icon("refresh.png"),
            "sorted": self._toolbar_icon("folder.png"),
            "log": self._toolbar_icon("clipboard_pen.png"),
            "settings": self._toolbar_icon("gear.png"),
        }
        self.toolbar_buttons = ctk.CTkFrame(self.toolbar_frame, fg_color="transparent")
        self.toolbar_buttons.grid(row=0, column=0, pady=4, sticky="e")
        self.btn_name_papers = ctk.CTkButton(
            self.toolbar_buttons,
            text="Name Papers",
            image=self.toolbar_icons["name"],
            compound="left",
            width=130,
            command=self.rename_papers_flow,
        )
        self.btn_name_papers.pack(side="left", padx=4)
        add_tooltip(self.btn_name_papers, "Use Gemini to rename PDFs in place without moving them into a sorted folder.")
        self.btn_refresh = ctk.CTkButton(
            self.toolbar_buttons,
            text="Refresh",
            image=self.toolbar_icons["refresh"],
            compound="left",
            width=104,
            command=self.refresh_to_sort_folder,
        )
        self.btn_refresh.pack(side="left", padx=4)
        add_tooltip(self.btn_refresh, "Rescan the To Sort folder and queue any PDFs that are waiting there.")
        self.btn_view_sorted = ctk.CTkButton(
            self.toolbar_buttons,
            text="Sorted",
            image=self.toolbar_icons["sorted"],
            compound="left",
            width=96,
            command=self.open_sorted_folder,
        )
        self.btn_view_sorted.pack(side="left", padx=4)
        add_tooltip(self.btn_view_sorted, "Open the configured sorted-paper root folder in Windows Explorer.")
        self.btn_view_log = ctk.CTkButton(
            self.toolbar_buttons,
            text="Log",
            image=self.toolbar_icons["log"],
            compound="left",
            width=96,
            command=self.open_log_file,
        )
        self.btn_view_log.pack(side="left", padx=4)
        add_tooltip(self.btn_view_log, "Open the text log that records app actions and moved papers.")
        self.settings_button = ctk.CTkButton(
            self.toolbar_buttons,
            text="Settings",
            image=self.toolbar_icons["settings"],
            compound="left",
            width=112,
            command=self.open_settings,
        )
        self.settings_button.pack(side="left", padx=(4, 0))
        add_tooltip(self.settings_button, "Set the To Sort folder and sorted-paper library root for this PC.")

        self.top_frame = ctk.CTkFrame(self.main_frame, fg_color="#18191a")  # Match log frame color
        self.top_frame.grid(row=1, column=0, padx=10, pady=10, sticky="nsew")
        self.top_frame.grid_columnconfigure(0, weight=1); self.top_frame.grid_rowconfigure((0, 2), weight=1)
        # DnD registration on a CTk frame still works because root is DnD-enabled (DnDCTk)
        self.top_frame.drop_target_register(DND_FILES); self.top_frame.dnd_bind('<<Drop>>', self.handle_drop)
        self.plus_label = ctk.CTkLabel(self.top_frame, text="+", font=ctk.CTkFont(size=50)); self.plus_label.grid(row=0, column=0, pady=(20, 0))
        self.browse_text_frame = ctk.CTkFrame(self.top_frame, fg_color="transparent"); self.browse_text_frame.grid(row=1, column=0, pady=(10, 20))
        self.drag_label = ctk.CTkLabel(self.browse_text_frame, text="To sort papers, drag them here, or ", font=ctk.CTkFont(size=14)); self.drag_label.pack(side="left")
        self.browse_label = ctk.CTkLabel(self.browse_text_frame, text="browse", font=ctk.CTkFont(size=14, underline=True), text_color=("blue", "cyan"), cursor="hand2")
        self.browse_label.pack(side="left"); self.browse_label.bind("<Button-1>", lambda e: self.select_and_add_papers())
        add_tooltip(self.browse_label, "Select one or more PDF files to copy into the To Sort folder.")
        self.after_browse_label = ctk.CTkLabel(self.browse_text_frame, text=" your computer...", font=ctk.CTkFont(size=14)); self.after_browse_label.pack(side="left")
        
        self.bottom_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.bottom_frame.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="nsew")
        self.bottom_frame.grid_columnconfigure(0, weight=1); self.bottom_frame.grid_rowconfigure(0, weight=1)
        
        self.log_textbox = ctk.CTkTextbox(self.bottom_frame, activate_scrollbars=True); self.log_textbox.grid(row=0, column=0, padx=0, pady=(0, 10), sticky="nsew")
        self.redirector = TextboxRedirector(self.log_textbox)
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', handlers=[
            logging.FileHandler(self.LOG_FILE, encoding='utf-8'), logging.StreamHandler(self.redirector)])
        self.file_queue = Queue(); self.rename_queue = Queue(); self.gui_queue = Queue()
        self.queue_lock = threading.Lock(); self.queued_sort_paths = set()
        self.rename_batch_total = 0; self.rename_batch_done = 0
        self.rename_batch_renamed = 0; self.rename_batch_skipped = 0
        self.root.after(100, self.start_app)
        # One-time safety on startup
        self.root.after(0, self._normalize_root)

    # --- NEW: normalize helper to fix any leaked alpha/disabled state from modals ---
    def _normalize_root(self):
        try:
            self.root.attributes("-alpha", 1.0)
            self.root.attributes("-disabled", False)
            self.root.update_idletasks()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def _ensure_settings(self):
        if self.settings and self.settings.is_complete():
            return True
        return self.open_settings()

    def _default_log_file(self):
        if self.settings and self.settings.sorted_folder:
            return self.settings.sorted_folder / 'paper_sorter_log.txt'
        return self.SCRIPT_DIRECTORY / 'paper_sorter_log.txt'

    def open_settings(self):
        dialog = SettingsDialog(self.root, self.settings or AppSettings())
        self.root.wait_window(dialog)
        self._normalize_root()
        if not dialog.result:
            return False
        try:
            dialog.result.watch_folder.mkdir(parents=True, exist_ok=True)
            dialog.result.sorted_folder.mkdir(parents=True, exist_ok=True)
            self.settings_manager.save(dialog.result)
        except Exception as e:
            CTkMessagebox(master=self.root, title="Settings Error", message=f"Could not save settings:\n{e}", icon="error")
            return False
        self.settings = dialog.result
        self.WATCH_FOLDER = self.settings.watch_folder
        self.SORTED_FOLDER = self.settings.sorted_folder
        self.LOG_FILE = self._default_log_file()
        if hasattr(self, "redirector"):
            self._replace_log_file_handler()
        logging.info(f"Settings saved. To Sort: {self.WATCH_FOLDER}; Sorted: {self.SORTED_FOLDER}")
        try:
            self._sync_watch_launch_setting()
        except Exception as e:
            self.settings.watch_and_launch_enabled = False
            try:
                self.settings_manager.save(self.settings)
            except Exception:
                pass
            CTkMessagebox(
                master=self.root,
                title="Watch and Launch Error",
                message=f"Settings were saved, but Watch and Launch could not be updated:\n{e}",
                icon="warning",
            )
        if hasattr(self, "observer"):
            try:
                self.observer.stop()
                self.observer.join(timeout=3)
            except Exception:
                pass
            self.start_watcher()
        self.process_existing_files()
        return True

    def _replace_log_file_handler(self):
        root_logger = logging.getLogger()
        formatter = logging.Formatter('%(asctime)s - %(message)s')
        for handler in list(root_logger.handlers):
            if isinstance(handler, logging.FileHandler):
                root_logger.removeHandler(handler)
                handler.close()
        file_handler = logging.FileHandler(self.LOG_FILE, encoding='utf-8')
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        root_logger.setLevel(logging.INFO)

    def start_app(self):
        if not self._active_api_key():
            logging.info("Gemini API key is not set. Basic naming is available. To enable AI naming, open Settings, click Get Key, and paste your key.")
        self.worker_thread = threading.Thread(target=self.processing_loop, daemon=True); self.worker_thread.start()
        self.rename_worker_thread = threading.Thread(target=self.rename_processing_loop, daemon=True); self.rename_worker_thread.start()
        if self.settings.is_complete():
            self.start_watcher()
            self.process_existing_files()
        else:
            logging.info("Folder settings are not configured yet. Choose Settings or add a paper to continue.")
            self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.process_gui_queue()
    def on_closing(self):
        logging.info("--- Shutting down... ---")
        if hasattr(self, "observer"):
            try: self.observer.stop(); self.observer.join(timeout=3)
            except Exception: pass
        self.root.destroy()
    def start_watcher(self):
        if not self.settings.is_complete():
            return
        if hasattr(self, "observer") and self.observer.is_alive():
            return
        event_handler = self.create_watchdog_handler(); self.observer = Observer()
        self.observer.schedule(event_handler, str(self.WATCH_FOLDER), recursive=False); self.observer.start()
        logging.info(f"Watching for new files in: {self.WATCH_FOLDER}"); self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    def create_watchdog_handler(self):
        class MyHandler(FileSystemEventHandler):
            def __init__(self, enqueue): self.enqueue = enqueue

            def _maybe_enqueue(self, path):
                pdf_path = Path(path)
                if pdf_path.suffix.lower() == ".pdf":
                    self.enqueue(pdf_path)

            def on_created(self, event):
                if not event.is_directory:
                    self._maybe_enqueue(event.src_path)

            def on_moved(self, event):
                if not event.is_directory:
                    self._maybe_enqueue(event.dest_path)

            def on_modified(self, event):
                if not event.is_directory:
                    self._maybe_enqueue(event.src_path)

        return MyHandler(self._queue_sort_file)

    def _sort_queue_key(self, pdf_path: Path):
        try:
            return str(pdf_path.resolve()).lower()
        except Exception:
            return str(pdf_path).lower()

    def _queue_sort_file(self, pdf_path: Path):
        key = self._sort_queue_key(pdf_path)
        with self.queue_lock:
            if key in self.queued_sort_paths:
                logging.info(f"Already queued for sorting: {pdf_path.name}")
                return
            self.queued_sort_paths.add(key)
        self.file_queue.put(pdf_path)
    def _clear_queued_sort_file(self, pdf_path: Path):
        key = self._sort_queue_key(pdf_path)
        with self.queue_lock:
            self.queued_sort_paths.discard(key)

    def _wait_for_file_ready(self, pdf_path: Path, timeout_seconds=60, stable_seconds=2):
        deadline = time.monotonic() + timeout_seconds
        last_signature = None
        stable_since = None
        last_error = None

        while time.monotonic() < deadline:
            try:
                stat = pdf_path.stat()
                if stat.st_size == 0:
                    stable_since = None
                    last_signature = None
                else:
                    with open(pdf_path, "rb"):
                        pass
                    signature = (stat.st_size, stat.st_mtime_ns)
                    if signature == last_signature:
                        stable_since = stable_since or time.monotonic()
                        if time.monotonic() - stable_since >= stable_seconds:
                            return True
                    else:
                        last_signature = signature
                        stable_since = time.monotonic()
            except FileNotFoundError:
                last_error = "file was not found"
                stable_since = None
                last_signature = None
            except OSError as e:
                last_error = str(e)
                stable_since = None
                last_signature = None
            time.sleep(0.5)

        detail = f" Last error: {last_error}" if last_error else ""
        logging.warning(f"Timed out waiting for file to finish copying: {pdf_path.name}.{detail}")
        return False

    def processing_loop(self):
        while True:
            pdf_path = self.file_queue.get()
            clear_when_done = True
            try:
                if not pdf_path.exists():
                    logging.warning(f"File disappeared before sorting: {pdf_path.name}")
                    continue
                if not self._wait_for_file_ready(pdf_path):
                    continue
                logging.info(f"--- Processing (sort): {pdf_path.name} ---")
                details = self._get_details_for_pdf(pdf_path)
                if details:
                    clear_when_done = False
                    self.gui_queue.put(("sort", pdf_path, details))
                else: logging.error(f"Could not get details for {pdf_path.name}.")
            finally:
                if clear_when_done:
                    self._clear_queued_sort_file(pdf_path)
    def rename_processing_loop(self):
        while True:
            pdf_path = self.rename_queue.get()
            logging.info(f"--- Processing (rename): {pdf_path.name} ---")
            details = self._get_details_for_pdf(pdf_path)
            if details: self.gui_queue.put(("rename", pdf_path, details))
            else:
                logging.error(f"Could not get details for {pdf_path.name}.")
                self.gui_queue.put(("rename_failed", pdf_path, {}))

    def _active_api_key(self):
        return (self.settings.api_key if self.settings else "") or self.ENV_API_KEY

    def _get_details_for_pdf(self, pdf_path: Path):
        mode = self.settings.clean_naming_mode() if self.settings else "Automatic"
        api_key = self._active_api_key()
        if mode == "Basic" or not api_key:
            if mode == "AI" and not api_key:
                logging.warning("AI naming was selected, but no Gemini API key is set. Using Basic naming instead.")
            return get_basic_paper_details(pdf_path)

        details = get_paper_details(pdf_path, api_key)
        if details:
            details["source"] = "AI"
            return details
        logging.warning(f"AI naming failed for {pdf_path.name}. Using Basic naming instead.")
        return get_basic_paper_details(pdf_path)

    def _proposed_filename(self, details: dict) -> str:
        source = details.get("source", "AI")
        author = cleanup_author_string(details.get('author', 'Unknown'))
        year = details.get('year', 'Unknown')
        journal = details.get('journal', 'Unknown')
        title = details.get('title', 'Unknown Title')
        is_multiple = bool(details.get('is_multiple_authors', True))

        if source == "Basic" and (author == "Unknown" or journal == "Unknown"):
            title_part = sanitize_filename_part(title)[:90] or "Paper"
            year_part = sanitize_filename_part(year)
            return f"{title_part}_{year_part}.pdf"

        author_string = f"{author} et al" if is_multiple else author
        new_filename_base = f"{sanitize_filename_part(author_string)}_{sanitize_filename_part(journal)}_{year}"
        return f"{new_filename_base}.pdf"
    def process_gui_queue(self):
        try:
            while not self.gui_queue.empty():
                mode, pdf_path, details = self.gui_queue.get()
                if mode == "sort":
                    try:
                        self.handle_user_confirmation_sort(pdf_path, details)
                    finally:
                        self._clear_queued_sort_file(pdf_path)
                elif mode == "rename": self.handle_rename_confirmation(pdf_path, details)
                elif mode == "rename_failed": self._finish_rename_item(skipped=True)
        finally:
            self.root.after(200, self.process_gui_queue)

    # --- FIXED: Reworked function + normalization after every modal ---
    def handle_user_confirmation_sort(self, pdf_path: Path, details: dict):
        details['author'] = cleanup_author_string(details.get('author', 'Unknown'))
        new_filename_ext = self._proposed_filename(details); title = details.get('title', 'Unknown Title')
        
        # --- STEP 1: Propose and Edit Name ---
        name_dialog = FilenameEditorDialog(self.root, original_name=pdf_path.name, ai_title=title, proposed_name=new_filename_ext)
        self.root.wait_window(name_dialog)
        self._normalize_root()  # <-- normalize after modal
        final_filename = name_dialog.result

        if not final_filename:
            logging.info(f"User skipped '{pdf_path.name}' at name proposal stage."); return

        # --- STEP 2: Check for Duplicates (based on the user-approved name) ---
        final_filename_base = Path(final_filename).stem
        if list(self.SORTED_FOLDER.rglob(f"{final_filename_base}*.pdf")):
            msg_text = (f"A potential duplicate exists for:'{final_filename}'\n\nAdd anyway?")
            msg = CTkMessagebox(master=self.root, title="Suspected Duplicate", message=msg_text, icon="question", option_1="Skip", option_2="Add Anyway")
            choice = msg.get()
            self._normalize_root()  # <-- normalize after modal
            if choice == "Skip":
                logging.warning(f"DUPLICATE: User chose to skip '{pdf_path.name}'.")
                return
        
        # --- STEP 3: Choose the destination folder ---
        dest_folder = self.choose_destination_folder()
        if not dest_folder: 
            logging.info(f"User canceled destination selection for '{pdf_path.name}'."); return

        final_destination_path = dest_folder / final_filename
        
        # --- STEP 4: Final Confirmation (No Edit button needed anymore) ---
        try:
            rel_folder = final_destination_path.parent.relative_to(self.SCRIPT_DIRECTORY)
            folder_display = f"...\\{rel_folder}"
        except ValueError:
            folder_display = str(final_destination_path.parent)
        confirm_text = (f"Destination Folder:\n{folder_display}\n\nFilename:\n{final_destination_path.name}")
        confirm_msg = CTkMessagebox(master=self.root, title="Confirm Move", message=confirm_text, icon="question", option_1="Cancel", option_2="Confirm")
        confirm_choice = confirm_msg.get()
        self._normalize_root()  # <-- normalize after modal
        
        if confirm_choice == "Cancel":
            logging.info(f"User canceled final move for '{pdf_path.name}'."); return
        
        # --- STEP 5: Move the file ---
        try:
            final_destination_path.parent.mkdir(parents=True, exist_ok=True)
            if final_destination_path.exists():
                final_destination_path = unique_path(final_destination_path)
            shutil.move(str(pdf_path), str(final_destination_path))
            logging.info(f"MOVED: '{pdf_path.name}' -> '{final_destination_path}'")
        except Exception as e:
            logging.error(f"Failed to move file: {e}")
        finally:
            self._normalize_root()  # extra safety

    def choose_destination_folder(self):
        if not self._ensure_settings():
            return None
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Choose Destination Folder",
            initialdir=str(self.SORTED_FOLDER),
            mustexist=False,
        )
        self._normalize_root()
        if not selected:
            return None
        dest = Path(selected)
        try:
            dest.relative_to(self.SORTED_FOLDER)
            return dest
        except ValueError:
            msg = CTkMessagebox(
                master=self.root,
                title="Outside Sorted Folder",
                message="This folder is outside your sorted papers root. Use it anyway?",
                icon="question",
                option_1="Cancel",
                option_2="Use Anyway",
            )
            choice = msg.get()
            self._normalize_root()
            return dest if choice == "Use Anyway" else None

    # (The rename flow can also be updated to use the new dialog if desired)
    def rename_papers_flow(self):
        if not self._ensure_settings():
            return
        if self.rename_batch_total:
            CTkMessagebox(master=self.root, title="Rename In Progress", message="Please wait for the current rename batch to finish.")
            return
        # Prompt user to select a folder or files
        choice = CTkMessagebox(master=self.root, title="Rename Papers", message="Would you like to select a folder or individual PDF files?", icon="question", option_1="Folder", option_2="Files", option_3="Cancel").get()
        if choice == "Cancel":
            logging.info("User canceled the rename papers operation.")
            return
        pdf_files = []
        if choice == "Folder":
            folder = filedialog.askdirectory(title="Select Folder Containing PDFs")
            if not folder:
                logging.info("No folder selected for renaming papers.")
                return
            folder_path = Path(folder)
            pdf_files = list(folder_path.glob('*.pdf'))
        elif choice == "Files":
            selected_files = filedialog.askopenfilenames(title="Select PDF files to rename", filetypes=[("PDF Documents", "*.pdf")])
            if not selected_files:
                logging.info("No files selected for renaming.")
                return
            pdf_files = [Path(f) for f in selected_files]
        if not pdf_files:
            logging.info("No PDF files found for renaming.")
            CTkMessagebox(master=self.root, title="No PDFs", message="No PDF files found.")
            return
        self.rename_batch_total = len(pdf_files)
        self.rename_batch_done = 0
        self.rename_batch_renamed = 0
        self.rename_batch_skipped = 0
        for pdf_path in pdf_files:
            self.rename_queue.put(pdf_path)
        logging.info(f"Queued {self.rename_batch_total} PDF(s) for AI naming.")
    def handle_rename_confirmation(self, pdf_path: Path, details: dict):
        details['author'] = cleanup_author_string(details.get('author', 'Unknown'))
        new_filename_ext = self._proposed_filename(details)
        title = details.get('title', 'Unknown Title')

        name_dialog = FilenameEditorDialog(self.root, original_name=pdf_path.name, ai_title=title, proposed_name=new_filename_ext)
        self.root.wait_window(name_dialog)
        self._normalize_root()
        final_filename = name_dialog.result
        if not final_filename:
            logging.info(f"User skipped '{pdf_path.name}' at name proposal stage.")
            self._finish_rename_item(skipped=True)
            return

        final_path = pdf_path.parent / final_filename
        if final_path.exists():
            CTkMessagebox(master=self.root, title="File Exists", message=f"A file named {final_filename} already exists. Skipping.")
            self._normalize_root()
            logging.info(f"Skipped renaming '{pdf_path.name}' because '{final_filename}' already exists.")
            self._finish_rename_item(skipped=True)
            return
        try:
            pdf_path.rename(final_path)
            logging.info(f"Renamed (AI Naming): {pdf_path.name} -> {final_filename}")
            self._finish_rename_item(renamed=True)
        except Exception as e:
            logging.error(f"Failed to rename {pdf_path.name}: {e}")
            CTkMessagebox(master=self.root, title="Rename Error", message=f"Failed to rename {pdf_path.name}: {e}")
            self._normalize_root()
            self._finish_rename_item(skipped=True)
    def _finish_rename_item(self, renamed=False, skipped=False):
        if renamed:
            self.rename_batch_renamed += 1
        if skipped:
            self.rename_batch_skipped += 1
        self.rename_batch_done += 1
        if self.rename_batch_total and self.rename_batch_done >= self.rename_batch_total:
            logging.info(f"Rename process finished. {self.rename_batch_renamed} renamed, {self.rename_batch_skipped} skipped, {self.rename_batch_total} total.")
            CTkMessagebox(master=self.root, title="Rename Complete", message=f"Renaming complete.\nRenamed: {self.rename_batch_renamed}\nSkipped: {self.rename_batch_skipped}\nTotal: {self.rename_batch_total}")
            self.rename_batch_total = 0
        
    def process_existing_files(self):
        if not self.settings.is_complete():
            return
        logging.info(f"Scanning for existing files in {self.WATCH_FOLDER}...")
        pdf_files = list(self.WATCH_FOLDER.glob('*.pdf'))
        if pdf_files:
            logging.info(f"Found {len(pdf_files)} PDF(s) to queue for processing.")
            for pdf_path in pdf_files:
                self._queue_sort_file(pdf_path)
        else:
            logging.info("No PDF files found; ToSort folder is empty.")

    def refresh_to_sort_folder(self):
        settings_were_ready = self.settings and self.settings.is_complete()
        if not self._ensure_settings():
            return
        if settings_were_ready:
            self.process_existing_files()

    def select_and_add_papers(self):
        if not self._ensure_settings():
            return
        selected_files = filedialog.askopenfilenames(title="Select PDF files to add", filetypes=[("PDF Documents", "*.pdf")])
        if not selected_files: logging.info("No files selected."); return
        added = 0
        for file_path_str in selected_files:
            source_path = Path(file_path_str)
            try:
                destination_path = unique_path(self.WATCH_FOLDER / source_path.name)
                shutil.copy2(source_path, destination_path); added += 1
            except Exception as e: logging.error(f"Failed to copy '{source_path.name}': {e}")
        logging.info(f"User added {added} paper(s) to the ToSort folder.")
        
    def handle_drop(self, event):
        if not self._ensure_settings():
            return
        file_paths_str = self.root.tk.splitlist(event.data); added_count = 0
        for path_str in file_paths_str:
            source_path = Path(path_str)
            if source_path.suffix.lower() == '.pdf':
                try:
                    destination_path = unique_path(self.WATCH_FOLDER / source_path.name)
                    shutil.copy2(source_path, destination_path); added_count += 1
                except Exception as e: logging.error(f"Failed to copy '{source_path.name}': {e}")
        if added_count > 0: logging.info(f"User dropped {added_count} paper(s) to the ToSort folder.")
        
    def open_watch_folder(self):
        if not self._ensure_settings():
            return
        webbrowser.open(self.WATCH_FOLDER)
    def open_sorted_folder(self):
        if not self._ensure_settings():
            return
        webbrowser.open(self.SORTED_FOLDER)
    def open_log_file(self):
        if not self.LOG_FILE.exists():
            logging.info("No log file exists yet.")
            return
        # Open the unified log file in the default text editor
        import os
        os.startfile(self.LOG_FILE)

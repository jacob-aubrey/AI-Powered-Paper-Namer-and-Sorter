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
from queue import Empty, Queue
from tkinter import filedialog
from xml.sax.saxutils import escape

from tkinterdnd2 import DND_FILES, TkinterDnD
import customtkinter as ctk
from CTkMessagebox import CTkMessagebox
from PIL import Image
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from core_logic import (
    DEFAULT_CUSTOM_FILENAME_TEMPLATE,
    build_proposed_filename,
    get_basic_document_details,
    get_document_details,
    unique_path,
    validate_document_filename,
    validate_filename_template,
)
from document_types import SUPPORTED_DOCUMENT_EXTENSIONS, is_processable_document
from settings import AppSettings, SettingsManager


DOCUMENT_FILE_TYPES = [
    ("Supported documents", "*.pdf *.docx"),
    ("PDF documents", "*.pdf"),
    ("Word documents", "*.docx"),
]
FILENAME_STYLE_LABELS = {
    "smart": "Smart (recommended)",
    "journal_compact": "Compact journal citation",
    "journal_detailed": "Detailed journal citation",
    "author_year_title": "Author – year – title",
    "title_year_type": "Title – year – type",
    "custom": "Custom template",
}
FILENAME_STYLE_IDS = {label: identifier for identifier, label in FILENAME_STYLE_LABELS.items()}

# --- NEW: DnD-enabled CTk root to keep CTk overlays/alpha in sync with main window ---
class DnDCTk(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self, *args, **kwargs):
        ctk.CTk.__init__(self, *args, **kwargs)
        # Initialize TkDnD on this CTk window
        self.TkdndVersion = TkinterDnD._require(self)
        TkinterDnD.DnDWrapper.__init__(self)

class TextboxRedirector:
    MOVED_PATH_RE = re.compile(r"MOVED: .* -> (.+)$")

    def __init__(self, textbox: ctk.CTkTextbox, resolve_document_path=None, on_link_error=None):
        self.textbox = textbox
        self.link_count = 0
        self._link_callbacks = {}
        self._resolve_document_path_callback = resolve_document_path
        self._on_link_error = on_link_error
        self.textbox.tag_config("log_link", foreground="#4da3ff", underline=True)
        # Bind on the Text widget rather than on each tag. In a disabled Tk Text
        # widget, tag click bindings can depend on a prior pointer-motion event.
        # This hit-tests the actual click, so links work on the first click while
        # the log remains read-only.
        self.textbox.bind("<Button-1>", self._on_textbox_click, add="+")
        self.textbox.bind("<Motion>", self._on_textbox_motion, add="+")
        # A disabled Tk Text still delivers mouse/tag bindings, so the log stays
        # clickable while users cannot type into or delete from it.
        self._set_read_only()

    def _set_read_only(self):
        self.textbox.configure(state="disabled")

    def _set_editable(self):
        self.textbox.configure(state="normal")

    def write(self, text):
        self.textbox.after(0, self._write_on_main_thread, text)

    def _write_on_main_thread(self, text):
        self._set_editable()
        try:
            for line in text.splitlines(keepends=True):
                has_newline = line.endswith(("\n", "\r"))
                line_text = line.rstrip("\r\n")
                self.textbox.insert("end", line_text)
                self._append_move_links(line_text)
                if has_newline:
                    self.textbox.insert("end", "\n")
            self.textbox.see("end")
        finally:
            self._set_read_only()

    def clear_display(self):
        """Clear only the on-screen widget; the persistent log file is untouched."""
        self._set_editable()
        try:
            self.textbox.delete("1.0", "end")
            self._link_callbacks.clear()
            self.link_count = 0
        finally:
            self._set_read_only()

    def _append_move_links(self, line_text: str):
        match = self.MOVED_PATH_RE.search(line_text)
        if not match:
            return
        paper_path = Path(match.group(1).strip().strip("'"))
        self.textbox.insert("end", "  ")
        self._append_link("View Location", lambda path=paper_path: self._open_location(path))
        self.textbox.insert("end", "  ")
        self._append_link("View Document", lambda path=paper_path: self._open_paper(path))

    def _append_link(self, label: str, callback):
        self.link_count += 1
        tag = f"log_link_{self.link_count}"
        self._link_callbacks[tag] = callback
        self.textbox.insert("end", label, ("log_link", tag))

    def _callback_at_event(self, event):
        """Return the link callback beneath a mouse event, if there is one."""
        try:
            index = self.textbox.index(f"@{event.x},{event.y}")
            for tag in self.textbox.tag_names(index):
                callback = self._link_callbacks.get(tag)
                if callback:
                    return callback
        except Exception:
            # A malformed or late Tk event should never make the log unusable.
            return None
        return None

    def _on_textbox_click(self, event):
        callback = self._callback_at_event(event)
        if callback:
            callback()
            # Do not let the disabled Text widget also handle this link click.
            return "break"
        return None

    def _on_textbox_motion(self, event):
        self.textbox.configure(cursor="hand2" if self._callback_at_event(event) else "")

    def _resolve_available_document_path(self, paper_path: Path) -> Path | None:
        if paper_path.is_file():
            return paper_path
        if not self._resolve_document_path_callback:
            return None
        try:
            resolved_path = self._resolve_document_path_callback(paper_path)
        except Exception:
            return None
        if resolved_path:
            resolved_path = Path(resolved_path)
            if resolved_path.is_file():
                logging.info("Resolved a moved document from an older log link: %s", paper_path.name)
                return resolved_path
        return None

    def _report_link_error(self, action: str, paper_path: Path, error: Exception):
        logging.error("Failed to %s for '%s': %s", action, paper_path, error)
        if self._on_link_error:
            self._on_link_error(action, paper_path, error)

    def _open_location(self, paper_path: Path):
        resolved_path = self._resolve_available_document_path(paper_path)
        folder_path = resolved_path.parent if resolved_path else paper_path.parent
        if not folder_path.is_dir():
            self._report_link_error(
                "open location",
                paper_path,
                FileNotFoundError("The saved folder no longer exists."),
            )
            return
        try:
            if os.name == "nt":
                # Explorer reliably opens a folder and highlights the paper when
                # it still exists. If the paper was later removed, the folder is
                # still useful and is opened normally.
                explorer_arguments = ["explorer.exe"]
                explorer_arguments.append(f"/select,{resolved_path}" if resolved_path else str(folder_path))
                subprocess.Popen(explorer_arguments, shell=False)
            else:
                webbrowser.open(folder_path.resolve().as_uri())
        except (OSError, subprocess.SubprocessError) as error:
            self._report_link_error("open location", paper_path, error)

    def _open_paper(self, paper_path: Path):
        resolved_path = self._resolve_available_document_path(paper_path)
        if not resolved_path:
            self._report_link_error(
                "open document",
                paper_path,
                FileNotFoundError("The document was moved, renamed, or deleted."),
            )
            return
        try:
            if os.name == "nt":
                os.startfile(str(resolved_path), "open")
            else:
                webbrowser.open(resolved_path.resolve().as_uri())
        except OSError as error:
            self._report_link_error("open document", paper_path, error)

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


def center_window_over_master(window, master, *, min_width=0, min_height=0):
    """Size a dialog to its requested content and center it over the main window."""
    try:
        master.update_idletasks()
        window.update_idletasks()

        # Use Tk's virtual desktop rather than only the primary screen. A main
        # window on a monitor left/above the primary display has negative screen
        # coordinates, so primary-screen clamping would misplace its dialogs.
        virtual_x = window.winfo_vrootx()
        virtual_y = window.winfo_vrooty()
        virtual_width = window.winfo_vrootwidth() or window.winfo_screenwidth()
        virtual_height = window.winfo_vrootheight() or window.winfo_screenheight()
        width = min(max(min_width, window.winfo_width(), window.winfo_reqwidth()), max(320, virtual_width - 32))
        height = min(max(min_height, window.winfo_height(), window.winfo_reqheight()), max(240, virtual_height - 64))
        master_width = max(master.winfo_width(), master.winfo_reqwidth())
        master_height = max(master.winfo_height(), master.winfo_reqheight())
        x = max(virtual_x + 16, min(master.winfo_rootx() + (master_width - width) // 2, virtual_x + virtual_width - width - 16))
        y = max(virtual_y + 16, min(master.winfo_rooty() + (master_height - height) // 2, virtual_y + virtual_height - height - 48))

        window.geometry(f"{width}x{height}+{x}+{y}")
        window.lift()
        window.focus_force()
    except Exception:
        # A dialog can be destroyed before its queued placement callback runs.
        pass


def create_centered_messagebox(master, *, center_on=None, **kwargs):
    """Create a CTk message box centered over the main application window."""
    messagebox = CTkMessagebox(master=master, **kwargs)
    center_target = center_on or master
    messagebox.after_idle(lambda: center_window_over_master(messagebox, center_target))
    return messagebox


def paths_overlap(first: Path, second: Path) -> bool:
    """Return whether two directory paths are the same or one contains the other."""
    try:
        first_resolved = first.expanduser().resolve()
        second_resolved = second.expanduser().resolve()
    except OSError:
        first_resolved = first.expanduser()
        second_resolved = second.expanduser()
    return (
        first_resolved == second_resolved
        or first_resolved in second_resolved.parents
        or second_resolved in first_resolved.parents
    )


def is_within_folder(candidate: Path, folder: Path) -> bool:
    """Return whether *candidate* is the folder or one of its descendants."""

    try:
        candidate.expanduser().resolve().relative_to(folder.expanduser().resolve())
        return True
    except (OSError, ValueError):
        return False

# --- NEW: Custom Dialog for Editing Filenames ---
class FilenameEditorDialog(ctk.CTkToplevel):
    def __init__(self, master, original_name: str, details: dict, proposed_name: str):
        super().__init__(master)
        self.main_window = master
        self.original_suffix = Path(original_name).suffix.lower()
        self.title("Propose & Edit Filename")
        self.geometry("720x460")
        self.minsize(640, 380)
        self.resizable(True, True)
        self.transient(master)
        self.grab_set()

        self.result = None  # This will store the final filename or None if skipped

        # Layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1, minsize=190)
        self.grid_rowconfigure(1, weight=0)
        self.grid_rowconfigure(2, weight=0)

        # Info Frame
        info_frame = ctk.CTkScrollableFrame(self, fg_color="transparent", height=210)
        info_frame.grid(row=0, column=0, padx=15, pady=15, sticky="ew")
        info_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(info_frame, text="Original File:", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(info_frame, text=original_name, wraplength=450).grid(row=0, column=1, sticky="w", padx=5)
        
        ctk.CTkLabel(info_frame, text="Detected Title:", font=ctk.CTkFont(weight="bold")).grid(row=1, column=0, sticky="nw", pady=(5, 0))
        ctk.CTkLabel(info_frame, text=details.get("title", "Unknown Title"), wraplength=510, justify="left").grid(row=1, column=1, sticky="w", padx=5, pady=(5, 0))
        ctk.CTkLabel(info_frame, text="Document Type:", font=ctk.CTkFont(weight="bold")).grid(row=2, column=0, sticky="nw", pady=(5, 0))
        confidence = details.get("confidence", 0.0)
        try:
            confidence_text = f"{float(confidence):.0%} confidence"
        except (TypeError, ValueError):
            confidence_text = "confidence unknown"
        ctk.CTkLabel(
            info_frame,
            text=f"{details.get('document_type_label', 'Unknown Document Type')} ({confidence_text})",
            wraplength=510,
            justify="left",
        ).grid(row=2, column=1, sticky="w", padx=5, pady=(5, 0))
        warnings = details.get("warnings") or []
        if details.get("needs_review") or warnings:
            review_message = "Review recommended."
            if warnings:
                review_message = f"Review recommended: {warnings[0]}"
            ctk.CTkLabel(
                info_frame,
                text=review_message,
                text_color="#f6c344",
                wraplength=590,
                justify="left",
            ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

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
        add_tooltip(self.skip_button, "Leave this document where it is and move on without sorting it.")
        self.continue_button = ctk.CTkButton(button_frame, text="Continue", command=self._on_continue)
        self.continue_button.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        add_tooltip(self.continue_button, "Accept the filename shown here and continue to folder selection.")

        self.after_idle(lambda: center_window_over_master(self, self.main_window, min_width=640, min_height=380))
    def _messagebox(self, **kwargs):
        return create_centered_messagebox(self, center_on=self.main_window, **kwargs)


    def _on_continue(self):
        try:
            self.result = validate_document_filename(self.filename_entry.get(), self.original_suffix)
        except ValueError as e:
            self._messagebox(title="Invalid Filename", message=str(e), icon="warning")
            return
        self.destroy()

    def _on_skip(self):
        self.result = None
        self.destroy()

class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master, settings: AppSettings):
        super().__init__(master)
        self.main_window = master
        self.title("Settings")
        self.geometry("820x650")
        self.minsize(700, 560)
        self.resizable(True, True)
        self.transient(master)
        self.grab_set()
        self.result = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        frame = ctk.CTkScrollableFrame(self)
        frame.grid(row=0, column=0, padx=16, pady=16, sticky="nsew")
        frame.grid_columnconfigure(1, weight=1)

        self.watch_var = ctk.StringVar(value=str(settings.watch_folder or ""))
        self.sorted_var = ctk.StringVar(value=str(settings.sorted_folder or ""))
        self.api_key_var = ctk.StringVar(value=settings.api_key or "")
        self.naming_mode_var = ctk.StringVar(value=settings.clean_naming_mode())
        filename_format = settings.clean_filename_format()
        self.filename_format_var = ctk.StringVar(value=FILENAME_STYLE_LABELS[filename_format])
        self.custom_filename_template_var = ctk.StringVar(
            value=settings.custom_filename_template or DEFAULT_CUSTOM_FILENAME_TEMPLATE
        )
        self.watch_launch_var = ctk.BooleanVar(value=settings.watch_and_launch_enabled)
        self.allow_cloud_ai_word_var = ctk.BooleanVar(value=settings.allow_cloud_ai_for_word_documents)

        ctk.CTkLabel(frame, text="To Sort folder").grid(row=0, column=0, padx=10, pady=(14, 6), sticky="w")
        ctk.CTkEntry(frame, textvariable=self.watch_var).grid(row=0, column=1, padx=10, pady=(14, 6), sticky="ew")
        self.watch_browse_button = ctk.CTkButton(frame, text="Browse", width=90, command=self._browse_watch)
        self.watch_browse_button.grid(row=0, column=2, padx=10, pady=(14, 6))
        add_tooltip(self.watch_browse_button, "Choose the incoming folder the app watches and copies supported PDFs and Word documents into.")

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

        ctk.CTkLabel(frame, text="Filename style").grid(row=4, column=0, padx=10, pady=(14, 6), sticky="w")
        self.filename_style_menu = ctk.CTkOptionMenu(
            frame,
            variable=self.filename_format_var,
            values=list(FILENAME_STYLE_LABELS.values()),
            command=lambda _value: self._update_filename_style_controls(),
        )
        self.filename_style_menu.grid(row=4, column=1, padx=10, pady=(14, 6), sticky="w")
        add_tooltip(
            self.filename_style_menu,
            "Choose how verified metadata is arranged in the proposed filename. This does not change AI/privacy mode.",
        )

        ctk.CTkLabel(frame, text="Custom template").grid(row=5, column=0, padx=10, pady=6, sticky="nw")
        self.custom_filename_template_entry = ctk.CTkEntry(
            frame,
            textvariable=self.custom_filename_template_var,
        )
        self.custom_filename_template_entry.grid(row=5, column=1, columnspan=2, padx=10, pady=6, sticky="ew")
        add_tooltip(
            self.custom_filename_template_entry,
            "Available only for Custom template. The original PDF or DOCX extension is always retained automatically.",
        )
        self.filename_style_preview = ctk.CTkLabel(frame, text="", justify="left", wraplength=650)
        self.filename_style_preview.grid(row=6, column=0, columnspan=3, padx=10, pady=(2, 10), sticky="w")
        ctk.CTkLabel(
            frame,
            text=(
                "Custom tokens: {author_last}, {author_last_et_al}, {first_author_full}, {journal}, "
                "{journal_abbreviation}, {venue_or_publisher}, {volume}, {issue}, {year}, {title}, {document_type}. "
                "Unavailable fields are omitted; the original .pdf or .docx extension is always retained."
            ),
            justify="left",
            wraplength=650,
            text_color=("gray35", "gray70"),
        ).grid(row=7, column=0, columnspan=3, padx=10, pady=(0, 10), sticky="w")

        self.word_ai_check = ctk.CTkCheckBox(
            frame,
            text="Allow AI analysis of Word documents (.docx)",
            variable=self.allow_cloud_ai_word_var,
        )
        self.word_ai_check.grid(row=8, column=1, padx=10, pady=(8, 4), sticky="w")
        add_tooltip(
            self.word_ai_check,
            "Off by default. Enable only if you want the app to send extracted Word-document text to Gemini for AI naming.",
        )

        self.watch_launch_check = ctk.CTkCheckBox(
            frame,
            text="Enable Watch & Launch at Windows login/unlock",
            variable=self.watch_launch_var,
            command=self._on_watch_launch_toggle,
        )
        self.watch_launch_check.grid(row=9, column=1, padx=10, pady=(4, 12), sticky="w")
        add_tooltip(self.watch_launch_check, "Run the lightweight watcher in the background so supported files added later can open the sorter app.")

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=1, column=0, padx=16, pady=(0, 16), sticky="ew")
        button_frame.grid_columnconfigure((0, 1), weight=1)
        self.cancel_button = ctk.CTkButton(button_frame, text="Cancel", command=self._cancel)
        self.cancel_button.grid(row=0, column=0, padx=6, sticky="ew")
        add_tooltip(self.cancel_button, "Close Settings without saving folder changes.")
        self.save_button = ctk.CTkButton(button_frame, text="Save", command=self._save)
        self.save_button.grid(row=0, column=1, padx=6, sticky="ew")
        add_tooltip(self.save_button, "Save these folder choices for this PC and restart folder watching.")
        self.custom_filename_template_var.trace_add("write", lambda *_args: self._update_filename_style_controls())
        self._update_filename_style_controls()
        self.after_idle(lambda: center_window_over_master(self, self.main_window, min_width=700, min_height=560))

    def _messagebox(self, **kwargs):
        return create_centered_messagebox(self, center_on=self.main_window, **kwargs)

    def _selected_filename_format(self):
        return FILENAME_STYLE_IDS.get(self.filename_format_var.get(), "smart")

    def _update_filename_style_controls(self):
        filename_format = self._selected_filename_format()
        is_custom = filename_format == "custom"
        self.custom_filename_template_entry.configure(state="normal" if is_custom else "disabled")
        sample_details = {
            "title": "A Practical Example Study",
            "primary_creator": "Jane Doe",
            "author": "Doe",
            "year": "2024",
            "document_type": "journal_article",
            "venue_or_publisher": "Journal of Example Research",
            "journal": "Journal of Example Research",
            "journal_abbreviation": "J Example Res",
            "volume": "12",
            "issue": "3",
            "is_multiple_creators": True,
        }
        template = self.custom_filename_template_var.get().strip()
        if is_custom:
            try:
                validate_filename_template(template)
            except ValueError as exc:
                self.filename_style_preview.configure(text=f"Custom template needs attention: {exc}")
                return
        preview = build_proposed_filename(
            sample_details,
            ".pdf",
            filename_format=filename_format,
            custom_template=template,
        )
        self.filename_style_preview.configure(text=f"Example: {preview}")

    def _browse_watch(self):
        initial = self.watch_var.get() or str(Path.home())
        selected = filedialog.askdirectory(parent=self.main_window, title="Choose To Sort folder", initialdir=initial)
        if selected:
            self.watch_var.set(selected)

    def _browse_sorted(self):
        initial = self.sorted_var.get() or str(Path.home())
        selected = filedialog.askdirectory(parent=self.main_window, title="Choose Sorted papers root", initialdir=initial)
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
        filename_format = self._selected_filename_format()
        custom_template = self.custom_filename_template_var.get().strip()
        if filename_format == "custom":
            try:
                custom_template = validate_filename_template(custom_template)
            except ValueError as exc:
                self._messagebox(title="Invalid Custom Template", message=str(exc), icon="warning")
                return
        if self.watch_launch_var.get() and not watch:
            self._browse_watch()
            watch = self.watch_var.get().strip()
        if not watch or not sorted_folder:
            self._messagebox(title="Missing Folders", message="Choose both folders before saving.", icon="warning")
            return
        try:
            # Persist stable absolute locations so the GUI and its optional
            # background helper cannot interpret a relative folder differently.
            watch_path = Path(watch).expanduser().resolve()
            sorted_path = Path(sorted_folder).expanduser().resolve()
        except OSError as exc:
            self._messagebox(
                title="Invalid Folder",
                message=f"Could not resolve the selected folder paths:\n{exc}",
                icon="warning",
            )
            return
        if paths_overlap(watch_path, sorted_path):
            self._messagebox(
                title="Folders Overlap",
                message="The To Sort folder and Sorted papers root must be separate folders. Neither can contain the other.",
                icon="warning",
            )
            return
        self.result = AppSettings(
            watch_folder=watch_path,
            sorted_folder=sorted_path,
            api_key=self.api_key_var.get().strip(),
            naming_mode=self.naming_mode_var.get(),
            filename_format=filename_format,
            custom_filename_template=custom_template,
            watch_and_launch_enabled=self.watch_launch_var.get(),
            allow_cloud_ai_for_word_documents=self.allow_cloud_ai_word_var.get(),
        )
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

    def _show_api_help(self):
        webbrowser.open("https://aistudio.google.com/app/apikey")
        self._messagebox(
            title="Gemini API Key",
            message=(
                "AI naming is optional.\n\n"
                "1. Create a Gemini API key in Google AI Studio.\n"
                "2. Paste it into the Gemini API key field.\n"
                "3. Set Naming mode to Automatic or AI.\n\n"
                "Basic naming stays on this PC. AI mode sends an extracted text excerpt to Gemini. "
                "Word-document AI analysis stays off unless you check its separate opt-in.\n\n"
                "Without a key, Basic naming still works."
            ),
            icon="info",
        )

class App:
    WATCH_LAUNCH_TASK_NAME = "AI Paper Sorter Watch and Launch"
    WATCH_LAUNCH_STARTUP_FILE = "AI Paper Sorter Watch and Launch.cmd"

    def _asset_path(self, *parts):
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            return Path(sys._MEIPASS) / "assets" / Path(*parts)
        if getattr(sys, 'frozen', False):
            return self.SCRIPT_DIRECTORY / "assets" / Path(*parts)
        return self.SCRIPT_DIRECTORY.parent / "assets" / Path(*parts)

    def _toolbar_icon(self, filename):
        image = Image.open(self._asset_path("icons", filename))
        return ctk.CTkImage(light_image=image, dark_image=image, size=(22, 22))

    def _watch_launcher_command(self):
        if getattr(sys, "frozen", False):
            exe_path = Path(sys.executable)
            return str(exe_path), "--watch", str(exe_path.parent)

        pythonw = Path(sys.executable).with_name("pythonw.exe")
        python_exe = pythonw if pythonw.exists() else Path(sys.executable)
        script = self.SCRIPT_DIRECTORY / "main.py"
        return str(python_exe), f'"{script}" --watch', str(self.SCRIPT_DIRECTORY)

    def _watch_launcher_popen_args(self):
        command, arguments, _working_directory = self._watch_launcher_command()
        if arguments == "--watch":
            return [command, "--watch"]
        if arguments.endswith(" --watch") and arguments.startswith('"'):
            script_path = arguments[1:arguments.rfind('"')]
            return [command, script_path, "--watch"]
        if arguments:
            return [command, *arguments.split()]
        return [command]

    def _watch_launcher_process_marker(self):
        """Return the exact executable or source entry point used by watcher mode."""
        if getattr(sys, "frozen", False):
            return str(Path(sys.executable).resolve())
        return str((self.SCRIPT_DIRECTORY / "main.py").resolve())

    def _current_windows_user(self):
        domain = os.environ.get("USERDOMAIN", "").strip()
        username = os.environ.get("USERNAME", "").strip()
        if domain and username:
            return f"{domain}\\{username}"
        return os.getlogin()

    def _startup_folder(self):
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return None
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"

    def _startup_file_path(self):
        startup_folder = self._startup_folder()
        if not startup_folder:
            return None
        return startup_folder / self.WATCH_LAUNCH_STARTUP_FILE

    def _watch_launch_task_xml(self):
        command, arguments, working_directory = self._watch_launcher_command()
        arguments_xml = f"<Arguments>{escape(arguments)}</Arguments>" if arguments else ""
        user_id = self._current_windows_user()
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
      <UserId>{escape(user_id)}</UserId>
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

    def _install_watch_launch_startup_file(self):
        startup_file = self._startup_file_path()
        if not startup_file:
            raise RuntimeError("Could not find the Windows Startup folder.")
        command, arguments, working_directory = self._watch_launcher_command()
        startup_file.parent.mkdir(parents=True, exist_ok=True)
        startup_file.write_text(
            "\n".join(
                [
                    "@echo off",
                    f'cd /d "{working_directory}"',
                    f'start "" "{command}" {arguments}'.rstrip(),
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self._start_watch_launcher_now()

    def _remove_watch_launch_startup_file(self):
        startup_file = self._startup_file_path()
        if startup_file:
            startup_file.unlink(missing_ok=True)

    def _start_watch_launcher_now(self):
        _command, _arguments, working_directory = self._watch_launcher_command()
        subprocess.Popen(
            self._watch_launcher_popen_args(),
            cwd=working_directory,
            creationflags=0x08000000,
        )

    def _remove_watch_launch_task(self):
        self._run_hidden(["schtasks", "/Delete", "/TN", self.WATCH_LAUNCH_TASK_NAME, "/F"])
        self._remove_watch_launch_startup_file()
        self._stop_watch_launcher_process()

    def _stop_watch_launcher_process(self):
        marker = self._watch_launcher_process_marker().replace("'", "''")
        powershell = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -and $_.CommandLine -like '*--watch*' -and "
            f"$_.CommandLine -like '*{marker}*' }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        self._run_hidden(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", powershell])

    def _sync_watch_launch_setting(self):
        if self.settings.watch_and_launch_enabled:
            try:
                # Restart the helper so a changed To Sort folder takes effect now.
                self._stop_watch_launcher_process()
                self._remove_watch_launch_startup_file()
                self._install_watch_launch_task()
                logging.info("Watch and Launch enabled for Windows login/unlock.")
            except Exception as task_error:
                self._install_watch_launch_startup_file()
                logging.warning(f"Could not create the login/unlock scheduled task. Enabled login startup fallback instead: {task_error}")
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
        self._folder_access_error = None

        # Unified log for both sorting and naming
        if self.settings.is_complete():
            try:
                self.WATCH_FOLDER.mkdir(parents=True, exist_ok=True)
                self.SORTED_FOLDER.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                # A disconnected drive or changed permission should not prevent
                # the UI from opening so the folders can be repaired in Settings.
                self._folder_access_error = str(exc)
        self.LOG_FILE = self._default_log_file()
        self._prepare_log_file()
        
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
            text="Name Documents",
            image=self.toolbar_icons["name"],
            compound="left",
            width=150,
            command=self.rename_papers_flow,
        )
        self.btn_name_papers.pack(side="left", padx=4)
        add_tooltip(self.btn_name_papers, "Rename PDFs or Word documents in place without moving them into a sorted folder.")
        self.btn_refresh = ctk.CTkButton(
            self.toolbar_buttons,
            text="Refresh",
            image=self.toolbar_icons["refresh"],
            compound="left",
            width=104,
            command=self.refresh_to_sort_folder,
        )
        self.btn_refresh.pack(side="left", padx=4)
        add_tooltip(self.btn_refresh, "Rescan the To Sort folder and queue supported documents that are waiting there.")
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
        self.btn_clear_log_display = ctk.CTkButton(
            self.toolbar_buttons,
            text="Clear Display",
            width=118,
            command=self.clear_log_display,
        )
        self.btn_clear_log_display.pack(side="left", padx=4)
        add_tooltip(
            self.btn_clear_log_display,
            "Clear only the on-screen log display. The paper_sorter_log.txt file is not changed.",
        )
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
        self.drag_label = ctk.CTkLabel(self.browse_text_frame, text="To sort documents, drag them here, or ", font=ctk.CTkFont(size=14)); self.drag_label.pack(side="left")
        self.browse_label = ctk.CTkLabel(self.browse_text_frame, text="browse", font=ctk.CTkFont(size=14, underline=True), text_color=("blue", "cyan"), cursor="hand2")
        self.browse_label.pack(side="left"); self.browse_label.bind("<Button-1>", lambda e: self.select_and_add_papers())
        add_tooltip(self.browse_label, "Select one or more PDF or Word (.docx) files to copy into the To Sort folder.")
        self.after_browse_label = ctk.CTkLabel(self.browse_text_frame, text=" your computer...", font=ctk.CTkFont(size=14)); self.after_browse_label.pack(side="left")
        
        self.bottom_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.bottom_frame.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="nsew")
        self.bottom_frame.grid_columnconfigure(0, weight=1); self.bottom_frame.grid_rowconfigure(0, weight=1)
        
        self.log_textbox = ctk.CTkTextbox(self.bottom_frame, activate_scrollbars=True); self.log_textbox.grid(row=0, column=0, padx=0, pady=(0, 10), sticky="nsew")
        self.redirector = TextboxRedirector(
            self.log_textbox,
            resolve_document_path=self._resolve_logged_document_path,
            on_link_error=self._show_log_link_error,
        )
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(message)s',
            handlers=[logging.FileHandler(self.LOG_FILE, encoding='utf-8'), logging.StreamHandler(self.redirector)],
            force=True,
        )
        self.file_queue = Queue(); self.rename_queue = Queue(); self.gui_queue = Queue()
        self.queue_lock = threading.Lock(); self.queued_sort_paths = set()
        self.snoozed_sort_signatures = {}
        self.ignored_watch_event_until = {}
        self.gui_modal_depth = 0
        self.gui_queue_after_id = None
        self.app_started = False
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

    def _messagebox(self, **kwargs):
        return create_centered_messagebox(self.root, center_on=self.root, **kwargs)

    def _run_modal(self, operation):
        """Run a modal action while keeping the queue pump from opening dialogs."""
        self.gui_modal_depth = getattr(self, "gui_modal_depth", 0) + 1
        try:
            return operation()
        finally:
            self.gui_modal_depth = max(0, self.gui_modal_depth - 1)

    def _wait_for_dialog(self, dialog):
        self._run_modal(lambda: self.root.wait_window(dialog))
        self._normalize_root()

    def _modal_is_active(self):
        if getattr(self, "gui_modal_depth", 0):
            return True
        try:
            return self.root.grab_current() is not None
        except Exception:
            return False

    def _schedule_gui_queue(self, delay=200):
        if getattr(self, "gui_queue_after_id", None) is None:
            self.gui_queue_after_id = self.root.after(delay, self.process_gui_queue)

    def _ensure_settings(self):
        if self.settings and self.settings.is_complete() and not self._folder_access_error:
            return True
        return self.open_settings()

    def _default_log_file(self):
        if self.settings and self.settings.sorted_folder and not getattr(self, "_folder_access_error", None):
            return self.settings.sorted_folder / 'paper_sorter_log.txt'
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "AI Paper Sorter" / "paper_sorter_log.txt"
        return Path(tempfile.gettempdir()) / "AI Paper Sorter" / "paper_sorter_log.txt"

    def _resolve_logged_document_path(self, logged_path: Path) -> Path | None:
        """Find one safe replacement for a stale historical log target.

        A log must keep the path that was true at the time of the move. If the
        user later files that document elsewhere, a link can still be useful when
        exactly one same-named document exists below the configured library root.
        Ambiguous matches are deliberately not guessed.
        """

        if logged_path.is_file():
            return logged_path
        sorted_root = getattr(self, "SORTED_FOLDER", None)
        if not sorted_root:
            return None
        sorted_root = Path(sorted_root)
        if not sorted_root.is_dir() or not logged_path.name:
            return None
        matches = []
        try:
            for candidate in sorted_root.rglob(logged_path.name):
                if candidate.is_file():
                    matches.append(candidate)
                    if len(matches) > 1:
                        return None
        except OSError:
            return None
        return matches[0] if len(matches) == 1 else None

    def _show_log_link_error(self, action: str, paper_path: Path, error: Exception):
        """Explain a stale/broken historical link without leaving the user in the log."""

        if isinstance(error, FileNotFoundError):
            message = (
                "This log entry remembers where the document was when it was sorted, "
                "but that file or folder is no longer there.\n\n"
                "The app looked in your Sorted folder for one clear current match and "
                "could not safely identify one. The document may have been moved, renamed, "
                "or deleted.\n\n"
                f"Document: {paper_path.name}\n\n"
                "Use the Sorted button to browse for it manually."
            )
            title = "This Log Link Is Out of Date"
        else:
            subject = "document" if action == "open document" else "folder"
            message = (
                f"Windows could not open this {subject}.\n\n"
                f"Document: {paper_path.name}\n\n"
                f"Details: {error}"
            )
            title = "Could Not Open Link"
        self._messagebox(title=title, message=message, icon="warning")

    def _prepare_log_file(self):
        """Ensure the file logger has a writable directory before it starts."""
        try:
            self.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.LOG_FILE = Path(tempfile.gettempdir()) / "AI Paper Sorter" / "paper_sorter_log.txt"
            self.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    def open_settings(self):
        dialog = SettingsDialog(self.root, self.settings or AppSettings())
        self._wait_for_dialog(dialog)
        if not dialog.result:
            return False
        try:
            dialog.result.watch_folder.mkdir(parents=True, exist_ok=True)
            dialog.result.sorted_folder.mkdir(parents=True, exist_ok=True)
            self.settings_manager.save(dialog.result)
        except Exception as e:
            self._messagebox(title="Settings Error", message=f"Could not save settings:\n{e}", icon="error")
            return False
        self.settings = dialog.result
        self._folder_access_error = None
        self.WATCH_FOLDER = self.settings.watch_folder
        self.SORTED_FOLDER = self.settings.sorted_folder
        self.LOG_FILE = self._default_log_file()
        self._prepare_log_file()
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
            self._messagebox(
                title="Watch and Launch Error",
                message=f"Settings were saved, but Watch and Launch could not be updated:\n{e}",
                icon="warning",
            )
        if getattr(self, "app_started", False) and hasattr(self, "observer"):
            try:
                self.observer.stop()
                self.observer.join(timeout=3)
            except Exception:
                pass
        if getattr(self, "app_started", False):
            self.start_watcher()
            self.process_existing_files()
        return True

    def _replace_log_file_handler(self):
        self._prepare_log_file()
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
        self.app_started = True
        if not self._active_api_key():
            logging.info("Gemini API key is not set. Basic naming is available. To enable AI naming, open Settings, click Get Key, and paste your key.")
        self.worker_thread = threading.Thread(target=self.processing_loop, daemon=True); self.worker_thread.start()
        self.rename_worker_thread = threading.Thread(target=self.rename_processing_loop, daemon=True); self.rename_worker_thread.start()
        if self.settings.is_complete() and not self._folder_access_error:
            self.start_watcher()
            self.process_existing_files()
        else:
            if self._folder_access_error:
                logging.warning("Saved folders are unavailable: %s", self._folder_access_error)
                self.root.after(
                    0,
                    lambda: self._messagebox(
                        title="Saved Folder Unavailable",
                        message=(
                            "The saved To Sort or Sorted papers folder cannot be opened.\n\n"
                            f"{self._folder_access_error}\n\n"
                            "Open Settings and choose accessible folders."
                        ),
                        icon="warning",
                    ),
                )
            else:
                logging.info("Folder settings are not configured yet. Choose Settings or add a document to continue.")
            self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.process_gui_queue()
    def on_closing(self):
        logging.info("--- Shutting down... ---")
        if hasattr(self, "observer"):
            try: self.observer.stop(); self.observer.join(timeout=3)
            except Exception: pass
        self.root.destroy()
    def start_watcher(self):
        if not self.settings.is_complete() or self._folder_access_error:
            return
        if hasattr(self, "observer") and self.observer.is_alive():
            return
        event_handler = self.create_watchdog_handler(); self.observer = Observer()
        self.observer.schedule(event_handler, str(self.WATCH_FOLDER), recursive=False); self.observer.start()
        logging.info(f"Watching for new files in: {self.WATCH_FOLDER}"); self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def create_watchdog_handler(self):
        try:
            watched_folder = self.WATCH_FOLDER.resolve()
        except OSError:
            watched_folder = self.WATCH_FOLDER

        class MyHandler(FileSystemEventHandler):
            def __init__(self, enqueue):
                self.enqueue = enqueue

            def _maybe_enqueue(self, path):
                document_path = Path(path)
                if not is_processable_document(document_path):
                    return
                try:
                    is_direct_child = document_path.resolve().parent == watched_folder
                except OSError:
                    is_direct_child = document_path.parent == watched_folder
                # A move *out* of To Sort reports an event to this observer too.
                # Never feed its external destination back into the sort queue.
                if is_direct_child:
                    self.enqueue(document_path)

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

    def _sort_queue_key(self, document_path: Path):
        try:
            return str(document_path.resolve()).lower()
        except Exception:
            return str(document_path).lower()

    @staticmethod
    def _file_signature(document_path: Path):
        try:
            stat = document_path.stat()
            return stat.st_size, stat.st_mtime_ns
        except OSError:
            return None

    def _snooze_sort_file(self, document_path: Path):
        """Do not re-prompt an unchanged file after the user skips or cancels it."""
        key = self._sort_queue_key(document_path)
        with self.queue_lock:
            self.snoozed_sort_signatures[key] = self._file_signature(document_path)

    def _suppress_watch_events_for(self, document_path: Path, seconds=5.0):
        """Ignore short-lived filesystem events caused by our own rename action."""
        key = self._sort_queue_key(document_path)
        now = time.monotonic()
        with self.queue_lock:
            ignored = getattr(self, "ignored_watch_event_until", {})
            self.ignored_watch_event_until = ignored
            for stale_key, expiry in list(ignored.items()):
                if expiry <= now:
                    ignored.pop(stale_key, None)
            ignored[key] = now + seconds

    def _queue_sort_file(self, document_path: Path, *, force=False):
        if not is_processable_document(document_path):
            return
        key = self._sort_queue_key(document_path)
        signature = self._file_signature(document_path)
        with self.queue_lock:
            now = time.monotonic()
            ignored = getattr(self, "ignored_watch_event_until", {})
            self.ignored_watch_event_until = ignored
            for stale_key, expiry in list(ignored.items()):
                if expiry <= now:
                    ignored.pop(stale_key, None)
            if not force and ignored.get(key, 0) > now:
                return
            if force:
                self.snoozed_sort_signatures.pop(key, None)
            elif key in self.snoozed_sort_signatures:
                if self.snoozed_sort_signatures[key] == signature:
                    return
                self.snoozed_sort_signatures.pop(key, None)
            if key in self.queued_sort_paths:
                return
            self.queued_sort_paths.add(key)
        self.file_queue.put(document_path)

    def _clear_queued_sort_file(self, document_path: Path):
        key = self._sort_queue_key(document_path)
        with self.queue_lock:
            self.queued_sort_paths.discard(key)

    def _wait_for_file_ready(self, document_path: Path, timeout_seconds=60, stable_seconds=2):
        deadline = time.monotonic() + timeout_seconds
        last_signature = None
        stable_since = None
        last_error = None

        while time.monotonic() < deadline:
            try:
                stat = document_path.stat()
                if stat.st_size == 0:
                    stable_since = None
                    last_signature = None
                else:
                    with open(document_path, "rb"):
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
        logging.warning(f"Timed out waiting for file to finish copying: {document_path.name}.{detail}")
        return False

    def processing_loop(self):
        while True:
            document_path = self.file_queue.get()
            clear_when_done = True
            try:
                if not document_path.exists():
                    logging.warning(f"File disappeared before sorting: {document_path.name}")
                    continue
                if not self._wait_for_file_ready(document_path):
                    continue
                logging.info(f"--- Processing (sort): {document_path.name} ---")
                details = self._get_details_for_document(document_path)
                if details:
                    clear_when_done = False
                    self.gui_queue.put(("sort", document_path, details))
                else:
                    logging.error(f"Could not get details for {document_path.name}.")
            finally:
                if clear_when_done:
                    self._clear_queued_sort_file(document_path)
                self.file_queue.task_done()

    def rename_processing_loop(self):
        while True:
            document_path = self.rename_queue.get()
            try:
                logging.info(f"--- Processing (rename): {document_path.name} ---")
                details = self._get_details_for_document(document_path)
                if details:
                    self.gui_queue.put(("rename", document_path, details))
                else:
                    logging.error(f"Could not get details for {document_path.name}.")
                    self.gui_queue.put(("rename_failed", document_path, {}))
            finally:
                self.rename_queue.task_done()

    def _active_api_key(self):
        return (self.settings.api_key if self.settings else "") or self.ENV_API_KEY

    def _get_details_for_document(self, document_path: Path):
        mode = self.settings.clean_naming_mode() if self.settings else "Automatic"
        api_key = self._active_api_key()
        if mode == "Basic" or not api_key:
            if mode == "AI" and not api_key:
                logging.warning("AI naming was selected, but no Gemini API key is set. Using Basic naming instead.")
            return get_basic_document_details(document_path)

        allow_cloud_ai_for_word = bool(self.settings and self.settings.allow_cloud_ai_for_word_documents)
        if document_path.suffix.lower() == ".docx" and not allow_cloud_ai_for_word:
            logging.info(
                "Using Basic naming for %s because AI analysis of Word documents is off in Settings.",
                document_path.name,
            )
            return get_basic_document_details(document_path)

        details = get_document_details(document_path, api_key, allow_cloud_ai=allow_cloud_ai_for_word)
        if details:
            return details
        logging.warning(f"AI naming failed for {document_path.name}. Using Basic naming instead.")
        return get_basic_document_details(document_path)

    def _proposed_filename(self, details: dict, document_path: Path) -> str:
        filename_format = self.settings.clean_filename_format() if self.settings else "smart"
        custom_template = self.settings.custom_filename_template if self.settings else ""
        return build_proposed_filename(
            details,
            document_path.suffix,
            filename_format=filename_format,
            custom_template=custom_template,
        )

    def process_gui_queue(self):
        self.gui_queue_after_id = None
        try:
            # wait_window() runs a nested Tk event loop. Process one item at a
            # time and abstain while any modal is active, or timer callbacks can
            # otherwise stack multiple filename dialogs on top of each other.
            if self._modal_is_active():
                return
            try:
                mode, document_path, details = self.gui_queue.get_nowait()
            except Empty:
                return
            self.gui_modal_depth += 1
            try:
                if mode == "sort":
                    try:
                        self.handle_user_confirmation_sort(document_path, details)
                    finally:
                        self._clear_queued_sort_file(document_path)
                elif mode == "rename":
                    self.handle_rename_confirmation(document_path, details)
                elif mode == "rename_failed":
                    self._finish_rename_item(skipped=True)
            finally:
                self.gui_modal_depth = max(0, self.gui_modal_depth - 1)
                self.gui_queue.task_done()
        finally:
            self._schedule_gui_queue()

    def handle_user_confirmation_sort(self, document_path: Path, details: dict):
        new_filename_ext = self._proposed_filename(details, document_path)
        name_dialog = FilenameEditorDialog(
            self.root,
            original_name=document_path.name,
            details=details,
            proposed_name=new_filename_ext,
        )
        self._wait_for_dialog(name_dialog)
        final_filename = name_dialog.result

        if not final_filename:
            self._snooze_sort_file(document_path)
            logging.info(f"User skipped '{document_path.name}' at name proposal stage.")
            return

        final_filename_base = Path(final_filename).stem
        duplicate_pattern = f"{final_filename_base}*{document_path.suffix.lower()}"
        if list(self.SORTED_FOLDER.rglob(duplicate_pattern)):
            msg_text = f"A potential duplicate exists for: '{final_filename}'\n\nAdd anyway?"
            msg = self._messagebox(
                title="Suspected Duplicate",
                message=msg_text,
                icon="question",
                option_1="Skip",
                option_2="Add Anyway",
            )
            choice = msg.get()
            self._normalize_root()
            if choice == "Skip":
                self._snooze_sort_file(document_path)
                logging.warning(f"DUPLICATE: User chose to skip '{document_path.name}'.")
                return

        dest_folder = self.choose_destination_folder()
        if not dest_folder:
            self._snooze_sort_file(document_path)
            logging.info(f"User canceled destination selection for '{document_path.name}'.")
            return

        final_destination_path = dest_folder / final_filename
        try:
            rel_folder = final_destination_path.parent.relative_to(self.SORTED_FOLDER)
            folder_display = f"...\\{rel_folder}"
        except ValueError:
            folder_display = str(final_destination_path.parent)
        confirm_text = (f"Destination Folder:\n{folder_display}\n\nFilename:\n{final_destination_path.name}")
        confirm_msg = self._messagebox(
            title="Confirm Move",
            message=confirm_text,
            icon="question",
            option_1="Cancel",
            option_2="Confirm",
        )
        confirm_choice = confirm_msg.get()
        self._normalize_root()
        if confirm_choice == "Cancel":
            self._snooze_sort_file(document_path)
            logging.info(f"User canceled final move for '{document_path.name}'.")
            return

        try:
            final_destination_path.parent.mkdir(parents=True, exist_ok=True)
            if final_destination_path.exists():
                final_destination_path = unique_path(final_destination_path)
            shutil.move(str(document_path), str(final_destination_path))
            logging.info(f"MOVED: {document_path.name} -> {final_destination_path}")
        except Exception as e:
            self._snooze_sort_file(document_path)
            logging.error(f"Failed to move file: {e}")
        finally:
            self._normalize_root()

    def choose_destination_folder(self):
        if not self._ensure_settings():
            return None
        selected = self._run_modal(
            lambda: filedialog.askdirectory(
                parent=self.root,
                title="Choose Destination Folder",
                initialdir=str(self.SORTED_FOLDER),
                mustexist=False,
            )
        )
        self._normalize_root()
        if not selected:
            return None
        dest = Path(selected)
        if is_within_folder(dest, self.WATCH_FOLDER):
            self._messagebox(
                title="Invalid Destination",
                message=(
                    "The To Sort folder and its subfolders cannot be destinations. "
                    "Choose a folder outside the watched inbox to prevent the file from being queued again."
                ),
                icon="warning",
            )
            return None
        try:
            dest.relative_to(self.SORTED_FOLDER)
            return dest
        except ValueError:
            msg = self._messagebox(
                title="Outside Sorted Folder",
                message="This folder is outside your sorted papers root. Use it anyway?",
                icon="question",
                option_1="Cancel",
                option_2="Use Anyway",
            )
            choice = msg.get()
            self._normalize_root()
            return dest if choice == "Use Anyway" else None

    def rename_papers_flow(self):
        if not self._ensure_settings():
            return
        if self.rename_batch_total:
            self._messagebox(title="Rename In Progress", message="Please wait for the current rename batch to finish.")
            return
        choice_dialog = self._messagebox(
            title="Rename Documents",
            message="Would you like to select a folder or individual PDF/Word files?",
            icon="question",
            option_1="Folder",
            option_2="Files",
            option_3="Cancel",
        )
        choice = self._run_modal(choice_dialog.get)
        self._normalize_root()
        if choice == "Cancel":
            logging.info("User canceled the rename documents operation.")
            return
        document_files = []
        if choice == "Folder":
            folder = self._run_modal(
                lambda: filedialog.askdirectory(parent=self.root, title="Select Folder Containing Documents")
            )
            if not folder:
                logging.info("No folder selected for renaming documents.")
                return
            folder_path = Path(folder)
            document_files = [path for path in folder_path.iterdir() if path.is_file() and is_processable_document(path)]
        elif choice == "Files":
            selected_files = self._run_modal(
                lambda: filedialog.askopenfilenames(
                    parent=self.root,
                    title="Select Documents to Rename",
                    filetypes=DOCUMENT_FILE_TYPES,
                )
            )
            if not selected_files:
                logging.info("No documents selected for renaming.")
                return
            document_files = [Path(file_path) for file_path in selected_files if is_processable_document(file_path)]
        self._normalize_root()
        if not document_files:
            logging.info("No supported documents found for renaming.")
            self._messagebox(title="No Documents", message="No supported PDF or Word documents were found.")
            return
        self.rename_batch_total = len(document_files)
        self.rename_batch_done = 0
        self.rename_batch_renamed = 0
        self.rename_batch_skipped = 0
        for document_path in document_files:
            self.rename_queue.put(document_path)
        logging.info(f"Queued {self.rename_batch_total} document(s) for naming.")

    def handle_rename_confirmation(self, document_path: Path, details: dict):
        new_filename_ext = self._proposed_filename(details, document_path)

        name_dialog = FilenameEditorDialog(
            self.root,
            original_name=document_path.name,
            details=details,
            proposed_name=new_filename_ext,
        )
        self._wait_for_dialog(name_dialog)
        final_filename = name_dialog.result
        if not final_filename:
            logging.info(f"User skipped '{document_path.name}' at name proposal stage.")
            self._finish_rename_item(skipped=True)
            return

        final_path = document_path.parent / final_filename
        if final_path.exists():
            self._messagebox(title="File Exists", message=f"A file named {final_filename} already exists. Skipping.")
            self._normalize_root()
            logging.info(f"Skipped renaming '{document_path.name}' because '{final_filename}' already exists.")
            self._finish_rename_item(skipped=True)
            return
        try:
            # If this file lives in To Sort, its rename emits a watcher event.
            # Suppress that app-generated event so Name Documents stays rename-only.
            self._suppress_watch_events_for(final_path)
            document_path.rename(final_path)
            logging.info(f"Renamed: {document_path.name} -> {final_filename}")
            self._finish_rename_item(renamed=True)
        except Exception as e:
            logging.error(f"Failed to rename {document_path.name}: {e}")
            self._messagebox(title="Rename Error", message=f"Failed to rename {document_path.name}: {e}")
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
            self._messagebox(title="Rename Complete", message=f"Renaming complete.\nRenamed: {self.rename_batch_renamed}\nSkipped: {self.rename_batch_skipped}\nTotal: {self.rename_batch_total}")
            self.rename_batch_total = 0

    def process_existing_files(self, *, force=False):
        if not self.settings.is_complete() or self._folder_access_error:
            return
        logging.info(f"Scanning for existing files in {self.WATCH_FOLDER}...")
        try:
            document_files = sorted(
                path for path in self.WATCH_FOLDER.iterdir() if path.is_file() and is_processable_document(path)
            )
        except OSError as exc:
            logging.error(f"Could not scan the To Sort folder: {exc}")
            return
        if document_files:
            logging.info(f"Found {len(document_files)} supported document(s) to queue for processing.")
            for document_path in document_files:
                self._queue_sort_file(document_path, force=force)
        else:
            logging.info("No supported documents found; the To Sort folder is empty.")

    def refresh_to_sort_folder(self):
        settings_were_ready = self.settings and self.settings.is_complete()
        if not self._ensure_settings():
            return
        if settings_were_ready:
            self.process_existing_files(force=True)

    def select_and_add_papers(self):
        if not self._ensure_settings():
            return
        selected_files = self._run_modal(
            lambda: filedialog.askopenfilenames(
                parent=self.root,
                title="Select Documents to Add",
                filetypes=DOCUMENT_FILE_TYPES,
            )
        )
        if not selected_files: logging.info("No files selected."); return
        added = 0
        for file_path_str in selected_files:
            source_path = Path(file_path_str)
            if not is_processable_document(source_path):
                logging.warning(f"Skipped unsupported document: {source_path.name}")
                continue
            try:
                destination_path = unique_path(self.WATCH_FOLDER / source_path.name)
                shutil.copy2(source_path, destination_path); added += 1
            except Exception as e: logging.error(f"Failed to copy '{source_path.name}': {e}")
        logging.info(f"User added {added} document(s) to the To Sort folder.")
        
    def handle_drop(self, event):
        if not self._ensure_settings():
            return
        file_paths_str = self.root.tk.splitlist(event.data); added_count = 0
        for path_str in file_paths_str:
            source_path = Path(path_str)
            if is_processable_document(source_path):
                try:
                    destination_path = unique_path(self.WATCH_FOLDER / source_path.name)
                    shutil.copy2(source_path, destination_path); added_count += 1
                except Exception as e: logging.error(f"Failed to copy '{source_path.name}': {e}")
            else:
                logging.warning(f"Skipped unsupported document: {source_path.name}")
        if added_count > 0: logging.info(f"User dropped {added_count} document(s) into the To Sort folder.")
        
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

    def clear_log_display(self):
        """Clear the visible log without modifying the on-disk audit trail."""
        self.redirector.clear_display()

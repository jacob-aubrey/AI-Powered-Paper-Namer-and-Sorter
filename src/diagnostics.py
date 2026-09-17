"""Offline packaged-app smoke check, isolated from user folders and settings."""
from pathlib import Path
import json
import logging
import tempfile
import time
import traceback

from version import APP_VERSION, WINDOW_TITLE


def run_self_test(report_path: str) -> int:
    started = time.perf_counter()
    report = {"version": APP_VERSION, "passed": [], "status": "failed"}
    root = None
    try:
        from app import App, DnDCTk, SettingsDialog
        from settings import AppSettings, SettingsManager
        import core_logic
        with tempfile.TemporaryDirectory(prefix="paper-sorter-self-test-") as directory:
            folder = Path(directory)
            originals = (SettingsManager.load, App.start_app, App._default_log_file, App._normalize_root)
            try:
                SettingsManager.load = lambda self: AppSettings()
                App.start_app = lambda self: None
                App._default_log_file = lambda self: folder / "smoke.log"
                App._normalize_root = lambda self: None
                root = DnDCTk()
                root.withdraw()
                instance = App(root)
                assert root.title() == WINDOW_TITLE
                report["window_title"] = root.title()
                root.update_idletasks()
                report["ui_construction_seconds"] = round(time.perf_counter() - started, 3)
                report["passed"].append("main window, icons, fonts, and drag-and-drop library")
                dialog = SettingsDialog(root, AppSettings(watch_folder=folder / "Incoming papers", sorted_folder=folder / "Research library"))
                dialog.withdraw()
                root.update_idletasks()
                assert dialog.watch_var.get().endswith("Incoming papers")
                assert dialog.sorted_var.get().endswith("Research library")
                dialog.destroy()
                report["passed"].append("settings window and arbitrary folder names")
                from CTkMessagebox import CTkMessagebox
                box = CTkMessagebox(master=root, message="Offline smoke check", option_1="Close")
                box.button_event("Close")
                report["passed"].append("messagebox assets")
                from docx import Document
                document = Document()
                document.add_paragraph("Offline document extraction check")
                document.save(folder / "sample.docx")
                assert "Offline document" in core_logic.extract_document(folder / "sample.docx").text
                report["passed"].append("DOCX template and extraction")
                from pypdf import PdfWriter
                writer = PdfWriter()
                writer.add_blank_page(width=72, height=72)
                writer.add_metadata({"/Title": "Offline PDF check"})
                writer.write(folder / "sample.pdf")
                assert core_logic.extract_document(folder / "sample.pdf").metadata["title"] == "Offline PDF check"
                report["passed"].append("PDF extraction")
                assert core_logic._load_genai()
                options = core_logic.genai_types.HttpOptions(timeout=20000, retry_options=core_logic.genai_types.HttpRetryOptions(attempts=1))
                client = core_logic.genai.Client(api_key="offline-self-test", http_options=options)
                client.close()
                report["passed"].append("AI SDK initialization (no request sent)")
                instance.redirector._write_on_main_thread("MOVED: sample.pdf -> " + str(folder / "sample.pdf") + "\n")
                assert instance.redirector.link_count == 2
                assert instance.log_textbox._textbox.cget("state") == "disabled"
                report["passed"].append("read-only log and document links")
                report["status"] = "passed"
            finally:
                SettingsManager.load, App.start_app, App._default_log_file, App._normalize_root = originals
                if root is not None:
                    root.destroy()
                    root = None
                logging.shutdown()
    except Exception:
        report["error"] = traceback.format_exc()
    report["total_seconds"] = round(time.perf_counter() - started, 3)
    destination = Path(report_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["status"] == "passed" else 1

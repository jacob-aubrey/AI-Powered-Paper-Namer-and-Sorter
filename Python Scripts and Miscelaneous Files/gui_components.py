# gui_components.py

import re
from pathlib import Path

import customtkinter as ctk
from customtkinter import CTkInputDialog
from CTkMessagebox import CTkMessagebox

# We need this function here for the FolderPicker
def list_dirs(parent: Path) -> list[Path]:
    try: return sorted([p for p in parent.iterdir() if p.is_dir() and not p.name.startswith('.')])
    except Exception: return []


class TextboxRedirector:
    def __init__(self, textbox: ctk.CTkTextbox): self.textbox = textbox
    def write(self, text): self.textbox.after(0, self.textbox.insert, "end", text); self.textbox.after(0, self.textbox.see, "end")
    def flush(self): pass


class FolderPicker(ctk.CTkToplevel):
    def __init__(self, master, root_path: Path):
        super().__init__(master)
        self.title("Choose Destination Folder"); self.geometry("520x360"); self.resizable(True, True)
        self.transient(master); self.grab_set()
        self.root_path = root_path; self.level_frames = []; self.selected_paths = []
        self.columnconfigure(0, weight=1); self.rowconfigure(0, weight=1)
        self.scroll = ctk.CTkScrollableFrame(self); self.scroll.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.btn_row = ctk.CTkFrame(self); self.btn_row.grid(row=1, column=0, padx=10, pady=(0,10), sticky="ew")
        self.btn_row.columnconfigure((0,1,2), weight=1)
        self.btn_ok = ctk.CTkButton(self.btn_row, text="Confirm", command=self._confirm); self.btn_ok.grid(row=0, column=0, padx=5, pady=5, sticky="ew")
        self.btn_root = ctk.CTkButton(self.btn_row, text="Jump to Root", command=self._reset_to_root); self.btn_root.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.btn_cancel = ctk.CTkButton(self.btn_row, text="Cancel", command=self._cancel); self.btn_cancel.grid(row=0, column=2, padx=5, pady=5, sticky="ew")
        self._add_level(parent=self.root_path, label_text=str(self.root_path.name or self.root_path)); self.result = None
    def _reset_to_root(self):
        for f in self.level_frames: f.destroy()
        self.level_frames.clear(); self.selected_paths.clear()
        self._add_level(parent=self.root_path, label_text=str(self.root_path.name or self.root_path))
    def _add_level(self, parent: Path, label_text: str):
        frame = ctk.CTkFrame(self.scroll); frame.pack(fill="x", padx=5, pady=5)
        lbl = ctk.CTkLabel(frame, text=label_text); lbl.pack(side="left", padx=5)
        children = list_dirs(parent); options = [p.name for p in children]; options.append("New folder..."); options.append("<Select none>")
        var = ctk.StringVar(value="<Select none>"); om = ctk.CTkOptionMenu(frame, variable=var, values=options, command=lambda choice, parent=parent: self._on_select(choice, parent))
        om.pack(side="left", padx=5, fill="x", expand=True)
        self.level_frames.append(frame); self.selected_paths.append(parent)
    def _remove_levels_after(self, index: int):
        while len(self.level_frames) > index + 1:
            f = self.level_frames.pop(); f.destroy(); self.selected_paths.pop()
    def _on_select(self, choice: str, parent: Path):
        idx = self._find_level_index_for_parent(parent)
        if idx is None: return
        self._remove_levels_after(idx)
        if choice == "<Select none>": return
        if choice == "New folder...": self._create_new_folder(parent, idx); return
        selected = parent / choice; self.selected_paths.append(selected); self._add_level(parent=selected, label_text=f"-> {selected.name}")
    def _create_new_folder(self, parent: Path, idx: int):
        dialog = CTkInputDialog(text="Enter new folder name:", title="New Folder"); name = dialog.get_input()
        if not name: return
        clean = re.sub(r'[\/*?:"<>|]', "", name).strip()
        if not clean: CTkMessagebox(title="Invalid Name", message="Folder name cannot be empty or only special characters."); return
        new_path = parent / clean
        try: new_path.mkdir(parents=True, exist_ok=False)
        except FileExistsError: CTkMessagebox(title="Exists", message="A folder with that name already exists.")
        except Exception as e: CTkMessagebox(title="Error", message=f"Failed to create folder: {e}")
        self._remove_levels_after(idx-1 if idx>0 else -1); self._add_level(parent=parent, label_text=str(parent.name))
    def _find_level_index_for_parent(self, parent: Path):
        for i, p in enumerate(self.selected_paths):
            if p == parent: return i
        return None
    def _final_path(self) -> Path:
        return self.selected_paths[-1] if self.selected_paths else self.root_path
    def _confirm(self): self.result = self._final_path(); self.destroy()
    def _cancel(self): self.result = None; self.destroy()

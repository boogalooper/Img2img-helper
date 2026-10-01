#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Forge JSON Editor
=================

Tkinter editor for two JSON formats used by Img2img Helper / Forge:

1) forge_model_hints
   kind = "img2img-helper-forge-model-hints"
   - profiles editor
   - ordered rules editor (first_match_wins)
   - match: starts / all / any / not
   - profile settings
   - raw JSON fallback

2) Forge schema
   kind = "photoshop-helper-forge-schema"
   - general schema properties
   - capabilities
   - controls editor with ordering
   - generation/fixed_values/image_stitch raw section editors
   - raw JSON fallback

No third-party dependencies are required.
Python 3.x + Tkinter only.
"""

import argparse
import copy
import json
import os
import shutil
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

APP_TITLE = "Forge JSON Editor"
INDENT = 2

HINTS_KIND = "img2img-helper-forge-model-hints"
SCHEMA_KIND = "photoshop-helper-forge-schema"

KNOWN_PROFILE_SETTINGS = [
    "sampler",
    "scheduler",
    "steps",
    "cfg",
    "distilled_cfg",
    "denoise",
    "shift",
]

CONTROL_TYPES = [
    "dropdown",
    "multiselect",
    "multiline",
    "integer",
    "float",
    "checkbox",
    "text",
]

COMMON_SOURCES = [
    "",
    "checkpoints",
    "modules",
    "samplers",
    "schedulers",
    "upscalers",
]


def json_pretty(value):
    return json.dumps(value, ensure_ascii=False, indent=INDENT)


def parse_json_text(text, field_name="JSON"):
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{field_name}: invalid JSON at line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ) from exc


def split_tokens(text):
    """One token per line; commas are also accepted for convenience."""
    values = []
    for line in text.replace(",", "\n").splitlines():
        value = line.strip()
        if value:
            values.append(value)
    return values


def join_tokens(values):
    if not isinstance(values, list):
        return ""
    return "\n".join(str(v) for v in values)


def bool_from_var(var):
    return bool(var.get())


class ScrollFrame(ttk.Frame):
    """Simple vertically scrollable frame."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)

        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(
            self, orient=tk.VERTICAL, command=self.canvas.yview
        )
        self.inner = ttk.Frame(self.canvas)

        self.window_id = self.canvas.create_window(
            (0, 0), window=self.inner, anchor="nw"
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.canvas.bind_all("<Button-4>", self._on_mousewheel_linux, add="+")
        self.canvas.bind_all("<Button-5>", self._on_mousewheel_linux, add="+")

    def _on_inner_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _on_mousewheel(self, event):
        # Scroll only when pointer is inside this widget.
        widget = self.winfo_containing(self.winfo_pointerx(), self.winfo_pointery())
        if widget is None:
            return
        w = widget
        inside = False
        while w is not None:
            if w == self or w == self.inner or w == self.canvas:
                inside = True
                break
            try:
                w = w.master
            except Exception:
                break
        if inside:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_mousewheel_linux(self, event):
        widget = self.winfo_containing(self.winfo_pointerx(), self.winfo_pointery())
        if widget is None:
            return
        w = widget
        inside = False
        while w is not None:
            if w == self or w == self.inner or w == self.canvas:
                inside = True
                break
            try:
                w = w.master
            except Exception:
                break
        if inside:
            self.canvas.yview_scroll(-1 if event.num == 4 else 1, "units")


class ForgeJsonEditor(tk.Tk):
    def __init__(self, initial_path=None):
        super().__init__()

        self.geometry("1320x850")
        self.minsize(1050, 680)

        self.file_path = None
        self.data = {}
        self.dirty = False
        self.mode = "unknown"

        self.profile_current = None
        self.rule_current_index = None
        self.control_current_index = None
        self.hints_form_kind = None
        self.schema_form_kind = None

        self._build_menu()
        self._build_ui()
        self._bind_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        if initial_path:
            self.load_path(initial_path)
        else:
            self.new_hints_document()

    # ------------------------------------------------------------------
    # Main UI
    # ------------------------------------------------------------------

    def _build_menu(self):
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="New model hints", command=self.new_hints_document)
        file_menu.add_command(label="New Forge schema", command=self.new_schema_document)
        file_menu.add_separator()
        file_menu.add_command(label="Open...", accelerator="Ctrl+O", command=self.open_file)
        file_menu.add_command(label="Save", accelerator="Ctrl+S", command=self.save_file)
        file_menu.add_command(
            label="Save As...",
            accelerator="Ctrl+Shift+S",
            command=self.save_file_as,
        )
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=False)
        edit_menu.add_command(
            label="Validate document",
            accelerator="F7",
            command=self.validate_document,
        )
        edit_menu.add_command(
            label="Reload structured view",
            command=self.refresh_mode_ui,
        )
        menubar.add_cascade(label="Document", menu=edit_menu)

        self.config(menu=menubar)

    def _build_ui(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)

        self.structured_tab = ttk.Frame(self.notebook)
        self.raw_tab = ttk.Frame(self.notebook)

        self.notebook.add(self.structured_tab, text="Structured")
        self.notebook.add(self.raw_tab, text="Raw JSON")

        self.mode_host = ttk.Frame(self.structured_tab)
        self.mode_host.pack(fill=tk.BOTH, expand=True)

        self._build_raw_tab()

        status = ttk.Frame(self)
        status.pack(fill=tk.X, padx=8, pady=(0, 8))

        self.status_var = tk.StringVar(value="Ready")
        self.file_var = tk.StringVar(value="Untitled")

        ttk.Label(status, textvariable=self.status_var).pack(side=tk.LEFT)
        ttk.Label(status, textvariable=self.file_var).pack(side=tk.RIGHT)

    def _build_raw_tab(self):
        toolbar = ttk.Frame(self.raw_tab)
        toolbar.pack(fill=tk.X, padx=8, pady=(8, 4))

        ttk.Button(
            toolbar, text="Apply Raw JSON", command=self.apply_raw_json
        ).pack(side=tk.LEFT)
        ttk.Button(
            toolbar, text="Reload from document", command=self.refresh_raw
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(
            toolbar, text="Format", command=self.format_raw
        ).pack(side=tk.LEFT)
        ttk.Button(
            toolbar, text="Validate", command=self.validate_raw
        ).pack(side=tk.RIGHT)

        frame = ttk.Frame(self.raw_tab)
        frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 8))

        self.raw_text = tk.Text(
            frame,
            wrap="none",
            undo=True,
            font=("Consolas", 10),
        )
        ys = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.raw_text.yview)
        xs = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.raw_text.xview)
        self.raw_text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)

        self.raw_text.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

    def _bind_shortcuts(self):
        self.bind("<Control-o>", lambda e: self.open_file())
        self.bind("<Control-s>", lambda e: self.save_file())
        self.bind("<Control-Shift-S>", lambda e: self.save_file_as())
        self.bind("<F7>", lambda e: self.validate_document())

    # ------------------------------------------------------------------
    # Document lifecycle
    # ------------------------------------------------------------------

    def new_hints_document(self):
        if hasattr(self, "dirty") and not self.maybe_save_changes():
            return

        self.data = {
            "kind": HINTS_KIND,
            "version": 2,
            "description": (
                "Optional Forge checkpoint helpTip database. "
                "Missing file or unmatched model means no model-specific helpTip."
            ),
            "behavior": {
                "first_match_wins": True,
                "match_target": (
                    "checkpoint basename; use Forge filename when available, "
                    "otherwise catalog title"
                ),
                "normalization": (
                    "lowercase and remove non-alphanumeric characters from both "
                    "target and match tokens"
                ),
                "unknown_model": "no_help_tip",
                "use_folder_for_classification": False,
                "schema_fallback": False,
            },
            "profiles": {},
            "rules": [],
        }
        self.file_path = None
        self.mode = "hints"
        self.set_dirty(False)
        self.refresh_mode_ui()
        self.refresh_raw()
        self.status("New model hints document")

    def new_schema_document(self):
        if hasattr(self, "dirty") and not self.maybe_save_changes():
            return

        self.data = {
            "kind": SCHEMA_KIND,
            "backend": "forge",
            "schema_version": 1,
            "id": "new-schema",
            "label": "New Schema",
            "ui_family": "standard",
            "order": 100,
            "size_multiple": 16,
            "capabilities": {
                "image_stitch": False,
                "max_image_inputs": 0,
            },
            "image_stitch_default": False,
            "controls": [],
            "image_stitch": {
                "visible": False,
            },
        }
        self.file_path = None
        self.mode = "schema"
        self.set_dirty(False)
        self.refresh_mode_ui()
        self.refresh_raw()
        self.status("New Forge schema")

    def open_file(self):
        if not self.maybe_save_changes():
            return

        path = filedialog.askopenfilename(
            title="Open JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.load_path(path)

    def load_path(self, path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not open JSON:\n\n{exc}")
            return False

        self.data = data
        self.file_path = os.path.abspath(path)
        self.mode = self.detect_mode(data)
        self.profile_current = None
        self.rule_current_index = None
        self.control_current_index = None

        self.set_dirty(False)
        self.refresh_mode_ui()
        self.refresh_raw()
        self.status(f"Loaded: {os.path.basename(path)}")
        return True

    @staticmethod
    def detect_mode(data):
        if not isinstance(data, dict):
            return "unknown"
        if data.get("kind") == HINTS_KIND:
            return "hints"
        if data.get("kind") == SCHEMA_KIND:
            return "schema"
        if "profiles" in data and "rules" in data:
            return "hints"
        if "controls" in data and data.get("backend") == "forge":
            return "schema"
        return "unknown"

    def maybe_save_changes(self):
        if not self.dirty:
            return True

        result = messagebox.askyesnocancel(
            APP_TITLE,
            "The document has unsaved changes.\n\nSave them now?",
        )
        if result is None:
            return False
        if result:
            return self.save_file()
        return True

    def save_file(self):
        if self.file_path is None:
            return self.save_file_as()

        if not self.commit_current_form():
            return False

        return self._write_file(self.file_path)

    def save_file_as(self):
        if not self.commit_current_form():
            return False

        path = filedialog.asksaveasfilename(
            title="Save JSON As",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return False

        if self._write_file(path):
            self.file_path = os.path.abspath(path)
            self.update_title()
            return True
        return False

    def _write_file(self, path):
        try:
            self.validate_data(raise_error=True)

            # Backup the existing file before replacement.
            if os.path.isfile(path):
                shutil.copy2(path, path + ".bak")

            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=INDENT)
                f.write("\n")

            os.replace(tmp, path)

            self.file_path = os.path.abspath(path)
            self.set_dirty(False)
            self.refresh_raw()
            self.status("Saved")
            return True

        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not save JSON:\n\n{exc}")
            return False

    def on_close(self):
        if self.maybe_save_changes():
            self.destroy()

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def refresh_mode_ui(self):
        for child in self.mode_host.winfo_children():
            child.destroy()

        self.profile_current = None
        self.rule_current_index = None
        self.control_current_index = None
        self.hints_form_kind = None
        self.schema_form_kind = None

        if self.mode == "hints":
            self.build_hints_ui()
        elif self.mode == "schema":
            self.build_schema_ui()
        else:
            self.build_unknown_ui()

        self.update_title()

    def build_unknown_ui(self):
        frame = ttk.Frame(self.mode_host)
        frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        ttk.Label(
            frame,
            text="Unknown JSON structure",
            font=("", 14, "bold"),
        ).pack(anchor="w")

        ttk.Label(
            frame,
            text=(
                "This file is not recognized as forge_model_hints or a Forge schema.\n"
                "Use the Raw JSON tab to edit it."
            ),
        ).pack(anchor="w", pady=(10, 0))

    # ------------------------------------------------------------------
    # HINTS editor
    # ------------------------------------------------------------------

    def build_hints_ui(self):
        outer = ttk.Panedwindow(self.mode_host, orient=tk.HORIZONTAL)
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        left = ttk.Frame(outer)
        right = ttk.Frame(outer)
        outer.add(left, weight=2)
        outer.add(right, weight=4)

        # Left notebook: profiles / rules
        self.hints_nav = ttk.Notebook(left)
        self.hints_nav.pack(fill=tk.BOTH, expand=True)

        profiles_tab = ttk.Frame(self.hints_nav)
        rules_tab = ttk.Frame(self.hints_nav)
        self.hints_nav.add(profiles_tab, text="Profiles")
        self.hints_nav.add(rules_tab, text="Rules")

        # Profiles list
        pbar = ttk.Frame(profiles_tab)
        pbar.pack(fill=tk.X, pady=(0, 4))

        ttk.Button(pbar, text="+", width=3, command=self.add_profile).pack(side=tk.LEFT)
        ttk.Button(pbar, text="Duplicate", command=self.duplicate_profile).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(pbar, text="Delete", command=self.delete_profile).pack(side=tk.LEFT)

        self.profile_list = tk.Listbox(profiles_tab, exportselection=False)
        pys = ttk.Scrollbar(profiles_tab, orient=tk.VERTICAL, command=self.profile_list.yview)
        self.profile_list.configure(yscrollcommand=pys.set)
        self.profile_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        pys.pack(side=tk.RIGHT, fill=tk.Y)
        self.profile_list.bind("<<ListboxSelect>>", self.on_profile_select)

        # Rules list
        rbar = ttk.Frame(rules_tab)
        rbar.pack(fill=tk.X, pady=(0, 4))

        ttk.Button(rbar, text="+", width=3, command=self.add_rule).pack(side=tk.LEFT)
        ttk.Button(rbar, text="Duplicate", command=self.duplicate_rule).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(rbar, text="Delete", command=self.delete_rule).pack(side=tk.LEFT)
        ttk.Button(rbar, text="↑", width=3, command=lambda: self.move_rule(-1)).pack(
            side=tk.RIGHT
        )
        ttk.Button(rbar, text="↓", width=3, command=lambda: self.move_rule(1)).pack(
            side=tk.RIGHT, padx=(4, 0)
        )

        self.rule_list = tk.Listbox(rules_tab, exportselection=False)
        rys = ttk.Scrollbar(rules_tab, orient=tk.VERTICAL, command=self.rule_list.yview)
        self.rule_list.configure(yscrollcommand=rys.set)
        self.rule_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        rys.pack(side=tk.RIGHT, fill=tk.Y)
        self.rule_list.bind("<<ListboxSelect>>", self.on_rule_select)

        # Right editor area
        self.hints_editor_host = ttk.Frame(right)
        self.hints_editor_host.pack(fill=tk.BOTH, expand=True)

        self.hints_top_bar = ttk.Frame(right)
        self.hints_top_bar.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(
            self.hints_top_bar,
            text="Document settings",
            command=self.show_hints_document_settings,
        ).pack(side=tk.LEFT)
        ttk.Button(
            self.hints_top_bar,
            text="Apply",
            command=self.commit_current_form,
        ).pack(side=tk.RIGHT)

        self.refresh_profile_list()
        self.refresh_rule_list()

        profiles = list(self.data.get("profiles", {}).keys())
        if profiles:
            self.profile_list.selection_set(0)
            self.profile_list.activate(0)
            self.on_profile_select()
        elif self.data.get("rules"):
            self.hints_nav.select(rules_tab)
            self.rule_list.selection_set(0)
            self.rule_list.activate(0)
            self.on_rule_select()
        else:
            self.show_hints_document_settings()

    def refresh_profile_list(self, select_name=None):
        if not hasattr(self, "profile_list"):
            return
        self.profile_list.delete(0, tk.END)
        names = list(self.data.get("profiles", {}).keys())
        for name in names:
            profile = self.data["profiles"][name]
            family = profile.get("family", "")
            text = name if not family else f"{name}  —  {family}"
            self.profile_list.insert(tk.END, text)

        if select_name in names:
            idx = names.index(select_name)
            self.profile_list.selection_set(idx)
            self.profile_list.see(idx)

    def refresh_rule_list(self, select_index=None):
        if not hasattr(self, "rule_list"):
            return
        self.rule_list.delete(0, tk.END)
        rules = self.data.get("rules", [])
        for i, rule in enumerate(rules):
            rid = rule.get("id", f"rule_{i}")
            profile = rule.get("profile", "")
            label = rule.get("label", "")
            tail = label or profile
            text = f"{i + 1:02d}. {rid}"
            if tail:
                text += f"  —  {tail}"
            self.rule_list.insert(tk.END, text)

        if select_index is not None and 0 <= select_index < len(rules):
            self.rule_list.selection_set(select_index)
            self.rule_list.see(select_index)

    def clear_hints_editor(self):
        if not hasattr(self, "hints_editor_host"):
            return
        for child in self.hints_editor_host.winfo_children():
            child.destroy()

    def on_profile_select(self, _event=None):
        sel = self.profile_list.curselection()
        if not sel:
            return

        if self.rule_current_index is not None:
            if not self.save_rule_form(silent=True):
                return

        names = list(self.data.get("profiles", {}).keys())
        idx = sel[0]
        if idx >= len(names):
            return

        name = names[idx]

        if self.profile_current and self.profile_current != name:
            if not self.save_profile_form(silent=True):
                return

        self.rule_current_index = None
        self.profile_current = name
        self.show_profile_editor(name)

    def show_profile_editor(self, name):
        self.clear_hints_editor()
        self.hints_form_kind = "profile"

        profile = self.data.get("profiles", {}).get(name, {})
        scroll = ScrollFrame(self.hints_editor_host)
        scroll.pack(fill=tk.BOTH, expand=True)

        host = scroll.inner
        host.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(host, text="Profile", font=("", 13, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 12)
        )
        row += 1

        self.p_name = tk.StringVar(value=name)
        self.p_family = tk.StringVar(value=str(profile.get("family", "")))
        self.p_task = tk.StringVar(value=str(profile.get("task", "")))
        self.p_support = tk.StringVar(value=str(profile.get("forge_support", "")))

        row = self.add_entry_row(host, row, "ID", self.p_name)
        row = self.add_entry_row(host, row, "Family", self.p_family)
        row = self.add_entry_row(host, row, "Task", self.p_task)
        row = self.add_entry_row(host, row, "Forge support", self.p_support)

        ttk.Label(host, text="Note").grid(
            row=row, column=0, sticky="nw", padx=8, pady=4
        )
        self.p_note = tk.Text(host, height=4, wrap="word")
        self.p_note.insert("1.0", str(profile.get("note", "")))
        self.p_note.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        row += 1

        ttk.Separator(host).grid(
            row=row, column=0, columnspan=2, sticky="ew", padx=8, pady=10
        )
        row += 1

        ttk.Label(host, text="Settings", font=("", 11, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(2, 6)
        )
        row += 1

        settings = profile.get("settings", {})
        if not isinstance(settings, dict):
            settings = {}

        self.p_setting_vars = {}
        for key in KNOWN_PROFILE_SETTINGS:
            var = tk.StringVar(
                value="" if key not in settings else str(settings.get(key, ""))
            )
            self.p_setting_vars[key] = var
            row = self.add_entry_row(host, row, key, var)

        extra_settings = {
            k: v for k, v in settings.items()
            if k not in KNOWN_PROFILE_SETTINGS
        }

        ttk.Label(host, text="Extra settings (JSON object)").grid(
            row=row, column=0, sticky="nw", padx=8, pady=4
        )
        self.p_extra_settings = tk.Text(host, height=5, font=("Consolas", 9))
        self.p_extra_settings.insert("1.0", json_pretty(extra_settings))
        self.p_extra_settings.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        row += 1

        known = {"family", "task", "forge_support", "settings", "note"}
        extras = {k: v for k, v in profile.items() if k not in known}

        ttk.Label(host, text="Extra profile fields (JSON object)").grid(
            row=row, column=0, sticky="nw", padx=8, pady=4
        )
        self.p_extra_fields = tk.Text(host, height=5, font=("Consolas", 9))
        self.p_extra_fields.insert("1.0", json_pretty(extras))
        self.p_extra_fields.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        row += 1

        ttk.Button(
            host, text="Apply profile", command=self.save_profile_form
        ).grid(row=row, column=1, sticky="e", padx=8, pady=12)

    def save_profile_form(self, silent=False):
        if self.profile_current is None or not hasattr(self, "p_name"):
            return True

        old_name = self.profile_current
        new_name = self.p_name.get().strip()
        if not new_name:
            if not silent:
                messagebox.showerror(APP_TITLE, "Profile ID cannot be empty.")
            return False

        profiles = self.data.setdefault("profiles", {})
        if new_name != old_name and new_name in profiles:
            if not silent:
                messagebox.showerror(
                    APP_TITLE, f'Profile "{new_name}" already exists.'
                )
            return False

        try:
            extra_settings = parse_json_text(
                self.p_extra_settings.get("1.0", "end-1c").strip() or "{}",
                "Extra settings",
            )
            if not isinstance(extra_settings, dict):
                raise ValueError("Extra settings must be a JSON object.")

            extras = parse_json_text(
                self.p_extra_fields.get("1.0", "end-1c").strip() or "{}",
                "Extra profile fields",
            )
            if not isinstance(extras, dict):
                raise ValueError("Extra profile fields must be a JSON object.")
        except ValueError as exc:
            if not silent:
                messagebox.showerror(APP_TITLE, str(exc))
            return False

        profile = dict(extras)

        family = self.p_family.get().strip()
        task = self.p_task.get().strip()
        support = self.p_support.get().strip()
        note = self.p_note.get("1.0", "end-1c").strip()

        if family:
            profile["family"] = family
        if task:
            profile["task"] = task
        if support:
            profile["forge_support"] = support

        settings = dict(extra_settings)
        for key, var in self.p_setting_vars.items():
            value = var.get().strip()
            if value:
                # Hints file intentionally stores these values as strings.
                settings[key] = value

        if settings:
            profile["settings"] = settings
        if note:
            profile["note"] = note

        if new_name == old_name:
            profiles[old_name] = profile
        else:
            # Rename while preserving insertion order.
            new_profiles = {}
            for key, value in profiles.items():
                if key == old_name:
                    new_profiles[new_name] = profile
                else:
                    new_profiles[key] = value
            self.data["profiles"] = new_profiles

            # Preserve referential integrity: rules that used the old profile
            # follow the rename automatically.
            for rule in self.data.get("rules", []):
                if rule.get("profile") == old_name:
                    rule["profile"] = new_name

            self.profile_current = new_name

        self.set_dirty(True)
        self.refresh_profile_list(select_name=self.profile_current)
        self.refresh_rule_list()

        if not silent:
            self.status("Profile applied")
            self.show_profile_editor(self.profile_current)
        return True

    def add_profile(self):
        if not self.commit_current_form():
            return

        profiles = self.data.setdefault("profiles", {})
        base = "new_profile"
        name = base
        i = 2
        while name in profiles:
            name = f"{base}_{i}"
            i += 1

        profiles[name] = {
            "family": "New profile",
            "settings": {},
        }

        self.set_dirty(True)
        self.refresh_profile_list(select_name=name)
        self.profile_current = name
        self.rule_current_index = None
        self.show_profile_editor(name)

    def duplicate_profile(self):
        sel = self.profile_list.curselection()
        if not sel:
            return
        if not self.commit_current_form():
            return

        names = list(self.data.get("profiles", {}).keys())
        old = names[sel[0]]
        profiles = self.data["profiles"]

        base = old + "_copy"
        name = base
        i = 2
        while name in profiles:
            name = f"{base}{i}"
            i += 1

        new_profiles = {}
        for key, value in profiles.items():
            new_profiles[key] = value
            if key == old:
                new_profiles[name] = copy.deepcopy(value)

        self.data["profiles"] = new_profiles
        self.set_dirty(True)
        self.refresh_profile_list(select_name=name)
        self.profile_current = name
        self.show_profile_editor(name)

    def delete_profile(self):
        sel = self.profile_list.curselection()
        if not sel:
            return

        names = list(self.data.get("profiles", {}).keys())
        name = names[sel[0]]

        refs = [
            rule.get("id", "?")
            for rule in self.data.get("rules", [])
            if rule.get("profile") == name
        ]

        text = f'Delete profile "{name}"?'
        if refs:
            text += (
                "\n\nReferenced by rules:\n"
                + "\n".join(refs[:10])
                + ("\n..." if len(refs) > 10 else "")
                + "\n\nThose rules will remain but their profile reference will be invalid."
            )

        if not messagebox.askyesno(APP_TITLE, text):
            return

        del self.data["profiles"][name]
        self.profile_current = None
        self.set_dirty(True)
        self.refresh_profile_list()
        self.clear_hints_editor()

    def on_rule_select(self, _event=None):
        sel = self.rule_list.curselection()
        if not sel:
            return

        if self.profile_current is not None:
            if not self.save_profile_form(silent=True):
                return

        idx = sel[0]

        if (
            self.rule_current_index is not None
            and self.rule_current_index != idx
        ):
            if not self.save_rule_form(silent=True):
                return

        self.profile_current = None
        self.rule_current_index = idx
        self.show_rule_editor(idx)

    def show_rule_editor(self, index):
        self.clear_hints_editor()
        self.hints_form_kind = "rule"

        rules = self.data.get("rules", [])
        if not (0 <= index < len(rules)):
            return

        rule = rules[index]
        match = rule.get("match", {})
        if not isinstance(match, dict):
            match = {}

        scroll = ScrollFrame(self.hints_editor_host)
        scroll.pack(fill=tk.BOTH, expand=True)

        host = scroll.inner
        host.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(
            host,
            text=f"Rule #{index + 1}",
            font=("", 13, "bold"),
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 12))
        row += 1

        self.r_id = tk.StringVar(value=str(rule.get("id", "")))
        self.r_profile = tk.StringVar(value=str(rule.get("profile", "")))
        self.r_label = tk.StringVar(value=str(rule.get("label", "")))
        self.r_starts = tk.StringVar(value=str(match.get("starts", "")))

        row = self.add_entry_row(host, row, "ID", self.r_id)

        ttk.Label(host, text="Profile").grid(
            row=row, column=0, sticky="w", padx=8, pady=4
        )
        self.r_profile_combo = ttk.Combobox(
            host,
            textvariable=self.r_profile,
            values=list(self.data.get("profiles", {}).keys()),
        )
        self.r_profile_combo.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        row += 1

        row = self.add_entry_row(host, row, "Label", self.r_label)

        ttk.Label(host, text="Note").grid(
            row=row, column=0, sticky="nw", padx=8, pady=4
        )
        self.r_note = tk.Text(host, height=4, wrap="word")
        self.r_note.insert("1.0", str(rule.get("note", "")))
        self.r_note.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        row += 1

        ttk.Separator(host).grid(
            row=row, column=0, columnspan=2, sticky="ew", padx=8, pady=10
        )
        row += 1

        ttk.Label(host, text="Match", font=("", 11, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(2, 6)
        )
        row += 1

        row = self.add_entry_row(host, row, "starts", self.r_starts)

        self.r_match_texts = {}
        for key in ("all", "any", "not"):
            ttk.Label(host, text=key).grid(
                row=row, column=0, sticky="nw", padx=8, pady=4
            )
            txt = tk.Text(host, height=4, font=("Consolas", 9))
            txt.insert("1.0", join_tokens(match.get(key, [])))
            txt.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
            self.r_match_texts[key] = txt
            row += 1

        ttk.Label(
            host,
            text="One token per line. Commas are also accepted.",
        ).grid(row=row, column=1, sticky="w", padx=8, pady=(0, 8))
        row += 1

        known_match = {"starts", "all", "any", "not"}
        extra_match = {k: v for k, v in match.items() if k not in known_match}

        ttk.Label(host, text="Extra match fields (JSON object)").grid(
            row=row, column=0, sticky="nw", padx=8, pady=4
        )
        self.r_extra_match = tk.Text(host, height=4, font=("Consolas", 9))
        self.r_extra_match.insert("1.0", json_pretty(extra_match))
        self.r_extra_match.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        row += 1

        known_rule = {"id", "match", "profile", "label", "note"}
        extras = {k: v for k, v in rule.items() if k not in known_rule}

        ttk.Label(host, text="Extra rule fields (JSON object)").grid(
            row=row, column=0, sticky="nw", padx=8, pady=4
        )
        self.r_extra_fields = tk.Text(host, height=4, font=("Consolas", 9))
        self.r_extra_fields.insert("1.0", json_pretty(extras))
        self.r_extra_fields.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        row += 1

        ttk.Button(
            host, text="Apply rule", command=self.save_rule_form
        ).grid(row=row, column=1, sticky="e", padx=8, pady=12)

    def save_rule_form(self, silent=False):
        idx = self.rule_current_index
        if idx is None or not hasattr(self, "r_id"):
            return True

        rules = self.data.get("rules", [])
        if not (0 <= idx < len(rules)):
            return True

        rid = self.r_id.get().strip()
        if not rid:
            if not silent:
                messagebox.showerror(APP_TITLE, "Rule ID cannot be empty.")
            return False

        for i, rule in enumerate(rules):
            if i != idx and rule.get("id") == rid:
                if not silent:
                    messagebox.showerror(
                        APP_TITLE, f'Rule ID "{rid}" already exists.'
                    )
                return False

        try:
            extra_match = parse_json_text(
                self.r_extra_match.get("1.0", "end-1c").strip() or "{}",
                "Extra match fields",
            )
            if not isinstance(extra_match, dict):
                raise ValueError("Extra match fields must be a JSON object.")

            extras = parse_json_text(
                self.r_extra_fields.get("1.0", "end-1c").strip() or "{}",
                "Extra rule fields",
            )
            if not isinstance(extras, dict):
                raise ValueError("Extra rule fields must be a JSON object.")
        except ValueError as exc:
            if not silent:
                messagebox.showerror(APP_TITLE, str(exc))
            return False

        match = dict(extra_match)

        starts = self.r_starts.get().strip()
        if starts:
            match["starts"] = starts

        for key, widget in self.r_match_texts.items():
            values = split_tokens(widget.get("1.0", "end-1c"))
            if values:
                match[key] = values

        rule = dict(extras)
        rule["id"] = rid
        rule["match"] = match

        profile = self.r_profile.get().strip()
        label = self.r_label.get().strip()
        note = self.r_note.get("1.0", "end-1c").strip()

        if profile:
            rule["profile"] = profile
        if label:
            rule["label"] = label
        if note:
            rule["note"] = note

        rules[idx] = rule

        self.set_dirty(True)
        self.refresh_rule_list(select_index=idx)

        if not silent:
            self.status("Rule applied")
            self.show_rule_editor(idx)
        return True

    def add_rule(self):
        if not self.commit_current_form():
            return

        rules = self.data.setdefault("rules", [])
        used = {str(r.get("id", "")) for r in rules}

        base = "new_rule"
        rid = base
        i = 2
        while rid in used:
            rid = f"{base}_{i}"
            i += 1

        profiles = list(self.data.get("profiles", {}).keys())
        rule = {
            "id": rid,
            "match": {"all": ["keyword"]},
        }
        if profiles:
            rule["profile"] = profiles[0]

        rules.append(rule)
        idx = len(rules) - 1

        self.set_dirty(True)
        self.refresh_rule_list(select_index=idx)
        self.rule_current_index = idx
        self.profile_current = None
        self.show_rule_editor(idx)

    def duplicate_rule(self):
        sel = self.rule_list.curselection()
        if not sel:
            return
        if not self.commit_current_form():
            return

        idx = sel[0]
        rules = self.data.get("rules", [])
        new_rule = copy.deepcopy(rules[idx])

        used = {str(r.get("id", "")) for r in rules}
        base = str(new_rule.get("id", "rule")) + "_copy"
        rid = base
        i = 2
        while rid in used:
            rid = f"{base}{i}"
            i += 1
        new_rule["id"] = rid

        rules.insert(idx + 1, new_rule)
        new_idx = idx + 1

        self.set_dirty(True)
        self.refresh_rule_list(select_index=new_idx)
        self.rule_current_index = new_idx
        self.show_rule_editor(new_idx)

    def delete_rule(self):
        sel = self.rule_list.curselection()
        if not sel:
            return
        idx = sel[0]
        rules = self.data.get("rules", [])
        if not (0 <= idx < len(rules)):
            return

        rid = rules[idx].get("id", f"#{idx + 1}")
        if not messagebox.askyesno(APP_TITLE, f'Delete rule "{rid}"?'):
            return

        del rules[idx]
        self.rule_current_index = None
        self.set_dirty(True)

        new_idx = min(idx, len(rules) - 1)
        self.refresh_rule_list(select_index=new_idx if rules else None)
        self.clear_hints_editor()

        if rules:
            self.rule_current_index = new_idx
            self.show_rule_editor(new_idx)

    def move_rule(self, delta):
        sel = self.rule_list.curselection()
        if not sel:
            return
        if not self.commit_current_form():
            return

        idx = sel[0]
        target = idx + delta
        rules = self.data.get("rules", [])

        if not (0 <= target < len(rules)):
            return

        rules[idx], rules[target] = rules[target], rules[idx]
        self.rule_current_index = target

        self.set_dirty(True)
        self.refresh_rule_list(select_index=target)
        self.show_rule_editor(target)
        self.status("Rule order changed")

    def show_hints_document_settings(self):
        if not self.commit_current_form():
            return

        self.profile_current = None
        self.rule_current_index = None
        self.clear_hints_editor()

        scroll = ScrollFrame(self.hints_editor_host)
        scroll.pack(fill=tk.BOTH, expand=True)
        host = scroll.inner
        host.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(
            host, text="Model hints document", font=("", 13, "bold")
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 12))
        row += 1

        self.h_kind = tk.StringVar(value=str(self.data.get("kind", HINTS_KIND)))
        self.h_version = tk.StringVar(value=str(self.data.get("version", 2)))

        row = self.add_entry_row(host, row, "kind", self.h_kind)
        row = self.add_entry_row(host, row, "version", self.h_version)

        ttk.Label(host, text="description").grid(
            row=row, column=0, sticky="nw", padx=8, pady=4
        )
        self.h_description = tk.Text(host, height=4, wrap="word")
        self.h_description.insert("1.0", str(self.data.get("description", "")))
        self.h_description.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        row += 1

        behavior = self.data.get("behavior", {})
        if not isinstance(behavior, dict):
            behavior = {}

        self.h_first_match = tk.BooleanVar(
            value=bool(behavior.get("first_match_wins", True))
        )
        self.h_folder = tk.BooleanVar(
            value=bool(behavior.get("use_folder_for_classification", False))
        )
        self.h_schema_fallback = tk.BooleanVar(
            value=bool(behavior.get("schema_fallback", False))
        )
        self.h_match_target = tk.StringVar(
            value=str(behavior.get("match_target", ""))
        )
        self.h_normalization = tk.StringVar(
            value=str(behavior.get("normalization", ""))
        )
        self.h_unknown = tk.StringVar(
            value=str(behavior.get("unknown_model", ""))
        )

        ttk.Checkbutton(
            host,
            text="first_match_wins",
            variable=self.h_first_match,
        ).grid(row=row, column=1, sticky="w", padx=8, pady=4)
        row += 1

        row = self.add_entry_row(host, row, "match_target", self.h_match_target)
        row = self.add_entry_row(host, row, "normalization", self.h_normalization)
        row = self.add_entry_row(host, row, "unknown_model", self.h_unknown)

        ttk.Checkbutton(
            host,
            text="use_folder_for_classification",
            variable=self.h_folder,
        ).grid(row=row, column=1, sticky="w", padx=8, pady=4)
        row += 1

        ttk.Checkbutton(
            host,
            text="schema_fallback",
            variable=self.h_schema_fallback,
        ).grid(row=row, column=1, sticky="w", padx=8, pady=4)
        row += 1

        known_behavior = {
            "first_match_wins",
            "match_target",
            "normalization",
            "unknown_model",
            "use_folder_for_classification",
            "schema_fallback",
        }
        extra_behavior = {
            k: v for k, v in behavior.items()
            if k not in known_behavior
        }

        ttk.Label(host, text="Extra behavior fields (JSON object)").grid(
            row=row, column=0, sticky="nw", padx=8, pady=4
        )
        self.h_extra_behavior = tk.Text(host, height=5, font=("Consolas", 9))
        self.h_extra_behavior.insert("1.0", json_pretty(extra_behavior))
        self.h_extra_behavior.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        row += 1

        known_top = {"kind", "version", "description", "behavior", "profiles", "rules"}
        extra_top = {
            k: v for k, v in self.data.items()
            if k not in known_top
        }

        ttk.Label(host, text="Extra top-level fields (JSON object)").grid(
            row=row, column=0, sticky="nw", padx=8, pady=4
        )
        self.h_extra_top = tk.Text(host, height=5, font=("Consolas", 9))
        self.h_extra_top.insert("1.0", json_pretty(extra_top))
        self.h_extra_top.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        row += 1

        ttk.Button(
            host,
            text="Apply document settings",
            command=self.save_hints_document_settings,
        ).grid(row=row, column=1, sticky="e", padx=8, pady=12)

        self.hints_form_kind = "document"

    def save_hints_document_settings(self, silent=False):
        if not hasattr(self, "h_kind"):
            return True

        try:
            version_text = self.h_version.get().strip()
            version = int(version_text)

            extra_behavior = parse_json_text(
                self.h_extra_behavior.get("1.0", "end-1c").strip() or "{}",
                "Extra behavior fields",
            )
            if not isinstance(extra_behavior, dict):
                raise ValueError("Extra behavior fields must be a JSON object.")

            extra_top = parse_json_text(
                self.h_extra_top.get("1.0", "end-1c").strip() or "{}",
                "Extra top-level fields",
            )
            if not isinstance(extra_top, dict):
                raise ValueError("Extra top-level fields must be a JSON object.")
        except (ValueError, TypeError) as exc:
            if not silent:
                messagebox.showerror(APP_TITLE, str(exc))
            return False

        behavior = dict(extra_behavior)
        behavior["first_match_wins"] = bool_from_var(self.h_first_match)
        behavior["match_target"] = self.h_match_target.get().strip()
        behavior["normalization"] = self.h_normalization.get().strip()
        behavior["unknown_model"] = self.h_unknown.get().strip()
        behavior["use_folder_for_classification"] = bool_from_var(self.h_folder)
        behavior["schema_fallback"] = bool_from_var(self.h_schema_fallback)

        profiles = self.data.get("profiles", {})
        rules = self.data.get("rules", [])

        new_data = dict(extra_top)
        new_data["kind"] = self.h_kind.get().strip() or HINTS_KIND
        new_data["version"] = version

        description = self.h_description.get("1.0", "end-1c").strip()
        if description:
            new_data["description"] = description

        new_data["behavior"] = behavior
        new_data["profiles"] = profiles
        new_data["rules"] = rules
        self.data = new_data

        self.set_dirty(True)
        if not silent:
            self.status("Document settings applied")
        return True

    # ------------------------------------------------------------------
    # SCHEMA editor
    # ------------------------------------------------------------------

    def build_schema_ui(self):
        outer = ttk.Panedwindow(self.mode_host, orient=tk.HORIZONTAL)
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        left = ttk.Frame(outer)
        right = ttk.Frame(outer)
        outer.add(left, weight=2)
        outer.add(right, weight=4)

        # Left: general + controls
        topbar = ttk.Frame(left)
        topbar.pack(fill=tk.X, pady=(0, 4))

        ttk.Button(
            topbar,
            text="General",
            command=self.show_schema_general,
        ).pack(side=tk.LEFT)

        ttk.Separator(left).pack(fill=tk.X, pady=(2, 6))

        cbar = ttk.Frame(left)
        cbar.pack(fill=tk.X, pady=(0, 4))

        ttk.Button(cbar, text="+", width=3, command=self.add_control).pack(side=tk.LEFT)
        ttk.Button(cbar, text="Duplicate", command=self.duplicate_control).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(cbar, text="Delete", command=self.delete_control).pack(side=tk.LEFT)
        ttk.Button(
            cbar, text="↑", width=3, command=lambda: self.move_control(-1)
        ).pack(side=tk.RIGHT)
        ttk.Button(
            cbar, text="↓", width=3, command=lambda: self.move_control(1)
        ).pack(side=tk.RIGHT, padx=(4, 0))

        self.control_list = tk.Listbox(left, exportselection=False)
        cys = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.control_list.yview)
        self.control_list.configure(yscrollcommand=cys.set)
        self.control_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cys.pack(side=tk.RIGHT, fill=tk.Y)
        self.control_list.bind("<<ListboxSelect>>", self.on_control_select)

        self.schema_editor_host = ttk.Frame(right)
        self.schema_editor_host.pack(fill=tk.BOTH, expand=True)

        self.schema_bottom_bar = ttk.Frame(right)
        self.schema_bottom_bar.pack(fill=tk.X, pady=(6, 0))

        ttk.Button(
            self.schema_bottom_bar,
            text="Apply",
            command=self.commit_current_form,
        ).pack(side=tk.RIGHT)

        self.refresh_control_list()

        controls = self.data.get("controls", [])
        if controls:
            self.control_list.selection_set(0)
            self.control_list.activate(0)
            self.on_control_select()
        else:
            self.show_schema_general()

    def refresh_control_list(self, select_index=None):
        if not hasattr(self, "control_list"):
            return

        self.control_list.delete(0, tk.END)
        controls = self.data.get("controls", [])

        for i, c in enumerate(controls):
            cid = c.get("id", f"control_{i}")
            label = c.get("label", "")
            typ = c.get("type", "")
            tail = label or typ
            text = f"{i + 1:02d}. {cid}"
            if tail:
                text += f"  —  {tail}"
            self.control_list.insert(tk.END, text)

        if select_index is not None and 0 <= select_index < len(controls):
            self.control_list.selection_set(select_index)
            self.control_list.see(select_index)

    def clear_schema_editor(self):
        if not hasattr(self, "schema_editor_host"):
            return
        for child in self.schema_editor_host.winfo_children():
            child.destroy()

    def show_schema_general(self):
        if not self.commit_current_form():
            return

        self.control_current_index = None
        self.clear_schema_editor()
        self.schema_form_kind = "general"

        scroll = ScrollFrame(self.schema_editor_host)
        scroll.pack(fill=tk.BOTH, expand=True)

        host = scroll.inner
        host.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(
            host, text="Forge schema", font=("", 13, "bold")
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 12))
        row += 1

        self.s_kind = tk.StringVar(value=str(self.data.get("kind", SCHEMA_KIND)))
        self.s_backend = tk.StringVar(value=str(self.data.get("backend", "forge")))
        self.s_version = tk.StringVar(value=str(self.data.get("schema_version", 1)))
        self.s_id = tk.StringVar(value=str(self.data.get("id", "")))
        self.s_label = tk.StringVar(value=str(self.data.get("label", "")))
        self.s_family = tk.StringVar(value=str(self.data.get("ui_family", "standard")))
        self.s_order = tk.StringVar(value=str(self.data.get("order", 0)))
        self.s_multiple = tk.StringVar(value=str(self.data.get("size_multiple", 16)))
        self.s_stitch_default = tk.BooleanVar(
            value=bool(self.data.get("image_stitch_default", False))
        )

        row = self.add_entry_row(host, row, "kind", self.s_kind)
        row = self.add_entry_row(host, row, "backend", self.s_backend)
        row = self.add_entry_row(host, row, "schema_version", self.s_version)
        row = self.add_entry_row(host, row, "id", self.s_id)
        row = self.add_entry_row(host, row, "label", self.s_label)
        row = self.add_entry_row(host, row, "ui_family", self.s_family)
        row = self.add_entry_row(host, row, "order", self.s_order)
        row = self.add_entry_row(host, row, "size_multiple", self.s_multiple)

        ttk.Checkbutton(
            host,
            text="image_stitch_default",
            variable=self.s_stitch_default,
        ).grid(row=row, column=1, sticky="w", padx=8, pady=4)
        row += 1

        ttk.Separator(host).grid(
            row=row, column=0, columnspan=2, sticky="ew", padx=8, pady=10
        )
        row += 1

        ttk.Label(host, text="Capabilities", font=("", 11, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(2, 6)
        )
        row += 1

        capabilities = self.data.get("capabilities", {})
        if not isinstance(capabilities, dict):
            capabilities = {}

        self.s_cap_stitch = tk.BooleanVar(
            value=bool(capabilities.get("image_stitch", False))
        )
        self.s_cap_max = tk.StringVar(
            value=str(capabilities.get("max_image_inputs", 0))
        )

        ttk.Checkbutton(
            host,
            text="image_stitch",
            variable=self.s_cap_stitch,
        ).grid(row=row, column=1, sticky="w", padx=8, pady=4)
        row += 1

        row = self.add_entry_row(host, row, "max_image_inputs", self.s_cap_max)

        known_caps = {"image_stitch", "max_image_inputs"}
        extra_caps = {
            k: v for k, v in capabilities.items()
            if k not in known_caps
        }

        ttk.Label(host, text="Extra capabilities (JSON object)").grid(
            row=row, column=0, sticky="nw", padx=8, pady=4
        )
        self.s_extra_caps = tk.Text(host, height=4, font=("Consolas", 9))
        self.s_extra_caps.insert("1.0", json_pretty(extra_caps))
        self.s_extra_caps.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        row += 1

        ttk.Separator(host).grid(
            row=row, column=0, columnspan=2, sticky="ew", padx=8, pady=10
        )
        row += 1

        # Keep arbitrary specialized sections intact and editable.
        special_sections = [
            ("generation", self.data.get("generation", None)),
            ("fixed_values", self.data.get("fixed_values", None)),
            ("image_stitch", self.data.get("image_stitch", None)),
        ]
        self.s_section_texts = {}

        for key, value in special_sections:
            ttk.Label(host, text=f"{key} (JSON)").grid(
                row=row, column=0, sticky="nw", padx=8, pady=4
            )
            txt = tk.Text(host, height=7, font=("Consolas", 9))
            txt.insert("1.0", "null" if value is None else json_pretty(value))
            txt.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
            self.s_section_texts[key] = txt
            row += 1

        known_top = {
            "kind",
            "backend",
            "schema_version",
            "id",
            "label",
            "ui_family",
            "order",
            "size_multiple",
            "capabilities",
            "image_stitch_default",
            "generation",
            "fixed_values",
            "controls",
            "image_stitch",
        }
        extras = {
            k: v for k, v in self.data.items()
            if k not in known_top
        }

        ttk.Label(host, text="Extra top-level fields (JSON object)").grid(
            row=row, column=0, sticky="nw", padx=8, pady=4
        )
        self.s_extra_top = tk.Text(host, height=6, font=("Consolas", 9))
        self.s_extra_top.insert("1.0", json_pretty(extras))
        self.s_extra_top.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        row += 1

        ttk.Button(
            host, text="Apply schema", command=self.save_schema_general
        ).grid(row=row, column=1, sticky="e", padx=8, pady=12)

        self.schema_form_kind = "general"

    def save_schema_general(self, silent=False):
        if not hasattr(self, "s_kind"):
            return True

        try:
            version = int(self.s_version.get().strip())
            order = int(self.s_order.get().strip())
            multiple = int(self.s_multiple.get().strip())
            max_inputs = int(self.s_cap_max.get().strip())

            extra_caps = parse_json_text(
                self.s_extra_caps.get("1.0", "end-1c").strip() or "{}",
                "Extra capabilities",
            )
            if not isinstance(extra_caps, dict):
                raise ValueError("Extra capabilities must be a JSON object.")

            extras = parse_json_text(
                self.s_extra_top.get("1.0", "end-1c").strip() or "{}",
                "Extra top-level fields",
            )
            if not isinstance(extras, dict):
                raise ValueError("Extra top-level fields must be a JSON object.")

            parsed_sections = {}
            for key, txt in self.s_section_texts.items():
                raw = txt.get("1.0", "end-1c").strip() or "null"
                parsed_sections[key] = parse_json_text(raw, key)

        except (ValueError, TypeError) as exc:
            if not silent:
                messagebox.showerror(APP_TITLE, str(exc))
            return False

        controls = self.data.get("controls", [])

        capabilities = dict(extra_caps)
        capabilities["image_stitch"] = bool_from_var(self.s_cap_stitch)
        capabilities["max_image_inputs"] = max_inputs

        new_data = dict(extras)
        new_data["kind"] = self.s_kind.get().strip() or SCHEMA_KIND
        new_data["backend"] = self.s_backend.get().strip() or "forge"
        new_data["schema_version"] = version
        new_data["id"] = self.s_id.get().strip()
        new_data["label"] = self.s_label.get().strip()
        new_data["ui_family"] = self.s_family.get().strip()
        new_data["order"] = order
        new_data["size_multiple"] = multiple
        new_data["capabilities"] = capabilities
        new_data["image_stitch_default"] = bool_from_var(self.s_stitch_default)

        for key, value in parsed_sections.items():
            if value is not None:
                new_data[key] = value

        new_data["controls"] = controls
        self.data = new_data

        self.set_dirty(True)
        if not silent:
            self.status("Schema applied")
        return True

    def on_control_select(self, _event=None):
        sel = self.control_list.curselection()
        if not sel:
            return

        idx = sel[0]
        if (
            self.control_current_index is not None
            and self.control_current_index != idx
        ):
            if not self.save_control_form(silent=True):
                return

        self.control_current_index = idx
        self.show_control_editor(idx)

    def show_control_editor(self, index):
        self.clear_schema_editor()
        self.schema_form_kind = "control"

        controls = self.data.get("controls", [])
        if not (0 <= index < len(controls)):
            return

        c = controls[index]

        scroll = ScrollFrame(self.schema_editor_host)
        scroll.pack(fill=tk.BOTH, expand=True)
        host = scroll.inner
        host.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(
            host,
            text=f"Control #{index + 1}",
            font=("", 13, "bold"),
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 12))
        row += 1

        self.c_id = tk.StringVar(value=str(c.get("id", "")))
        self.c_label = tk.StringVar(value=str(c.get("label", "")))
        self.c_type = tk.StringVar(value=str(c.get("type", "")))
        self.c_source = tk.StringVar(value=str(c.get("source", "")))
        self.c_visible = tk.BooleanVar(value=bool(c.get("visible", True)))
        self.c_required_visible = tk.BooleanVar(
            value=bool(c.get("required_visible", False))
        )
        self.c_payload_key = tk.StringVar(value=str(c.get("payload_key", "")))
        self.c_option_key = tk.StringVar(value=str(c.get("option_key", "")))
        self.c_enabled_by = tk.StringVar(value=str(c.get("enabled_by", "")))

        row = self.add_entry_row(host, row, "id", self.c_id)
        row = self.add_entry_row(host, row, "label", self.c_label)

        ttk.Label(host, text="type").grid(
            row=row, column=0, sticky="w", padx=8, pady=4
        )
        ttk.Combobox(
            host,
            textvariable=self.c_type,
            values=CONTROL_TYPES,
        ).grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        row += 1

        ttk.Label(host, text="source").grid(
            row=row, column=0, sticky="w", padx=8, pady=4
        )
        ttk.Combobox(
            host,
            textvariable=self.c_source,
            values=COMMON_SOURCES,
        ).grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        row += 1

        row = self.add_json_value_row(
            host, row, "value", c.get("value", None), "c_value"
        )
        row = self.add_json_value_row(
            host, row, "min", c.get("min", None), "c_min"
        )
        row = self.add_json_value_row(
            host, row, "max", c.get("max", None), "c_max"
        )
        row = self.add_json_value_row(
            host, row, "step", c.get("step", None), "c_step"
        )

        ttk.Checkbutton(
            host, text="visible", variable=self.c_visible
        ).grid(row=row, column=1, sticky="w", padx=8, pady=4)
        row += 1

        ttk.Checkbutton(
            host, text="required_visible", variable=self.c_required_visible
        ).grid(row=row, column=1, sticky="w", padx=8, pady=4)
        row += 1

        row = self.add_entry_row(host, row, "payload_key", self.c_payload_key)
        row = self.add_entry_row(host, row, "option_key", self.c_option_key)
        row = self.add_entry_row(host, row, "enabled_by", self.c_enabled_by)

        ttk.Label(host, text="help").grid(
            row=row, column=0, sticky="nw", padx=8, pady=4
        )
        self.c_help = tk.Text(host, height=4, wrap="word")
        self.c_help.insert("1.0", str(c.get("help", "")))
        self.c_help.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        row += 1

        known = {
            "id",
            "label",
            "type",
            "source",
            "value",
            "min",
            "max",
            "step",
            "visible",
            "required_visible",
            "payload_key",
            "option_key",
            "help",
            "enabled_by",
        }
        extras = {k: v for k, v in c.items() if k not in known}

        ttk.Label(host, text="Extra control fields (JSON object)").grid(
            row=row, column=0, sticky="nw", padx=8, pady=4
        )
        self.c_extra = tk.Text(host, height=6, font=("Consolas", 9))
        self.c_extra.insert("1.0", json_pretty(extras))
        self.c_extra.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        row += 1

        ttk.Button(
            host, text="Apply control", command=self.save_control_form
        ).grid(row=row, column=1, sticky="e", padx=8, pady=12)

    def add_json_value_row(self, host, row, label, value, attr_name):
        ttk.Label(host, text=label).grid(
            row=row, column=0, sticky="w", padx=8, pady=4
        )
        # blank = field omitted; otherwise literal JSON (e.g. "", [], false, 1)
        var = tk.StringVar(
            value="" if value is None else json.dumps(value, ensure_ascii=False)
        )
        setattr(self, attr_name, var)
        ttk.Entry(host, textvariable=var, font=("Consolas", 9)).grid(
            row=row, column=1, sticky="ew", padx=8, pady=4
        )
        return row + 1

    def save_control_form(self, silent=False):
        idx = self.control_current_index
        if idx is None or not hasattr(self, "c_id"):
            return True

        controls = self.data.get("controls", [])
        if not (0 <= idx < len(controls)):
            return True

        cid = self.c_id.get().strip()
        if not cid:
            if not silent:
                messagebox.showerror(APP_TITLE, "Control ID cannot be empty.")
            return False

        for i, control in enumerate(controls):
            if i != idx and control.get("id") == cid:
                if not silent:
                    messagebox.showerror(
                        APP_TITLE, f'Control ID "{cid}" already exists.'
                    )
                return False

        try:
            extras = parse_json_text(
                self.c_extra.get("1.0", "end-1c").strip() or "{}",
                "Extra control fields",
            )
            if not isinstance(extras, dict):
                raise ValueError("Extra control fields must be a JSON object.")

            values = {}
            for field, var in [
                ("value", self.c_value),
                ("min", self.c_min),
                ("max", self.c_max),
                ("step", self.c_step),
            ]:
                raw = var.get().strip()
                if raw:
                    values[field] = parse_json_text(raw, field)

        except ValueError as exc:
            if not silent:
                messagebox.showerror(APP_TITLE, str(exc))
            return False

        control = dict(extras)
        control["id"] = cid

        label = self.c_label.get().strip()
        typ = self.c_type.get().strip()
        source = self.c_source.get().strip()

        if label:
            control["label"] = label
        if typ:
            control["type"] = typ
        if source:
            control["source"] = source

        control.update(values)
        control["visible"] = bool_from_var(self.c_visible)

        if bool_from_var(self.c_required_visible):
            control["required_visible"] = True

        payload_key = self.c_payload_key.get().strip()
        option_key = self.c_option_key.get().strip()
        enabled_by = self.c_enabled_by.get().strip()
        help_text = self.c_help.get("1.0", "end-1c").strip()

        if payload_key:
            control["payload_key"] = payload_key
        if option_key:
            control["option_key"] = option_key
        if enabled_by:
            control["enabled_by"] = enabled_by
        if help_text:
            control["help"] = help_text

        controls[idx] = control

        self.set_dirty(True)
        self.refresh_control_list(select_index=idx)

        if not silent:
            self.status("Control applied")
            self.show_control_editor(idx)
        return True

    def add_control(self):
        if not self.commit_current_form():
            return

        controls = self.data.setdefault("controls", [])
        used = {str(c.get("id", "")) for c in controls}

        base = "new_control"
        cid = base
        i = 2
        while cid in used:
            cid = f"{base}_{i}"
            i += 1

        controls.append(
            {
                "id": cid,
                "label": "New Control",
                "type": "text",
                "value": "",
                "visible": True,
            }
        )
        idx = len(controls) - 1

        self.set_dirty(True)
        self.refresh_control_list(select_index=idx)
        self.control_current_index = idx
        self.show_control_editor(idx)

    def duplicate_control(self):
        sel = self.control_list.curselection()
        if not sel:
            return
        if not self.commit_current_form():
            return

        idx = sel[0]
        controls = self.data.get("controls", [])
        new_control = copy.deepcopy(controls[idx])

        used = {str(c.get("id", "")) for c in controls}
        base = str(new_control.get("id", "control")) + "_copy"
        cid = base
        i = 2
        while cid in used:
            cid = f"{base}{i}"
            i += 1
        new_control["id"] = cid

        controls.insert(idx + 1, new_control)
        new_idx = idx + 1

        self.set_dirty(True)
        self.refresh_control_list(select_index=new_idx)
        self.control_current_index = new_idx
        self.show_control_editor(new_idx)

    def delete_control(self):
        sel = self.control_list.curselection()
        if not sel:
            return

        idx = sel[0]
        controls = self.data.get("controls", [])
        if not (0 <= idx < len(controls)):
            return

        cid = controls[idx].get("id", f"#{idx + 1}")
        if not messagebox.askyesno(APP_TITLE, f'Delete control "{cid}"?'):
            return

        del controls[idx]
        self.control_current_index = None
        self.set_dirty(True)

        new_idx = min(idx, len(controls) - 1)
        self.refresh_control_list(select_index=new_idx if controls else None)
        self.clear_schema_editor()

        if controls:
            self.control_current_index = new_idx
            self.show_control_editor(new_idx)
        else:
            self.show_schema_general()

    def move_control(self, delta):
        sel = self.control_list.curselection()
        if not sel:
            return
        if not self.commit_current_form():
            return

        idx = sel[0]
        target = idx + delta
        controls = self.data.get("controls", [])

        if not (0 <= target < len(controls)):
            return

        controls[idx], controls[target] = controls[target], controls[idx]
        self.control_current_index = target

        self.set_dirty(True)
        self.refresh_control_list(select_index=target)
        self.show_control_editor(target)
        self.status("Control order changed")

    # ------------------------------------------------------------------
    # Common structured helpers
    # ------------------------------------------------------------------

    def add_entry_row(self, host, row, label, variable):
        ttk.Label(host, text=label).grid(
            row=row, column=0, sticky="w", padx=8, pady=4
        )
        ttk.Entry(host, textvariable=variable).grid(
            row=row, column=1, sticky="ew", padx=8, pady=4
        )
        return row + 1

    def commit_current_form(self):
        if self.mode == "hints":
            if self.hints_form_kind == "profile" and self.profile_current is not None:
                return self.save_profile_form(silent=True)
            if self.hints_form_kind == "rule" and self.rule_current_index is not None:
                return self.save_rule_form(silent=True)
            if self.hints_form_kind == "document":
                return self.save_hints_document_settings(silent=True)
            return True

        if self.mode == "schema":
            if self.schema_form_kind == "control" and self.control_current_index is not None:
                return self.save_control_form(silent=True)
            if self.schema_form_kind == "general":
                return self.save_schema_general(silent=True)
            return True

        return True

    # ------------------------------------------------------------------
    # Raw JSON
    # ------------------------------------------------------------------

    def on_tab_changed(self, _event=None):
        selected = self.notebook.select()
        if selected == str(self.raw_tab):
            if self.commit_current_form():
                self.refresh_raw()

    def refresh_raw(self):
        self.raw_text.delete("1.0", tk.END)
        self.raw_text.insert("1.0", json_pretty(self.data))

    def apply_raw_json(self):
        raw = self.raw_text.get("1.0", "end-1c")
        try:
            data = parse_json_text(raw, "Document")
        except ValueError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return False

        self.data = data
        self.mode = self.detect_mode(data)
        self.set_dirty(True)
        self.refresh_mode_ui()
        self.refresh_raw()
        self.status("Raw JSON applied")
        return True

    def format_raw(self):
        raw = self.raw_text.get("1.0", "end-1c")
        try:
            data = parse_json_text(raw, "Document")
        except ValueError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        self.raw_text.delete("1.0", tk.END)
        self.raw_text.insert("1.0", json_pretty(data))
        self.status("Raw JSON formatted")

    def validate_raw(self):
        raw = self.raw_text.get("1.0", "end-1c")
        try:
            parse_json_text(raw, "Document")
        except ValueError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return False

        messagebox.showinfo(APP_TITLE, "Raw JSON syntax is valid.")
        return True

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_document(self):
        if not self.commit_current_form():
            return False

        try:
            warnings = self.validate_data(raise_error=True)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return False

        if warnings:
            messagebox.showwarning(
                APP_TITLE,
                "JSON is structurally valid, but there are warnings:\n\n"
                + "\n".join(f"• {w}" for w in warnings),
            )
        else:
            messagebox.showinfo(APP_TITLE, "Document is valid.")

        self.status("Document validated")
        return True

    def validate_data(self, raise_error=False):
        warnings = []

        try:
            json.dumps(self.data, ensure_ascii=False)
        except Exception as exc:
            if raise_error:
                raise ValueError(f"Document is not JSON-serializable: {exc}")
            return [str(exc)]

        mode = self.detect_mode(self.data)

        if mode == "hints":
            profiles = self.data.get("profiles")
            rules = self.data.get("rules")

            if not isinstance(profiles, dict):
                raise ValueError('"profiles" must be an object.')
            if not isinstance(rules, list):
                raise ValueError('"rules" must be an array.')

            rule_ids = set()
            for i, rule in enumerate(rules):
                if not isinstance(rule, dict):
                    raise ValueError(f"rules[{i}] must be an object.")

                rid = rule.get("id")
                if not rid:
                    warnings.append(f"rules[{i}] has no id")
                elif rid in rule_ids:
                    warnings.append(f'duplicate rule id: "{rid}"')
                else:
                    rule_ids.add(rid)

                profile = rule.get("profile")
                if profile and profile not in profiles:
                    warnings.append(
                        f'rule "{rid or i}" references missing profile "{profile}"'
                    )

                match = rule.get("match")
                if not isinstance(match, dict):
                    warnings.append(f'rule "{rid or i}" has no valid match object')
                elif not any(k in match for k in ("starts", "all", "any")):
                    warnings.append(
                        f'rule "{rid or i}" has no positive match condition'
                    )

        elif mode == "schema":
            controls = self.data.get("controls")
            if not isinstance(controls, list):
                raise ValueError('"controls" must be an array.')

            ids = set()
            for i, control in enumerate(controls):
                if not isinstance(control, dict):
                    raise ValueError(f"controls[{i}] must be an object.")

                cid = control.get("id")
                if not cid:
                    warnings.append(f"controls[{i}] has no id")
                elif cid in ids:
                    warnings.append(f'duplicate control id: "{cid}"')
                else:
                    ids.add(cid)

                enabled_by = control.get("enabled_by")
                if enabled_by and enabled_by not in ids and not any(
                    c.get("id") == enabled_by for c in controls
                ):
                    warnings.append(
                        f'control "{cid or i}" enabled_by references '
                        f'missing control "{enabled_by}"'
                    )

        elif mode == "unknown":
            warnings.append("Unknown JSON structure; only syntax was validated.")

        return warnings

    # ------------------------------------------------------------------
    # Status / title
    # ------------------------------------------------------------------

    def set_dirty(self, value):
        self.dirty = bool(value)
        self.update_title()

    def update_title(self):
        name = os.path.basename(self.file_path) if self.file_path else "Untitled"
        marker = " *" if self.dirty else ""
        mode_label = {
            "hints": "Model Hints",
            "schema": "Forge Schema",
            "unknown": "JSON",
        }.get(self.mode, "JSON")

        self.title(f"{APP_TITLE} — {name} [{mode_label}]{marker}")
        if hasattr(self, "file_var"):
            self.file_var.set(self.file_path or "Untitled")

    def status(self, text):
        self.status_var.set(text)


def main():
    parser = argparse.ArgumentParser(description="Edit Forge JSON files.")
    parser.add_argument(
        "file",
        nargs="?",
        help="Optional JSON file to open",
    )
    args = parser.parse_args()

    app = ForgeJsonEditor(args.file)
    app.mainloop()


if __name__ == "__main__":
    main()

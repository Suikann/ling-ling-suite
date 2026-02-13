# -*- coding: utf-8 -*-
"""
主視窗

應用程式的主要視窗，整合所有 UI 面板。
"""
import os
import tkinter as tk
from typing import Optional
import customtkinter as ctk
from core.constants import (
    DEFAULT_MASTER_TEMPLATE,
    DEFAULT_MASTER_TEMPLATE_EN,
    DEFAULT_SUBFOLDER_TEMPLATE,
    DEFAULT_SUBFOLDER_TEMPLATE_EN,
    TEMPLATE_VARIABLES,
)
from core.locale import t, get_locale, set_locale
from core.models import Project
from services.file_service import FileService
from services.import_service import ImportService
from services.preferences_service import PreferencesService
from ui.instrument_list import InstrumentListEditor


class MainWindow(ctk.CTkFrame):
    """應用程式主視窗"""

    def __init__(
        self,
        master: ctk.CTk,
        project: Project,
        preferences: PreferencesService,
        **kwargs,
    ):
        super().__init__(master, **kwargs)
        self.master_window: ctk.CTk = master
        self.project = project
        self._preferences = preferences
        self.file_service = FileService()
        self.import_service = ImportService(self.file_service)
        self._project_path: Optional[str] = None
        self._modified = False
        self._group_panel = None
        self._rename_service = None
        self._undo_service = None
        self._project_service = None
        self.pack(fill="both", expand=True)
        self._create_menu()
        self._create_layout()
        self._update_title()

    def _create_menu(self):
        self._menubar = tk.Menu(self.master_window)
        self.master_window.config(menu=self._menubar)
        # 檔案選單
        file_menu = tk.Menu(self._menubar, tearoff=0)
        file_menu.add_command(
            label=t("menu.file.new"), command=self._new_project, accelerator="Ctrl+N",
        )
        file_menu.add_command(
            label=t("menu.file.open"), command=self._open_project, accelerator="Ctrl+O",
        )
        file_menu.add_separator()
        file_menu.add_command(
            label=t("menu.file.save"), command=self._save_project, accelerator="Ctrl+S",
        )
        file_menu.add_command(
            label=t("menu.file.save_as"), command=self._save_project_as,
        )
        self._menubar.add_cascade(label=t("menu.file"), menu=file_menu)
        # 編輯選單
        edit_menu = tk.Menu(self._menubar, tearoff=0)
        edit_menu.add_command(
            label=t("menu.edit.undo"), command=self._undo_last, accelerator="Ctrl+Z",
        )
        self._menubar.add_cascade(label=t("menu.edit"), menu=edit_menu)
        # 匯入選單
        import_menu = tk.Menu(self._menubar, tearoff=0)
        import_menu.add_command(
            label=t("menu.import.files"), command=self._import_files,
        )
        import_menu.add_command(
            label=t("menu.import.folder"), command=self._import_folder,
        )
        self._menubar.add_cascade(label=t("menu.import"), menu=import_menu)
        # 檢視選單
        view_menu = tk.Menu(self._menubar, tearoff=0)
        appearance_menu = tk.Menu(view_menu, tearoff=0)
        current_mode = self._preferences.get("appearance_mode") or "Dark"
        self._appearance_var = tk.StringVar(value=current_mode)
        for mode, label_key in [
            ("Dark", "menu.view.appearance.dark"),
            ("Light", "menu.view.appearance.light"),
            ("System", "menu.view.appearance.system"),
        ]:
            appearance_menu.add_radiobutton(
                label=t(label_key),
                variable=self._appearance_var,
                value=mode,
                command=lambda m=mode: self._set_appearance(m),
            )
        view_menu.add_cascade(label=t("menu.view.appearance"), menu=appearance_menu)
        language_menu = tk.Menu(view_menu, tearoff=0)
        self._language_var = tk.StringVar(value=get_locale())
        for lang, label_key in [
            ("zh_TW", "menu.view.language.zh_TW"),
            ("en", "menu.view.language.en"),
        ]:
            language_menu.add_radiobutton(
                label=t(label_key),
                variable=self._language_var,
                value=lang,
                command=lambda la=lang: self._set_language(la),
            )
        view_menu.add_cascade(label=t("menu.view.language"), menu=language_menu)
        self._menubar.add_cascade(label=t("menu.view"), menu=view_menu)

    def _set_appearance(self, mode: str):
        ctk.set_appearance_mode(mode)
        self._preferences.set("appearance_mode", mode)
        self._preferences.save()

    def _set_language(self, lang_code: str):
        if lang_code == get_locale():
            return
        set_locale(lang_code)
        self._preferences.set("language", lang_code)
        self._preferences.save()
        self._rebuild_ui()

    def _rebuild_ui(self):
        """銷毀並重建所有 UI 面板，用於語言切換"""
        # 先同步群組面板的狀態
        if self._group_panel:
            self._group_panel.sync_to_project()
        # 記住目前值
        current_master_template = self.project.master_template
        current_subfolder_template = self.project.subfolder_template
        current_use_subfolders = self.project.use_subfolders
        current_instruments = list(self.project.instruments)
        # 銷毀所有子元件
        for child in self.winfo_children():
            child.destroy()
        self._group_panel = None
        # 重建選單
        self._create_menu()
        # 重建佈局
        self._create_layout()
        # 還原樂器表
        self._instrument_editor.set_instruments(current_instruments)
        # 還原模板值
        self._master_template_entry.delete(0, "end")
        self._master_template_entry.insert(0, current_master_template)
        self._subfolder_var.set(current_use_subfolders)
        self._subfolder_template_entry.delete(0, "end")
        self._subfolder_template_entry.insert(0, current_subfolder_template)
        # 更新視窗標題
        self.master_window.title(t("app.title"))
        self._update_title()
        # 延遲重建群組面板
        self.master_window.after(50, self._rebuild_group_panel)

    def _rebuild_group_panel(self):
        """重建群組面板"""
        from ui.group_panel import GroupPanel
        if self._center_placeholder:
            self._center_placeholder.destroy()
            self._center_placeholder = None
        group_panel = GroupPanel(
            self._center_panel,
            self.project,
            self,
        )
        group_panel.pack(fill="both", expand=True)
        self._group_panel = group_panel

    def _create_layout(self):
        self._left_panel = ctk.CTkFrame(self, width=250)
        self._left_panel.pack(side="left", fill="y", padx=(4, 0), pady=4)
        self._left_panel.pack_propagate(False)
        self._instrument_editor = InstrumentListEditor(
            self._left_panel,
            on_instruments_changed=self._on_instruments_changed,
        )
        self._instrument_editor.pack(fill="both", expand=True)
        right_area = ctk.CTkFrame(self, fg_color="transparent")
        right_area.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        self._center_panel = ctk.CTkFrame(right_area)
        self._center_panel.pack(fill="both", expand=True)
        self._center_placeholder = ctk.CTkLabel(
            self._center_panel, text=t("group.loading"),
            font=ctk.CTkFont(size=16),
        )
        self._center_placeholder.pack(expand=True)
        self._create_bottom_panel(right_area)

    def _create_bottom_panel(self, parent):
        bottom = ctk.CTkFrame(parent)
        bottom.pack(fill="x", pady=(4, 0))
        template_row = ctk.CTkFrame(bottom, fg_color="transparent")
        template_row.pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(template_row, text=t("panel.master_template")).pack(side="left")
        self._master_template_entry = ctk.CTkEntry(template_row, width=400)
        self._master_template_entry.pack(side="left", fill="x", expand=True, padx=(4, 4))
        self._master_template_entry.insert(0, self.project.master_template)
        self._master_template_entry.bind("<KeyRelease>", self._on_master_template_changed)
        vars_btn = ctk.CTkButton(
            template_row, text=t("panel.insert_variable"), width=80,
            command=self._show_variable_menu,
        )
        vars_btn.pack(side="right")
        self._vars_button = vars_btn
        subfolder_row = ctk.CTkFrame(bottom, fg_color="transparent")
        subfolder_row.pack(fill="x", padx=8, pady=2)
        self._subfolder_var = ctk.BooleanVar(value=self.project.use_subfolders)
        self._subfolder_check = ctk.CTkCheckBox(
            subfolder_row, text=t("panel.subfolder"),
            variable=self._subfolder_var,
            command=self._on_subfolder_toggled,
        )
        self._subfolder_check.pack(side="left")
        ctk.CTkLabel(subfolder_row, text=t("panel.subfolder_template")).pack(side="left")
        self._subfolder_template_entry = ctk.CTkEntry(subfolder_row, width=300)
        self._subfolder_template_entry.pack(side="left", fill="x", expand=True, padx=4)
        self._subfolder_template_entry.insert(0, self.project.subfolder_template)
        self._subfolder_template_entry.bind("<KeyRelease>", self._on_subfolder_template_changed)
        action_row = ctk.CTkFrame(bottom, fg_color="transparent")
        action_row.pack(fill="x", padx=8, pady=(4, 8))
        self._preview_btn = ctk.CTkButton(
            action_row, text=t("panel.preview_rename"),
            font=ctk.CTkFont(size=14, weight="bold"),
            height=36, command=self._preview_and_rename,
        )
        self._preview_btn.pack(side="right")
        self._status_label = ctk.CTkLabel(action_row, text=t("status.ready"), anchor="w")
        self._status_label.pack(side="left", fill="x", expand=True)

    def _show_variable_menu(self):
        menu = tk.Menu(self.master_window, tearoff=0)
        locale = get_locale()
        for var in TEMPLATE_VARIABLES:
            if locale == "en":
                label = f"{{{var.name_en}}} - {var.description}"
            else:
                label = f"{{{var.name}}} - {var.description}"
            var_name = var.name_en if locale == "en" else var.name
            menu.add_command(
                label=label,
                command=lambda v=var_name: self._insert_variable(v),
            )
        btn = self._vars_button
        x = btn.winfo_rootx()
        y = btn.winfo_rooty() + btn.winfo_height()
        menu.tk_popup(x, y)

    def _insert_variable(self, var_name: str):
        self._master_template_entry.insert("insert", f"{{{var_name}}}")
        self._on_master_template_changed()

    def _on_master_template_changed(self, event=None):
        self.project.master_template = self._master_template_entry.get()
        self._mark_modified()

    def _on_subfolder_toggled(self):
        self.project.use_subfolders = self._subfolder_var.get()
        self._mark_modified()

    def _on_subfolder_template_changed(self, event=None):
        self.project.subfolder_template = self._subfolder_template_entry.get()
        self._mark_modified()

    def _on_instruments_changed(self, instruments):
        self.project.instruments = instruments
        self._mark_modified()
        if self._group_panel:
            self._group_panel.on_instruments_changed(instruments)

    def _import_files(self):
        from tkinter import filedialog
        paths = filedialog.askopenfilenames(
            title=t("filedialog.select_pdf"),
            filetypes=[(t("filedialog.pdf_files"), "*.pdf")],
        )
        if not paths:
            return
        files = self.import_service.import_files(list(paths))
        self.project.ungrouped_files.extend(files)
        self._mark_modified()
        if self._group_panel:
            self._group_panel.refresh_ungrouped()
        self._set_status(t("status.imported_files", count=len(files)))

    def _import_folder(self):
        from tkinter import filedialog
        folder = filedialog.askdirectory(title=t("filedialog.select_folder"))
        if not folder:
            return
        groups, ungrouped = self.import_service.import_folder(folder)
        self.project.ungrouped_files.extend(ungrouped)
        for g in groups:
            self.project.groups.append(g)
        self._mark_modified()
        if self._group_panel:
            self._group_panel.reload_all()
        self._set_status(
            t("status.imported_groups", groups=len(groups), files=len(ungrouped)),
        )

    def _preview_and_rename(self):
        from tkinter import messagebox
        if self._group_panel:
            self._group_panel.sync_to_project()
        if not self.project.master_template.strip():
            messagebox.showwarning(
                t("dialog.warning"), t("dialog.warning.empty_template"),
            )
            return
        warnings = []
        for group in self.project.groups:
            n_inst = len(group.selected_instruments)
            n_files = len(group.files)
            if n_files > 0 and n_inst > 0 and n_files != n_inst:
                warnings.append(
                    t("dialog.mismatch.detail",
                      name=group.name or group.id[:8],
                      n_inst=n_inst, n_files=n_files),
                )
        if warnings:
            msg = t("dialog.mismatch.header") + "\n\n" + "\n".join(warnings)
            msg += "\n\n" + t("dialog.mismatch.footer")
            if not messagebox.askyesno(t("dialog.mismatch"), msg):
                return
        missing = []
        for group in self.project.groups:
            for f in group.files:
                if not os.path.isfile(f.original_path):
                    missing.append(f.display_name)
        if missing:
            msg = t("dialog.missing_files.header", count=len(missing)) + "\n\n"
            msg += "\n".join(missing[:10])
            if len(missing) > 10:
                msg += "\n" + t("dialog.missing_files.more", count=len(missing))
            messagebox.showerror(t("dialog.missing_files"), msg)
            return
        if not self._rename_service:
            from services.rename_service import RenameService
            self._rename_service = RenameService(self.file_service)
        from ui.preview_dialog import PreviewDialog
        plan = self._rename_service.generate_rename_plan(self.project)
        if not plan:
            messagebox.showinfo(t("dialog.info"), t("dialog.info.no_files"))
            return
        conflicts = self._rename_service.detect_conflicts(plan)
        dialog = PreviewDialog(
            self.master_window, plan, conflicts,
            on_execute=lambda p: self._execute_rename(p),
        )
        dialog.grab_set()

    def _execute_rename(self, plan):
        from tkinter import messagebox
        if not self._rename_service:
            return
        long_paths = [e.new_path for e in plan if len(e.new_path) > 255]
        if long_paths:
            msg = t("dialog.long_path.message", count=len(long_paths)) + "\n\n"
            msg += "\n".join(os.path.basename(p) for p in long_paths[:5])
            msg += "\n\n" + t("dialog.long_path.confirm")
            if not messagebox.askyesno(t("dialog.long_path"), msg):
                return
        try:
            record = self._rename_service.execute_rename(plan, self.project)
            if not self._undo_service:
                from services.undo_service import UndoService
                self._undo_service = UndoService(self.file_service)
            self._undo_service.save_undo_record(record)
            self._set_status(t("status.renamed", count=len(record.mappings)))
            messagebox.showinfo(
                t("dialog.complete"),
                t("dialog.complete.renamed", count=len(record.mappings)),
            )
        except PermissionError as e:
            messagebox.showerror(
                t("dialog.error"), t("dialog.error.permission", error=e),
            )
        except OSError as e:
            messagebox.showerror(
                t("dialog.error"), t("dialog.error.rename_failed", error=e),
            )

    def _undo_last(self):
        if not self._undo_service:
            from services.undo_service import UndoService
            self._undo_service = UndoService(self.file_service)
        record = self._undo_service.get_latest_undo_record()
        if not record:
            from tkinter import messagebox
            messagebox.showinfo(t("dialog.info"), t("dialog.info.no_undo"))
            return
        from tkinter import messagebox
        if not messagebox.askyesno(
            t("dialog.confirm_undo"),
            t("dialog.confirm_undo.message", description=record.description),
        ):
            return
        try:
            self._undo_service.execute_undo(record)
            self._set_status(t("status.undone"))
            messagebox.showinfo(t("dialog.complete"), t("dialog.complete.undone"))
        except Exception as e:
            messagebox.showerror(
                t("dialog.error"), t("dialog.error.undo_failed", error=e),
            )

    def _new_project(self):
        if self._modified:
            from tkinter import messagebox
            result = messagebox.askyesnocancel(
                t("dialog.unsaved"), t("dialog.unsaved.message"),
            )
            if result is None:
                return
            if result:
                self._save_project()
        locale = get_locale()
        if locale == "en":
            default_master = DEFAULT_MASTER_TEMPLATE_EN
            default_subfolder = DEFAULT_SUBFOLDER_TEMPLATE_EN
        else:
            default_master = DEFAULT_MASTER_TEMPLATE
            default_subfolder = DEFAULT_SUBFOLDER_TEMPLATE
        self.project = Project()
        self.project.master_template = default_master
        self.project.subfolder_template = default_subfolder
        self._project_path = None
        self._modified = False
        self._instrument_editor.set_instruments([])
        self._master_template_entry.delete(0, "end")
        self._master_template_entry.insert(0, self.project.master_template)
        self._subfolder_var.set(False)
        self._subfolder_template_entry.delete(0, "end")
        self._subfolder_template_entry.insert(0, self.project.subfolder_template)
        if self._group_panel:
            self._group_panel.reload_all()
        self._update_title()

    def _open_project(self):
        if self._modified:
            from tkinter import messagebox
            result = messagebox.askyesnocancel(
                t("dialog.unsaved"), t("dialog.unsaved.message"),
            )
            if result is None:
                return
            if result:
                self._save_project()
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title=t("filedialog.open_project"),
            filetypes=[(t("filedialog.project_files"), "*.llproj")],
        )
        if not path:
            return
        try:
            if not self._project_service:
                from services.project_service import ProjectService
                self._project_service = ProjectService()
            self.project = self._project_service.load_project(path)
            self._project_path = path
            self._modified = False
            self._instrument_editor.set_instruments(self.project.instruments)
            self._master_template_entry.delete(0, "end")
            self._master_template_entry.insert(0, self.project.master_template)
            self._subfolder_var.set(self.project.use_subfolders)
            self._subfolder_template_entry.delete(0, "end")
            self._subfolder_template_entry.insert(0, self.project.subfolder_template)
            if self._group_panel:
                self._group_panel.reload_all()
            self._update_title()
            self._set_status(t("status.opened", path=path))
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror(
                t("dialog.error"), t("dialog.error.open_failed", error=e),
            )

    def _save_project(self):
        if not self._project_path:
            self._save_project_as()
            return
        self._do_save(self._project_path)

    def _save_project_as(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            title=t("filedialog.save_project"),
            defaultextension=".llproj",
            filetypes=[(t("filedialog.project_files"), "*.llproj")],
        )
        if not path:
            return
        self._do_save(path)

    def _do_save(self, path: str):
        try:
            if self._group_panel:
                self._group_panel.sync_to_project()
            if not self._project_service:
                from services.project_service import ProjectService
                self._project_service = ProjectService()
            self._project_service.save_project(self.project, path)
            self._project_path = path
            self._modified = False
            self._update_title()
            self._set_status(t("status.saved", path=path))
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror(
                t("dialog.error"), t("dialog.error.save_failed", error=e),
            )

    def _mark_modified(self):
        if not self._modified:
            self._modified = True
            self._update_title()

    def _update_title(self):
        title = t("app.title")
        if self._project_path:
            title += f" - {os.path.basename(self._project_path)}"
        else:
            title += f" - {t('app.unsaved_project')}"
        if self._modified:
            title += " *"
        self.master_window.title(title)

    def _set_status(self, text: str):
        self._status_label.configure(text=text)

    def set_group_panel(self, panel):
        """設定群組面板參考

        Args:
            panel: GroupPanel 實例
        """
        self._group_panel = panel
        if self._center_placeholder:
            self._center_placeholder.destroy()
            self._center_placeholder = None

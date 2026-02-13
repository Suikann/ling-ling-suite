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
    APP_DISPLAY_NAME,
    DEFAULT_MASTER_TEMPLATE,
    DEFAULT_SUBFOLDER_TEMPLATE,
    TEMPLATE_VARIABLES,
)
from core.models import Project
from services.file_service import FileService
from services.import_service import ImportService
from ui.instrument_list import InstrumentListEditor


class MainWindow(ctk.CTkFrame):
    """應用程式主視窗"""

    def __init__(self, master: ctk.CTk, project: Project, **kwargs):
        super().__init__(master, **kwargs)
        self.master_window: ctk.CTk = master
        self.project = project
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
        menubar = tk.Menu(self.master_window)
        self.master_window.config(menu=menubar)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="新增專案", command=self._new_project, accelerator="Ctrl+N")
        file_menu.add_command(label="開啟專案...", command=self._open_project, accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label="儲存專案", command=self._save_project, accelerator="Ctrl+S")
        file_menu.add_command(label="另存新檔...", command=self._save_project_as)
        menubar.add_cascade(label="檔案", menu=file_menu)
        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="復原上次操作", command=self._undo_last, accelerator="Ctrl+Z")
        menubar.add_cascade(label="編輯", menu=edit_menu)
        import_menu = tk.Menu(menubar, tearoff=0)
        import_menu.add_command(label="匯入檔案...", command=self._import_files)
        import_menu.add_command(label="匯入資料夾...", command=self._import_folder)
        menubar.add_cascade(label="匯入", menu=import_menu)

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
            self._center_panel, text="群組面板（載入中...）",
            font=ctk.CTkFont(size=16),
        )
        self._center_placeholder.pack(expand=True)
        self._create_bottom_panel(right_area)

    def _create_bottom_panel(self, parent):
        bottom = ctk.CTkFrame(parent)
        bottom.pack(fill="x", pady=(4, 0))
        template_row = ctk.CTkFrame(bottom, fg_color="transparent")
        template_row.pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(template_row, text="大模板：").pack(side="left")
        self._master_template_entry = ctk.CTkEntry(template_row, width=400)
        self._master_template_entry.pack(side="left", fill="x", expand=True, padx=(4, 4))
        self._master_template_entry.insert(0, self.project.master_template)
        self._master_template_entry.bind("<KeyRelease>", self._on_master_template_changed)
        vars_btn = ctk.CTkButton(
            template_row, text="插入變數", width=80,
            command=self._show_variable_menu,
        )
        vars_btn.pack(side="right")
        self._vars_button = vars_btn
        subfolder_row = ctk.CTkFrame(bottom, fg_color="transparent")
        subfolder_row.pack(fill="x", padx=8, pady=2)
        self._subfolder_var = ctk.BooleanVar(value=self.project.use_subfolders)
        self._subfolder_check = ctk.CTkCheckBox(
            subfolder_row, text="建立子資料夾",
            variable=self._subfolder_var,
            command=self._on_subfolder_toggled,
        )
        self._subfolder_check.pack(side="left")
        ctk.CTkLabel(subfolder_row, text="  資料夾模板：").pack(side="left")
        self._subfolder_template_entry = ctk.CTkEntry(subfolder_row, width=300)
        self._subfolder_template_entry.pack(side="left", fill="x", expand=True, padx=4)
        self._subfolder_template_entry.insert(0, self.project.subfolder_template)
        self._subfolder_template_entry.bind("<KeyRelease>", self._on_subfolder_template_changed)
        action_row = ctk.CTkFrame(bottom, fg_color="transparent")
        action_row.pack(fill="x", padx=8, pady=(4, 8))
        self._preview_btn = ctk.CTkButton(
            action_row, text="預覽並重新命名",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=36, command=self._preview_and_rename,
        )
        self._preview_btn.pack(side="right")
        self._status_label = ctk.CTkLabel(action_row, text="就緒", anchor="w")
        self._status_label.pack(side="left", fill="x", expand=True)

    def _show_variable_menu(self):
        menu = tk.Menu(self.master_window, tearoff=0)
        for var in TEMPLATE_VARIABLES:
            menu.add_command(
                label=f"{{{var.name}}} - {var.description}",
                command=lambda v=var.name: self._insert_variable(v),
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
            title="選擇 PDF 檔案",
            filetypes=[("PDF 檔案", "*.pdf")],
        )
        if not paths:
            return
        files = self.import_service.import_files(list(paths))
        self.project.ungrouped_files.extend(files)
        self._mark_modified()
        if self._group_panel:
            self._group_panel.refresh_ungrouped()
        self._set_status(f"已匯入 {len(files)} 個檔案")

    def _import_folder(self):
        from tkinter import filedialog
        folder = filedialog.askdirectory(title="選擇資料夾")
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
            f"已匯入 {len(groups)} 個群組，{len(ungrouped)} 個未分組檔案"
        )

    def _preview_and_rename(self):
        from tkinter import messagebox
        if self._group_panel:
            self._group_panel.sync_to_project()
        if not self.project.master_template.strip():
            messagebox.showwarning("警告", "大模板為空，請先設定模板。")
            return
        warnings = []
        for group in self.project.groups:
            n_inst = len(group.selected_instruments)
            n_files = len(group.files)
            if n_files > 0 and n_inst > 0 and n_files != n_inst:
                warnings.append(
                    f"「{group.name or group.id[:8]}」：{n_inst} 個樂器 / {n_files} 個檔案不匹配"
                )
        if warnings:
            msg = "以下群組的樂器與檔案數量不匹配：\n\n" + "\n".join(warnings)
            msg += "\n\n不匹配的群組將被跳過。是否繼續？"
            if not messagebox.askyesno("數量不匹配", msg):
                return
        missing = []
        for group in self.project.groups:
            for f in group.files:
                if not os.path.isfile(f.original_path):
                    missing.append(f.display_name)
        if missing:
            msg = f"以下 {len(missing)} 個檔案不存在：\n\n"
            msg += "\n".join(missing[:10])
            if len(missing) > 10:
                msg += f"\n...等共 {len(missing)} 個"
            messagebox.showerror("檔案不存在", msg)
            return
        if not self._rename_service:
            from services.rename_service import RenameService
            self._rename_service = RenameService(self.file_service)
        from ui.preview_dialog import PreviewDialog
        plan = self._rename_service.generate_rename_plan(self.project)
        if not plan:
            messagebox.showinfo("提示", "沒有需要重新命名的檔案。")
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
            msg = f"以下 {len(long_paths)} 個路徑超過 255 字元，可能導致錯誤：\n\n"
            msg += "\n".join(os.path.basename(p) for p in long_paths[:5])
            if not messagebox.askyesno("路徑過長警告", msg + "\n\n是否繼續？"):
                return
        try:
            record = self._rename_service.execute_rename(plan, self.project)
            if not self._undo_service:
                from services.undo_service import UndoService
                self._undo_service = UndoService(self.file_service)
            self._undo_service.save_undo_record(record)
            self._set_status(f"已重新命名 {len(record.mappings)} 個檔案")
            messagebox.showinfo("完成", f"已成功重新命名 {len(record.mappings)} 個檔案。")
        except PermissionError as e:
            messagebox.showerror("權限錯誤", f"沒有足夠的權限重新命名檔案：\n{e}")
        except OSError as e:
            messagebox.showerror("錯誤", f"重新命名失敗：\n{e}")

    def _undo_last(self):
        if not self._undo_service:
            from services.undo_service import UndoService
            self._undo_service = UndoService(self.file_service)
        record = self._undo_service.get_latest_undo_record()
        if not record:
            from tkinter import messagebox
            messagebox.showinfo("提示", "沒有可復原的操作。")
            return
        from tkinter import messagebox
        if not messagebox.askyesno("確認復原", f"是否復原上次操作？\n{record.description}"):
            return
        try:
            self._undo_service.execute_undo(record)
            self._set_status("已復原上次操作")
            messagebox.showinfo("完成", "已成功復原上次操作。")
        except Exception as e:
            messagebox.showerror("錯誤", f"復原失敗：\n{e}")

    def _new_project(self):
        if self._modified:
            from tkinter import messagebox
            result = messagebox.askyesnocancel("未儲存的變更", "是否儲存目前的專案？")
            if result is None:
                return
            if result:
                self._save_project()
        self.project = Project()
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
            result = messagebox.askyesnocancel("未儲存的變更", "是否儲存目前的專案？")
            if result is None:
                return
            if result:
                self._save_project()
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="開啟專案",
            filetypes=[("泠靈專案檔", "*.llproj")],
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
            self._set_status(f"已開啟專案：{path}")
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("錯誤", f"無法開啟專案：\n{e}")

    def _save_project(self):
        if not self._project_path:
            self._save_project_as()
            return
        self._do_save(self._project_path)

    def _save_project_as(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            title="儲存專案",
            defaultextension=".llproj",
            filetypes=[("泠靈專案檔", "*.llproj")],
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
            self._set_status(f"已儲存專案：{path}")
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("錯誤", f"儲存失敗：\n{e}")

    def _mark_modified(self):
        if not self._modified:
            self._modified = True
            self._update_title()

    def _update_title(self):
        title = APP_DISPLAY_NAME
        if self._project_path:
            import os
            title += f" - {os.path.basename(self._project_path)}"
        else:
            title += " - 未儲存的專案"
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

# -*- coding: utf-8 -*-
"""
群組管理面板

提供群組標籤管理、樂器勾選與群組變數輸入。
"""
from typing import List, Optional, TYPE_CHECKING
import customtkinter as ctk
from core.locale import t
from core.models import Group, Project, FileInfo
from core.template_engine import detect_piece_name

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class GroupPanel(ctk.CTkFrame):
    """群組管理面板，使用 CTkTabview 管理多個群組標籤"""

    def __init__(
        self,
        master,
        project: Project,
        main_window: "MainWindow",
        **kwargs,
    ):
        super().__init__(master, **kwargs)
        self.project = project
        self.main_window = main_window
        self._tab_contents = {}
        self._ungrouped_tab_name = t("group.ungrouped")
        self._build_ui()

    def _build_ui(self):
        top_bar = ctk.CTkFrame(self, fg_color="transparent")
        top_bar.pack(fill="x", padx=4, pady=(4, 0))
        add_btn = ctk.CTkButton(
            top_bar, text=t("group.add"), width=100,
            command=self._add_group,
        )
        add_btn.pack(side="right")
        self._tabview = ctk.CTkTabview(self, anchor="nw")
        self._tabview.pack(fill="both", expand=True, padx=4, pady=4)
        self._tabview.add(self._ungrouped_tab_name)
        ungrouped_content = UngroupedTabContent(
            self._tabview.tab(self._ungrouped_tab_name),
            self.project,
            self.main_window,
        )
        ungrouped_content.pack(fill="both", expand=True)
        self._tab_contents[self._ungrouped_tab_name] = ungrouped_content
        for group in self.project.groups:
            self._create_group_tab(group)

    def _add_group(self):
        group = Group(name=t("group.new_name", number=len(self.project.groups) + 1))
        self.project.groups.append(group)
        self._create_group_tab(group)
        self.main_window._mark_modified()

    def _create_group_tab(self, group: Group):
        tab_name = group.name or group.id[:8]
        if tab_name in [self._tabview._name_list[i] for i in range(len(self._tabview._name_list))] if hasattr(self._tabview, '_name_list') else False:
            tab_name = f"{tab_name} ({group.id[:4]})"
        self._tabview.add(tab_name)
        content = GroupTabContent(
            self._tabview.tab(tab_name),
            group,
            self.project,
            self.main_window,
            on_delete=lambda g=group, t_name=tab_name: self._delete_group(g, t_name),
        )
        content.pack(fill="both", expand=True)
        self._tab_contents[tab_name] = content
        self._tabview.set(tab_name)

    def _delete_group(self, group: Group, tab_name: str):
        from tkinter import messagebox
        if not messagebox.askyesno(
            t("dialog.delete_group"),
            t("dialog.delete_group.message", name=group.name),
        ):
            return
        self.project.ungrouped_files.extend(group.files)
        if group in self.project.groups:
            self.project.groups.remove(group)
        if tab_name in self._tab_contents:
            del self._tab_contents[tab_name]
        self._tabview.delete(tab_name)
        self._tabview.set(self._ungrouped_tab_name)
        self.refresh_ungrouped()
        self.main_window._mark_modified()

    def on_instruments_changed(self, instruments: List[str]):
        """樂器表變更時更新所有群組的勾選框"""
        for name, content in self._tab_contents.items():
            if hasattr(content, 'on_instruments_changed'):
                content.on_instruments_changed(instruments)

    def refresh_ungrouped(self):
        """重新整理未分組標籤"""
        if self._ungrouped_tab_name in self._tab_contents:
            self._tab_contents[self._ungrouped_tab_name].refresh()

    def reload_all(self):
        """重新載入所有標籤（用於專案開啟或重設）"""
        for name in list(self._tab_contents.keys()):
            if name != self._ungrouped_tab_name:
                self._tabview.delete(name)
        self._tab_contents = {}
        self._tabview.delete(self._ungrouped_tab_name)
        self._ungrouped_tab_name = t("group.ungrouped")
        self._tabview.add(self._ungrouped_tab_name)
        ungrouped_content = UngroupedTabContent(
            self._tabview.tab(self._ungrouped_tab_name),
            self.project,
            self.main_window,
        )
        ungrouped_content.pack(fill="both", expand=True)
        self._tab_contents[self._ungrouped_tab_name] = ungrouped_content
        for group in self.project.groups:
            self._create_group_tab(group)

    def sync_to_project(self):
        """將所有面板的目前狀態同步至 project 資料"""
        for name, content in self._tab_contents.items():
            if hasattr(content, 'sync_to_group'):
                content.sync_to_group()


class UngroupedTabContent(ctk.CTkFrame):
    """未分組標籤內容"""

    def __init__(self, master, project: Project, main_window: "MainWindow", **kwargs):
        super().__init__(master, **kwargs)
        self.project = project
        self.main_window = main_window
        self._build_ui()

    def _build_ui(self):
        self._scroll = ctk.CTkScrollableFrame(self)
        self._scroll.pack(fill="both", expand=True, padx=4, pady=4)
        self._refresh_list()

    def _refresh_list(self):
        for widget in self._scroll.winfo_children():
            widget.destroy()
        if not self.project.ungrouped_files:
            ctk.CTkLabel(
                self._scroll, text=t("ungrouped.empty"),
                font=ctk.CTkFont(size=13), text_color="gray",
            ).pack(expand=True, pady=40)
            return
        for i, file_info in enumerate(self.project.ungrouped_files):
            row = ctk.CTkFrame(self._scroll, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(row, text=file_info.display_name, anchor="w").pack(
                side="left", fill="x", expand=True, padx=4,
            )
            move_btn = ctk.CTkButton(
                row, text=t("group.move_to_group"), width=90,
                command=lambda idx=i: self._move_to_group(idx),
            )
            move_btn.pack(side="right", padx=2)
            del_btn = ctk.CTkButton(
                row, text="\u2715", width=28, height=24,
                fg_color="#c0392b", hover_color="#e74c3c",
                command=lambda idx=i: self._remove_file(idx),
            )
            del_btn.pack(side="right", padx=2)

    def _move_to_group(self, file_index: int):
        if not self.project.groups:
            from tkinter import messagebox
            messagebox.showinfo(t("dialog.info"), t("dialog.info.create_group_first"))
            return
        file_info = self.project.ungrouped_files[file_index]
        menu = __import__('tkinter').Menu(self, tearoff=0)
        for group in self.project.groups:
            menu.add_command(
                label=group.name or group.id[:8],
                command=lambda g=group, fi=file_info, idx=file_index: self._do_move(fi, g, idx),
            )
        widget = self._scroll.winfo_children()[file_index] if file_index < len(self._scroll.winfo_children()) else self._scroll
        menu.tk_popup(widget.winfo_rootx(), widget.winfo_rooty())

    def _do_move(self, file_info: FileInfo, group: Group, index: int):
        if 0 <= index < len(self.project.ungrouped_files):
            self.project.ungrouped_files.pop(index)
        group.files.append(file_info)
        self._refresh_list()
        self.main_window._mark_modified()
        group_panel = self.main_window._group_panel
        if group_panel:
            for name, content in group_panel._tab_contents.items():
                if hasattr(content, '_group') and content._group is group:
                    content.refresh_file_list()

    def _remove_file(self, index: int):
        if 0 <= index < len(self.project.ungrouped_files):
            self.project.ungrouped_files.pop(index)
            self._refresh_list()
            self.main_window._mark_modified()

    def refresh(self):
        """重新整理顯示"""
        self._refresh_list()

    def on_instruments_changed(self, instruments: List[str]):
        pass


class GroupTabContent(ctk.CTkFrame):
    """群組標籤內容"""

    def __init__(
        self,
        master,
        group: Group,
        project: Project,
        main_window: "MainWindow",
        on_delete=None,
        **kwargs,
    ):
        super().__init__(master, **kwargs)
        self._group = group
        self.project = project
        self.main_window = main_window
        self._on_delete = on_delete
        self._instrument_vars = []
        self._file_list_widget = None
        self._build_ui()

    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(8, 4))
        del_btn = ctk.CTkButton(
            top, text=t("group.delete"), width=100,
            fg_color="#c0392b", hover_color="#e74c3c",
            command=self._on_delete,
        )
        del_btn.pack(side="right")
        name_frame = ctk.CTkFrame(top, fg_color="transparent")
        name_frame.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(name_frame, text=t("group.name_label")).pack(side="left")
        self._name_entry = ctk.CTkEntry(name_frame, width=200)
        self._name_entry.pack(side="left", padx=4)
        self._name_entry.insert(0, self._group.name)
        vars_frame = ctk.CTkFrame(self, fg_color="transparent")
        vars_frame.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(vars_frame, text=t("group.piece_name_label")).pack(side="left")
        self._piece_name_entry = ctk.CTkEntry(vars_frame, width=200)
        self._piece_name_entry.pack(side="left", padx=(4, 8))
        self._piece_name_entry.insert(0, self._group.piece_name)
        auto_btn = ctk.CTkButton(
            vars_frame, text=t("group.auto_detect"), width=80,
            command=self._auto_detect_piece_name,
        )
        auto_btn.pack(side="left", padx=(0, 16))
        ctk.CTkLabel(vars_frame, text=t("group.movement_num_label")).pack(side="left")
        self._movement_num_entry = ctk.CTkEntry(vars_frame, width=60)
        self._movement_num_entry.pack(side="left", padx=(4, 8))
        self._movement_num_entry.insert(0, self._group.movement_number)
        ctk.CTkLabel(vars_frame, text=t("group.movement_name_label")).pack(side="left")
        self._movement_name_entry = ctk.CTkEntry(vars_frame, width=150)
        self._movement_name_entry.pack(side="left", padx=4)
        self._movement_name_entry.insert(0, self._group.movement_name)
        middle = ctk.CTkFrame(self, fg_color="transparent")
        middle.pack(fill="both", expand=True, padx=8, pady=4)
        left_col = ctk.CTkFrame(middle)
        left_col.pack(side="left", fill="both", expand=False, padx=(0, 4))
        ctk.CTkLabel(left_col, text=t("group.instrument_check"), font=ctk.CTkFont(weight="bold")).pack(pady=(4, 2))
        self._instrument_scroll = ctk.CTkScrollableFrame(left_col, width=180)
        self._instrument_scroll.pack(fill="both", expand=True, padx=4, pady=4)
        self._mismatch_label = ctk.CTkLabel(
            left_col, text="", text_color="#e74c3c",
            font=ctk.CTkFont(size=12),
        )
        self._mismatch_label.pack(padx=4, pady=2)
        right_col = ctk.CTkFrame(middle)
        right_col.pack(side="left", fill="both", expand=True, padx=(4, 0))
        ctk.CTkLabel(right_col, text=t("group.file_list"), font=ctk.CTkFont(weight="bold")).pack(pady=(4, 2))
        self._file_scroll = ctk.CTkScrollableFrame(right_col)
        self._file_scroll.pack(fill="both", expand=True, padx=4, pady=4)
        file_btn_row = ctk.CTkFrame(right_col, fg_color="transparent")
        file_btn_row.pack(fill="x", padx=4, pady=4)
        ctk.CTkButton(
            file_btn_row, text=t("group.add_files"), width=100,
            command=self._add_files,
        ).pack(side="left")
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=8, pady=(4, 8))
        self._small_template_var = ctk.BooleanVar(value=self._group.use_small_template)
        self._small_template_check = ctk.CTkCheckBox(
            bottom, text=t("group.use_small_template"),
            variable=self._small_template_var,
            command=self._on_small_template_toggled,
        )
        self._small_template_check.pack(side="left")
        self._small_template_entry = ctk.CTkEntry(bottom, width=400)
        self._small_template_entry.pack(side="left", fill="x", expand=True, padx=8)
        if self._group.small_template:
            self._small_template_entry.insert(0, self._group.small_template)
        self._small_template_entry.configure(
            state="normal" if self._group.use_small_template else "disabled",
        )
        self._refresh_instruments()
        self._refresh_file_list()
        self._auto_detect_if_empty()

    def _auto_detect_if_empty(self):
        """曲名欄位為空時自動偵測一次"""
        if self._piece_name_entry.get().strip():
            return
        if not self._group.files:
            return
        filenames = [f.display_name for f in self._group.files]
        detected = detect_piece_name(filenames)
        if detected:
            self._piece_name_entry.delete(0, "end")
            self._piece_name_entry.insert(0, detected)
            self._group.piece_name = detected

    def _refresh_instruments(self):
        for widget in self._instrument_scroll.winfo_children():
            widget.destroy()
        self._instrument_vars = []
        instruments = self.project.instruments
        if not instruments:
            ctk.CTkLabel(
                self._instrument_scroll, text=t("group.no_instruments"),
                text_color="gray",
            ).pack(pady=8)
            return
        for i, name in enumerate(instruments):
            var = ctk.BooleanVar(value=(i in self._group.selected_instruments))
            cb = ctk.CTkCheckBox(
                self._instrument_scroll, text=name,
                variable=var,
                command=self._on_instrument_check_changed,
            )
            cb.pack(anchor="w", padx=4, pady=1)
            self._instrument_vars.append(var)
        self._check_mismatch()

    def _on_instrument_check_changed(self):
        self._group.selected_instruments = [
            i for i, var in enumerate(self._instrument_vars) if var.get()
        ]
        self._check_mismatch()
        self._refresh_file_list()
        self.main_window._mark_modified()

    def _check_mismatch(self):
        n_instruments = len(self._group.selected_instruments)
        n_files = len(self._group.files)
        if n_files == 0 and n_instruments == 0:
            self._mismatch_label.configure(text="")
        elif n_files != n_instruments:
            self._mismatch_label.configure(
                text=t("group.mismatch", n_inst=n_instruments, n_files=n_files),
            )
        else:
            self._mismatch_label.configure(
                text=t("group.match", count=n_instruments),
            )
            self._mismatch_label.configure(text_color=("green", "#2ecc71"))

    def _refresh_file_list(self):
        for widget in self._file_scroll.winfo_children():
            widget.destroy()
        if not self._group.files:
            ctk.CTkLabel(
                self._file_scroll, text=t("file_list.empty"), text_color="gray",
            ).pack(pady=8)
            return
        instruments = self.project.instruments
        selected = self._group.selected_instruments
        for i, file_info in enumerate(self._group.files):
            row = ctk.CTkFrame(self._file_scroll, fg_color="transparent")
            row.pack(fill="x", pady=1)
            if i < len(selected) and selected[i] < len(instruments):
                inst_text = instruments[selected[i]]
                ctk.CTkLabel(
                    row, text=inst_text, width=100, anchor="w",
                    font=ctk.CTkFont(size=11), text_color=("gray40", "gray60"),
                ).pack(side="left", padx=(4, 2))
            ctk.CTkLabel(row, text=file_info.display_name, anchor="w").pack(
                side="left", fill="x", expand=True, padx=2,
            )
            btn_frame = ctk.CTkFrame(row, fg_color="transparent")
            btn_frame.pack(side="right")
            ctk.CTkButton(
                btn_frame, text="\u25B2", width=28, height=24,
                command=lambda idx=i: self._move_file_up(idx),
            ).pack(side="left", padx=1)
            ctk.CTkButton(
                btn_frame, text="\u25BC", width=28, height=24,
                command=lambda idx=i: self._move_file_down(idx),
            ).pack(side="left", padx=1)
            ctk.CTkButton(
                btn_frame, text="\u2715", width=28, height=24,
                fg_color="#c0392b", hover_color="#e74c3c",
                command=lambda idx=i: self._remove_file(idx),
            ).pack(side="left", padx=1)

    def _move_file_up(self, index: int):
        if index <= 0:
            return
        files = self._group.files
        files[index], files[index - 1] = files[index - 1], files[index]
        self._refresh_file_list()
        self.main_window._mark_modified()

    def _move_file_down(self, index: int):
        files = self._group.files
        if index >= len(files) - 1:
            return
        files[index], files[index + 1] = files[index + 1], files[index]
        self._refresh_file_list()
        self.main_window._mark_modified()

    def _remove_file(self, index: int):
        if 0 <= index < len(self._group.files):
            removed = self._group.files.pop(index)
            self.project.ungrouped_files.append(removed)
            self._refresh_file_list()
            self._check_mismatch()
            self.main_window._mark_modified()
            group_panel = self.main_window._group_panel
            if group_panel:
                group_panel.refresh_ungrouped()

    def _add_files(self):
        from tkinter import filedialog
        paths = filedialog.askopenfilenames(
            title=t("filedialog.select_pdf"),
            filetypes=[(t("filedialog.pdf_files"), "*.pdf")],
        )
        if not paths:
            return
        from services.import_service import ImportService
        from services.file_service import FileService
        import_svc = ImportService(FileService())
        files = import_svc.import_files(list(paths))
        self._group.files.extend(files)
        self._refresh_file_list()
        self._check_mismatch()
        self._auto_detect_if_empty()
        self.main_window._mark_modified()

    def _auto_detect_piece_name(self):
        filenames = [f.display_name for f in self._group.files]
        detected = detect_piece_name(filenames)
        if detected:
            self._piece_name_entry.delete(0, "end")
            self._piece_name_entry.insert(0, detected)
            self.main_window._mark_modified()
        else:
            from tkinter import messagebox
            messagebox.showinfo(t("dialog.info"), t("dialog.info.cannot_detect"))

    def _on_small_template_toggled(self):
        enabled = self._small_template_var.get()
        self._group.use_small_template = enabled
        if enabled:
            self._small_template_entry.configure(state="normal")
            if not self._small_template_entry.get():
                self._small_template_entry.insert(0, self.project.master_template)
        else:
            self._small_template_entry.configure(state="disabled")
        self.main_window._mark_modified()

    def on_instruments_changed(self, instruments: List[str]):
        """樂器表變更時重新建立勾選框"""
        valid = set(range(len(instruments)))
        self._group.selected_instruments = [
            i for i in self._group.selected_instruments if i in valid
        ]
        self._refresh_instruments()
        self._refresh_file_list()

    def refresh_file_list(self):
        """從外部觸發檔案清單重新整理"""
        self._refresh_file_list()
        self._check_mismatch()

    def sync_to_group(self):
        """將 UI 狀態同步至 group 資料物件"""
        self._group.name = self._name_entry.get().strip()
        self._group.piece_name = self._piece_name_entry.get().strip()
        self._group.movement_number = self._movement_num_entry.get().strip()
        self._group.movement_name = self._movement_name_entry.get().strip()
        self._group.use_small_template = self._small_template_var.get()
        if self._group.use_small_template:
            self._group.small_template = self._small_template_entry.get()
        self._group.selected_instruments = [
            i for i, var in enumerate(self._instrument_vars) if var.get()
        ]

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
        self._tabview = ctk.CTkTabview(self, anchor="nw")
        self._tabview.pack(fill="both", expand=True, padx=4, pady=4)
        # 工作區內的操作按鈕列
        btn_bar = ctk.CTkFrame(self, fg_color="transparent")
        btn_bar.pack(fill="x", padx=8, pady=(0, 4))
        add_btn = ctk.CTkButton(
            btn_bar, text=t("group.add"), width=100,
            command=self._add_group,
        )
        add_btn.pack(side="left")
        link_btn = ctk.CTkButton(
            btn_bar, text=t("group.link_movements"), width=140,
            command=self._link_as_movements,
        )
        link_btn.pack(side="left", padx=(8, 0))
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

    def _link_as_movements(self):
        """將多個群組連結為同一曲目的不同樂章"""
        if len(self.project.groups) < 2:
            from tkinter import messagebox
            messagebox.showinfo(t("dialog.info"), t("group.link_movements.need_two"))
            return
        from ui.merge_dialog import LinkMovementsDialog
        LinkMovementsDialog(
            self.winfo_toplevel(),
            self.project.groups,
            on_confirm=self._on_link_confirmed,
        )

    def _on_link_confirmed(self, selected_groups: list, piece_name: str):
        """連結確認後更新群組資料"""
        for i, group in enumerate(selected_groups):
            group.piece_name = piece_name
            group.movement_number = str(i + 1)
            if not group.movement_name:
                group.movement_name = group.name
        self.reload_all()
        self.main_window._mark_modified()
        self.main_window._set_status(
            t("group.link_movements.done", count=len(selected_groups), piece=piece_name),
        )

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
    """未分組標籤內容（支援多選批次操作）"""

    def __init__(self, master, project: Project, main_window: "MainWindow", **kwargs):
        super().__init__(master, **kwargs)
        self.project = project
        self.main_window = main_window
        self._check_vars: List = []
        self._build_ui()

    def _build_ui(self):
        action_bar = ctk.CTkFrame(self, fg_color="transparent")
        action_bar.pack(fill="x", padx=4, pady=(4, 2))
        self._select_all_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            action_bar, text=t("ungrouped.select_all"),
            variable=self._select_all_var,
            command=self._toggle_select_all,
        ).pack(side="left")
        self._move_btn = ctk.CTkButton(
            action_bar, text=t("ungrouped.move_selected"),
            width=120, command=self._move_selected,
        )
        self._move_btn.pack(side="left", padx=(8, 4))
        ctk.CTkButton(
            action_bar, text=t("ungrouped.new_group_from_selected"),
            width=160, command=self._new_group_from_selected,
        ).pack(side="left", padx=4)
        self._scroll = ctk.CTkScrollableFrame(self)
        self._scroll.pack(fill="both", expand=True, padx=4, pady=4)
        self._refresh_list()

    def _refresh_list(self):
        try:
            pack_info = self._scroll.pack_info()
            self._scroll.pack_forget()
        except Exception:
            pack_info = None
        for widget in self._scroll.winfo_children():
            widget.destroy()
        self._check_vars = []
        self._select_all_var.set(False)
        if not self.project.ungrouped_files:
            ctk.CTkLabel(
                self._scroll, text=t("ungrouped.empty"),
                font=ctk.CTkFont(size=13), text_color="gray",
            ).pack(expand=True, pady=40)
        else:
            for i, file_info in enumerate(self.project.ungrouped_files):
                self._create_ungrouped_row(i, file_info)
        if pack_info:
            self._scroll.pack(**pack_info)

    def _create_ungrouped_row(self, index: int, file_info: FileInfo):
        row = ctk.CTkFrame(self._scroll, fg_color="transparent")
        row._idx = index
        row.pack(fill="x", pady=1)
        var = ctk.BooleanVar(value=False)
        self._check_vars.append(var)
        ctk.CTkCheckBox(
            row, text="", variable=var, width=24,
        ).pack(side="left", padx=(4, 2))
        ctk.CTkLabel(row, text=file_info.display_name, anchor="w").pack(
            side="left", fill="x", expand=True, padx=2,
        )
        ctk.CTkButton(
            row, text="\u00D7", width=28, height=28,
            fg_color="#c0392b", hover_color="#e74c3c",
            command=lambda r=row: self._remove_file(r._idx),
        ).pack(side="right", padx=2)

    def _toggle_select_all(self):
        val = self._select_all_var.get()
        for var in self._check_vars:
            var.set(val)

    def _get_selected_indices(self) -> List[int]:
        return [i for i, var in enumerate(self._check_vars) if var.get()]

    def _move_selected(self):
        selected = self._get_selected_indices()
        if not selected:
            return
        if not self.project.groups:
            from tkinter import messagebox
            messagebox.showinfo(t("dialog.info"), t("dialog.info.create_group_first"))
            return
        import tkinter as tk
        menu = tk.Menu(self, tearoff=0)
        for group in self.project.groups:
            menu.add_command(
                label=group.name or group.id[:8],
                command=lambda g=group: self._do_batch_move(g),
            )
        btn = self._move_btn
        menu.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height())

    def _do_batch_move(self, group: Group):
        selected = self._get_selected_indices()
        files_to_move = [self.project.ungrouped_files[i] for i in selected]
        for i in sorted(selected, reverse=True):
            self.project.ungrouped_files.pop(i)
        group.files.extend(files_to_move)
        self._refresh_list()
        self.main_window._mark_modified()
        group_panel = self.main_window._group_panel
        if group_panel:
            for name, content in group_panel._tab_contents.items():
                if hasattr(content, '_group') and content._group is group:
                    content.refresh_file_list()

    def _new_group_from_selected(self):
        selected = self._get_selected_indices()
        if not selected:
            return
        files_to_move = [self.project.ungrouped_files[i] for i in selected]
        for i in sorted(selected, reverse=True):
            self.project.ungrouped_files.pop(i)
        new_group = Group(
            name=t("group.new_name", number=len(self.project.groups) + 1),
            files=files_to_move,
        )
        self.project.groups.append(new_group)
        self._refresh_list()
        self.main_window._mark_modified()
        group_panel = self.main_window._group_panel
        if group_panel:
            group_panel._create_group_tab(new_group)

    def _remove_file(self, index: int):
        if 0 <= index < len(self.project.ungrouped_files):
            self.project.ungrouped_files.pop(index)
            if index < len(self._check_vars):
                self._check_vars.pop(index)
            rows = self._scroll.winfo_children()
            if index < len(rows):
                rows[index].destroy()
            if not self.project.ungrouped_files:
                self._refresh_list()
            else:
                for i, row in enumerate(self._scroll.winfo_children()):
                    row._idx = i
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
        score_frame = ctk.CTkFrame(right_col, fg_color="transparent")
        score_frame.pack(fill="x", padx=4, pady=(4, 2))
        ctk.CTkLabel(
            score_frame, text=t("group.score_file"),
            font=ctk.CTkFont(weight="bold"),
        ).pack(side="left")
        self._score_label = ctk.CTkLabel(
            score_frame, text=t("group.score_file.none"),
            text_color="gray", anchor="w",
        )
        self._score_label.pack(side="left", fill="x", expand=True, padx=4)
        ctk.CTkButton(
            score_frame, text=t("group.score_file.clear"), width=50, height=24,
            fg_color=("gray75", "gray35"), hover_color=("gray65", "gray45"),
            command=self._clear_score_file,
        ).pack(side="right", padx=1)
        ctk.CTkButton(
            score_frame, text=t("group.score_file.set"), width=80, height=24,
            command=self._set_score_file,
        ).pack(side="right", padx=1)
        self._update_score_display()
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

    def _update_score_display(self):
        """更新總譜顯示"""
        if self._group.score_file:
            self._score_label.configure(
                text=self._group.score_file.display_name,
                text_color=("black", "white"),
            )
        else:
            self._score_label.configure(
                text=t("group.score_file.none"),
                text_color="gray",
            )

    def _set_score_file(self):
        """從檔案清單中指定一個檔案為總譜"""
        if not self._group.files:
            return
        import tkinter as tk
        menu = tk.Menu(self, tearoff=0)
        for i, f in enumerate(self._group.files):
            menu.add_command(
                label=f.display_name,
                command=lambda idx=i: self._do_set_score(idx),
            )
        btn = self._score_label
        menu.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height())

    def _do_set_score(self, file_index: int):
        """將指定檔案設為總譜"""
        if self._group.score_file:
            self._group.files.append(self._group.score_file)
        if 0 <= file_index < len(self._group.files):
            self._group.score_file = self._group.files.pop(file_index)
        self._update_score_display()
        self._refresh_file_list()
        self._check_mismatch()
        self.main_window._mark_modified()

    def _clear_score_file(self):
        """清除總譜指定，將檔案放回清單"""
        if self._group.score_file:
            self._group.files.insert(0, self._group.score_file)
            self._group.score_file = None
            self._update_score_display()
            self._refresh_file_list()
            self._check_mismatch()
            self.main_window._mark_modified()

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
        try:
            pack_info = self._instrument_scroll.pack_info()
            self._instrument_scroll.pack_forget()
        except Exception:
            pack_info = None
        for widget in self._instrument_scroll.winfo_children():
            widget.destroy()
        self._instrument_vars = []
        instruments = self.project.instruments
        if not instruments:
            ctk.CTkLabel(
                self._instrument_scroll, text=t("group.no_instruments"),
                text_color="gray",
            ).pack(pady=8)
        else:
            for i, name in enumerate(instruments):
                var = ctk.BooleanVar(value=(i in self._group.selected_instruments))
                cb = ctk.CTkCheckBox(
                    self._instrument_scroll, text=name,
                    variable=var,
                    command=self._on_instrument_check_changed,
                )
                cb.pack(anchor="w", padx=4, pady=1)
                self._instrument_vars.append(var)
        if pack_info:
            self._instrument_scroll.pack(**pack_info)
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
        try:
            pack_info = self._file_scroll.pack_info()
            self._file_scroll.pack_forget()
        except Exception:
            pack_info = None
        for widget in self._file_scroll.winfo_children():
            widget.destroy()
        if not self._group.files:
            ctk.CTkLabel(
                self._file_scroll, text=t("file_list.empty"), text_color="gray",
            ).pack(pady=8)
        else:
            instruments = self.project.instruments
            selected = self._group.selected_instruments
            for i, file_info in enumerate(self._group.files):
                self._create_file_row(i, file_info, instruments, selected)
        if pack_info:
            self._file_scroll.pack(**pack_info)

    def _create_file_row(self, index, file_info, instruments, selected):
        row = ctk.CTkFrame(self._file_scroll, fg_color="transparent")
        row._idx = index
        row.pack(fill="x", pady=1)
        if index < len(selected) and selected[index] < len(instruments):
            inst_text = instruments[selected[index]]
            ctk.CTkLabel(
                row, text=inst_text, width=100, anchor="w",
                font=ctk.CTkFont(size=11), text_color=("gray40", "gray60"),
            ).pack(side="left", padx=(4, 2))
        file_label = ctk.CTkLabel(row, text=file_info.display_name, anchor="w")
        file_label.pack(side="left", fill="x", expand=True, padx=2)
        row._file_label = file_label
        btn_frame = ctk.CTkFrame(row, fg_color="transparent")
        btn_frame.pack(side="right")
        ctk.CTkButton(
            btn_frame, text="\u2191", width=28, height=28,
            command=lambda r=row: self._move_file_up(r._idx),
        ).pack(side="left", padx=1)
        ctk.CTkButton(
            btn_frame, text="\u2193", width=28, height=28,
            command=lambda r=row: self._move_file_down(r._idx),
        ).pack(side="left", padx=1)
        ctk.CTkButton(
            btn_frame, text="\u2190", width=28, height=28,
            fg_color=("gray75", "gray35"),
            hover_color=("gray65", "gray45"),
            command=lambda r=row: self._remove_file(r._idx),
        ).pack(side="left", padx=1)

    def _move_file_up(self, index: int):
        if index <= 0:
            return
        files = self._group.files
        files[index], files[index - 1] = files[index - 1], files[index]
        rows = self._file_scroll.winfo_children()
        if index < len(rows) and index - 1 < len(rows):
            a, b = rows[index]._file_label, rows[index - 1]._file_label
            ta, tb = a.cget("text"), b.cget("text")
            a.configure(text=tb)
            b.configure(text=ta)
        self.main_window._mark_modified()

    def _move_file_down(self, index: int):
        files = self._group.files
        if index >= len(files) - 1:
            return
        files[index], files[index + 1] = files[index + 1], files[index]
        rows = self._file_scroll.winfo_children()
        if index < len(rows) and index + 1 < len(rows):
            a, b = rows[index]._file_label, rows[index + 1]._file_label
            ta, tb = a.cget("text"), b.cget("text")
            a.configure(text=tb)
            b.configure(text=ta)
        self.main_window._mark_modified()

    def _remove_file(self, index: int):
        if 0 <= index < len(self._group.files):
            removed = self._group.files.pop(index)
            self.project.ungrouped_files.append(removed)
            rows = self._file_scroll.winfo_children()
            if index < len(rows):
                rows[index].destroy()
            if not self._group.files:
                self._refresh_file_list()
            else:
                for i, row in enumerate(self._file_scroll.winfo_children()):
                    row._idx = i
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

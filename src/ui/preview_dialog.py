# -*- coding: utf-8 -*-
"""
預覽對話框

顯示重新命名計畫的預覽，包含衝突警告。
"""
import os
from typing import Callable, Dict, List, Optional
import customtkinter as ctk
from core.models import RenameEntry


class PreviewDialog(ctk.CTkToplevel):
    """重新命名預覽對話框"""

    def __init__(
        self,
        master,
        plan: List[RenameEntry],
        conflicts: Dict[str, List[str]],
        on_execute: Optional[Callable[[List[RenameEntry]], None]] = None,
        **kwargs,
    ):
        super().__init__(master, **kwargs)
        self.title("預覽重新命名")
        self.geometry("800x600")
        self.minsize(600, 400)
        self._plan = plan
        self._conflicts = conflicts
        self._on_execute = on_execute
        self._build_ui()
        self.transient(master)
        self.focus_set()

    def _build_ui(self):
        has_conflicts = bool(self._conflicts)
        if has_conflicts:
            conflict_count = sum(len(v) for v in self._conflicts.values())
            warn_frame = ctk.CTkFrame(self, fg_color="#c0392b")
            warn_frame.pack(fill="x", padx=8, pady=(8, 4))
            ctk.CTkLabel(
                warn_frame,
                text=f"偵測到 {conflict_count} 個檔名衝突！選擇「繼續」將自動加後綴區分。",
                text_color="white",
                font=ctk.CTkFont(size=13, weight="bold"),
            ).pack(padx=12, pady=8)
        conflict_paths = set()
        for originals in self._conflicts.values():
            conflict_paths.update(originals)
        ctk.CTkLabel(
            self, text=f"共 {len(self._plan)} 個檔案",
            font=ctk.CTkFont(size=13),
        ).pack(padx=8, pady=(4, 2))
        scroll = ctk.CTkScrollableFrame(self)
        scroll.pack(fill="both", expand=True, padx=8, pady=4)
        for entry in self._plan:
            is_conflict = entry.original_path in conflict_paths
            row = ctk.CTkFrame(scroll, fg_color="transparent")
            row.pack(fill="x", pady=1)
            old_name = os.path.basename(entry.original_path)
            new_name = os.path.basename(entry.new_path)
            new_dir = os.path.dirname(entry.new_path)
            original_dir = os.path.dirname(entry.original_path)
            if new_dir != original_dir:
                rel_dir = os.path.relpath(new_dir, original_dir)
                display_new = os.path.join(rel_dir, new_name)
            else:
                display_new = new_name
            text_color = "#e74c3c" if is_conflict else None
            ctk.CTkLabel(
                row, text=f"{old_name}  \u2192  {display_new}",
                anchor="w", text_color=text_color,
            ).pack(side="left", fill="x", expand=True, padx=4)
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=8, pady=8)
        cancel_btn = ctk.CTkButton(
            btn_frame, text="取消", width=100,
            fg_color="gray", hover_color="gray30",
            command=self.destroy,
        )
        cancel_btn.pack(side="right", padx=4)
        if has_conflicts:
            exec_btn = ctk.CTkButton(
                btn_frame, text="繼續（自動加後綴）", width=160,
                fg_color="#e67e22", hover_color="#d35400",
                command=self._execute_with_suffix,
            )
        else:
            exec_btn = ctk.CTkButton(
                btn_frame, text="執行重新命名", width=140,
                command=self._execute,
            )
        exec_btn.pack(side="right", padx=4)

    def _execute(self):
        if self._on_execute:
            self._on_execute(self._plan)
        self.destroy()

    def _execute_with_suffix(self):
        if self._on_execute:
            from services.rename_service import RenameService
            from services.file_service import FileService
            svc = RenameService(FileService())
            fixed_plan = svc.apply_auto_suffix(self._plan)
            self._on_execute(fixed_plan)
        self.destroy()

# -*- coding: utf-8 -*-
"""
檔案清單元件

提供檔案列表的顯示、排序與管理功能。
"""
from typing import Callable, List, Optional
import customtkinter as ctk
from core.locale import t
from core.models import FileInfo


class FileListWidget(ctk.CTkFrame):
    """可排序的檔案清單元件"""

    def __init__(
        self,
        master,
        on_changed: Optional[Callable[[], None]] = None,
        **kwargs,
    ):
        super().__init__(master, **kwargs)
        self._files: List[FileInfo] = []
        self._instrument_labels: List[str] = []
        self._on_changed = on_changed
        self._build_ui()

    def _build_ui(self):
        self._scroll = ctk.CTkScrollableFrame(self)
        self._scroll.pack(fill="both", expand=True)

    def set_files(self, files: List[FileInfo]):
        """設定檔案清單"""
        self._files = files
        self._refresh()

    def set_instrument_labels(self, labels: List[str]):
        """設定對應的樂器標籤"""
        self._instrument_labels = labels
        self._refresh()

    def get_files(self) -> List[FileInfo]:
        """取得目前檔案清單"""
        return list(self._files)

    def _create_row(self, index: int, file_info: FileInfo):
        row = ctk.CTkFrame(self._scroll, fg_color="transparent")
        row._idx = index
        row.pack(fill="x", pady=1)
        if index < len(self._instrument_labels):
            ctk.CTkLabel(
                row, text=self._instrument_labels[index],
                width=100, anchor="w",
                font=ctk.CTkFont(size=11),
                text_color=("gray40", "gray60"),
            ).pack(side="left", padx=(4, 2))
        file_label = ctk.CTkLabel(
            row, text=file_info.display_name, anchor="w",
        )
        file_label.pack(side="left", fill="x", expand=True, padx=2)
        row._file_label = file_label
        btn_frame = ctk.CTkFrame(row, fg_color="transparent")
        btn_frame.pack(side="right")
        ctk.CTkButton(
            btn_frame, text="\u2191", width=28, height=28,
            command=lambda r=row: self._move_up(r._idx),
        ).pack(side="left", padx=1)
        ctk.CTkButton(
            btn_frame, text="\u2193", width=28, height=28,
            command=lambda r=row: self._move_down(r._idx),
        ).pack(side="left", padx=1)
        ctk.CTkButton(
            btn_frame, text="\u00D7", width=28, height=28,
            fg_color="#c0392b", hover_color="#e74c3c",
            command=lambda r=row: self._remove(r._idx),
        ).pack(side="left", padx=1)

    def _refresh(self):
        try:
            pack_info = self._scroll.pack_info()
            self._scroll.pack_forget()
        except Exception:
            pack_info = None
        for widget in self._scroll.winfo_children():
            widget.destroy()
        if not self._files:
            ctk.CTkLabel(
                self._scroll, text=t("file_list.empty"), text_color="gray",
            ).pack(pady=8)
        else:
            for i, file_info in enumerate(self._files):
                self._create_row(i, file_info)
        if pack_info:
            self._scroll.pack(**pack_info)

    def _move_up(self, index: int):
        if index <= 0:
            return
        self._files[index], self._files[index - 1] = (
            self._files[index - 1], self._files[index]
        )
        rows = self._scroll.winfo_children()
        if index < len(rows) and index - 1 < len(rows):
            a, b = rows[index]._file_label, rows[index - 1]._file_label
            ta, tb = a.cget("text"), b.cget("text")
            a.configure(text=tb)
            b.configure(text=ta)
        self._notify()

    def _move_down(self, index: int):
        if index >= len(self._files) - 1:
            return
        self._files[index], self._files[index + 1] = (
            self._files[index + 1], self._files[index]
        )
        rows = self._scroll.winfo_children()
        if index < len(rows) and index + 1 < len(rows):
            a, b = rows[index]._file_label, rows[index + 1]._file_label
            ta, tb = a.cget("text"), b.cget("text")
            a.configure(text=tb)
            b.configure(text=ta)
        self._notify()

    def _remove(self, index: int):
        if 0 <= index < len(self._files):
            self._files.pop(index)
            rows = self._scroll.winfo_children()
            if index < len(rows):
                rows[index].destroy()
            if not self._files:
                self._refresh()
            else:
                for i, row in enumerate(self._scroll.winfo_children()):
                    row._idx = i
            self._notify()

    def _notify(self):
        if self._on_changed:
            self._on_changed()

# -*- coding: utf-8 -*-
"""
檔案清單元件

提供檔案列表的顯示、排序與管理功能。
"""
from typing import Callable, List, Optional
import customtkinter as ctk
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
        """設定檔案清單

        Args:
            files: FileInfo 清單
        """
        self._files = files
        self._refresh()

    def set_instrument_labels(self, labels: List[str]):
        """設定對應的樂器標籤

        Args:
            labels: 樂器名稱清單
        """
        self._instrument_labels = labels
        self._refresh()

    def get_files(self) -> List[FileInfo]:
        """取得目前檔案清單"""
        return list(self._files)

    def _refresh(self):
        for widget in self._scroll.winfo_children():
            widget.destroy()
        if not self._files:
            ctk.CTkLabel(
                self._scroll, text="尚無檔案", text_color="gray",
            ).pack(pady=8)
            return
        for i, file_info in enumerate(self._files):
            row = ctk.CTkFrame(self._scroll, fg_color="transparent")
            row.pack(fill="x", pady=1)
            if i < len(self._instrument_labels):
                ctk.CTkLabel(
                    row, text=self._instrument_labels[i],
                    width=100, anchor="w",
                    font=ctk.CTkFont(size=11),
                    text_color=("gray40", "gray60"),
                ).pack(side="left", padx=(4, 2))
            ctk.CTkLabel(
                row, text=file_info.display_name, anchor="w",
            ).pack(side="left", fill="x", expand=True, padx=2)
            btn_frame = ctk.CTkFrame(row, fg_color="transparent")
            btn_frame.pack(side="right")
            ctk.CTkButton(
                btn_frame, text="\u25B2", width=28, height=24,
                command=lambda idx=i: self._move_up(idx),
            ).pack(side="left", padx=1)
            ctk.CTkButton(
                btn_frame, text="\u25BC", width=28, height=24,
                command=lambda idx=i: self._move_down(idx),
            ).pack(side="left", padx=1)
            ctk.CTkButton(
                btn_frame, text="\u2715", width=28, height=24,
                fg_color="#c0392b", hover_color="#e74c3c",
                command=lambda idx=i: self._remove(idx),
            ).pack(side="left", padx=1)

    def _move_up(self, index: int):
        if index <= 0:
            return
        self._files[index], self._files[index - 1] = (
            self._files[index - 1], self._files[index]
        )
        self._refresh()
        self._notify()

    def _move_down(self, index: int):
        if index >= len(self._files) - 1:
            return
        self._files[index], self._files[index + 1] = (
            self._files[index + 1], self._files[index]
        )
        self._refresh()
        self._notify()

    def _remove(self, index: int):
        if 0 <= index < len(self._files):
            self._files.pop(index)
            self._refresh()
            self._notify()

    def _notify(self):
        if self._on_changed:
            self._on_changed()

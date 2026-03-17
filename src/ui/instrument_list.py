# -*- coding: utf-8 -*-
"""
樂器表編輯器

提供樂器新增、刪除、排序的 UI 元件。
"""
from typing import Callable, List, Optional, TYPE_CHECKING
import customtkinter as ctk
from core.locale import t

if TYPE_CHECKING:
    from core.models import Project


class InstrumentListEditor(ctk.CTkFrame):
    """樂器表編輯面板"""

    def __init__(
        self,
        master,
        on_instruments_changed: Optional[Callable[[List[str]], None]] = None,
        project: Optional["Project"] = None,
        **kwargs,
    ):
        super().__init__(master, **kwargs)
        self._instruments: List[str] = []
        self._on_changed = on_instruments_changed
        self._project = project
        self._build_ui()

    def _build_ui(self):
        title = ctk.CTkLabel(self, text=t("instrument.title"), font=ctk.CTkFont(size=14, weight="bold"))
        title.pack(padx=8, pady=(8, 4))
        self._scroll_frame = ctk.CTkScrollableFrame(self, width=210)
        self._scroll_frame.pack(fill="both", expand=True, padx=4, pady=4)
        input_frame = ctk.CTkFrame(self, fg_color="transparent")
        input_frame.pack(fill="x", padx=4, pady=(0, 8))
        self._entry = ctk.CTkEntry(input_frame, placeholder_text=t("instrument.placeholder"))
        self._entry.pack(side="left", fill="x", expand=True, padx=(4, 4))
        self._entry.bind("<Return>", lambda e: self._add_instrument())
        add_btn = ctk.CTkButton(
            input_frame, text=t("instrument.add"), width=50, command=self._add_instrument,
        )
        add_btn.pack(side="right", padx=(0, 4))
        extract_btn = ctk.CTkButton(
            self, text=t("instrument.auto_extract"), command=self._auto_extract,
        )
        extract_btn.pack(fill="x", padx=8, pady=(0, 8))

    def _create_row(self, index: int, name: str):
        """建立單一樂器列"""
        row = ctk.CTkFrame(self._scroll_frame, fg_color="transparent")
        row._idx = index
        row.pack(fill="x", pady=1)
        label = ctk.CTkLabel(row, text=name, anchor="w")
        label.pack(side="left", fill="x", expand=True, padx=4)
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

    def _add_instrument(self):
        name = self._entry.get().strip()
        if not name:
            return
        self._instruments.append(name)
        self._entry.delete(0, "end")
        self._create_row(len(self._instruments) - 1, name)
        self._notify_changed()

    def _move_up(self, index: int):
        if index <= 0:
            return
        self._instruments[index], self._instruments[index - 1] = (
            self._instruments[index - 1], self._instruments[index]
        )
        self._swap_row_text(index, index - 1)
        self._notify_changed()

    def _move_down(self, index: int):
        if index >= len(self._instruments) - 1:
            return
        self._instruments[index], self._instruments[index + 1] = (
            self._instruments[index + 1], self._instruments[index]
        )
        self._swap_row_text(index, index + 1)
        self._notify_changed()

    def _swap_row_text(self, idx_a: int, idx_b: int):
        """交換兩列的顯示文字，不重建元件"""
        rows = self._scroll_frame.winfo_children()
        if idx_a < len(rows) and idx_b < len(rows):
            rows[idx_a].winfo_children()[0].configure(
                text=self._instruments[idx_a],
            )
            rows[idx_b].winfo_children()[0].configure(
                text=self._instruments[idx_b],
            )

    def _remove(self, index: int):
        if 0 <= index < len(self._instruments):
            self._instruments.pop(index)
            rows = self._scroll_frame.winfo_children()
            if index < len(rows):
                rows[index].destroy()
            for i, row in enumerate(self._scroll_frame.winfo_children()):
                row._idx = i
            self._notify_changed()

    def _refresh_list(self):
        for widget in self._scroll_frame.winfo_children():
            widget.destroy()
        for i, name in enumerate(self._instruments):
            self._create_row(i, name)

    def _notify_changed(self):
        if self._on_changed:
            self._on_changed(list(self._instruments))

    def _auto_extract(self):
        """從已匯入的檔名中自動擷取樂器名稱"""
        from tkinter import messagebox
        if not self._project:
            messagebox.showinfo(t("dialog.info"), t("instrument.auto_extract.empty"))
            return
        all_filenames = []
        for group in self._project.groups:
            all_filenames.extend([f.display_name for f in group.files])
        all_filenames.extend([f.display_name for f in self._project.ungrouped_files])
        if not all_filenames:
            messagebox.showinfo(t("dialog.info"), t("instrument.auto_extract.empty"))
            return
        from core.template_engine import extract_instruments_from_filenames
        extracted = extract_instruments_from_filenames(all_filenames)
        seen = set()
        unique = []
        for inst in extracted:
            if inst not in seen:
                seen.add(inst)
                unique.append(inst)
        if not unique:
            messagebox.showinfo(t("dialog.info"), t("instrument.auto_extract.empty"))
            return
        preview = "\n".join(f"  {i+1}. {name}" for i, name in enumerate(unique))
        if messagebox.askyesno(
            t("instrument.auto_extract.title"),
            t("instrument.auto_extract.confirm", instruments=preview),
        ):
            self._instruments = unique
            self._refresh_list()
            self._notify_changed()

    def get_instruments(self) -> List[str]:
        """取得目前樂器清單"""
        return list(self._instruments)

    def set_instruments(self, instruments: List[str]):
        """設定樂器清單

        Args:
            instruments: 樂器名稱清單
        """
        self._instruments = list(instruments)
        self._refresh_list()
        self._notify_changed()

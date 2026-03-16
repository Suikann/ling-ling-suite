# -*- coding: utf-8 -*-
"""
連結樂章對話框

提供將多個群組連結為同一曲目的不同樂章的 UI。
"""
from typing import Callable, List
import customtkinter as ctk
from core.locale import t
from core.models import Group


class LinkMovementsDialog(ctk.CTkToplevel):
    """連結群組為樂章的對話框"""

    def __init__(
        self,
        master,
        groups: List[Group],
        on_confirm: Callable[[list, str], None],
    ):
        super().__init__(master)
        self.title(t("group.link_movements.title"))
        self.geometry("500x520")
        self.resizable(False, False)
        self._groups = groups
        self._on_confirm = on_confirm
        self._check_vars = []
        self._order_list = []
        self._build_ui()
        self.grab_set()

    def _build_ui(self):
        ctk.CTkLabel(
            self, text=t("group.link_movements.select"),
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(16, 8))
        self._check_frame = ctk.CTkScrollableFrame(self, height=180)
        self._check_frame.pack(fill="x", padx=16, pady=(0, 8))
        for group in self._groups:
            var = ctk.BooleanVar(value=False)
            cb = ctk.CTkCheckBox(
                self._check_frame,
                text=group.name or group.id[:8],
                variable=var,
            )
            cb.pack(anchor="w", padx=4, pady=2)
            self._check_vars.append((group, var))
        name_frame = ctk.CTkFrame(self, fg_color="transparent")
        name_frame.pack(fill="x", padx=16, pady=8)
        ctk.CTkLabel(
            name_frame, text=t("group.link_movements.piece_name"),
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")
        self._piece_name_entry = ctk.CTkEntry(name_frame, width=280)
        self._piece_name_entry.pack(side="left", padx=(8, 0), fill="x", expand=True)
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=(16, 16))
        ctk.CTkButton(
            btn_frame, text=t("group.link_movements.cancel"),
            width=100, command=self.destroy,
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            btn_frame, text=t("group.link_movements.confirm"),
            width=100, command=self._confirm,
        ).pack(side="right")

    def _confirm(self):
        selected = [g for g, var in self._check_vars if var.get()]
        if len(selected) < 2:
            from tkinter import messagebox
            messagebox.showinfo(
                t("dialog.info"), t("group.link_movements.need_two"),
            )
            return
        piece_name = self._piece_name_entry.get().strip()
        if not piece_name:
            from tkinter import messagebox
            messagebox.showinfo(
                t("dialog.info"), t("group.link_movements.piece_name"),
            )
            return
        self.destroy()
        self._on_confirm(selected, piece_name)

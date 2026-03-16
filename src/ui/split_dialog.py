# -*- coding: utf-8 -*-
"""
PDF 分割對話框

提供選擇 PDF 檔案並設定分割方式的 UI。
"""
import os
from typing import Optional
import customtkinter as ctk
from core.locale import t


class SplitPdfDialog(ctk.CTkToplevel):
    """PDF 分割對話框"""

    def __init__(self, master):
        super().__init__(master)
        self.title(t("split.title"))
        self.geometry("560x440")
        self.resizable(False, False)
        self._pdf_path: Optional[str] = None
        self._page_count = 0
        self._build_ui()
        self.grab_set()

    def _build_ui(self):
        file_frame = ctk.CTkFrame(self, fg_color="transparent")
        file_frame.pack(fill="x", padx=16, pady=(16, 8))
        ctk.CTkLabel(file_frame, text=t("split.select_file")).pack(anchor="w")
        row = ctk.CTkFrame(file_frame, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        self._file_label = ctk.CTkLabel(
            row, text=t("split.no_file"), anchor="w",
            text_color="gray",
        )
        self._file_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            row, text=t("split.browse"), width=80,
            command=self._browse_file,
        ).pack(side="right")
        self._page_info = ctk.CTkLabel(
            file_frame, text="", font=ctk.CTkFont(size=12),
        )
        self._page_info.pack(anchor="w", pady=(4, 0))
        mode_frame = ctk.CTkFrame(self, fg_color="transparent")
        mode_frame.pack(fill="x", padx=16, pady=(8, 4))
        ctk.CTkLabel(
            mode_frame, text=t("split.mode"),
            font=ctk.CTkFont(weight="bold"),
        ).pack(anchor="w")
        self._mode_var = ctk.StringVar(value="every_n")
        r1 = ctk.CTkFrame(mode_frame, fg_color="transparent")
        r1.pack(fill="x", pady=4)
        ctk.CTkRadioButton(
            r1, text=t("split.every_n_pages"),
            variable=self._mode_var, value="every_n",
            command=self._on_mode_changed,
        ).pack(side="left")
        self._n_entry = ctk.CTkEntry(r1, width=60)
        self._n_entry.pack(side="left", padx=8)
        self._n_entry.insert(0, "1")
        ctk.CTkLabel(r1, text=t("split.pages_unit")).pack(side="left")
        r2 = ctk.CTkFrame(mode_frame, fg_color="transparent")
        r2.pack(fill="x", pady=4)
        ctk.CTkRadioButton(
            r2, text=t("split.custom_ranges"),
            variable=self._mode_var, value="custom",
            command=self._on_mode_changed,
        ).pack(side="left")
        self._ranges_entry = ctk.CTkEntry(r2, width=280, state="disabled")
        self._ranges_entry.pack(side="left", padx=8)
        ctk.CTkLabel(
            mode_frame, text=t("split.ranges_hint"),
            font=ctk.CTkFont(size=11), text_color="gray",
        ).pack(anchor="w", padx=(24, 0))
        output_frame = ctk.CTkFrame(self, fg_color="transparent")
        output_frame.pack(fill="x", padx=16, pady=(8, 4))
        ctk.CTkLabel(
            output_frame, text=t("split.output_dir"),
            font=ctk.CTkFont(weight="bold"),
        ).pack(anchor="w")
        dir_row = ctk.CTkFrame(output_frame, fg_color="transparent")
        dir_row.pack(fill="x", pady=(4, 0))
        self._dir_label = ctk.CTkLabel(
            dir_row, text=t("split.same_as_source"), anchor="w",
            text_color="gray",
        )
        self._dir_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            dir_row, text=t("split.browse"), width=80,
            command=self._browse_dir,
        ).pack(side="right")
        self._output_dir: Optional[str] = None
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=(16, 16))
        ctk.CTkButton(
            btn_frame, text=t("split.cancel"), width=100,
            command=self.destroy,
        ).pack(side="right", padx=(8, 0))
        self._split_btn = ctk.CTkButton(
            btn_frame, text=t("split.execute"), width=100,
            command=self._execute, state="disabled",
        )
        self._split_btn.pack(side="right")

    def _on_mode_changed(self):
        mode = self._mode_var.get()
        if mode == "every_n":
            self._n_entry.configure(state="normal")
            self._ranges_entry.configure(state="disabled")
        else:
            self._n_entry.configure(state="disabled")
            self._ranges_entry.configure(state="normal")

    def _browse_file(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title=t("split.select_file"),
            filetypes=[(t("filedialog.pdf_files"), "*.pdf")],
        )
        if not path:
            return
        self._pdf_path = path
        self._file_label.configure(
            text=os.path.basename(path), text_color=("black", "white"),
        )
        try:
            from services.pdf_service import get_page_count
            self._page_count = get_page_count(path)
            self._page_info.configure(
                text=t("split.page_count", count=self._page_count),
            )
            self._split_btn.configure(state="normal")
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror(t("dialog.error"), str(e))

    def _browse_dir(self):
        from tkinter import filedialog
        folder = filedialog.askdirectory(title=t("split.output_dir"))
        if folder:
            self._output_dir = folder
            self._dir_label.configure(
                text=folder, text_color=("black", "white"),
            )

    def _execute(self):
        from tkinter import messagebox
        if not self._pdf_path:
            return
        output_dir = self._output_dir or os.path.dirname(self._pdf_path)
        mode = self._mode_var.get()
        try:
            from services.pdf_service import (
                split_pdf,
                split_pdf_every_n_pages,
            )
            if mode == "every_n":
                n_str = self._n_entry.get().strip()
                if not n_str.isdigit() or int(n_str) < 1:
                    messagebox.showwarning(
                        t("dialog.warning"), t("split.invalid_n"),
                    )
                    return
                results = split_pdf_every_n_pages(
                    self._pdf_path, int(n_str), output_dir,
                )
            else:
                ranges_str = self._ranges_entry.get().strip()
                ranges = self._parse_ranges(ranges_str)
                if not ranges:
                    messagebox.showwarning(
                        t("dialog.warning"), t("split.invalid_ranges"),
                    )
                    return
                results = split_pdf(self._pdf_path, ranges, output_dir)
            messagebox.showinfo(
                t("dialog.complete"),
                t("split.done", count=len(results)),
            )
            self.destroy()
        except ImportError:
            messagebox.showerror(
                t("dialog.error"), t("split.missing_dependency"),
            )
        except Exception as e:
            messagebox.showerror(t("dialog.error"), str(e))

    @staticmethod
    def _parse_ranges(text: str):
        """解析頁面範圍字串，例如 '1-3, 4-6, 7-10'"""
        ranges = []
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                pieces = part.split("-", 1)
                try:
                    start = int(pieces[0].strip())
                    end = int(pieces[1].strip())
                    if start < 1 or end < start:
                        return None
                    ranges.append((start, end))
                except ValueError:
                    return None
            else:
                try:
                    page = int(part)
                    if page < 1:
                        return None
                    ranges.append((page, page))
                except ValueError:
                    return None
        return ranges if ranges else None

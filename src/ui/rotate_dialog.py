# -*- coding: utf-8 -*-
"""
PDF 旋轉對話框

提供選擇 PDF 檔案並設定旋轉角度與頁面範圍的 UI。
"""
import os
from typing import Optional
import customtkinter as ctk
from core.locale import t


class RotatePdfDialog(ctk.CTkToplevel):
    """PDF 旋轉對話框"""

    def __init__(self, master):
        super().__init__(master)
        self.title(t("rotate.title"))
        self.geometry("520x420")
        self.resizable(False, False)
        self._pdf_path: Optional[str] = None
        self._page_count = 0
        self._build_ui()
        self.grab_set()

    def _build_ui(self):
        # 檔案選擇
        file_frame = ctk.CTkFrame(self, fg_color="transparent")
        file_frame.pack(fill="x", padx=16, pady=(16, 8))
        ctk.CTkLabel(file_frame, text=t("rotate.select_file")).pack(anchor="w")
        row = ctk.CTkFrame(file_frame, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        self._file_label = ctk.CTkLabel(
            row, text=t("rotate.no_file"), anchor="w", text_color="gray",
        )
        self._file_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            row, text=t("rotate.browse"), width=80,
            command=self._browse_file,
        ).pack(side="right")
        self._page_info = ctk.CTkLabel(
            file_frame, text="", font=ctk.CTkFont(size=12),
        )
        self._page_info.pack(anchor="w", pady=(4, 0))
        # 旋轉角度
        angle_frame = ctk.CTkFrame(self, fg_color="transparent")
        angle_frame.pack(fill="x", padx=16, pady=(8, 4))
        ctk.CTkLabel(
            angle_frame, text=t("rotate.angle"),
            font=ctk.CTkFont(weight="bold"),
        ).pack(anchor="w")
        self._angle_var = ctk.StringVar(value="90")
        angles_row = ctk.CTkFrame(angle_frame, fg_color="transparent")
        angles_row.pack(fill="x", pady=4)
        for value, label_key in [
            ("90", "rotate.cw_90"),
            ("180", "rotate.180"),
            ("270", "rotate.ccw_90"),
        ]:
            ctk.CTkRadioButton(
                angles_row, text=t(label_key),
                variable=self._angle_var, value=value,
            ).pack(side="left", padx=(0, 16))
        # 頁面範圍
        scope_frame = ctk.CTkFrame(self, fg_color="transparent")
        scope_frame.pack(fill="x", padx=16, pady=(8, 4))
        ctk.CTkLabel(
            scope_frame, text=t("rotate.scope"),
            font=ctk.CTkFont(weight="bold"),
        ).pack(anchor="w")
        self._scope_var = ctk.StringVar(value="all")
        r1 = ctk.CTkFrame(scope_frame, fg_color="transparent")
        r1.pack(fill="x", pady=4)
        ctk.CTkRadioButton(
            r1, text=t("rotate.all_pages"),
            variable=self._scope_var, value="all",
            command=self._on_scope_changed,
        ).pack(side="left")
        r2 = ctk.CTkFrame(scope_frame, fg_color="transparent")
        r2.pack(fill="x", pady=4)
        ctk.CTkRadioButton(
            r2, text=t("rotate.custom_pages"),
            variable=self._scope_var, value="custom",
            command=self._on_scope_changed,
        ).pack(side="left")
        self._pages_entry = ctk.CTkEntry(r2, width=240, state="disabled")
        self._pages_entry.pack(side="left", padx=8)
        ctk.CTkLabel(
            scope_frame, text=t("rotate.pages_hint"),
            font=ctk.CTkFont(size=11), text_color="gray",
        ).pack(anchor="w", padx=(24, 0))
        # 覆寫或另存
        output_frame = ctk.CTkFrame(self, fg_color="transparent")
        output_frame.pack(fill="x", padx=16, pady=(8, 4))
        self._overwrite_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            output_frame, text=t("rotate.overwrite"),
            variable=self._overwrite_var,
        ).pack(anchor="w")
        # 按鈕
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=(16, 16))
        ctk.CTkButton(
            btn_frame, text=t("rotate.cancel"), width=100,
            command=self.destroy,
        ).pack(side="right", padx=(8, 0))
        self._exec_btn = ctk.CTkButton(
            btn_frame, text=t("rotate.execute"), width=100,
            command=self._execute, state="disabled",
        )
        self._exec_btn.pack(side="right")

    def _on_scope_changed(self):
        if self._scope_var.get() == "custom":
            self._pages_entry.configure(state="normal")
        else:
            self._pages_entry.configure(state="disabled")

    def _browse_file(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title=t("rotate.select_file"),
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
                text=t("rotate.page_count", count=self._page_count),
            )
            self._exec_btn.configure(state="normal")
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror(t("dialog.error"), str(e))

    def _execute(self):
        from tkinter import messagebox
        if not self._pdf_path:
            return
        angle = int(self._angle_var.get())
        page_ranges = []
        if self._scope_var.get() == "custom":
            parsed = self._parse_ranges(self._pages_entry.get().strip())
            if parsed is None:
                messagebox.showwarning(
                    t("dialog.warning"), t("rotate.invalid_pages"),
                )
                return
            page_ranges = parsed
        overwrite = self._overwrite_var.get()
        if overwrite:
            output_path = self._pdf_path
        else:
            from tkinter import filedialog
            output_path = filedialog.asksaveasfilename(
                title=t("rotate.save_as"),
                defaultextension=".pdf",
                initialfile=os.path.basename(self._pdf_path),
                filetypes=[(t("filedialog.pdf_files"), "*.pdf")],
            )
            if not output_path:
                return
        try:
            from services.pdf_service import rotate_pdf
            rotate_pdf(self._pdf_path, angle, page_ranges, output_path)
            messagebox.showinfo(t("dialog.complete"), t("rotate.done"))
            self.destroy()
        except ImportError:
            messagebox.showerror(
                t("dialog.error"), t("split.missing_dependency"),
            )
        except Exception as e:
            messagebox.showerror(t("dialog.error"), str(e))

    @staticmethod
    def _parse_ranges(text: str):
        """解析頁面範圍，例如 '1-3, 5, 7-10'"""
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

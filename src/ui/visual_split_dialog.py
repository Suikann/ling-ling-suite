# -*- coding: utf-8 -*-
"""
PDF 視覺化分譜對話框

提供頁面縮圖預覽，讓使用者直觀地標記分割點，
將合併的 PDF 分譜拆分為各樂器的獨立檔案。
"""
import os
from typing import Dict, List, Optional, Set, Tuple
import customtkinter as ctk
from core.locale import t

SECTION_COLORS = [
    "#3B82F6",
    "#10B981",
    "#F59E0B",
    "#EF4444",
    "#8B5CF6",
    "#EC4899",
    "#06B6D4",
    "#F97316",
]


class VisualSplitDialog(ctk.CTkToplevel):
    """PDF 視覺化分譜對話框"""

    _THUMB_WIDTH = 160
    _MAX_COLS = 4

    def __init__(self, master, project=None):
        super().__init__(master)
        self.title(t("vsplit.title"))
        self.geometry("1100x720")
        self.minsize(900, 520)
        self._project = project
        self._pdf_path: Optional[str] = None
        self._page_count = 0
        self._ctk_images: List[ctk.CTkImage] = []
        self._split_starts: Set[int] = {0}
        self._section_name_vars: Dict[int, ctk.StringVar] = {}
        self._output_dir: Optional[str] = None
        self._build_ui()
        self.grab_set()

    # --- UI 建構 ---

    def _build_ui(self):
        self._build_top_bar()
        self._build_content()
        self._build_bottom_bar()

    def _build_top_bar(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(12, 4))
        ctk.CTkLabel(top, text=t("vsplit.select_file")).pack(side="left")
        self._file_label = ctk.CTkLabel(
            top, text=t("vsplit.no_file"), text_color="gray", anchor="w",
        )
        self._file_label.pack(side="left", padx=(8, 0), fill="x", expand=True)
        self._page_info_label = ctk.CTkLabel(
            top, text="", font=ctk.CTkFont(size=12),
        )
        self._page_info_label.pack(side="right", padx=(0, 8))
        ctk.CTkButton(
            top, text=t("vsplit.browse"), width=80,
            command=self._browse_file,
        ).pack(side="right")

    def _build_content(self):
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=12, pady=4)
        self._page_scroll = ctk.CTkScrollableFrame(content)
        self._page_scroll.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self._empty_hint = ctk.CTkLabel(
            self._page_scroll,
            text=t("vsplit.hint_empty"),
            font=ctk.CTkFont(size=14), text_color="gray",
        )
        self._empty_hint.pack(expand=True, pady=40)
        right = ctk.CTkFrame(content, width=280)
        right.pack(side="right", fill="y", padx=(4, 0))
        right.pack_propagate(False)
        self._build_right_panel(right)

    def _build_right_panel(self, parent):
        ctk.CTkLabel(
            parent, text=t("vsplit.assignments"),
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=8, pady=(8, 2))
        ctk.CTkLabel(
            parent, text=t("vsplit.click_hint"),
            font=ctk.CTkFont(size=11), text_color="gray",
            wraplength=260, justify="left",
        ).pack(anchor="w", padx=8, pady=(0, 4))
        self._assign_scroll = ctk.CTkScrollableFrame(parent)
        self._assign_scroll.pack(fill="both", expand=True, padx=4, pady=4)
        if self._project and self._project.instruments:
            self._use_project_var = ctk.BooleanVar(value=True)
            ctk.CTkCheckBox(
                parent, text=t("vsplit.use_project_instruments"),
                variable=self._use_project_var,
                command=self._refresh_assignment_names,
            ).pack(anchor="w", padx=8, pady=4)
        else:
            self._use_project_var = None
        out_frame = ctk.CTkFrame(parent, fg_color="transparent")
        out_frame.pack(fill="x", padx=8, pady=(4, 8))
        ctk.CTkLabel(
            out_frame, text=t("vsplit.output_dir"),
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w")
        dir_row = ctk.CTkFrame(out_frame, fg_color="transparent")
        dir_row.pack(fill="x", pady=(2, 0))
        self._dir_label = ctk.CTkLabel(
            dir_row, text=t("vsplit.same_as_source"),
            text_color="gray", anchor="w", font=ctk.CTkFont(size=11),
        )
        self._dir_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            dir_row, text="...", width=32, command=self._browse_dir,
        ).pack(side="right")

    def _build_bottom_bar(self):
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=12, pady=(4, 12))
        ctk.CTkButton(
            bottom, text=t("vsplit.cancel"), width=100,
            fg_color="transparent", border_width=1,
            command=self.destroy,
        ).pack(side="right", padx=(8, 0))
        self._execute_btn = ctk.CTkButton(
            bottom, text=t("vsplit.execute"), width=120,
            command=self._execute_split, state="disabled",
        )
        self._execute_btn.pack(side="right")

    # --- 檔案載入 ---

    def _browse_file(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title=t("vsplit.select_file"),
            filetypes=[(t("filedialog.pdf_files"), "*.pdf")],
        )
        if path:
            self._load_pdf(path)

    def _load_pdf(self, path: str):
        from tkinter import messagebox
        try:
            from services.pdf_service import get_page_count, render_page_thumbnails
        except ImportError:
            messagebox.showerror(
                t("dialog.error"), t("vsplit.missing_dependency"),
            )
            return
        try:
            self._pdf_path = path
            self._page_count = get_page_count(path)
            self._file_label.configure(
                text=os.path.basename(path),
                text_color=("black", "white"),
            )
            self._page_info_label.configure(
                text=t("vsplit.page_count", count=self._page_count),
            )
            pil_images = render_page_thumbnails(path, max_width=self._THUMB_WIDTH)
            self._ctk_images = [
                ctk.CTkImage(
                    light_image=img, dark_image=img,
                    size=(img.width, img.height),
                )
                for img in pil_images
            ]
            self._split_starts = {0}
            self._render_page_grid()
            self._update_assignment_panel()
            self._execute_btn.configure(state="normal")
        except Exception as e:
            messagebox.showerror(t("dialog.error"), str(e))

    # --- 頁面縮圖顯示 ---

    def _render_page_grid(self):
        for child in self._page_scroll.winfo_children():
            child.destroy()
        if not self._ctk_images:
            return
        sections = self._get_sections()
        for sec_idx, (start, end) in enumerate(sections):
            color = SECTION_COLORS[sec_idx % len(SECTION_COLORS)]
            if sec_idx > 0:
                self._build_divider(self._page_scroll, color)
            self._build_section_header(self._page_scroll, sec_idx, start, end, color)
            self._build_section_pages(self._page_scroll, start, end, color)

    def _build_divider(self, parent, color: str):
        ctk.CTkFrame(parent, height=3, fg_color=color).pack(
            fill="x", padx=8, pady=(10, 2),
        )

    def _build_section_header(
        self, parent, sec_idx: int, start: int, end: int, color: str,
    ):
        if start == end:
            page_str = f"p.{start + 1}"
        else:
            page_str = f"p.{start + 1}-{end + 1}"
        ctk.CTkLabel(
            parent,
            text=f"  {sec_idx + 1}  |  {page_str}  ({end - start + 1})",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=color,
        ).pack(anchor="w", padx=4, pady=(4, 2))

    def _build_section_pages(
        self, parent, start: int, end: int, color: str,
    ):
        row_frame = None
        col = 0
        for page_idx in range(start, end + 1):
            if col == 0:
                row_frame = ctk.CTkFrame(parent, fg_color="transparent")
                row_frame.pack(anchor="w", padx=4, pady=1)
            outer = ctk.CTkFrame(row_frame, fg_color=color, corner_radius=6)
            outer.pack(side="left", padx=3, pady=2)
            inner = ctk.CTkFrame(outer, corner_radius=4)
            inner.pack(padx=2, pady=2)
            lbl = ctk.CTkLabel(
                inner, image=self._ctk_images[page_idx],
                text="", cursor="hand2",
            )
            lbl.pack(padx=1, pady=(1, 0))
            lbl.bind(
                "<Button-1>",
                lambda e, idx=page_idx: self._on_page_click(idx),
            )
            lbl.bind(
                "<Button-3>",
                lambda e, idx=page_idx: self._on_page_right_click(idx),
            )
            num_lbl = ctk.CTkLabel(
                inner, text=str(page_idx + 1),
                font=ctk.CTkFont(size=10),
            )
            num_lbl.pack(pady=(0, 2))
            num_lbl.bind(
                "<Button-1>",
                lambda e, idx=page_idx: self._on_page_click(idx),
            )
            col += 1
            if col >= self._MAX_COLS:
                col = 0

    def _get_sections(self) -> List[Tuple[int, int]]:
        """取得目前各分譜的頁面範圍（0-based）"""
        starts = sorted(self._split_starts)
        sections = []
        for i, start in enumerate(starts):
            end = (starts[i + 1] - 1) if (i + 1 < len(starts)) else (self._page_count - 1)
            sections.append((start, end))
        return sections

    # --- 頁面互動 ---

    def _on_page_click(self, page_idx: int):
        """左鍵切換分割點"""
        if page_idx == 0:
            return
        if page_idx in self._split_starts:
            self._split_starts.discard(page_idx)
        else:
            self._split_starts.add(page_idx)
        self._render_page_grid()
        self._update_assignment_panel()

    def _on_page_right_click(self, page_idx: int):
        """右鍵顯示頁面放大預覽"""
        try:
            from services.pdf_service import render_single_page
            pil_img = render_single_page(self._pdf_path, page_idx, max_width=600)
        except Exception:
            return
        preview = ctk.CTkToplevel(self)
        preview.title(t("vsplit.page_label", num=page_idx + 1))
        ctk_img = ctk.CTkImage(
            light_image=pil_img, dark_image=pil_img,
            size=(pil_img.width, pil_img.height),
        )
        preview._img_ref = ctk_img
        scroll = ctk.CTkScrollableFrame(preview)
        scroll.pack(fill="both", expand=True)
        ctk.CTkLabel(scroll, image=ctk_img, text="").pack(padx=4, pady=4)
        w = min(pil_img.width + 40, 800)
        h = min(pil_img.height + 60, 900)
        preview.geometry(f"{w}x{h}")

    # --- 分譜指派面板 ---

    def _update_assignment_panel(self):
        old_names = {
            idx: var.get() for idx, var in self._section_name_vars.items()
        }
        for child in self._assign_scroll.winfo_children():
            child.destroy()
        sections = self._get_sections()
        self._section_name_vars = {}
        instruments = self._get_instrument_suggestions()
        for sec_idx, (start, end) in enumerate(sections):
            color = SECTION_COLORS[sec_idx % len(SECTION_COLORS)]
            row = ctk.CTkFrame(self._assign_scroll, fg_color="transparent")
            row.pack(fill="x", pady=3)
            dot = ctk.CTkFrame(
                row, width=14, height=14,
                fg_color=color, corner_radius=7,
            )
            dot.pack(side="left", padx=(0, 6))
            dot.pack_propagate(False)
            var = ctk.StringVar()
            if sec_idx in old_names:
                var.set(old_names[sec_idx])
            elif sec_idx < len(instruments):
                var.set(instruments[sec_idx])
            else:
                var.set(t("vsplit.part_default", index=sec_idx + 1))
            self._section_name_vars[sec_idx] = var
            ctk.CTkEntry(
                row, textvariable=var, width=140, height=28,
            ).pack(side="left")
            if start == end:
                range_text = f"p.{start + 1}"
            else:
                range_text = f"p.{start + 1}-{end + 1}"
            n_pages = end - start + 1
            ctk.CTkLabel(
                row, text=f"{range_text} ({n_pages})",
                font=ctk.CTkFont(size=11), text_color="gray",
            ).pack(side="left", padx=(6, 0))

    def _get_instrument_suggestions(self) -> List[str]:
        if self._use_project_var and self._use_project_var.get():
            if self._project and self._project.instruments:
                return list(self._project.instruments)
        return []

    def _refresh_assignment_names(self):
        sections = self._get_sections()
        instruments = self._get_instrument_suggestions()
        for sec_idx in range(len(sections)):
            var = self._section_name_vars.get(sec_idx)
            if not var:
                continue
            if sec_idx < len(instruments):
                var.set(instruments[sec_idx])
            elif not instruments:
                var.set(t("vsplit.part_default", index=sec_idx + 1))

    # --- 輸出 ---

    def _browse_dir(self):
        from tkinter import filedialog
        folder = filedialog.askdirectory(title=t("vsplit.output_dir"))
        if folder:
            self._output_dir = folder
            self._dir_label.configure(
                text=folder, text_color=("black", "white"),
            )

    def _execute_split(self):
        from tkinter import messagebox
        if not self._pdf_path:
            return
        output_dir = self._output_dir or os.path.dirname(self._pdf_path)
        sections = self._get_sections()
        try:
            from services.pdf_service import split_pdf
            output_files = []
            for sec_idx, (start, end) in enumerate(sections):
                var = self._section_name_vars.get(sec_idx)
                name = var.get().strip() if var else f"Part {sec_idx + 1}"
                safe_name = _sanitize_filename(name)
                files = split_pdf(
                    self._pdf_path,
                    [(start + 1, end + 1)],
                    output_dir,
                    name_pattern=safe_name,
                )
                output_files.extend(files)
            messagebox.showinfo(
                t("dialog.complete"),
                t("vsplit.done", count=len(output_files)),
            )
            self.destroy()
        except Exception as e:
            messagebox.showerror(t("dialog.error"), str(e))


def _sanitize_filename(name: str) -> str:
    """移除檔名中的非法字元"""
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name.strip() or "Part"

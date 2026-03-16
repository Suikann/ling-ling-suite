# -*- coding: utf-8 -*-
"""
PDF 分割對話框

提供頁面縮圖預覽，讓使用者直觀地標記分割點，
將合併的 PDF 拆分為各樂器的獨立檔案。
"""
import os
from typing import Callable, Dict, List, Optional, Set, Tuple
import customtkinter as ctk
from core.constants import INSTRUMENT_PRESETS
from core.locale import t, get_locale
from core.models import FileInfo

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


class SplitPdfDialog(ctk.CTkToplevel):
    """PDF 分割對話框"""

    _THUMB_WIDTH = 160
    _MAX_COLS = 4

    def __init__(
        self,
        master,
        project,
        on_split_complete: Optional[Callable] = None,
    ):
        super().__init__(master)
        self.title(t("split.title"))
        self.geometry("1100x720")
        self.minsize(900, 520)
        self._project = project
        self._on_split_complete = on_split_complete
        self._pdf_path: Optional[str] = None
        self._page_count = 0
        self._ctk_images: List[ctk.CTkImage] = []
        self._split_starts: Set[int] = {0}
        self._section_name_vars: Dict[int, ctk.StringVar] = {}
        self._output_dir: Optional[str] = None
        self._file_map: Dict[str, str] = {}
        self._active_instruments: List[str] = (
            list(project.instruments) if project and project.instruments else []
        )
        self._preview_win: Optional[ctk.CTkToplevel] = None
        self._preview_page_idx = 0
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
        ctk.CTkLabel(top, text=t("split.select_file")).pack(side="left")
        project_files = self._collect_project_files()
        labels = [label for label, _ in project_files]
        self._file_map = {label: path for label, path in project_files}
        self._file_combo = ctk.CTkOptionMenu(
            top, values=labels or [t("split.no_project_files")],
            width=500,
            state="normal" if labels else "disabled",
            command=self._on_file_selected,
            dynamic_resizing=False,
        )
        if labels:
            self._file_combo.set(t("split.no_file"))
        self._file_combo.pack(side="left", padx=(8, 8), fill="x", expand=True)
        self._page_info_label = ctk.CTkLabel(
            top, text="", font=ctk.CTkFont(size=12),
        )
        self._page_info_label.pack(side="right")

    def _collect_project_files(self) -> List[Tuple[str, str]]:
        """蒐集專案內所有 PDF 檔案"""
        files: List[Tuple[str, str]] = []
        if not self._project:
            return files
        ungrouped_label = t("group.ungrouped")
        for f in self._project.ungrouped_files:
            if f.original_path.lower().endswith(".pdf"):
                label = f"{f.display_name}  [{ungrouped_label}]"
                files.append((label, f.original_path))
        for group in self._project.groups:
            group_name = group.name or group.id[:8]
            for f in group.files:
                if f.original_path.lower().endswith(".pdf"):
                    label = f"{f.display_name}  [{group_name}]"
                    files.append((label, f.original_path))
        return files

    def _build_content(self):
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=12, pady=4)
        self._page_scroll = ctk.CTkScrollableFrame(content)
        self._page_scroll.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self._empty_hint = ctk.CTkLabel(
            self._page_scroll,
            text=t("split.hint_empty"),
            font=ctk.CTkFont(size=14), text_color="gray",
        )
        self._empty_hint.pack(expand=True, pady=40)
        right = ctk.CTkFrame(content, width=280)
        right.pack(side="right", fill="y", padx=(4, 0))
        right.pack_propagate(False)
        self._build_right_panel(right)

    def _build_right_panel(self, parent):
        ctk.CTkLabel(
            parent, text=t("split.assignments"),
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=8, pady=(8, 2))
        ctk.CTkLabel(
            parent, text=t("split.click_hint"),
            font=ctk.CTkFont(size=11), text_color="gray",
            wraplength=260, justify="left",
        ).pack(anchor="w", padx=8, pady=(0, 4))
        preset_frame = ctk.CTkFrame(parent, fg_color="transparent")
        preset_frame.pack(fill="x", padx=8, pady=(4, 4))
        ctk.CTkLabel(
            preset_frame, text=t("split.load_names"),
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w")
        preset_options = self._build_preset_options()
        self._preset_menu = ctk.CTkOptionMenu(
            preset_frame, values=preset_options,
            command=self._on_preset_selected,
            dynamic_resizing=False,
        )
        if self._project and self._project.instruments:
            self._preset_menu.set(t("split.preset_project"))
        else:
            self._preset_menu.set(t("split.preset_manual"))
        self._preset_menu.pack(fill="x", pady=(2, 0))
        self._assign_scroll = ctk.CTkScrollableFrame(parent)
        self._assign_scroll.pack(fill="both", expand=True, padx=4, pady=4)
        out_frame = ctk.CTkFrame(parent, fg_color="transparent")
        out_frame.pack(fill="x", padx=8, pady=(4, 8))
        ctk.CTkLabel(
            out_frame, text=t("split.output_dir"),
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w")
        dir_row = ctk.CTkFrame(out_frame, fg_color="transparent")
        dir_row.pack(fill="x", pady=(2, 0))
        self._dir_label = ctk.CTkLabel(
            dir_row, text=t("split.same_as_source"),
            text_color="gray", anchor="w", font=ctk.CTkFont(size=11),
        )
        self._dir_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            dir_row, text="...", width=32, command=self._browse_dir,
        ).pack(side="right")

    def _build_preset_options(self) -> List[str]:
        """建構預設編制表選項清單"""
        options = [t("split.preset_manual")]
        if self._project and self._project.instruments:
            options.append(t("split.preset_project"))
        locale = get_locale()
        for preset in INSTRUMENT_PRESETS:
            name = preset.name_en if locale == "en" else preset.name
            options.append(name)
        return options

    def _build_bottom_bar(self):
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=12, pady=(4, 12))
        ctk.CTkButton(
            bottom, text=t("split.cancel"), width=100,
            fg_color="transparent", border_width=1,
            command=self.destroy,
        ).pack(side="right", padx=(8, 0))
        self._execute_btn = ctk.CTkButton(
            bottom, text=t("split.execute"), width=120,
            command=self._execute_split, state="disabled",
        )
        self._execute_btn.pack(side="right")

    # --- 檔案載入 ---

    def _on_file_selected(self, choice: str):
        path = self._file_map.get(choice)
        if path:
            self._load_pdf(path)

    def _load_pdf(self, path: str):
        from tkinter import messagebox
        if not os.path.isfile(path):
            messagebox.showerror(
                t("dialog.error"),
                t("dialog.missing_files.header", count=1) + f"\n{path}",
            )
            return
        try:
            from services.pdf_service import get_page_count, render_page_thumbnails
        except ImportError:
            messagebox.showerror(
                t("dialog.error"), t("split.missing_dependency"),
            )
            return
        try:
            self._pdf_path = path
            self._page_count = get_page_count(path)
            self._page_info_label.configure(
                text=t("split.page_count", count=self._page_count),
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
                ctk.CTkFrame(
                    self._page_scroll, height=3, fg_color=color,
                ).pack(fill="x", padx=8, pady=(10, 2))
            self._build_section_header(sec_idx, start, end, color)
            self._build_section_pages(start, end, color)

    def _build_section_header(
        self, sec_idx: int, start: int, end: int, color: str,
    ):
        if start == end:
            page_str = f"p.{start + 1}"
        else:
            page_str = f"p.{start + 1}-{end + 1}"
        ctk.CTkLabel(
            self._page_scroll,
            text=f"  {sec_idx + 1}  |  {page_str}  ({end - start + 1})",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=color,
        ).pack(anchor="w", padx=4, pady=(4, 2))

    def _build_section_pages(self, start: int, end: int, color: str):
        row_frame = None
        col = 0
        for page_idx in range(start, end + 1):
            if col == 0:
                row_frame = ctk.CTkFrame(
                    self._page_scroll, fg_color="transparent",
                )
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
                lambda e, idx=page_idx: self._open_preview(idx),
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

    # --- 頁面放大預覽 ---

    def _open_preview(self, page_idx: int, _event=None):
        """開啟頁面放大預覽視窗（含翻頁）"""
        if self._preview_win and self._preview_win.winfo_exists():
            self._preview_win.destroy()
        self.grab_release()
        preview = ctk.CTkToplevel(self)
        preview.transient(self)
        self._preview_win = preview
        self._preview_page_idx = page_idx
        nav = ctk.CTkFrame(preview, fg_color="transparent")
        nav.pack(fill="x", padx=8, pady=(8, 4))
        self._prev_btn = ctk.CTkButton(
            nav, text="\u25C0", width=40,
            command=self._preview_prev,
        )
        self._prev_btn.pack(side="left")
        self._preview_page_label = ctk.CTkLabel(
            nav, text="", font=ctk.CTkFont(size=13),
        )
        self._preview_page_label.pack(side="left", fill="x", expand=True)
        self._next_btn = ctk.CTkButton(
            nav, text="\u25B6", width=40,
            command=self._preview_next,
        )
        self._next_btn.pack(side="right")
        self._preview_scroll = ctk.CTkScrollableFrame(preview)
        self._preview_scroll.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._preview_img_label = ctk.CTkLabel(self._preview_scroll, text="")
        self._preview_img_label.pack(padx=4, pady=4)
        preview.geometry("660x900")
        self._render_preview_page(page_idx)
        preview.grab_set()
        preview.protocol("WM_DELETE_WINDOW", self._close_preview)
        preview.bind("<Left>", lambda e: self._preview_prev())
        preview.bind("<Right>", lambda e: self._preview_next())
        preview.bind("<Escape>", lambda e: self._close_preview())

    def _render_preview_page(self, page_idx: int):
        """算繪並顯示指定頁面"""
        try:
            from services.pdf_service import render_single_page
            pil_img = render_single_page(self._pdf_path, page_idx, max_width=600)
        except Exception:
            return
        ctk_img = ctk.CTkImage(
            light_image=pil_img, dark_image=pil_img,
            size=(pil_img.width, pil_img.height),
        )
        self._preview_win._img_ref = ctk_img
        self._preview_img_label.configure(image=ctk_img)
        self._preview_page_idx = page_idx
        self._preview_win.title(t("split.page_label", num=page_idx + 1))
        self._preview_page_label.configure(
            text=f"{page_idx + 1} / {self._page_count}",
        )
        self._prev_btn.configure(
            state="normal" if page_idx > 0 else "disabled",
        )
        self._next_btn.configure(
            state="normal" if page_idx < self._page_count - 1 else "disabled",
        )

    def _preview_prev(self):
        if self._preview_page_idx > 0:
            self._render_preview_page(self._preview_page_idx - 1)

    def _preview_next(self):
        if self._preview_page_idx < self._page_count - 1:
            self._render_preview_page(self._preview_page_idx + 1)

    def _close_preview(self):
        if self._preview_win and self._preview_win.winfo_exists():
            self._preview_win.destroy()
        self._preview_win = None
        self.grab_set()

    # --- 分譜指派面板 ---

    def _update_assignment_panel(self):
        old_names = {
            idx: var.get() for idx, var in self._section_name_vars.items()
        }
        for child in self._assign_scroll.winfo_children():
            child.destroy()
        sections = self._get_sections()
        self._section_name_vars = {}
        instruments = self._active_instruments
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
                var.set(t("split.part_default", index=sec_idx + 1))
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

    def _on_preset_selected(self, choice: str):
        """切換預設編制表"""
        if choice == t("split.preset_manual"):
            self._active_instruments = []
        elif choice == t("split.preset_project"):
            self._active_instruments = (
                list(self._project.instruments)
                if self._project else []
            )
        else:
            locale = get_locale()
            for preset in INSTRUMENT_PRESETS:
                name = preset.name_en if locale == "en" else preset.name
                if name == choice:
                    self._active_instruments = list(preset.instruments)
                    break
        sections = self._get_sections()
        for sec_idx in range(len(sections)):
            var = self._section_name_vars.get(sec_idx)
            if not var:
                continue
            if sec_idx < len(self._active_instruments):
                var.set(self._active_instruments[sec_idx])
            else:
                var.set(t("split.part_default", index=sec_idx + 1))

    # --- 輸出 ---

    def _browse_dir(self):
        from tkinter import filedialog
        folder = filedialog.askdirectory(title=t("split.output_dir"))
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
            output_files: List[str] = []
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
            new_file_infos = [
                FileInfo(
                    original_path=path,
                    display_name=os.path.basename(path),
                )
                for path in output_files
            ]
            if self._on_split_complete and new_file_infos:
                self._on_split_complete(new_file_infos)
            messagebox.showinfo(
                t("dialog.complete"),
                t("split.done", count=len(output_files)),
            )
            self.destroy()
        except Exception as e:
            messagebox.showerror(t("dialog.error"), str(e))


def _sanitize_filename(name: str) -> str:
    """移除檔名中的非法字元"""
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name.strip() or "Part"

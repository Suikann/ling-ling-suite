# -*- coding: utf-8 -*-
"""
PDF 分割對話框

提供頁面縮圖預覽，讓使用者直觀地標記分割點，
將合併的 PDF 拆分為各樂器的獨立檔案。
"""
import os
from typing import Callable, Dict, List, Optional, Set, Tuple
import customtkinter as ctk
from core.locale import t
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
        initial_group=None,
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
        self._deleted_pages: Set[int] = set()
        self._section_name_vars: Dict[int, ctk.StringVar] = {}
        self._output_dir: Optional[str] = None
        self._file_map: Dict[str, tuple] = {}
        self._source_group = None
        self._filter_group = initial_group
        self._preview_win: Optional[ctk.CTkToplevel] = None
        self._preview_page_idx: int = 0
        self._render_width: int = 560
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
        filter_options = self._build_group_filter_options()
        self._group_filter = ctk.CTkOptionMenu(
            top, values=filter_options, width=160,
            command=self._on_group_filter_changed,
            dynamic_resizing=False,
        )
        initial_name = self._get_group_filter_name(self._filter_group)
        self._group_filter.set(initial_name)
        self._group_filter.pack(side="left")
        self._file_combo = ctk.CTkOptionMenu(
            top, values=[t("split.no_file")], width=400,
            state="disabled",
            command=self._on_file_selected,
            dynamic_resizing=False,
        )
        self._file_combo.pack(side="left", padx=(8, 8), fill="x", expand=True)
        self._page_info_label = ctk.CTkLabel(
            top, text="", font=ctk.CTkFont(size=12),
        )
        self._page_info_label.pack(side="right")
        self._refresh_file_combo()

    _ALL_GROUPS_KEY = "__all__"

    def _build_group_filter_options(self) -> List[str]:
        """建構群組過濾選項"""
        options = [t("group.ungrouped")]
        if self._project:
            for group in self._project.groups:
                options.append(group.name or group.id[:8])
        return options

    def _get_group_filter_name(self, group) -> str:
        if group is None:
            return t("group.ungrouped")
        return group.name or group.id[:8]

    def _on_group_filter_changed(self, choice: str):
        """群組過濾變更"""
        if choice == t("group.ungrouped"):
            self._filter_group = None
        elif self._project:
            for g in self._project.groups:
                if (g.name or g.id[:8]) == choice:
                    self._filter_group = g
                    break
        self._refresh_file_combo()

    def _refresh_file_combo(self):
        """依群組過濾重建檔案下拉"""
        files = self._collect_project_files()
        labels = [label for label, _, _ in files]
        self._file_map = {label: (path, group) for label, path, group in files}
        if labels:
            self._file_combo.configure(values=labels, state="normal")
            self._file_combo.set(t("split.no_file"))
        else:
            self._file_combo.configure(
                values=[t("split.no_project_files")], state="disabled",
            )

    def _collect_project_files(self) -> List[Tuple[str, str, object]]:
        """蒐集專案內的 PDF 檔案，依 _filter_group 過濾"""
        files: List[Tuple[str, str, object]] = []
        if not self._project:
            return files
        if self._filter_group is None:
            for f in self._project.ungrouped_files:
                if f.original_path.lower().endswith(".pdf"):
                    files.append((f.display_name, f.original_path, None))
        else:
            for f in self._filter_group.files:
                if f.original_path.lower().endswith(".pdf"):
                    files.append((f.display_name, f.original_path, self._filter_group))
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
        ctk.CTkButton(
            parent, text=t("split.clear_splits"), height=24,
            fg_color="transparent", border_width=1,
            text_color=("gray40", "gray70"),
            command=self._clear_all_splits,
        ).pack(anchor="w", padx=8, pady=(0, 4))
        self._assign_scroll = ctk.CTkScrollableFrame(parent)
        self._assign_scroll.pack(fill="both", expand=True, padx=4, pady=4)
        subfolder_row = ctk.CTkFrame(parent, fg_color="transparent")
        subfolder_row.pack(fill="x", padx=8, pady=(4, 2))
        self._subfolder_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            subfolder_row, text=t("split.use_subfolder"),
            variable=self._subfolder_var,
        ).pack(side="left")
        self._subfolder_entry = ctk.CTkEntry(subfolder_row, width=80)
        self._subfolder_entry.pack(side="left", padx=(8, 0))
        self._subfolder_entry.insert(0, t("split.subfolder_default"))
        out_frame = ctk.CTkFrame(parent, fg_color="transparent")
        out_frame.pack(fill="x", padx=8, pady=(2, 8))
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
        entry = self._file_map.get(choice)
        if entry:
            path, group = entry
            self._source_group = group
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
            from services.pdf_service import get_page_count
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
            self._execute_btn.configure(state="disabled")
            for child in self._page_scroll.winfo_children():
                child.destroy()
            ctk.CTkLabel(
                self._page_scroll,
                text=t("split.loading", count=self._page_count),
                font=ctk.CTkFont(size=14), text_color="gray",
            ).pack(expand=True, pady=40)
            import threading
            threading.Thread(
                target=self._render_thumbnails_bg,
                args=(path,),
                daemon=True,
            ).start()
        except Exception as e:
            messagebox.showerror(t("dialog.error"), str(e))

    def _render_thumbnails_bg(self, path: str):
        """背景執行緒算繪縮圖"""
        try:
            from services.pdf_service import render_page_thumbnails
            pil_images = render_page_thumbnails(path, max_width=self._THUMB_WIDTH)
            self.after(0, self._on_thumbnails_ready, pil_images)
        except Exception as e:
            self.after(0, self._on_thumbnails_error, str(e))

    def _on_thumbnails_ready(self, pil_images):
        """縮圖算繪完成，更新 UI"""
        self._ctk_images = [
            ctk.CTkImage(
                light_image=img, dark_image=img,
                size=(img.width, img.height),
            )
            for img in pil_images
        ]
        self._split_starts = {0}
        self._deleted_pages = set()
        self._render_page_grid()
        self._update_assignment_panel()
        self._execute_btn.configure(state="normal")

    def _on_thumbnails_error(self, error_msg: str):
        """縮圖算繪失敗"""
        from tkinter import messagebox
        messagebox.showerror(t("dialog.error"), error_msg)
        for child in self._page_scroll.winfo_children():
            child.destroy()

    # --- 頁面縮圖顯示 ---

    def _render_page_grid(self):
        try:
            pack_info = self._page_scroll.pack_info()
            self._page_scroll.pack_forget()
        except Exception:
            pack_info = None
        for child in self._page_scroll.winfo_children():
            child.destroy()
        if not self._ctk_images:
            if pack_info:
                self._page_scroll.pack(**pack_info)
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
        if pack_info:
            self._page_scroll.pack(**pack_info)

    def _build_section_header(
        self, sec_idx: int, start: int, end: int, color: str,
    ):
        total = end - start + 1
        actual = sum(
            1 for p in range(start, end + 1) if p not in self._deleted_pages
        )
        if start == end:
            page_str = f"p.{start + 1}"
        else:
            page_str = f"p.{start + 1}-{end + 1}"
        count_str = f"({actual}/{total})" if actual < total else f"({total})"
        ctk.CTkLabel(
            self._page_scroll,
            text=f"  {sec_idx + 1}  |  {page_str}  {count_str}",
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
            is_deleted = page_idx in self._deleted_pages
            border_color = "gray50" if is_deleted else color
            outer = ctk.CTkFrame(row_frame, fg_color=border_color, corner_radius=6)
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
            num_text = f"{page_idx + 1} X" if is_deleted else str(page_idx + 1)
            num_color = "gray50" if is_deleted else None
            num_lbl = ctk.CTkLabel(
                inner, text=num_text,
                font=ctk.CTkFont(size=10),
                text_color=num_color,
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

    def _clear_all_splits(self):
        """移除所有分割點，回到單一區段"""
        self._split_starts = {0}
        self._render_page_grid()
        self._section_name_vars = {}
        self._update_assignment_panel()

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
        """開啟頁面放大預覽視窗"""
        if self._preview_win and self._preview_win.winfo_exists():
            self._preview_win.destroy()
        try:
            self.grab_release()
        except Exception:
            pass
        preview = ctk.CTkToplevel(self)
        preview.transient(self)
        self._preview_win = preview
        self._preview_page_idx = page_idx
        # 頂部列：頁碼
        top_bar = ctk.CTkFrame(preview, fg_color="transparent")
        top_bar.pack(fill="x", padx=8, pady=(4, 0))
        self._preview_page_label = ctk.CTkLabel(
            top_bar, text="", font=ctk.CTkFont(size=13),
        )
        self._preview_page_label.pack(expand=True)
        # 中間：[<] 譜面 [>]（小型圓角按鈕垂直置中）
        middle = ctk.CTkFrame(preview, fg_color="transparent")
        middle.pack(fill="both", expand=True, pady=2)
        left_col = ctk.CTkFrame(middle, fg_color="transparent")
        left_col.pack(side="left", fill="y", padx=(2, 0))
        self._prev_nav = ctk.CTkButton(
            left_col, text="\u276E", width=32, height=48,
            corner_radius=16, border_width=0,
            fg_color=("gray84", "gray26"),
            hover_color=("gray72", "gray38"),
            text_color=("gray35", "gray80"),
            font=ctk.CTkFont(size=18),
            command=self._preview_prev,
        )
        self._prev_nav.pack(expand=True)
        self._preview_scroll = ctk.CTkScrollableFrame(middle)
        self._preview_scroll.pack(side="left", fill="both", expand=True)
        self._preview_img_label = ctk.CTkLabel(self._preview_scroll, text="")
        self._preview_img_label.pack(padx=2, pady=2)
        right_col = ctk.CTkFrame(middle, fg_color="transparent")
        right_col.pack(side="right", fill="y", padx=(0, 2))
        self._next_nav = ctk.CTkButton(
            right_col, text="\u276F", width=32, height=48,
            corner_radius=16, border_width=0,
            fg_color=("gray84", "gray26"),
            hover_color=("gray72", "gray38"),
            text_color=("gray35", "gray80"),
            font=ctk.CTkFont(size=18),
            command=self._preview_next,
        )
        self._next_nav.pack(expand=True)
        # 底部列：分割點 + 刪除（統一高度，與閱讀區留間距）
        bottom_bar = ctk.CTkFrame(preview, fg_color="transparent")
        bottom_bar.pack(fill="x", padx=12, pady=(6, 12))
        self._split_toggle_btn = ctk.CTkButton(
            bottom_bar, text="", height=32,
            corner_radius=16,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="white",
            command=self._preview_toggle_split,
        )
        self._split_toggle_btn.pack(side="left", padx=(0, 8))
        self._delete_toggle_btn = ctk.CTkButton(
            bottom_bar, text="", height=32,
            corner_radius=16,
            font=ctk.CTkFont(size=12),
            command=self._preview_toggle_delete,
        )
        self._delete_toggle_btn.pack(side="left")
        # 根據螢幕高度計算視窗與渲染尺寸，確保 A4 完整顯示
        screen_h = preview.winfo_screenheight()
        win_h = min(int(screen_h * 0.84), 950)
        chrome_h = 90
        self._render_width = min(600, int((win_h - chrome_h) / 1.414))
        win_w = self._render_width + 130
        preview.geometry(f"{win_w}x{win_h}")
        self._render_preview_page(page_idx)
        # 鍵盤
        preview.bind("<Left>", lambda e: self._preview_prev())
        preview.bind("<Right>", lambda e: self._preview_next())
        preview.bind("<Escape>", lambda e: self._close_preview())
        preview.bind("<space>", lambda e: self._preview_toggle_split())
        preview.bind("<Delete>", lambda e: self._preview_toggle_delete())
        preview.focus_force()
        preview.protocol("WM_DELETE_WINDOW", self._close_preview)

    def _render_preview_page(self, page_idx: int):
        """算繪並顯示指定頁面"""
        try:
            from services.pdf_service import render_single_page
            pil_img = render_single_page(
                self._pdf_path, page_idx,
                max_width=getattr(self, "_render_width", 600),
            )
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
        self._prev_nav.configure(
            state="normal" if page_idx > 0 else "disabled",
        )
        self._next_nav.configure(
            state="normal" if page_idx < self._page_count - 1 else "disabled",
        )
        self._update_preview_split_indicator()
        self._update_preview_delete_indicator()

    def _update_preview_split_indicator(self):
        """更新分割點指示"""
        idx = self._preview_page_idx
        is_split = idx in self._split_starts
        sec_idx = self._get_section_for_page(idx)
        color = SECTION_COLORS[sec_idx % len(SECTION_COLORS)]
        if idx == 0:
            self._split_toggle_btn.configure(
                text=t("split.mark_split"), state="disabled",
                fg_color=color, hover_color=color,
            )
        elif is_split:
            self._split_toggle_btn.configure(
                text=t("split.remove_split"), state="normal",
                fg_color=color, hover_color=color,
            )
        else:
            self._split_toggle_btn.configure(
                text=t("split.mark_split"), state="normal",
                fg_color=color, hover_color=color,
            )

    def _update_preview_delete_indicator(self):
        """更新刪除頁面指示"""
        idx = self._preview_page_idx
        is_deleted = idx in self._deleted_pages
        if is_deleted:
            self._delete_toggle_btn.configure(
                text=t("split.restore_page"),
                fg_color=("#2563EB", "#1D4ED8"),
                hover_color=("#3B82F6", "#2563EB"),
            )
        else:
            self._delete_toggle_btn.configure(
                text=t("split.delete_page"),
                fg_color=("#DC2626", "#991B1B"),
                hover_color=("#EF4444", "#DC2626"),
            )

    def _get_section_for_page(self, page_idx: int) -> int:
        """取得頁面所屬的分譜索引"""
        sections = self._get_sections()
        for sec_idx, (start, end) in enumerate(sections):
            if start <= page_idx <= end:
                return sec_idx
        return 0

    def _preview_toggle_split(self):
        """在預覽中切換分割點"""
        idx = self._preview_page_idx
        if idx == 0:
            return
        if idx in self._split_starts:
            self._split_starts.discard(idx)
        else:
            self._split_starts.add(idx)
        self._render_page_grid()
        self._update_assignment_panel()
        self._update_preview_split_indicator()

    def _preview_toggle_delete(self):
        """在預覽中切換頁面刪除"""
        idx = self._preview_page_idx
        if idx in self._deleted_pages:
            self._deleted_pages.discard(idx)
        else:
            self._deleted_pages.add(idx)
        self._render_page_grid()
        self._update_assignment_panel()
        self._update_preview_delete_indicator()

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
        try:
            self.grab_set()
        except Exception:
            pass

    # --- 分譜指派面板 ---

    def _get_used_instruments(self) -> set:
        """取得專案中已指派給群組的樂器名稱"""
        used = set()
        if not self._project:
            return used
        instruments = self._project.instruments
        for group in self._project.groups:
            for idx in group.selected_instruments:
                if idx < len(instruments):
                    used.add(instruments[idx])
        return used

    def _next_unused_instrument(self, used: set, start_after: int = -1) -> str:
        """取得下一個未使用的樂器名稱

        Args:
            used: 已使用的樂器名稱集合
            start_after: 從此索引之後開始搜尋（-1 表示從頭）
        """
        instruments = self._project.instruments
        for i in range(start_after + 1, len(instruments)):
            if instruments[i] not in used:
                return instruments[i]
        return t("split.part_default", index=len(used) + 1)

    def _cycle_instrument(self, sec_idx: int, direction: int):
        """切換指定分譜的樂器，後續分譜連鎖更新"""
        var = self._section_name_vars.get(sec_idx)
        if not var or not self._project.instruments:
            return
        current = var.get()
        instruments = self._project.instruments
        try:
            cur_idx = instruments.index(current)
        except ValueError:
            cur_idx = -1 if direction > 0 else len(instruments)
        new_idx = (cur_idx + direction) % len(instruments)
        var.set(instruments[new_idx])
        # 連鎖：從變更點之後依序重新指派
        used: Set[str] = set()
        for i in range(sec_idx + 1):
            v = self._section_name_vars.get(i)
            if v:
                used.add(v.get())
        search_from = new_idx
        sections = self._get_sections()
        for i in range(sec_idx + 1, len(sections)):
            v = self._section_name_vars.get(i)
            if not v:
                continue
            name = self._next_unused_instrument(used, start_after=search_from)
            v.set(name)
            used.add(name)
            try:
                search_from = instruments.index(name)
            except ValueError:
                search_from = len(instruments)

    def _update_assignment_panel(self):
        old_names = {
            idx: var.get() for idx, var in self._section_name_vars.items()
        }
        for child in self._assign_scroll.winfo_children():
            child.destroy()
        sections = self._get_sections()
        self._section_name_vars = {}
        instruments = self._project.instruments
        used_names: Set[str] = set(self._get_used_instruments())
        last_inst_idx = -1
        for sec_idx, (start, end) in enumerate(sections):
            color = SECTION_COLORS[sec_idx % len(SECTION_COLORS)]
            row = ctk.CTkFrame(self._assign_scroll, fg_color="transparent")
            row.pack(fill="x", pady=3)
            dot = ctk.CTkFrame(
                row, width=14, height=14,
                fg_color=color, corner_radius=7,
            )
            dot.pack(side="left", padx=(0, 4))
            dot.pack_propagate(False)
            var = ctk.StringVar()
            if sec_idx in old_names:
                name = old_names[sec_idx]
            elif instruments:
                name = self._next_unused_instrument(used_names, start_after=last_inst_idx)
            else:
                name = t("split.part_default", index=sec_idx + 1)
            used_names.add(name)
            try:
                last_inst_idx = instruments.index(name)
            except (ValueError, AttributeError):
                pass
            var.set(name)
            self._section_name_vars[sec_idx] = var
            if instruments:
                ctk.CTkButton(
                    row, text="\u25C0", width=24, height=24,
                    fg_color="transparent", hover_color=("gray80", "gray30"),
                    command=lambda i=sec_idx: self._cycle_instrument(i, -1),
                ).pack(side="left", padx=1)
            ctk.CTkEntry(
                row, textvariable=var, width=110, height=28,
            ).pack(side="left")
            if instruments:
                ctk.CTkButton(
                    row, text="\u25B6", width=24, height=24,
                    fg_color="transparent", hover_color=("gray80", "gray30"),
                    command=lambda i=sec_idx: self._cycle_instrument(i, 1),
                ).pack(side="left", padx=1)
            actual = sum(
                1 for p in range(start, end + 1) if p not in self._deleted_pages
            )
            total = end - start + 1
            if start == end:
                range_text = f"p.{start + 1}"
            else:
                range_text = f"p.{start + 1}-{end + 1}"
            count_str = f"({actual}/{total})" if actual < total else f"({total})"
            ctk.CTkLabel(
                row, text=f"{range_text} {count_str}",
                font=ctk.CTkFont(size=11), text_color="gray",
            ).pack(side="left", padx=(4, 0))


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
        base_dir = self._output_dir or os.path.dirname(self._pdf_path)
        if self._subfolder_var.get():
            subfolder_name = self._subfolder_entry.get().strip()
            if not subfolder_name:
                subfolder_name = t("split.subfolder_default")
            output_dir = os.path.join(base_dir, subfolder_name)
        else:
            output_dir = base_dir
        sections = self._get_sections()
        try:
            from services.pdf_service import extract_pages
            os.makedirs(output_dir, exist_ok=True)
            split_files: List[FileInfo] = []
            split_instruments: List[str] = []
            for sec_idx, (start, end) in enumerate(sections):
                pages = [
                    p for p in range(start, end + 1)
                    if p not in self._deleted_pages
                ]
                if not pages:
                    continue
                var = self._section_name_vars.get(sec_idx)
                name = var.get().strip() if var else f"Part {sec_idx + 1}"
                safe_name = _sanitize_filename(name)
                if not safe_name.lower().endswith(".pdf"):
                    safe_name += ".pdf"
                output_path = os.path.join(output_dir, safe_name)
                extract_pages(self._pdf_path, pages, output_path)
                split_files.append(FileInfo(
                    original_path=output_path,
                    display_name=os.path.basename(output_path),
                ))
                split_instruments.append(name)
            if not split_files:
                messagebox.showinfo(t("dialog.info"), t("dialog.info.no_files"))
                return
            if self._on_split_complete:
                self._on_split_complete(
                    split_files,
                    split_instruments,
                    self._source_group,
                    self._pdf_path,
                )
            messagebox.showinfo(
                t("dialog.complete"),
                t("split.done", count=len(split_files)),
            )
            self.destroy()
        except Exception as e:
            messagebox.showerror(t("dialog.error"), str(e))


def _sanitize_filename(name: str) -> str:
    """移除檔名中的非法字元"""
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name.strip() or "Part"

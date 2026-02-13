# -*- coding: utf-8 -*-
"""
泠靈小工具 - 應用程式進入點

啟動 customtkinter 主視窗。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import customtkinter as ctk
from core.models import Project
from ui.main_window import MainWindow


def main():
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    app = ctk.CTk()
    app.title("泠靈小工具")
    app.geometry("1200x800")
    app.minsize(900, 600)
    project = Project()
    main_window = MainWindow(app, project)
    # 延遲載入群組面板，確保主視窗已建立
    app.after(100, lambda: _init_group_panel(app, main_window))
    # 鍵盤快捷鍵
    app.bind("<Control-n>", lambda e: main_window._new_project())
    app.bind("<Control-o>", lambda e: main_window._open_project())
    app.bind("<Control-s>", lambda e: main_window._save_project())
    app.bind("<Control-z>", lambda e: main_window._undo_last())
    # 關閉視窗確認
    app.protocol("WM_DELETE_WINDOW", lambda: _on_close(app, main_window))
    app.mainloop()


def _init_group_panel(app: ctk.CTk, main_window: MainWindow):
    from ui.group_panel import GroupPanel
    group_panel = GroupPanel(
        main_window._center_panel,
        main_window.project,
        main_window,
    )
    group_panel.pack(fill="both", expand=True)
    main_window.set_group_panel(group_panel)


def _on_close(app: ctk.CTk, main_window: MainWindow):
    if main_window._modified:
        from tkinter import messagebox
        result = messagebox.askyesnocancel("關閉程式", "是否儲存目前的專案？")
        if result is None:
            return
        if result:
            main_window._save_project()
    app.destroy()


if __name__ == "__main__":
    main()

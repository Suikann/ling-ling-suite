# -*- coding: utf-8 -*-
"""
從譜庫下載的挑選視窗

譜庫視窗版本頁的「下載…」開這個視窗：列出版本資料夾（含子資料夾）裡的 PDF，勾選幾份，
或勾「整個版本」（全部勾上、保留子資料夾）。按「下載」後由呼叫端問要下載到哪個資料夾，
放的位置與下載交給 core/catalog_download.py 的 folder_items 與下載服務。

使用範例：
    dialog = CatalogDownloadDialog(page.folder_pdfs(), window)
    if dialog.exec() == QDialog.Accepted:
        items = folder_items(dialog.chosen(), folder, dialog.whole())
"""
from typing import List, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from core.catalog_models import FolderPdf
from core.locale import t


class CatalogDownloadDialog(QDialog):
    """勾選版本資料夾裡要下載的 PDF，或整個版本"""

    def __init__(self, pdfs: Sequence[FolderPdf], parent=None):
        """
        Args:
            pdfs: 版本資料夾裡全部的 PDF（依路徑排序）
            parent: 父元件
        """
        super().__init__(parent)
        self.setWindowTitle(t("download.title"))
        self.resize(520, 480)
        layout = QVBoxLayout(self)
        self._whole = QCheckBox(t("catalog.download.whole"))
        self._whole.setChecked(True)
        self._whole.toggled.connect(self._on_whole_toggled)
        layout.addWidget(self._whole)
        self._files = QListWidget()
        for pdf in pdfs:
            item = QListWidgetItem(pdf.path)
            item.setData(Qt.UserRole, pdf)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self._files.addItem(item)
        self._files.itemChanged.connect(lambda _item: self._show_count())
        layout.addWidget(self._files, stretch=1)
        buttons = QHBoxLayout()
        self._count = QLabel()
        buttons.addWidget(self._count)
        buttons.addStretch()
        cancel = QPushButton(t("dialog.cancel_btn"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self._start = QPushButton(t("catalog.download.start"))
        self._start.clicked.connect(self.accept)
        buttons.addWidget(self._start)
        layout.addLayout(buttons)
        self._on_whole_toggled(True)

    def whole(self) -> bool:
        """是否下載整個版本（保留子資料夾）"""
        return self._whole.isChecked()

    def chosen(self) -> List[FolderPdf]:
        """勾了的 PDF（整個版本時是全部），依清單的順序"""
        return [
            self._files.item(i).data(Qt.UserRole) for i in range(self._files.count())
            if self.whole() or self._files.item(i).checkState() == Qt.Checked
        ]

    def _on_whole_toggled(self, checked: bool) -> None:
        """勾「整個版本」時全部勾上、清單不能改；取消時可以逐份勾選"""
        if checked:
            for i in range(self._files.count()):
                self._files.item(i).setCheckState(Qt.Checked)
        self._files.setEnabled(not checked)
        self._show_count()

    def _show_count(self) -> None:
        """寫出勾了幾份；一份都沒勾時「下載」按不下去"""
        count = len(self.chosen())
        self._count.setText(t("catalog.download.count", count=count))
        self._start.setEnabled(count > 0)

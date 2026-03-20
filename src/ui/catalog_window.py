# -*- coding: utf-8 -*-
"""
譜庫瀏覽器視窗

提供譜庫的樹狀瀏覽、搜尋、曲目編輯與分譜狀態管理。
"""
from dataclasses import replace
from typing import Dict, List, Optional
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QLabel, QLineEdit, QPushButton,
    QToolBar, QStatusBar, QMessageBox, QComboBox, QFormLayout,
    QGroupBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QTextEdit, QDialog, QDialogButtonBox,
    QAbstractItemView, QInputDialog,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QBrush
from core.locale import t
from core.catalog_constants import PartStatus, PerformanceStatus
from core.catalog_models import (
    Composer, Piece, Edition, Movement, Part, Performance, PieceDetail,
)
from services.sheets_service import SheetsService
from services.drive_service import DriveService
from services.google_auth_service import GoogleAuthService
from services.preferences_service import PreferencesService


_STATUS_COLORS = {
    PartStatus.AVAILABLE.value: QColor("#2ecc71"),
    PartStatus.MISSING.value: QColor("#e74c3c"),
    PartStatus.DAMAGED.value: QColor("#f39c12"),
    PartStatus.UNKNOWN.value: QColor("#95a5a6"),
}


class CatalogWindow(QMainWindow):
    """譜庫管理主視窗"""

    def __init__(
        self, auth_service: GoogleAuthService,
        preferences: PreferencesService, parent=None,
    ):
        super().__init__(parent)
        self._auth = auth_service
        self._prefs = preferences
        self._sheets: Optional[SheetsService] = None
        self._drive: Optional[DriveService] = None
        self._composers: List[Composer] = []
        self._pieces: List[Piece] = []
        self._current_detail: Optional[PieceDetail] = None
        self.setWindowTitle(t("catalog.title"))
        self.resize(1200, 750)
        self._build_ui()
        QTimer.singleShot(100, self._init_services)

    def _build_ui(self):
        self._toolbar = QToolBar()
        self._toolbar.setMovable(False)
        self.addToolBar(self._toolbar)
        add_piece_btn = QPushButton(t("catalog.toolbar.add_piece"))
        add_piece_btn.clicked.connect(self._add_piece)
        self._toolbar.addWidget(add_piece_btn)
        self._toolbar.addSeparator()
        refresh_btn = QPushButton(t("catalog.toolbar.refresh"))
        refresh_btn.clicked.connect(self._refresh_all)
        self._toolbar.addWidget(refresh_btn)
        self._toolbar.addSeparator()
        self._search_entry = QLineEdit()
        self._search_entry.setPlaceholderText(t("catalog.toolbar.search"))
        self._search_entry.setFixedWidth(200)
        self._search_entry.textChanged.connect(self._on_search)
        self._toolbar.addWidget(self._search_entry)
        self._toolbar.addSeparator()
        self._toolbar.addWidget(QLabel(f" {t('catalog.groupby')} "))
        self._groupby_combo = QComboBox()
        self._groupby_combo.addItem(t("catalog.groupby.composer"), "composer")
        self._groupby_combo.addItem(t("catalog.groupby.genre"), "genre")
        self._groupby_combo.addItem(t("catalog.groupby.instrumentation"), "instrumentation")
        self._groupby_combo.addItem(t("catalog.groupby.title"), "title")
        self._groupby_combo.currentIndexChanged.connect(
            lambda: self._rebuild_tree(self._search_entry.text()),
        )
        self._toolbar.addWidget(self._groupby_combo)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        splitter = QSplitter(Qt.Horizontal)
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setMinimumWidth(280)
        self._tree.currentItemChanged.connect(self._on_tree_select)
        splitter.addWidget(self._tree)
        self._detail_area = QWidget()
        self._detail_layout = QVBoxLayout(self._detail_area)
        self._detail_layout.setContentsMargins(4, 4, 4, 4)
        self._detail_placeholder = QLabel(t("catalog.tree.no_items"))
        self._detail_placeholder.setAlignment(Qt.AlignCenter)
        self._detail_placeholder.setStyleSheet("color: gray; font-size: 14px;")
        self._detail_layout.addWidget(self._detail_placeholder)
        splitter.addWidget(self._detail_area)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter)
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

    def _init_services(self):
        """初始化 Google API 服務"""
        creds = self._auth.get_credentials()
        if not creds:
            self._status_bar.showMessage(t("catalog.no_connection"))
            return
        spreadsheet_id = self._prefs.get("catalog_spreadsheet_id") or ""
        if not spreadsheet_id:
            self._status_bar.showMessage(t("catalog.no_connection"))
            return
        try:
            self._sheets = SheetsService(creds, spreadsheet_id)
            self._drive = DriveService(creds)
            self._refresh_all()
        except Exception as e:
            self._status_bar.showMessage(t("catalog.error", error=str(e)))

    def _refresh_all(self):
        """重新載入所有資料"""
        if not self._sheets:
            return
        try:
            self._composers = self._sheets.list_composers()
            self._pieces = self._sheets.list_pieces()
            self._rebuild_tree()
            self._status_bar.showMessage(
                t("catalog.status.stats",
                  composers=len(self._composers),
                  pieces=len(self._pieces)),
            )
        except Exception as e:
            self._status_bar.showMessage(t("catalog.error", error=str(e)))

    def _rebuild_tree(self, filter_text: str = ""):
        """依選定的分類方式重建樹狀結構"""
        self._tree.clear()
        q = filter_text.lower() if filter_text else ""
        groupby = self._groupby_combo.currentData() or "composer"
        composer_map = {c.id: c for c in self._composers}
        filtered = []
        for piece in self._pieces:
            display = piece.title_short or piece.title
            if q and not self._piece_matches_search(piece, composer_map, q):
                continue
            filtered.append(piece)
        if groupby == "title":
            self._build_flat_tree(sorted(filtered, key=lambda p: p.title))
        elif groupby == "composer":
            self._build_grouped_tree(
                filtered, composer_map,
                key_fn=lambda p: p.composer_id,
                label_fn=lambda kid: (
                    (composer_map[kid].name_short or composer_map[kid].name)
                    if kid in composer_map else t("catalog.tree.no_composer")
                ),
            )
        elif groupby == "genre":
            self._build_grouped_tree(
                filtered, composer_map,
                key_fn=lambda p: p.genre or t("catalog.tree.no_composer"),
                label_fn=lambda g: g,
            )
        elif groupby == "instrumentation":
            self._build_grouped_tree(
                filtered, composer_map,
                key_fn=lambda p: p.instrumentation or t("catalog.tree.no_composer"),
                label_fn=lambda g: g,
            )
        if self._tree.topLevelItemCount() == 0:
            empty = QTreeWidgetItem([t("catalog.tree.no_items")])
            empty.setFlags(Qt.NoItemFlags)
            self._tree.addTopLevelItem(empty)

    def _piece_matches_search(
        self, piece: Piece, composer_map: Dict, q: str,
    ) -> bool:
        """判斷曲目是否符合搜尋條件"""
        if q in piece.title.lower():
            return True
        if q in (piece.title_short or "").lower():
            return True
        if q in piece.genre.lower():
            return True
        if q in piece.opus.lower():
            return True
        if q in piece.instrumentation.lower():
            return True
        composer = composer_map.get(piece.composer_id)
        if composer and (q in composer.name.lower() or q in (composer.name_short or "").lower()):
            return True
        return False

    def _build_flat_tree(self, pieces: List[Piece]):
        """建立扁平（無分組）的樹狀結構"""
        for piece in pieces:
            display = piece.title_short or piece.title
            if piece.opus:
                display += f" ({piece.opus})"
            node = QTreeWidgetItem([display])
            node.setData(0, Qt.UserRole, ("piece", piece.id))
            self._tree.addTopLevelItem(node)

    def _build_grouped_tree(self, pieces, composer_map, key_fn, label_fn):
        """建立分組的樹狀結構"""
        groups: Dict[str, List[Piece]] = {}
        for piece in pieces:
            key = key_fn(piece)
            if key not in groups:
                groups[key] = []
            groups[key].append(piece)
        for key in sorted(groups.keys()):
            group_node = QTreeWidgetItem([label_fn(key)])
            group_node.setData(0, Qt.UserRole, ("group", key))
            for piece in groups[key]:
                display = piece.title_short or piece.title
                if piece.opus:
                    display += f" ({piece.opus})"
                piece_node = QTreeWidgetItem([display])
                piece_node.setData(0, Qt.UserRole, ("piece", piece.id))
                group_node.addChild(piece_node)
            self._tree.addTopLevelItem(group_node)
            group_node.setExpanded(True)

    def _on_search(self, text: str):
        """搜尋篩選"""
        self._rebuild_tree(text)

    def _on_tree_select(self, current: QTreeWidgetItem, previous):
        """樹狀結構選取變更"""
        if not current:
            return
        data = current.data(0, Qt.UserRole)
        if not data:
            return
        node_type, node_id = data
        if node_type == "piece":
            self._show_piece_detail(node_id)

    # --- 清除並重建右側面板 ---

    def _clear_detail(self):
        """清除右側面板內容"""
        while self._detail_layout.count():
            child = self._detail_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    # --- 曲目 ---

    def _add_piece(self):
        """新增曲目（作曲家不存在時自動建立）"""
        if not self._sheets:
            return
        dialog = _NewPieceDialog(self._composers, self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            composer_id = dialog.composer_id
            if not composer_id and dialog.composer_name:
                composer_id = self._sheets.create_composer(
                    Composer(name=dialog.composer_name),
                )
            piece = replace(dialog.piece, composer_id=composer_id)
            piece_id = self._sheets.create_piece(piece)
            self._refresh_all()
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _show_piece_detail(self, piece_id: str):
        """顯示曲目完整資訊"""
        if not self._sheets:
            return
        detail = self._sheets.get_piece_detail(piece_id)
        if not detail:
            return
        self._current_detail = detail
        self._clear_detail()
        tabs = QTabWidget()
        tabs.addTab(
            self._build_piece_info_tab(detail), t("catalog.piece.title"),
        )
        tabs.addTab(
            self._build_editions_tab(detail), t("catalog.edition.title"),
        )
        tabs.addTab(
            self._build_movements_tab(detail), t("catalog.movement.title"),
        )
        tabs.addTab(
            self._build_parts_tab(detail), t("catalog.part.title"),
        )
        tabs.addTab(
            self._build_performances_tab(detail), t("catalog.performance.title"),
        )
        self._detail_layout.addWidget(tabs)

    def _build_piece_info_tab(self, detail: PieceDetail) -> QWidget:
        """建立曲目資訊頁籤"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()
        piece = detail.piece
        self._piece_title_edit = QLineEdit(piece.title)
        form.addRow(t("catalog.piece.name"), self._piece_title_edit)
        self._piece_short_edit = QLineEdit(piece.title_short)
        form.addRow(t("catalog.piece.name_short"), self._piece_short_edit)
        self._piece_composer_combo = QComboBox()
        for c in self._composers:
            self._piece_composer_combo.addItem(
                c.name_short or c.name, c.id,
            )
        idx = next(
            (i for i, c in enumerate(self._composers)
             if c.id == piece.composer_id), -1,
        )
        if idx >= 0:
            self._piece_composer_combo.setCurrentIndex(idx)
        form.addRow(t("catalog.piece.composer"), self._piece_composer_combo)
        self._piece_opus_edit = QLineEdit(piece.opus)
        form.addRow(t("catalog.piece.opus"), self._piece_opus_edit)
        self._piece_catalog_edit = QLineEdit(piece.catalog_number)
        form.addRow(t("catalog.piece.catalog_number"), self._piece_catalog_edit)
        self._piece_genre_edit = QLineEdit(piece.genre)
        form.addRow(t("catalog.piece.genre"), self._piece_genre_edit)
        self._piece_instrumentation_edit = QLineEdit(piece.instrumentation)
        form.addRow(t("catalog.piece.instrumentation"), self._piece_instrumentation_edit)
        self._piece_duration_edit = QLineEdit(piece.duration_minutes)
        self._piece_duration_edit.setFixedWidth(80)
        form.addRow(t("catalog.piece.duration"), self._piece_duration_edit)
        self._piece_difficulty_edit = QLineEdit(piece.difficulty_level)
        form.addRow(t("catalog.piece.difficulty"), self._piece_difficulty_edit)
        self._piece_notes_edit = QTextEdit(piece.notes)
        self._piece_notes_edit.setMaximumHeight(80)
        form.addRow(t("catalog.piece.notes"), self._piece_notes_edit)
        layout.addLayout(form)
        btn_row = QHBoxLayout()
        save_btn = QPushButton(t("catalog.save"))
        save_btn.clicked.connect(lambda: self._save_piece(detail))
        btn_row.addWidget(save_btn)
        del_btn = QPushButton(t("catalog.piece.delete"))
        del_btn.setStyleSheet("background-color: #c0392b; color: white;")
        del_btn.clicked.connect(lambda: self._delete_piece(detail.piece))
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addStretch()
        return widget

    def _save_piece(self, detail: PieceDetail):
        """儲存曲目變更"""
        composer_id = self._piece_composer_combo.currentData() or ""
        updated = replace(
            detail.piece,
            title=self._piece_title_edit.text().strip(),
            title_short=self._piece_short_edit.text().strip(),
            composer_id=composer_id,
            opus=self._piece_opus_edit.text().strip(),
            catalog_number=self._piece_catalog_edit.text().strip(),
            genre=self._piece_genre_edit.text().strip(),
            instrumentation=self._piece_instrumentation_edit.text().strip(),
            duration_minutes=self._piece_duration_edit.text().strip(),
            difficulty_level=self._piece_difficulty_edit.text().strip(),
            notes=self._piece_notes_edit.toPlainText().strip(),
        )
        try:
            self._sheets.update_piece(updated)
            self._refresh_all()
            self._show_piece_detail(updated.id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _delete_piece(self, piece: Piece):
        """刪除曲目及所有關聯資料"""
        result = QMessageBox.question(
            self, t("catalog.piece.delete"),
            t("catalog.piece.confirm_delete", name=piece.title),
        )
        if result != QMessageBox.Yes:
            return
        try:
            if self._current_detail:
                for perf in self._current_detail.performances:
                    self._sheets.delete_performance(perf.id)
                for part in self._current_detail.parts:
                    self._sheets.delete_part(part.id)
                for edition in self._current_detail.editions:
                    self._sheets.delete_edition(edition.id)
                for movement in self._current_detail.movements:
                    self._sheets.delete_movement(movement.id)
            self._sheets.delete_piece(piece.id)
            self._refresh_all()
            self._clear_detail()
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    # --- 版本頁籤 ---

    def _build_editions_tab(self, detail: PieceDetail) -> QWidget:
        """建立版本管理頁籤"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        btn_row = QHBoxLayout()
        add_btn = QPushButton(t("catalog.edition.add"))
        add_btn.clicked.connect(lambda: self._add_edition(detail.piece.id))
        btn_row.addWidget(add_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        for edition in detail.editions:
            is_active = edition.id == detail.piece.active_edition_id
            group = QGroupBox(
                f"{edition.publisher}"
                f"{' ' + t('catalog.edition.active') if is_active else ''}",
            )
            form = QFormLayout(group)
            pub_edit = QLineEdit(edition.publisher)
            form.addRow(t("catalog.edition.publisher"), pub_edit)
            label_edit = QLineEdit(edition.edition_label)
            form.addRow(t("catalog.edition.label"), label_edit)
            cat_edit = QLineEdit(edition.catalog_number)
            form.addRow(t("catalog.edition.catalog_number"), cat_edit)
            year_edit = QLineEdit(edition.year)
            year_edit.setFixedWidth(80)
            form.addRow(t("catalog.edition.year"), year_edit)
            folder_edit = QLineEdit(edition.drive_folder_id)
            form.addRow(t("catalog.edition.drive_folder"), folder_edit)
            notes_edit = QLineEdit(edition.notes)
            form.addRow(t("catalog.edition.notes"), notes_edit)
            ed_btn_row = QHBoxLayout()
            save_btn = QPushButton(t("catalog.save"))
            save_btn.clicked.connect(
                lambda checked=False, e=edition, pw=pub_edit, lw=label_edit,
                cw=cat_edit, yw=year_edit, fw=folder_edit, nw=notes_edit:
                self._save_edition(e, pw, lw, cw, yw, fw, nw),
            )
            ed_btn_row.addWidget(save_btn)
            if not is_active:
                active_btn = QPushButton(t("catalog.edition.set_active"))
                active_btn.clicked.connect(
                    lambda checked=False, eid=edition.id:
                    self._set_active_edition(detail.piece, eid),
                )
                ed_btn_row.addWidget(active_btn)
            del_btn = QPushButton(t("catalog.edition.delete"))
            del_btn.setStyleSheet("background-color: #c0392b; color: white;")
            del_btn.clicked.connect(
                lambda checked=False, eid=edition.id:
                self._delete_edition(eid, detail.piece.id),
            )
            ed_btn_row.addWidget(del_btn)
            ed_btn_row.addStretch()
            form.addRow("", ed_btn_row)
            layout.addWidget(group)
        layout.addStretch()
        return widget

    def _add_edition(self, piece_id: str):
        """新增版本"""
        publisher, ok = QInputDialog.getText(
            self, t("catalog.edition.add"), t("catalog.edition.publisher"),
        )
        if not ok or not publisher.strip():
            return
        try:
            self._sheets.create_edition(
                Edition(piece_id=piece_id, publisher=publisher.strip()),
            )
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _save_edition(self, original, pub_w, label_w, cat_w, year_w, folder_w, notes_w):
        """儲存版本變更"""
        updated = replace(
            original,
            publisher=pub_w.text().strip(),
            edition_label=label_w.text().strip(),
            catalog_number=cat_w.text().strip(),
            year=year_w.text().strip(),
            drive_folder_id=folder_w.text().strip(),
            notes=notes_w.text().strip(),
        )
        try:
            self._sheets.update_edition(updated)
            self._show_piece_detail(original.piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _set_active_edition(self, piece: Piece, edition_id: str):
        """設定使用中版本"""
        updated = replace(piece, active_edition_id=edition_id)
        try:
            self._sheets.update_piece(updated)
            self._refresh_all()
            self._show_piece_detail(piece.id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _delete_edition(self, edition_id: str, piece_id: str):
        """刪除版本"""
        result = QMessageBox.question(
            self, t("catalog.edition.delete"),
            t("catalog.edition.confirm_delete"),
        )
        if result != QMessageBox.Yes:
            return
        try:
            parts = self._sheets.list_parts(edition_id=edition_id)
            for part in parts:
                self._sheets.delete_part(part.id)
            self._sheets.delete_edition(edition_id)
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    # --- 樂章頁籤 ---

    def _build_movements_tab(self, detail: PieceDetail) -> QWidget:
        """建立樂章管理頁籤"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        btn_row = QHBoxLayout()
        add_btn = QPushButton(t("catalog.movement.add"))
        add_btn.clicked.connect(lambda: self._add_movement(detail.piece.id))
        btn_row.addWidget(add_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        table = QTableWidget()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels([
            t("catalog.movement.number"), t("catalog.movement.name"),
            t("catalog.movement.tempo"), t("catalog.movement.key"),
            t("catalog.save"), t("catalog.movement.delete"),
        ])
        table.horizontalHeader().setStretchLastSection(True)
        table.setRowCount(len(detail.movements))
        for row, mov in enumerate(detail.movements):
            table.setItem(row, 0, QTableWidgetItem(mov.movement_number))
            table.setItem(row, 1, QTableWidgetItem(mov.name))
            table.setItem(row, 2, QTableWidgetItem(mov.tempo_marking))
            table.setItem(row, 3, QTableWidgetItem(mov.key_signature))
            save_btn = QPushButton(t("catalog.save"))
            save_btn.clicked.connect(
                lambda checked=False, r=row, m=mov, tbl=table:
                self._save_movement(m, tbl, r, detail.piece.id),
            )
            table.setCellWidget(row, 4, save_btn)
            del_btn = QPushButton(t("catalog.movement.delete"))
            del_btn.setStyleSheet("color: #c0392b;")
            del_btn.clicked.connect(
                lambda checked=False, mid=mov.id:
                self._delete_movement(mid, detail.piece.id),
            )
            table.setCellWidget(row, 5, del_btn)
        table.resizeColumnsToContents()
        layout.addWidget(table)
        layout.addStretch()
        return widget

    def _add_movement(self, piece_id: str):
        """新增樂章"""
        movements = self._sheets.list_movements(piece_id)
        next_num = str(len(movements) + 1)
        try:
            self._sheets.create_movement(Movement(
                piece_id=piece_id,
                movement_number=next_num,
                sort_order=next_num,
            ))
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _save_movement(self, original, table, row, piece_id):
        """儲存樂章變更"""
        updated = replace(
            original,
            movement_number=table.item(row, 0).text().strip(),
            name=table.item(row, 1).text().strip(),
            tempo_marking=table.item(row, 2).text().strip(),
            key_signature=table.item(row, 3).text().strip(),
        )
        try:
            self._sheets.update_movement(updated)
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _delete_movement(self, movement_id: str, piece_id: str):
        """刪除樂章"""
        result = QMessageBox.question(
            self, t("catalog.movement.delete"),
            t("catalog.movement.confirm_delete"),
        )
        if result != QMessageBox.Yes:
            return
        try:
            parts = self._sheets.list_parts(movement_id=movement_id)
            for part in parts:
                self._sheets.delete_part(part.id)
            self._sheets.delete_movement(movement_id)
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    # --- 分譜狀態頁籤 ---

    def _build_parts_tab(self, detail: PieceDetail) -> QWidget:
        """建立分譜狀態矩陣頁籤"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        if not detail.editions:
            layout.addWidget(QLabel(t("catalog.tree.no_items")))
            return widget
        edition_combo = QComboBox()
        for edition in detail.editions:
            label = edition.publisher
            if edition.id == detail.piece.active_edition_id:
                label += f" {t('catalog.edition.active')}"
            edition_combo.addItem(label, edition.id)
        layout.addWidget(edition_combo)
        parts_container = QWidget()
        parts_layout = QVBoxLayout(parts_container)
        layout.addWidget(parts_container)

        def rebuild_parts_grid(edition_id: str):
            while parts_layout.count():
                child = parts_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
            parts = [p for p in detail.parts if p.edition_id == edition_id]
            movements = sorted(detail.movements, key=lambda m: m.sort_order)
            if not movements:
                parts_layout.addWidget(QLabel(t("catalog.tree.no_items")))
                return
            instruments = sorted(set(p.instrument_name for p in parts))
            if not instruments:
                btn_row = QHBoxLayout()
                add_btn = QPushButton(t("catalog.part.add_instruments"))
                add_btn.clicked.connect(
                    lambda: self._add_parts_from_instruments(
                        edition_id, detail.piece.id, movements,
                    ),
                )
                btn_row.addWidget(add_btn)
                scan_btn = QPushButton(t("catalog.part.scan_folder"))
                scan_btn.clicked.connect(
                    lambda: self._scan_parts_from_drive(
                        edition_id, detail.piece.id, movements,
                    ),
                )
                btn_row.addWidget(scan_btn)
                btn_row.addStretch()
                parts_layout.addLayout(btn_row)
                return
            table = QTableWidget()
            table.setRowCount(len(instruments))
            table.setColumnCount(len(movements))
            headers = [
                f"{m.movement_number}. {m.name}" if m.name
                else m.movement_number
                for m in movements
            ]
            table.setHorizontalHeaderLabels(headers)
            table.setVerticalHeaderLabels(instruments)
            part_map = {}
            for p in parts:
                part_map[(p.instrument_name, p.movement_id)] = p
            for row, inst in enumerate(instruments):
                for col, mov in enumerate(movements):
                    part = part_map.get((inst, mov.id))
                    status = part.status if part else PartStatus.UNKNOWN.value
                    status_label = t(f"catalog.part.status.{status}")
                    item = QTableWidgetItem(status_label)
                    item.setTextAlignment(Qt.AlignCenter)
                    color = _STATUS_COLORS.get(status, _STATUS_COLORS[PartStatus.UNKNOWN.value])
                    item.setBackground(QBrush(color))
                    item.setForeground(QBrush(QColor("white")))
                    item.setData(Qt.UserRole, (part, inst, mov.id, edition_id))
                    table.setItem(row, col, item)
            table.cellDoubleClicked.connect(
                lambda r, c, tbl=table: self._toggle_part_status(
                    tbl, r, c, detail.piece.id,
                ),
            )
            table.resizeColumnsToContents()
            table.horizontalHeader().setStretchLastSection(True)
            parts_layout.addWidget(table)

        edition_combo.currentIndexChanged.connect(
            lambda idx: rebuild_parts_grid(edition_combo.itemData(idx) or ""),
        )
        if detail.editions:
            rebuild_parts_grid(detail.editions[0].id)
        return widget

    def _toggle_part_status(self, table, row, col, piece_id):
        """雙擊切換分譜狀態"""
        item = table.item(row, col)
        if not item:
            return
        data = item.data(Qt.UserRole)
        if not data:
            return
        part, inst, movement_id, edition_id = data
        cycle = [
            PartStatus.AVAILABLE.value,
            PartStatus.MISSING.value,
            PartStatus.DAMAGED.value,
            PartStatus.UNKNOWN.value,
        ]
        current = part.status if part else PartStatus.UNKNOWN.value
        idx = cycle.index(current) if current in cycle else 0
        new_status = cycle[(idx + 1) % len(cycle)]
        try:
            if part and part.id:
                updated = replace(part, status=new_status)
                self._sheets.update_part(updated)
            else:
                self._sheets.create_part(Part(
                    edition_id=edition_id,
                    movement_id=movement_id,
                    instrument_name=inst,
                    sort_order=str(row),
                    status=new_status,
                ))
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _add_parts_from_instruments(self, edition_id, piece_id, movements):
        """從主視窗的樂器表新增分譜記錄"""
        text, ok = QInputDialog.getMultiLineText(
            self, t("catalog.part.add_instruments"),
            t("catalog.part.instrument"),
            "",
        )
        if not ok or not text.strip():
            return
        instruments = [line.strip() for line in text.strip().split("\n") if line.strip()]
        try:
            for mov in movements:
                for i, inst in enumerate(instruments):
                    self._sheets.create_part(Part(
                        edition_id=edition_id,
                        movement_id=mov.id,
                        instrument_name=inst,
                        sort_order=str(i),
                        status=PartStatus.UNKNOWN.value,
                    ))
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _scan_parts_from_drive(self, edition_id, piece_id, movements):
        """從 Drive 資料夾掃描 PDF 檔案並建立分譜記錄"""
        if not self._drive:
            return
        edition = self._sheets.get_edition(edition_id)
        if not edition or not edition.drive_folder_id:
            QMessageBox.information(
                self, t("catalog.part.scan_folder"),
                t("catalog.edition.drive_folder"),
            )
            return
        try:
            pdfs = self._drive.list_pdfs_in_folder(edition.drive_folder_id)
            if not pdfs:
                return
            first_movement = movements[0] if movements else None
            if not first_movement:
                return
            for i, pdf in enumerate(pdfs):
                name = pdf["name"].replace(".pdf", "").replace(".PDF", "")
                self._sheets.create_part(Part(
                    edition_id=edition_id,
                    movement_id=first_movement.id,
                    instrument_name=name,
                    sort_order=str(i),
                    status=PartStatus.AVAILABLE.value,
                    drive_file_id=pdf["id"],
                    file_name=pdf["name"],
                ))
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    # --- 演出紀錄頁籤 ---

    def _build_performances_tab(self, detail: PieceDetail) -> QWidget:
        """建立演出紀錄頁籤"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        btn_row = QHBoxLayout()
        add_btn = QPushButton(t("catalog.performance.add"))
        add_btn.clicked.connect(
            lambda: self._add_performance(detail.piece.id),
        )
        btn_row.addWidget(add_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        table = QTableWidget()
        table.setColumnCount(8)
        table.setHorizontalHeaderLabels([
            t("catalog.performance.date"),
            t("catalog.performance.venue"),
            t("catalog.performance.conductor"),
            t("catalog.performance.ensemble"),
            t("catalog.performance.status"),
            t("catalog.performance.program_order"),
            t("catalog.save"),
            t("catalog.performance.delete"),
        ])
        table.horizontalHeader().setStretchLastSection(True)
        table.setRowCount(len(detail.performances))
        status_options = [
            PerformanceStatus.PLANNED.value,
            PerformanceStatus.REHEARSING.value,
            PerformanceStatus.PERFORMED.value,
            PerformanceStatus.CANCELLED.value,
        ]
        for row, perf in enumerate(detail.performances):
            table.setItem(row, 0, QTableWidgetItem(perf.performance_date))
            table.setItem(row, 1, QTableWidgetItem(perf.venue))
            table.setItem(row, 2, QTableWidgetItem(perf.conductor))
            table.setItem(row, 3, QTableWidgetItem(perf.ensemble))
            status_combo = QComboBox()
            for s in status_options:
                status_combo.addItem(
                    t(f"catalog.performance.status.{s}"), s,
                )
            idx = status_options.index(perf.status) if perf.status in status_options else 0
            status_combo.setCurrentIndex(idx)
            table.setCellWidget(row, 4, status_combo)
            table.setItem(row, 5, QTableWidgetItem(perf.program_order))
            save_btn = QPushButton(t("catalog.save"))
            save_btn.clicked.connect(
                lambda checked=False, r=row, p=perf, tbl=table:
                self._save_performance(p, tbl, r, detail.piece.id),
            )
            table.setCellWidget(row, 6, save_btn)
            del_btn = QPushButton(t("catalog.performance.delete"))
            del_btn.setStyleSheet("color: #c0392b;")
            del_btn.clicked.connect(
                lambda checked=False, pid=perf.id:
                self._delete_performance(pid, detail.piece.id),
            )
            table.setCellWidget(row, 7, del_btn)
        table.resizeColumnsToContents()
        layout.addWidget(table)
        layout.addStretch()
        return widget

    def _add_performance(self, piece_id: str):
        """新增演出紀錄"""
        try:
            self._sheets.create_performance(Performance(
                piece_id=piece_id,
                status=PerformanceStatus.PLANNED.value,
            ))
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _save_performance(self, original, table, row, piece_id):
        """儲存演出紀錄變更"""
        status_combo = table.cellWidget(row, 4)
        updated = replace(
            original,
            performance_date=table.item(row, 0).text().strip(),
            venue=table.item(row, 1).text().strip(),
            conductor=table.item(row, 2).text().strip(),
            ensemble=table.item(row, 3).text().strip(),
            status=status_combo.currentData() if status_combo else original.status,
            program_order=table.item(row, 5).text().strip(),
        )
        try:
            self._sheets.update_performance(updated)
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _delete_performance(self, performance_id: str, piece_id: str):
        """刪除演出紀錄"""
        result = QMessageBox.question(
            self, t("catalog.performance.delete"),
            t("catalog.performance.confirm_delete"),
        )
        if result != QMessageBox.Yes:
            return
        try:
            self._sheets.delete_performance(performance_id)
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))


class _NewPieceDialog(QDialog):
    """新增曲目對話框"""

    def __init__(self, composers: List[Composer], parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("catalog.toolbar.add_piece"))
        self.setMinimumWidth(400)
        self.piece = Piece()
        self.composer_id = ""
        self.composer_name = ""
        self._composers = composers
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._title_edit = QLineEdit()
        form.addRow(t("catalog.piece.name"), self._title_edit)
        self._composer_edit = QComboBox()
        self._composer_edit.setEditable(True)
        self._composer_edit.addItem("")
        for c in composers:
            self._composer_edit.addItem(c.name_short or c.name, c.id)
        form.addRow(t("catalog.piece.composer"), self._composer_edit)
        self._genre_edit = QLineEdit()
        form.addRow(t("catalog.piece.genre"), self._genre_edit)
        self._instrumentation_edit = QLineEdit()
        form.addRow(t("catalog.piece.instrumentation"), self._instrumentation_edit)
        self._opus_edit = QLineEdit()
        form.addRow(t("catalog.piece.opus"), self._opus_edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        title = self._title_edit.text().strip()
        if not title:
            return
        composer_text = self._composer_edit.currentText().strip()
        self.composer_id = self._composer_edit.currentData() or ""
        if not self.composer_id and composer_text:
            for c in self._composers:
                if (c.name_short or c.name) == composer_text or c.name == composer_text:
                    self.composer_id = c.id
                    break
            if not self.composer_id:
                self.composer_name = composer_text
        self.piece = Piece(
            title=title,
            genre=self._genre_edit.text().strip(),
            instrumentation=self._instrumentation_edit.text().strip(),
            opus=self._opus_edit.text().strip(),
        )
        self.accept()

# -*- coding: utf-8 -*-
"""
群組連到譜庫的對話框

依序挑曲目 → 版本 → 樂章或整首；各層找不到時可選「新建」，以群組的曲名、作曲家、樂章編號與名稱建立。
開啟時依群組的曲名與樂章編號預選。按「連線」後在背景讀那一處（或新建後再讀），回到主執行緒交給專案的
連線操作（Project.link_group），連上就發出 linked 並關閉；兩邊都有編制時先開合併差異表（CatalogMergeDialog），
照選的那一邊連線，取消就留在這裡重新挑。讀寫譜庫都經譜庫服務在背景執行，失敗時頂端顯示
提示列並寫進紀錄檔，按處理按鈕重做（新建時以同一個 PendingPlace 重送，不重複建立）。

群組已連線時就是改連：讀那處時一併讀這個群組在原處寫過的編制（CatalogService.revert_rows），連上後有撤得掉的
就開確認框（CatalogRevertDialog），選「撤掉」記下（Project.queue_catalog_revert），存檔時寫進原處；
在合併差異表取消時不問、連線不變。

使用範例：
    dialog = CatalogLinkDialog(connect, project, group, root_folder_id, parent=window)
    dialog.linked.connect(table.show_linked)
    dialog.open()
"""
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QMessageBox, QPushButton, QVBoxLayout,
)

from core.catalog_errors import CatalogError, CatalogFailure
from core.catalog_models import CatalogPlace, Edition, LinkTargets, Movement, Piece
from core.catalog_revert import RevertRow, pending_revert, revert_count
from core.locale import t
from core.models import DuplicateVoicesError, Group, LinkChoice, LinkResult, Project
from core.voice_suggestions import VoiceCandidates
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService, PendingPlace
from ui.catalog_error_bar import CatalogErrorBar
from ui.catalog_tasks import CatalogTasks
from ui.catalog_merge_dialog import CatalogMergeDialog
from ui.catalog_revert_dialog import CatalogRevertDialog
from ui.instrumentation_table import link_text


class CatalogLinkDialog(QDialog):
    """挑選群組要連到譜庫的哪一處，連上後發出 linked（LinkResult）"""

    linked = Signal(object)

    def __init__(
        self, connect: Callable[[], CatalogService], project: Project, group: Group, root_folder_id: str,
        parent=None, log: Optional[ActivityLog] = None, sign_in: Optional[Callable[[], bool]] = None,
        open_settings: Optional[Callable[[], bool]] = None, voice_candidates: Optional[VoiceCandidates] = None,
    ):
        """
        Args:
            connect: 在背景執行緒取得譜庫服務的函式，拿不到時丟出 CatalogError
            project: 群組所在的專案（連線經它的編輯操作寫入）
            group: 要連線的群組
            root_folder_id: 譜庫根資料夾的 Drive ID（新建版本的資料夾建在這裡）
            parent: 父元件
            log: 紀錄檔，失敗時寫進去；省略時不寫
            sign_in: 提示列「重新登入 Google」時呼叫，回傳是否登入成功；省略時直接重試
            open_settings: 提示列「開啟譜庫設定」時呼叫，回傳是否改了設定；省略時直接重試
            voice_candidates: 自動完成的候選來源，讀到譜庫時記下用過的聲部名稱；省略時不記
        """
        super().__init__(parent)
        self._connect = connect
        self._project = project
        self._group = group
        self._root_folder_id = root_folder_id
        self._voice_candidates = voice_candidates
        # 改連時原本的連線；第一次連線時為 None
        self._previous = group.catalog_link
        self._tasks = CatalogTasks(self)
        self._targets = LinkTargets()
        # 新建時要送的一處；選擇改了就換一個，失敗後重送沿用同一個
        self._pending: Optional[PendingPlace] = None
        self.setWindowTitle(t("catalog.relink.title") if self._previous else t("catalog.link.title"))
        self.setMinimumWidth(420)
        self._build_ui(log, sign_in, open_settings)
        self._load()

    def done(self, result: int):
        """關閉對話框：放棄還在跑的讀寫，之後它們不再碰這個對話框"""
        self._tasks.abandon_all()
        super().done(result)

    def _build_ui(
        self, log: Optional[ActivityLog], sign_in: Optional[Callable[[], bool]],
        open_settings: Optional[Callable[[], bool]],
    ):
        """排出提示列、曲目、版本、樂章三個選單與按鈕"""
        layout = QVBoxLayout(self)
        self._error_bar = CatalogErrorBar(log=log)
        self._error_bar.connect_remedies(sign_in, open_settings)
        layout.addWidget(self._error_bar)
        form = QFormLayout()
        self._piece_combo = QComboBox()
        self._edition_combo = QComboBox()
        self._movement_combo = QComboBox()
        form.addRow(t("catalog.link.piece"), self._piece_combo)
        form.addRow(t("catalog.link.edition"), self._edition_combo)
        form.addRow(t("catalog.link.movement"), self._movement_combo)
        layout.addLayout(form)
        self._piece_combo.currentIndexChanged.connect(self._on_piece_chosen)
        self._edition_combo.currentIndexChanged.connect(self._forget_pending)
        self._movement_combo.currentIndexChanged.connect(self._forget_pending)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self._confirm_button = QPushButton(t("catalog.link.confirm"))
        self._confirm_button.setEnabled(False)
        buttons.addButton(self._confirm_button, QDialogButtonBox.AcceptRole)
        self._confirm_button.clicked.connect(self._confirm)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # --- 讀取可以挑的 ---

    def _load(self):
        """在背景讀曲目、版本與樂章"""
        def failed(error: CatalogError):
            """讀不到：顯示提示列，重試時再讀一次"""
            self._error_bar.show_failure(
                t("catalog.error.cannot_read"), CatalogFailure.from_error(error, t("catalog.link.title")), self._load,
            )
        self._error_bar.clear()
        self._tasks.run(lambda: self._connect().link_targets(), self._show_targets, failed)

    def _show_targets(self, targets: LinkTargets):
        """列出曲目（最後一項是新建），預選曲名與群組相同的那首；記下譜庫用過的聲部名稱給自動完成"""
        self._targets = targets
        if self._voice_candidates is not None:
            self._voice_candidates.remember_catalog(targets.voice_names)
        self._piece_combo.blockSignals(True)
        self._piece_combo.clear()
        for piece in targets.pieces:
            self._piece_combo.addItem(piece.title, piece)
        self._piece_combo.addItem(t("catalog.link.new_piece", title=self._new_title()), None)
        title = self._group.piece_name.strip()
        match = next((i for i, piece in enumerate(targets.pieces) if title and piece.title.strip() == title), None)
        self._piece_combo.setCurrentIndex(len(targets.pieces) if match is None else match)
        self._piece_combo.blockSignals(False)
        self._on_piece_chosen()
        self._confirm_button.setEnabled(True)

    def _on_piece_chosen(self, _index: int = 0):
        """選了曲目：列出它的版本（預選使用中的）與樂章（預選編號與群組相同的；群組沒有樂章時預選整首）"""
        piece: Optional[Piece] = self._piece_combo.currentData()
        editions = self._targets.editions_of(piece.id) if piece else []
        movements = self._targets.movements_of(piece.id) if piece else []
        self._edition_combo.blockSignals(True)
        self._edition_combo.clear()
        for edition in editions:
            label = edition.edition_label or edition.publisher or t("catalog.link.unnamed_edition")
            self._edition_combo.addItem(label, edition)
        self._edition_combo.addItem(t("catalog.link.new_edition"), None)
        active = next((i for i, e in enumerate(editions) if piece and e.id == piece.active_edition_id), 0)
        self._edition_combo.setCurrentIndex(active if editions else 0)
        self._edition_combo.blockSignals(False)
        self._movement_combo.blockSignals(True)
        self._movement_combo.clear()
        self._movement_combo.addItem(t("catalog.link.whole"), "")
        for movement in movements:
            self._movement_combo.addItem(_movement_text(movement), movement)
        self._movement_combo.addItem(t("catalog.link.new_movement", name=self._new_movement_text()), None)
        self._movement_combo.setCurrentIndex(self._preselected_movement(movements))
        self._movement_combo.blockSignals(False)
        self._forget_pending()

    def _preselected_movement(self, movements: List[Movement]) -> int:
        """樂章選單預選的列：編號與群組相同的樂章；沒有時群組有樂章就是新建，否則是整首"""
        number = self._group.movement_number.strip()
        match = next((i for i, m in enumerate(movements) if number and m.movement_number.strip() == number), None)
        if match is not None:
            return match + 1
        has_movement = number or self._group.movement_name.strip()
        return len(movements) + 1 if has_movement else 0

    def _new_title(self) -> str:
        """新建曲目的曲名：群組的曲名，沒有時為群組名稱"""
        return self._group.piece_name.strip() or self._group.name

    def _new_movement_text(self) -> str:
        """新建樂章的顯示：群組的樂章編號與名稱"""
        parts = (self._group.movement_number.strip(), self._group.movement_name.strip())
        return " ".join(part for part in parts if part)

    def _forget_pending(self, _index: int = 0):
        """選擇改了：之前準備的新建不再沿用"""
        self._pending = None

    # --- 連線 ---

    def _confirm(self):
        """按「連線」：在背景讀選到的那處（有要新建的先新建），回來後連線"""
        piece: Optional[Piece] = self._piece_combo.currentData()
        edition: Optional[Edition] = self._edition_combo.currentData()
        movement = self._movement_combo.currentData()
        movement_id = movement.id if isinstance(movement, Movement) else ""
        orchestra_type = self._project.orchestra_type
        creating = piece is None or edition is None or movement is None
        if not creating:
            self._pending = None
        elif self._pending is None:
            self._pending = PendingPlace(
                title=piece.title if piece else self._new_title(),
                composer=self._group.composer,
                root_folder_id=self._root_folder_id,
                piece_id=piece.id if piece else "",
                edition_id=edition.id if edition else "",
                movement_id=movement_id,
                new_movement=movement is None,
                movement_number=self._group.movement_number.strip(),
                movement_name=self._group.movement_name.strip(),
            )
        pending = self._pending
        previous = self._previous
        group_id, group_name = self._group.id, self._group.name

        def work() -> Tuple[CatalogPlace, List[RevertRow]]:
            """新建還沒有的部分再讀那處，或直接讀那處；改連到別處時一併讀群組在原處寫過的編制（背景執行緒）"""
            catalog = self._connect()
            if pending is not None:
                place = catalog.create_place(pending, orchestra_type)
            else:
                place = catalog.read_place(edition.id, movement_id, orchestra_type)
            here = (place.edition_id, place.movement_id)
            if previous is None or here == (previous.edition_id, previous.movement_id):
                return place, []
            return place, catalog.revert_rows(
                previous.edition_id, previous.movement_id, group_id, group_name, orchestra_type,
            )

        def failed(error: CatalogError):
            """讀寫失敗：顯示提示列，重試時以同樣的選擇再送一次"""
            self._confirm_button.setEnabled(True)
            heading = t("catalog.error.cannot_write") if creating else t("catalog.error.cannot_read")
            failure = CatalogFailure.from_error(
                error, t("catalog.link.title"), piece_title=self._piece_combo.currentText(),
            )
            self._error_bar.show_failure(heading, failure, self._confirm)
        self._error_bar.clear()
        self._confirm_button.setEnabled(False)
        self._tasks.run(work, lambda found: self._link(*found), failed)

    def _link(self, place: CatalogPlace, reverts: List[RevertRow], choice: Optional[LinkChoice] = None):
        """讀到那處：交給專案連線；兩邊都有編制時先開合併差異表，樂器表重名時說明原因並關閉，群組不變

        Args:
            place: 讀到的那處
            reverts: 改連時群組在原處寫過的編制；第一次連線或連回原處時為空清單
            choice: 合併差異表選的那一邊；還沒選時為 None
        """
        self._confirm_button.setEnabled(True)
        try:
            result = self._project.link_group(self._group, place, choice)
        except DuplicateVoicesError as e:
            QMessageBox.warning(self, t("catalog.link.title"), duplicate_rows_text(self._group, e.rows))
            self.reject()
            return
        if result.needs_merge:
            self._ask_merge(place, reverts)
            return
        if revert_count(reverts):
            self._ask_revert(reverts, result)
            return
        self._finish(result)

    def _finish(self, result: LinkResult):
        """連好了：發出 linked 並關閉"""
        self.linked.emit(result)
        self.accept()

    def _ask_merge(self, place: CatalogPlace, reverts: List[RevertRow]):
        """兩邊都有編制：開合併差異表，選了哪一邊就照它連線；取消時留在這裡重新挑"""
        table = CatalogMergeDialog(self._project.merge_rows(self._group, place), self._group.name, self)
        table.chosen.connect(lambda choice: self._link(place, reverts, choice))
        table.finished.connect(table.deleteLater)
        table.open()

    def _ask_revert(self, reverts: List[RevertRow], result: LinkResult):
        """改連好了、原處有撤得掉的編制：問要不要撤掉，選「撤掉」就記下（存檔時寫入），選哪個都連好了"""
        previous = self._previous
        question = CatalogRevertDialog(reverts, link_text(previous), self)

        def answered(code: int):
            """按了「撤掉」就記下要寫進原處的撤掉，再發出 linked"""
            if code == QDialog.Accepted:
                self._project.queue_catalog_revert(
                    self._group, pending_revert(previous.edition_id, previous.movement_id, reverts),
                )
            question.deleteLater()
            self._finish(result)
        question.finished.connect(answered)
        question.open()


def duplicate_rows_text(group: Group, rows: List[int]) -> str:
    """樂器表有重名的列、不能連線時的說明：逐列寫出列號（從 1 起算）與聲部"""
    lines = "\n".join(t("catalog.link.duplicate_row", row=row + 1, voice=group.instruments[row]) for row in rows)
    return t("catalog.link.duplicates", rows=lines)


def _movement_text(movement: Movement) -> str:
    """樂章選單的一項：編號與名稱"""
    return " ".join(part for part in (movement.movement_number.strip(), movement.name.strip()) if part) or movement.id

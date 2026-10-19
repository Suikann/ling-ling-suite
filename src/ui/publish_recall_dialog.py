# -*- coding: utf-8 -*-
"""
收回的對話框（原型 #63 收回的變體 D）

依公演分組列出要收回的曲目：已結束的公演 › 曲目 › 樂譜，沒有已結束公演的曲目一組放最後。只能勾曲目；
建議收回的（services/recall_service.py 的 RecallPiece.suggested）預設勾選。別人放的檔排前面並標色。
還掛在未結束公演上的曲目寫出「還要演：哪場、幾天後」、預設不勾，勾了才在下方出現紅色警告，寫明會刪掉團員那場要用的
幾份譜。底部寫出勾了幾首、幾份；「收回」按鈕旁有「勾選的不收回」。只顯示預覽，按下按鈕關閉對話框，
收回或不收回由譜庫視窗執行。

使用範例：
    dialog = RecallDialog(preview, parent=window)
    if dialog.exec() == QDialog.Accepted:
        pieces, decision = dialog.chosen(), dialog.decision   # decision 是 PublishAction.RECALL 或 KEEP
    QMessageBox.information(window, t("recall.title"), result_text(result))
"""
import datetime
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from core.constants import ALERT_BANNER_STYLE, MUTED_COLORS, WARNING_COLORS
from core.locale import t
from core.publish_log import PublishAction
from services.huaxia_client import HuaXiaEvent, HuaXiaScore
from services.recall_service import RecallPiece, RecallPreview, RecallResult

# 別人放的檔、還要演的曲目與底部份數的字色（同原型的琥珀色）；說明的灰字
_OTHERS_COLOR = WARNING_COLORS[0]
_NOTE_COLOR = MUTED_COLORS[0]
# 樹的欄位（介面字串鍵的結尾）
_TREE_COLUMNS = ("item", "voice", "note")


def _date(moment: datetime.datetime) -> str:
    """場次時間在這台電腦時區的日期（YYYY-MM-DD）"""
    return moment.astimezone().date().isoformat()


def _days_until(event: HuaXiaEvent, now: datetime.datetime) -> int:
    """距離場次開始還有幾天（以這台電腦時區的日期算，當天為 0）"""
    return (event.starts_at.astimezone().date() - now.astimezone().date()).days


def piece_note(piece: RecallPiece, now: datetime.datetime) -> str:
    """曲目那一列的說明：幾份（別人放的幾份）、還要演哪場幾天後、標過不收回"""
    parts = [t("recall.piece.count", count=len(piece.scores), others=len(piece.others))]
    if piece.upcoming:
        event = piece.upcoming[0]
        parts.append(t("recall.still_playing", event=event.title, date=_date(event.starts_at), days=_days_until(event, now)))
    if piece.kept:
        parts.append(t("recall.piece.kept"))
    return "・".join(parts)


def _score_name(score: HuaXiaScore) -> str:
    """樂譜的名稱（HuaXia 上的樂譜名稱，沒有名稱時寫 Storage 路徑的檔名）"""
    return score.title or score.file_path.rsplit("/", 1)[-1]


class RecallDialog(QDialog):
    """收回的預覽；按「收回」或「勾選的不收回」時 accept，decision 是哪一個，chosen 是勾了的曲目"""

    def __init__(self, preview: RecallPreview, parent: Optional[QWidget] = None):
        """
        Args:
            preview: 收回的預覽（RecallService.preview）
            parent: 父元件
        """
        super().__init__(parent)
        self.preview = preview
        self.decision: Optional[PublishAction] = None
        self.setWindowTitle(t("recall.title"))
        self.resize(900, 640)
        layout = QVBoxLayout(self)
        intro = QLabel(t("recall.intro"))
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {_NOTE_COLOR};")
        layout.addWidget(intro)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([t(f"recall.column.{name}") for name in _TREE_COLUMNS])
        self.piece_items: List[QTreeWidgetItem] = []
        self._pieces: List[RecallPiece] = []
        for event, pieces in preview.groups():
            if event is not None:
                heading = t("recall.group.ended", event=event.title, date=_date(event.ends_at))
            else:
                heading = t("recall.group.no_concert")
            group = QTreeWidgetItem(self.tree, [heading, "", t("recall.group.count", count=len(pieces))])
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            group.setFlags(Qt.ItemIsEnabled)
            for piece in pieces:
                self._add_piece(group, piece)
            group.setExpanded(True)
        if not self.piece_items:
            layout.addWidget(QLabel(t("recall.empty")))
        self.tree.resizeColumnToContents(0)
        layout.addWidget(self.tree, stretch=1)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet(ALERT_BANNER_STYLE)
        layout.addWidget(self.warning)
        bottom = QHBoxLayout()
        self.total = QLabel()
        self.total.setStyleSheet(f"color: {_OTHERS_COLOR};")
        bottom.addWidget(self.total)
        bottom.addStretch()
        cancel = QPushButton(t("dialog.cancel_btn"))
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        self.keep_button = QPushButton(t("recall.keep"))
        self.keep_button.clicked.connect(lambda: self._decide(PublishAction.KEEP))
        bottom.addWidget(self.keep_button)
        self.confirm = QPushButton(t("recall.confirm"))
        self.confirm.clicked.connect(lambda: self._decide(PublishAction.RECALL))
        bottom.addWidget(self.confirm)
        layout.addLayout(bottom)
        self.tree.itemChanged.connect(self._update)
        self._update()

    def chosen(self) -> List[RecallPiece]:
        """勾了的曲目（依畫面的順序）"""
        return [piece for piece, item in zip(self._pieces, self.piece_items) if item.checkState(0) == Qt.Checked]

    def _add_piece(self, group: QTreeWidgetItem, piece: RecallPiece) -> None:
        """曲目一列（可以勾，建議收回的預設勾選；還要演的標色），底下每份樂譜一列（別人放的在前、標色）"""
        item = QTreeWidgetItem(group, [piece.piece_title, "", piece_note(piece, self.preview.now)])
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked if piece.suggested else Qt.Unchecked)
        if piece.upcoming:
            for column in (0, 2):
                item.setForeground(column, QColor(_OTHERS_COLOR))
        for score in piece.scores:
            source = t("recall.source.lingling") if score.lingling else t("recall.source.others")
            voice = piece.voice(score) or t("retract.full_score")
            child = QTreeWidgetItem(item, [_score_name(score), voice, source])
            child.setFlags(Qt.ItemIsEnabled)
            if not score.lingling:
                for column in range(len(_TREE_COLUMNS)):
                    child.setForeground(column, QColor(_OTHERS_COLOR))
        self.piece_items.append(item)
        self._pieces.append(piece)

    def _decide(self, decision: PublishAction) -> None:
        """按了「收回」或「勾選的不收回」：記下是哪一個，關閉對話框"""
        self.decision = decision
        self.accept()

    def _update(self, *_args) -> None:
        """依勾了的曲目更新底部的份數、還要演的紅色警告與兩顆按鈕（沒勾就按不下去）"""
        chosen = self.chosen()
        self.total.setText(t("recall.total", count=len(chosen), scores=sum(len(piece.scores) for piece in chosen)))
        playing = [piece for piece in chosen if piece.upcoming]
        self.warning.setText(t(
            "recall.warning",
            pieces="、".join(
                t("recall.warning.piece", piece=piece.piece_title, event=piece.upcoming[0].title,
                  date=_date(piece.upcoming[0].starts_at))
                for piece in playing
            ),
            count=sum(len(piece.scores) for piece in playing),
        ))
        self.warning.setVisible(bool(playing))
        self.confirm.setEnabled(bool(chosen))
        self.keep_button.setEnabled(bool(chosen))


def result_text(result: RecallResult) -> str:
    """收回結束後的說明：收回了幾首幾份或中途停下、「收回」寫不進譜庫"""
    parts = []
    if result.recalled:
        parts.append(t("recall.done", count=len(result.recalled), scores=result.scores_removed))
    if result.error is not None:
        parts.append(t("recall.stopped", error=result.error.detail or str(result.error)))
    if result.log_error is not None:
        parts.append(t("recall.not_logged"))
    return "\n\n".join(parts)

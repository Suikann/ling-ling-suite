# -*- coding: utf-8 -*-
"""
PROTOTYPE（拋棄式，只留在 prototype/instrumentation-table 分支，不進 develop）

回答 wayfinder ticket「編制表與差異畫面的原型」（#60）：編制表與幾個差異畫面要長什麼樣子。
六個畫面，各有 2～3 個結構不同的變體；資料全是假的，按鈕不寫入任何地方。

    python src/ui/prototype_instrumentation_table.py
    python src/ui/prototype_instrumentation_table.py --screen=catalog --variant=B
    python src/ui/prototype_instrumentation_table.py --shots=<資料夾>    # 每個變體截一張圖後結束

底部黃色切換列：左邊選畫面，箭頭（或鍵盤左右鍵，焦點不在表格或輸入框時）換變體。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QComboBox,
    QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenuBar, QMessageBox, QPushButton, QRadioButton,
    QSpinBox, QSplitter, QStyleFactory, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from core.constants import LOCALE_ZH_TW
from core.locale import set_locale, t

# --- 假資料 ---------------------------------------------------------------

LINK = ("長城隨想", "人民音樂出版社", "第二樂章 烽火操")

# 聲部、最少、最多（None 表示「以上」）、聲部組
VOICES = [
    ("梆笛", 1, 1, "吹管"), ("曲笛", 1, 2, "吹管"), ("高音笙", 1, 1, "吹管"), ("中音笙", 1, 1, "吹管"),
    ("高音嗩吶", 1, 1, "吹管"), ("中音嗩吶", 1, 1, "吹管"),
    ("定音鼓", 1, 1, "打擊"), ("打擊 1", 1, 1, "打擊"), ("打擊 2", 1, 1, "打擊"),
    ("揚琴", 1, 1, "彈撥"), ("柳琴", 1, 2, "彈撥"), ("琵琶", 2, 3, "彈撥"), ("中阮", 2, 3, "彈撥"),
    ("大阮", 1, 2, "彈撥"), ("古箏", 1, 1, "彈撥"), ("豎琴", 1, 1, "彈撥"),
    ("二胡獨奏", 1, 1, "拉弦"), ("高胡", 3, 4, "拉弦"), ("二胡 I", 6, 8, "拉弦"), ("二胡 II", 6, 8, "拉弦"),
    ("中胡", 4, 6, "拉弦"), ("大提琴", 4, None, "低音"), ("低音提琴", 2, 3, "低音"),
]
SECTION = {v[0]: v[3] for v in VOICES}
SECTION.update({"排鼓": "打擊"})
SECTIONS_CN = ["吹管", "拉弦", "彈撥", "打擊", "低音"]
ABSENT = {"豎琴"}                       # 本專案沒有這份分譜
UNSYNCED = {"二胡 II": (5, 8)}          # 專案裡改了、存檔時沒寫進譜庫
DURATION_PROJECT, DURATION_CATALOG = "07:45", "07:30"


def file_for(voice):
    return f"烽火操_{voice.replace(' ', '')}.pdf"


REGISTERED = {v[0]: file_for(v[0]) for v in VOICES if v[0] not in ("中音嗩吶", "豎琴", "二胡 II")}
UNREGISTERED = ["掃描_二胡2.pdf", "Erhu2_2019.pdf", "烽火操_揚琴_修訂.pdf", "烽火操_總譜.pdf"]


def _reopen_lists():
    """開啟專案時：專案裡的快照與譜庫現在的聲部清單（譜庫刪了中音嗩吶、加了排鼓、古箏移到揚琴前）"""
    before = [v[0] for v in VOICES]
    now = [n for n in before if n not in ("中音嗩吶", "古箏")]
    now.insert(now.index("打擊 2") + 1, "排鼓")
    now.insert(now.index("揚琴"), "古箏")
    return before, now


REOPEN_BEFORE, REOPEN_NOW = _reopen_lists()


def _group_side():
    """連線時群組這邊的編制：沒有豎琴、多一個排鼓、兩個聲部人數不同"""
    rows = []
    for name, lo, hi, sec in VOICES:
        if name == "豎琴":
            continue
        if name == "二胡 I":
            lo, hi = 8, 10
        if name == "大提琴":
            lo, hi = 4, 6
        rows.append((name, lo, hi, sec))
        if name == "打擊 2":
            rows.append(("排鼓", 1, 1, "打擊"))
    return rows


GROUP_SIDE = _group_side()
GROUP_FILES = [file_for(r[0]) for r in GROUP_SIDE]

# 存檔衝突：項目、原本、你改成、譜庫現在（原本＝譜庫現在表示沒有衝突）
SAVE_CHANGES = [
    ("二胡 II 的人數", "6–8", "5–8", "6–10"),
    ("時長", "07:30", "07:45", "08:00"),
    ("打擊 2", "人數 1", "刪除這個聲部", "人數 2"),
    ("中胡 的人數", "4–6", "4–8", "4–6"),
    ("新增聲部「排鼓」", "（沒有）", "第 10 個，人數 1", "（沒有）"),
    ("大提琴 的人數", "4–6", "4 以上", "4–6"),
    ("聲部順序", "古箏在大阮後面", "古箏移到揚琴前面", "古箏在大阮後面"),
]

SECTION_MAP = {
    "國樂團": [(n, SECTION[n], c) for n, c in [
        ("梆笛", 14), ("曲笛", 15), ("新笛", 6), ("高音笙", 15), ("中音笙", 13), ("低音笙", 4),
        ("高音嗩吶", 12), ("中音嗩吶", 9), ("定音鼓", 10), ("打擊 1", 15), ("打擊 2", 12),
        ("揚琴", 16), ("柳琴", 13), ("琵琶", 16), ("中阮", 16), ("大阮", 14), ("古箏", 7), ("豎琴", 3),
        ("二胡獨奏", 2), ("高胡", 15), ("二胡 I", 16), ("二胡 II", 16), ("中胡", 16),
        ("大提琴", 16), ("低音提琴", 16),
    ] if n in SECTION] + [("新笛", "吹管", 6), ("低音笙", "吹管", 4), ("排鼓", None, 3), ("雲鑼", None, 1)],
    "管弦樂團": [
        ("Flute 1", "木管", 5), ("Oboe", "木管", 4), ("Clarinet in B♭ 1", "木管", 4), ("Bassoon", "木管", 3),
        ("Horn in F 1", "銅管", 4), ("Trumpet in C 1", "銅管", 3), ("Trombone", "銅管", 2),
        ("Timpani", "打擊", 4), ("Percussion", "打擊", 3), ("Piano", "鍵盤", 1),
        ("Violin I", "弦樂", 5), ("Violin II", "弦樂", 5), ("Viola", "弦樂", 5), ("Violoncello", "弦樂", 5),
        ("Contrabass", "弦樂", 5), ("Harp", None, 2), ("Celesta", None, 1),
    ],
}
SECTION_SETS = {"國樂團": SECTIONS_CN, "管弦樂團": ["木管", "銅管", "打擊", "鍵盤", "弦樂"]}

# --- 顏色（配合主程式的深色主題）--------------------------------------------

AMBER_BG, AMBER_FG = "#5b4613", "#f2c14e"     # 未同步
GRAY = "#7a7f93"                               # 本專案沒有、唯讀
GREEN_BG, GREEN_FG = "#1d4a2f", "#7ddc9a"      # 新增、有
RED_BG, RED_FG = "#5a2129", "#ff8a8a"          # 移除、衝突、缺
BLUE_BG, BLUE_FG = "#253c63", "#8fb8ff"        # 移動、待儲存

# --- 小工具 -------------------------------------------------------------------


def fmt_count(lo, hi):
    if hi is None:
        return f"{lo} 以上"
    return str(lo) if lo == hi else f"{lo}–{hi}"


def total_text(rows):
    lo = sum(r[1] for r in rows)
    if any(r[2] is None for r in rows):
        return f"至少 {lo} 人"
    return f"{lo}–{sum(r[2] for r in rows)} 人"


def label(text, color=None, bold=False, size=None, wrap=False, italic=False, bg=None):
    w = QLabel(text)
    css = []
    if color:
        css.append(f"color: {color};")
    if bold:
        css.append("font-weight: bold;")
    if size:
        css.append(f"font-size: {size}px;")
    if italic:
        css.append("font-style: italic;")
    if bg:
        css.append(f"background: {bg}; padding: 3px 8px; border-radius: 4px;")
    w.setStyleSheet(" ".join(css))
    w.setWordWrap(wrap)
    return w


def line(text="", width=None, bg=None, placeholder=None):
    w = QLineEdit(text)
    if width:
        w.setFixedWidth(width)
    if bg:
        w.setStyleSheet(f"background: {bg}; color: {AMBER_FG};")
    if placeholder:
        w.setPlaceholderText(placeholder)
    return w


def btn(text, danger=False, primary=False, slot=None):
    w = QPushButton(text)
    if danger:
        w.setStyleSheet("background-color: #c0392b; color: white;")
    if primary:
        w.setStyleSheet("background-color: #4338CA; color: #eef2ff; font-weight: bold;")
    if slot:
        w.clicked.connect(slot)
    return w


def hbox(*items, margins=None):
    lay = QHBoxLayout()
    if margins is not None:
        lay.setContentsMargins(*margins)
    for w in items:
        if w is None:
            lay.addStretch()
        elif isinstance(w, str):
            lay.addWidget(QLabel(w))
        elif isinstance(w, QHBoxLayout):
            lay.addLayout(w)
        else:
            grow = isinstance(w, QLineEdit) and w.maximumWidth() > 10000
            lay.addWidget(w, 1 if grow else 0)
    return lay


def cell(text, fg=None, bg=None, italic=False, strike=False, bold=False, tip=None, editable=False, center=False):
    it = QTableWidgetItem(str(text))
    if not editable:
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if fg:
        it.setForeground(QColor(fg))
    if bg:
        it.setBackground(QColor(bg))
    if italic or strike or bold:
        f = it.font()
        f.setItalic(italic)
        f.setStrikeOut(strike)
        f.setBold(bold)
        it.setFont(f)
    if tip:
        it.setToolTip(tip)
    if center:
        it.setTextAlignment(Qt.AlignCenter)
    return it


def table(headers, rows, stretch=None):
    tbl = QTableWidget(len(rows), len(headers))
    tbl.setHorizontalHeaderLabels(headers)
    tbl.verticalHeader().setVisible(False)
    has_widgets = any(isinstance(v, QWidget) for row in rows for v in row)
    tbl.verticalHeader().setDefaultSectionSize(38 if has_widgets else 28)
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            if v is None:
                continue
            if isinstance(v, QWidget):
                tbl.setCellWidget(r, c, v)
            else:
                tbl.setItem(r, c, v if isinstance(v, QTableWidgetItem) else cell(v))
    hdr = tbl.horizontalHeader()
    for c in range(len(headers)):
        hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
    if stretch is not None:
        hdr.setSectionResizeMode(stretch, QHeaderView.Stretch)
    return tbl


def centered(widget):
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(widget, alignment=Qt.AlignCenter)
    return holder


def banner(text, buttons=(), color=AMBER_FG, bg=AMBER_BG):
    frame = QFrame()
    frame.setObjectName("banner")
    frame.setStyleSheet(f"QFrame#banner {{ background: {bg}; border-radius: 5px; }}")
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(10, 6, 10, 6)
    lay.addWidget(label(text, color=color, wrap=True), 1)
    for b in buttons:
        lay.addWidget(b)
    return frame


def confirm_overwrite(parent, voice, old, new):
    """指定給已登記了檔的聲部時，提示後覆蓋（「譜庫的收網」Q27）"""
    if not old:
        return True
    answer = QMessageBox.question(
        parent, "覆蓋登記",
        f"「{voice}」已經登記了「{old}」。\n\n"
        f"按儲存後改登記「{new}」，「{old}」會丟進 Drive 垃圾桶，30 天內可以從 Drive 垃圾桶救回。\n\n要覆蓋嗎？",
    )
    return answer == QMessageBox.Yes


def link_text():
    return " › ".join(LINK)


# --- 主視窗外框 ---------------------------------------------------------------


def group_fields(lay):
    """群組分頁上方的既有欄位（照 GroupTab 排）"""
    lay.addLayout(hbox(t("group.name_label"), line("烽火操"), btn(t("group.delete"), danger=True)))
    lay.addLayout(hbox(
        t("group.piece_name_label"), line("長城隨想"), btn(t("group.auto_detect")),
        t("group.movement_num_label"), line("2", width=60), t("group.movement_name_label"), line("烽火操"),
    ))
    lay.addLayout(hbox(t("group.composer_label"), line("劉文金"), t("group.genre_label"), line("協奏曲"), None))
    lay.addLayout(hbox(
        t("group.score_file"), label("烽火操_總譜.pdf"), None,
        btn(t("group.score_file.set")), btn(t("group.score_file.clear")),
    ))


def classic_file_list(lay, pairs_title=True):
    """既有的檔案清單：分譜依位置配對聲部，本專案沒有的聲部跳過"""
    present = [v for v in VOICES if v[0] not in ABSENT]
    lay.addWidget(label(
        f"{len(VOICES)} 個聲部（{len(ABSENT)} 個本專案沒有）= {len(present)} 個檔案", color="#2ecc71",
    ))
    if pairs_title:
        lay.addWidget(QLabel(t("group.file_list")))
    lst = QListWidget()
    for name, *_ in present:
        lst.addItem(f"{name}  |  {file_for(name)}")
    lay.addWidget(lst, 1)
    lay.addLayout(hbox(btn(t("group.add_files")), btn("← " + t("group.ungrouped")),
                       btn(t("file.delete_from_disk"), danger=True), None))


def main_shell(left, group_tab, group_title="烽火操"):
    page = QWidget()
    v = QVBoxLayout(page)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)
    mb = QMenuBar()
    for key in ("menu.file", "menu.edit", "menu.import", "menu.catalog", "menu.tools", "menu.view"):
        mb.addMenu(t(key))
    v.addWidget(mb)
    body = QWidget()
    bv = QVBoxLayout(body)
    bv.setContentsMargins(4, 4, 4, 4)
    splitter = QSplitter(Qt.Horizontal)
    if left is not None:
        splitter.addWidget(left)
    right = QWidget()
    rl = QVBoxLayout(right)
    rl.setContentsMargins(6, 0, 0, 0)
    rl.setSpacing(4)
    rl.addLayout(hbox(btn(t("group.add")), btn(t("group.link_movements")), None))
    tabs = QTabWidget()
    tabs.addTab(QLabel(t("ungrouped.empty")), t("group.ungrouped"))
    tabs.addTab(group_tab, group_title)
    for name in ("關山行", "忠魂祭", "遙望篇"):
        tabs.addTab(QWidget(), name)
    tabs.setCurrentIndex(1)
    rl.addWidget(tabs)
    splitter.addWidget(right)
    splitter.setStretchFactor(splitter.count() - 1, 1)
    bv.addWidget(splitter, 1)
    bv.addLayout(hbox(t("panel.master_template"), line("{曲名}_{樂章編號}_{序號}_{樂器}"), btn(t("panel.insert_variable"))))
    preview = btn(t("panel.preview_rename"))
    preview.setStyleSheet("font-size: 14px; font-weight: bold; padding: 8px 24px;")
    bv.addLayout(hbox(QLabel(t("status.ready")), None, preview))
    v.addWidget(body, 1)
    return page


# --- 畫面一：主視窗的編制表 -----------------------------------------------------


def ensemble_table_a(voices=VOICES, ghosts=None, unsynced=UNSYNCED, counts=None):
    """變體 A 的編制表：聲部｜人數｜聲部組，一列一個聲部"""
    rows = []
    for name, lo, hi, sec in voices:
        lo, hi = (counts or {}).get(name, (lo, hi))
        if name in ABSENT:
            rows.append([
                cell(f"{name}（本專案沒有）", fg=GRAY, italic=True, tip="本專案沒有這份分譜：配對時跳過，只存在專案"),
                cell(fmt_count(lo, hi), fg=GRAY, italic=True), cell(sec, fg=GRAY, italic=True),
            ])
            continue
        count = cell(fmt_count(lo, hi), editable=True)
        if name in unsynced:
            count = cell(fmt_count(*unsynced[name]), bg=AMBER_BG, fg=AMBER_FG, editable=True,
                         tip=f"未同步：譜庫現在是 {fmt_count(lo, hi)}，存檔時沒寫進去")
        rows.append([cell(name, editable=True), count,
                     cell(sec, fg=GRAY, tip="依全社團的聲部組對照；到譜庫視窗的「聲部組對照」修改")])
    if ghosts:
        rows = ghosts(rows)
    return table(["聲部", "人數", "聲部組"], rows, stretch=0)


def main_a():
    left = QWidget()
    left.setFixedWidth(450)
    lay = QVBoxLayout(left)
    lay.setContentsMargins(4, 4, 4, 4)
    lay.addWidget(label("編制表", bold=True))
    box = QGroupBox("譜庫")
    bl = QVBoxLayout(box)
    bl.addLayout(hbox(label(link_text()), None, btn("改連…")))
    bl.addLayout(hbox("時長", line(DURATION_PROJECT, width=70, bg=AMBER_BG), label("分:秒", color=GRAY), None))
    bl.addLayout(hbox(
        label("未同步 2 項（二胡 II 的人數、時長）：存檔時斷網，下次存檔或開啟專案時重試", color=AMBER_FG, wrap=True),
        btn("現在重試"),
    ))
    lay.addWidget(box)
    lay.addWidget(ensemble_table_a(), 1)
    present = [v for v in VOICES if v[0] not in ABSENT]
    lay.addWidget(label(f"{len(VOICES)} 個聲部（{len(ABSENT)} 個本專案沒有）．{total_text(present)}", color=GRAY))
    lay.addLayout(hbox(line(placeholder="輸入聲部名稱..."), btn(t("instrument.add"))))
    lay.addLayout(hbox(btn(t("instrument.auto_extract")), btn(t("instrument.remove")), btn(t("instrument.export"))))
    lay.addWidget(label("拖拉列排序．人數輸入 6-8、4+ 或 1．右鍵：標記「本專案沒有這份分譜」", color=GRAY, wrap=True))
    tab = QWidget()
    tl = QVBoxLayout(tab)
    tl.setContentsMargins(4, 4, 4, 4)
    group_fields(tl)
    classic_file_list(tl)
    return main_shell(left, tab, "烽火操（未同步）")


def main_b():
    tab = QWidget()
    tl = QVBoxLayout(tab)
    tl.setContentsMargins(4, 4, 4, 4)
    group_fields(tl)
    strip = QFrame()
    strip.setObjectName("strip")
    strip.setStyleSheet("QFrame#strip { border: 1px solid #42445c; border-radius: 5px; }")
    sl = QHBoxLayout(strip)
    sl.setContentsMargins(8, 4, 8, 4)
    badge = label("未同步 2", color=AMBER_FG, bg=AMBER_BG, bold=True)
    badge.setToolTip("二胡 II 的人數、時長：存檔時斷網，下次存檔或開啟專案時重試")
    for w in (label("譜庫", color=GRAY), label(link_text(), bold=True), btn("改連…")):
        sl.addWidget(w)
    sl.addStretch()
    for w in (QLabel("時長"), line(DURATION_PROJECT, width=70, bg=AMBER_BG), badge, btn("重試")):
        sl.addWidget(w)
    tl.addWidget(strip)
    rows = []
    serial = 0
    for name, lo, hi, sec in VOICES:
        if name in ABSENT:
            rows.append([cell(""), cell(name, fg=GRAY, italic=True), cell(fmt_count(lo, hi), fg=GRAY, italic=True),
                         cell(sec, fg=GRAY, italic=True), cell("本專案沒有這份分譜（配對時跳過）", fg=GRAY, italic=True)])
            continue
        serial += 1
        count = cell(fmt_count(lo, hi), editable=True)
        if name in UNSYNCED:
            count = cell(fmt_count(*UNSYNCED[name]), bg=AMBER_BG, fg=AMBER_FG, editable=True,
                         tip=f"未同步：譜庫現在是 {fmt_count(lo, hi)}")
        rows.append([cell(f"{serial:02d}", fg=GRAY, center=True), cell(name, editable=True), count,
                     cell(sec, fg=GRAY), cell(file_for(name))])
    tl.addWidget(table(["序號", "聲部", "人數", "聲部組", "分譜檔"], rows, stretch=4), 1)
    present = [v for v in VOICES if v[0] not in ABSENT]
    tl.addWidget(label(f"{len(VOICES)} 個聲部（{len(ABSENT)} 個本專案沒有）、{len(present)} 個檔案，全部配對．{total_text(present)}．沒配到聲部的檔：無", color=GRAY))
    tl.addLayout(hbox(btn("+ 新增聲部"), btn("移除聲部"), btn("標記本專案沒有"), label("｜", color=GRAY),
                      btn(t("group.add_files")), btn("← " + t("group.ungrouped")), btn(t("file.delete_from_disk"), danger=True), None))
    tl.addWidget(label("拖拉一列：聲部連同它的分譜一起移動．按住 Alt 拖拉「分譜檔」欄：只換分譜的位置", color=GRAY))
    return main_shell(None, tab, "烽火操（未同步）")


def main_c():
    left = QWidget()
    left.setFixedWidth(260)
    ll = QVBoxLayout(left)
    ll.setContentsMargins(4, 4, 4, 4)
    ll.addWidget(label(t("instrument.title"), bold=True))
    combo = QComboBox()
    combo.addItem(t("instrument.load_preset"))
    ll.addWidget(combo)
    lst = QListWidget()
    for name, *_ in VOICES:
        it = QListWidgetItem(name)
        if name in ABSENT:
            it.setText(f"{name}（本專案沒有）")
            it.setForeground(QColor(GRAY))
        if name in UNSYNCED:
            it.setText(f"{name}（未同步）")
            it.setForeground(QColor(AMBER_FG))
        lst.addItem(it)
    ll.addWidget(lst, 1)
    ll.addLayout(hbox(line(placeholder=t("instrument.placeholder")), btn(t("instrument.add"))))
    ll.addLayout(hbox(btn(t("instrument.auto_extract")), btn(t("instrument.remove"))))

    tab = QWidget()
    tl = QVBoxLayout(tab)
    tl.setContentsMargins(4, 4, 4, 4)
    tl.addWidget(banner("這個群組有 2 項改動還沒寫進譜庫（存檔時斷網）。下次存檔或開啟專案時會自動重試。", [btn("現在重試")]))
    group_fields(tl)
    sub = QTabWidget()
    files_page = QWidget()
    classic_file_list(QVBoxLayout(files_page), pairs_title=False)
    sub.addTab(files_page, "分譜")

    ens = QWidget()
    el = QVBoxLayout(ens)
    box = QGroupBox("連到譜庫")
    bl = QHBoxLayout(box)
    for k, v in zip(("曲目", "版本", "樂章"), LINK):
        bl.addWidget(label(k, color=GRAY))
        bl.addWidget(label(v, bold=True))
        bl.addSpacing(12)
    bl.addStretch()
    bl.addWidget(btn("改連…"))
    el.addWidget(box)
    mins, secs = QSpinBox(), QSpinBox()
    mins.setValue(7)
    secs.setRange(0, 59)
    secs.setValue(45)
    for s in (mins, secs):
        s.setStyleSheet(f"background: {AMBER_BG}; color: {AMBER_FG};")
    el.addLayout(hbox("時長", mins, "分", secs, "秒", label("（未同步：譜庫現在是 07:30）", color=AMBER_FG), None))
    rows = []
    for name, lo, hi, sec in VOICES:
        if name in UNSYNCED:
            lo, hi = UNSYNCED[name]
        has = QCheckBox()
        has.setChecked(name not in ABSENT)
        has.setToolTip("取消勾選＝本專案沒有這份分譜，配對時跳過")
        lo_w, hi_w = QSpinBox(), QSpinBox()
        lo_w.setValue(lo)
        hi_w.setValue(hi or 0)
        hi_w.setEnabled(hi is not None)
        plus = QCheckBox()
        plus.setChecked(hi is None)
        plus.toggled.connect(lambda on, w=hi_w: w.setEnabled(not on))
        sec_w = QComboBox()
        sec_w.addItems(SECTIONS_CN)
        sec_w.setCurrentText(sec)
        if name in UNSYNCED:
            for w in (lo_w, hi_w):
                w.setStyleSheet(f"background: {AMBER_BG}; color: {AMBER_FG};")
        fg = GRAY if name in ABSENT else None
        rows.append([centered(has), cell(name, fg=fg, italic=bool(fg), editable=True), lo_w, hi_w, centered(plus), sec_w])
    el.addWidget(table(["本專案有", "聲部", "最少", "最多", "以上", "聲部組"], rows, stretch=1), 1)
    present = [v for v in VOICES if v[0] not in ABSENT]
    el.addWidget(label(f"{len(VOICES)} 個聲部，本專案用 {len(present)} 個．{total_text(present)}", color=GRAY))
    el.addWidget(label("聲部組是全社團共用的對照（國樂團）：在這裡改，其他曲目的同名聲部也跟著改", color=AMBER_FG))
    sub.addTab(ens, "編制（未同步 2）")
    sub.setCurrentIndex(1)
    tl.addWidget(sub, 1)
    return main_shell(left, tab, "烽火操（未同步）")


# --- 畫面二：譜庫視窗的版本頁 -----------------------------------------------------


def catalog_shell(content):
    page = QWidget()
    v = QVBoxLayout(page)
    v.setContentsMargins(4, 4, 4, 4)
    v.addLayout(hbox(btn(t("catalog.toolbar.new_folder")), btn(t("catalog.toolbar.refresh")),
                     line(width=200, placeholder=t("catalog.toolbar.search")), None, btn("聲部組對照")))
    splitter = QSplitter(Qt.Horizontal)
    tree = QTreeWidget()
    tree.setHeaderHidden(True)
    tree.setMinimumWidth(240)
    root = QTreeWidgetItem(tree, ["華夏國樂社譜庫"])
    for composer, pieces in (("劉文金", ["長城隨想", "豫北敘事曲"]), ("李煥之", ["春節序曲"]), ("彭修文", ["秦‧兵馬俑", "丰收鑼鼓"])):
        c = QTreeWidgetItem(root, [composer])
        for p in pieces:
            item = QTreeWidgetItem(c, [p])
            if p == "長城隨想":
                tree.setCurrentItem(item)
    tree.expandAll()
    splitter.addWidget(tree)
    tabs = QTabWidget()
    for name in ("曲目資訊", "編制與分譜", "演出紀錄", "樂章", "版本"):
        tabs.addTab(content if name == "編制與分譜" else QWidget(), name)
    tabs.setCurrentIndex(1)
    splitter.addWidget(tabs)
    splitter.setStretchFactor(1, 3)
    v.addWidget(splitter, 1)
    v.addWidget(label("已連線：華夏國樂社譜庫（共用帳號）", color=GRAY))
    return page


def selector_row():
    ed = QComboBox()
    ed.addItems(["人民音樂出版社（使用中）", "華夏改編版（2019）"])
    mv = QComboBox()
    mv.addItems(["整首", "第一樂章 關山行", "第二樂章 烽火操", "第三樂章 忠魂祭", "第四樂章 遙望篇"])
    mv.setCurrentIndex(2)
    return hbox("版本", ed, "樂章", mv, "時長", line(DURATION_CATALOG, width=70), label("分:秒", color=GRAY), None)


def status_cells(name, pending=None):
    if pending:
        return cell("有（待儲存）", fg=BLUE_FG), cell(f"{pending}（儲存後登記）", fg=BLUE_FG, italic=True)
    if name in REGISTERED:
        return cell("有", fg=GREEN_FG, center=True), cell(REGISTERED[name])
    return cell("缺", fg=RED_FG, center=True), cell("（沒有登記）", fg=GRAY)


def save_row(unsaved):
    return hbox(unsaved, None, btn("放棄改動"), btn("儲存", primary=True))


def catalog_a():
    content = QWidget()
    lay = QVBoxLayout(content)
    lay.addLayout(selector_row())
    split = QSplitter(Qt.Horizontal)
    rows = []
    for name, lo, hi, sec in VOICES:
        status, f = status_cells(name)
        rows.append([cell(name, editable=True), cell(fmt_count(lo, hi), editable=True), cell(sec, fg=GRAY), status, f])
    tbl = table(["聲部", "人數", "聲部組", "狀態", "登記的檔"], rows, stretch=4)
    tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
    split.addWidget(tbl)
    box = QGroupBox(f"資料夾裡沒有登記的檔（{len(UNREGISTERED)}）")
    bl = QVBoxLayout(box)
    files = QListWidget()
    files.addItems(UNREGISTERED)
    bl.addWidget(files, 1)
    bl.addWidget(label("選一個檔，再選左邊的聲部", color=GRAY))
    unsaved = label("", color=BLUE_FG)
    pending = []

    def assign():
        r, it = tbl.currentRow(), files.currentItem()
        if r < 0 or it is None:
            unsaved.setText("先選一個檔和一個聲部")
            return
        voice = VOICES[r][0]
        if not confirm_overwrite(content, voice, REGISTERED.get(voice), it.text()):
            return
        status, f = status_cells(voice, pending=it.text())
        tbl.setItem(r, 3, status)
        tbl.setItem(r, 4, f)
        files.takeItem(files.row(it))
        pending.append(voice)
        unsaved.setText(f"{len(pending)} 項改動未儲存")

    bl.addWidget(btn("← 指定給選取的聲部", slot=assign))
    bl.addWidget(btn("在 Drive 開啟"))
    split.addWidget(box)
    split.setStretchFactor(0, 3)
    lay.addWidget(split, 1)
    lay.addLayout(save_row(unsaved))
    return catalog_shell(content)


def catalog_b():
    content = QWidget()
    lay = QVBoxLayout(content)
    lay.addLayout(selector_row())
    unsaved = label("", color=BLUE_FG)
    rows = []
    pending = set()

    for r, (name, lo, hi, sec) in enumerate(VOICES):
        combo = QComboBox()
        combo.addItem(REGISTERED.get(name, "（沒有登記）"))
        combo.insertSeparator(1)
        combo.addItems(UNREGISTERED)
        status, _ = status_cells(name)
        rows.append([cell(name, editable=True), cell(fmt_count(lo, hi), editable=True), cell(sec, fg=GRAY), status, combo])
    tbl = table(["聲部", "人數", "聲部組", "狀態", "登記的檔"], rows, stretch=4)

    def changed(index, r, combo):
        voice = VOICES[r][0]
        if index <= 1:
            pending.discard(voice)
            tbl.setItem(r, 3, status_cells(voice)[0])
        elif confirm_overwrite(content, voice, REGISTERED.get(voice), combo.currentText()):
            pending.add(voice)
            tbl.setItem(r, 3, status_cells(voice, pending=combo.currentText())[0])
        else:
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        unsaved.setText(f"{len(pending)} 項改動未儲存" if pending else "")

    for r in range(len(VOICES)):
        combo = tbl.cellWidget(r, 4)
        combo.currentIndexChanged.connect(lambda i, r=r, c=combo: changed(i, r, c))
    lay.addWidget(tbl, 1)
    lay.addWidget(label("還沒指定給聲部的檔：" + "、".join(UNREGISTERED), color=GRAY, wrap=True))
    lay.addLayout(save_row(unsaved))
    return catalog_shell(content)


def catalog_c():
    content = QWidget()
    lay = QVBoxLayout(content)
    lay.addLayout(selector_row())
    split = QSplitter(Qt.Vertical)
    vrows = []
    for name, lo, hi, sec in VOICES:
        vrows.append([cell(name, editable=True), cell(fmt_count(lo, hi), editable=True), cell(sec, fg=GRAY), status_cells(name)[0]])
    vt = table(["聲部", "人數", "聲部組", "狀態"], vrows, stretch=0)
    split.addWidget(vt)
    box = QGroupBox("版本資料夾裡的檔（Drive）")
    bl = QVBoxLayout(box)
    voices = [v[0] for v in VOICES]
    files = [(f, "（根目錄）", None) for f in UNREGISTERED] + [(f, SECTION[v], v) for v, f in REGISTERED.items()]
    unsaved = label("", color=BLUE_FG)
    frows = []
    for f, folder, voice in files:
        combo = QComboBox()
        combo.addItem("（沒有登記）")
        combo.addItems(voices)
        if voice:
            combo.setCurrentText(voice)
        else:
            combo.setStyleSheet(f"color: {AMBER_FG};")
        frows.append([cell(f), cell(folder, fg=GRAY), combo])
    ft = table(["檔名", "子資料夾", "登記給聲部"], frows, stretch=0)
    ft.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
    ft.setColumnWidth(2, 180)

    def changed(r, combo):
        voice = combo.currentText()
        if voice in REGISTERED and REGISTERED[voice] != files[r][0]:
            if not confirm_overwrite(content, voice, REGISTERED[voice], files[r][0]):
                combo.blockSignals(True)
                combo.setCurrentIndex(0)
                combo.blockSignals(False)
                return
        if voice in voices:
            vt.setItem(voices.index(voice), 3, cell("有（待儲存）", fg=BLUE_FG))
        unsaved.setText("有改動未儲存")

    for r in range(len(files)):
        combo = ft.cellWidget(r, 2)
        combo.currentIndexChanged.connect(lambda _i, r=r, c=combo: changed(r, c))
    bl.addWidget(ft)
    split.addWidget(box)
    lay.addWidget(split, 1)
    lay.addLayout(save_row(unsaved))
    return catalog_shell(content)


# --- 對話框外框 ---------------------------------------------------------------


def fit_rows(tbl):
    """列數少的表格高度剛好放下所有列"""
    tbl.setFixedHeight(tbl.horizontalHeader().sizeHint().height() + tbl.verticalHeader().defaultSectionSize() * tbl.rowCount() + 16)
    return tbl


def dialog_page(title, body_items, buttons, width=980, grow=True):
    page = QFrame()
    page.setObjectName("backdrop")
    page.setStyleSheet("QFrame#backdrop { background: #0d0e13; }")
    outer = QVBoxLayout(page)
    frame = QFrame()
    frame.setObjectName("dlg")
    frame.setStyleSheet("QFrame#dlg { background: #16171e; border: 1px solid #4a4c63; border-radius: 8px; }")
    frame.setFixedWidth(width)
    v = QVBoxLayout(frame)
    v.setContentsMargins(16, 12, 16, 12)
    v.addWidget(label(title, bold=True, size=16))
    for w in body_items:
        if isinstance(w, QHBoxLayout):
            v.addLayout(w)
        elif isinstance(w, str):
            v.addWidget(label(w, wrap=True))
        else:
            if not grow and isinstance(w, QTableWidget):
                fit_rows(w)
            v.addWidget(w, 1 if grow and isinstance(w, (QTableWidget, QSplitter, QTabWidget)) else 0)
    v.addLayout(hbox(None, *buttons))
    if not grow:
        frame.setMinimumHeight(v.totalHeightForWidth(width) if v.hasHeightForWidth() else v.sizeHint().height())
        outer.addStretch()
    outer.addWidget(frame, 1 if grow else 0, Qt.AlignHCenter)
    if not grow:
        outer.addStretch()
    return page


# --- 畫面三：聲部組對照 ---------------------------------------------------------


def sections_a():
    rows = []
    entries = [(otype, n, s, c) for otype, items in SECTION_MAP.items() for n, s, c in items]
    entries.sort(key=lambda e: e[2] is not None)
    for otype, name, sec, count in entries:
        combo = QComboBox()
        combo.addItem("（未歸類）")
        combo.addItems(SECTION_SETS[otype])
        if sec:
            combo.setCurrentText(sec)
        else:
            combo.setStyleSheet(f"color: {AMBER_FG};")
        rows.append([cell(otype, fg=GRAY), cell(name, editable=True), combo, cell(f"{count} 首", fg=GRAY, center=True)])
    tbl = table(["樂團類型", "聲部名稱", "聲部組", "譜庫裡用到"], rows, stretch=1)
    tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
    tbl.setColumnWidth(2, 150)
    otype = QComboBox()
    otype.addItems(["全部", "國樂團", "管弦樂團"])
    return dialog_page("聲部組對照（全社團共用）", [
        label("同一個聲部名稱在每個樂團類型各歸一組；所有曲目、所有專案都照這張表。譜庫裡出現、還沒歸類的聲部排在最前面。", color=GRAY, wrap=True),
        hbox("樂團類型", otype, "搜尋", line(width=220), None, btn("+ 新增"), btn("刪除")),
        tbl,
    ], [btn("取消"), btn("儲存", primary=True)], width=760)


def sections_b():
    tabs = QTabWidget()
    for otype, items in SECTION_MAP.items():
        page = QWidget()
        h = QHBoxLayout(page)
        for sec in [None] + SECTION_SETS[otype]:
            col = QVBoxLayout()
            names = [n for n, s, _ in items if s == sec]
            col.addWidget(label(f"{sec or '未歸類'}（{len(names)}）", bold=True, color=AMBER_FG if sec is None else None))
            lst = QListWidget()
            lst.setDragDropMode(QAbstractItemView.DragDrop)
            lst.setDefaultDropAction(Qt.MoveAction)
            lst.addItems(names)
            if sec is None:
                lst.setStyleSheet(f"QListWidget {{ border: 1px solid {AMBER_FG}; }}")
            col.addWidget(lst)
            h.addLayout(col)
        tabs.addTab(page, otype)
    return dialog_page("聲部組對照（全社團共用）", [
        label("把聲部名稱拖到別的組。每個樂團類型一套；所有曲目、所有專案都照這張表。", color=GRAY, wrap=True),
        tabs,
        hbox(line(width=220, placeholder="新增聲部名稱..."), btn("加到未歸類"), None),
    ], [btn("取消"), btn("儲存", primary=True)], width=1060)


# --- 畫面四：開啟專案時，譜庫的聲部清單變了 -------------------------------------------


REOPEN_TITLE = f"「烽火操」連到的譜庫聲部清單變了"
REOPEN_INTRO = f"譜庫（{link_text()}）的聲部清單跟這個專案上次存的不一樣。分譜依位置配對聲部，所以先請你確認。"
REOPEN_APPLIED = "人數的變動已直接套用：二胡 I 6–8 → 6–10"


def reopen_mark(name):
    """變動的種類與標記顏色"""
    if name not in REOPEN_NOW:
        return "removed"
    if name not in REOPEN_BEFORE:
        return "added"
    if name == "古箏":
        return "moved"
    return None


def reopen_a():
    left_rows, right_rows = [], []
    for i, name in enumerate(REOPEN_BEFORE, 1):
        kind = reopen_mark(name)
        if kind == "removed":
            left_rows.append([cell(i, fg=RED_FG, center=True), cell(f"{name}（譜庫已移除）", fg=RED_FG, bg=RED_BG, strike=True)])
        elif kind == "moved":
            left_rows.append([cell(i, fg=BLUE_FG, center=True), cell(f"{name}（移到第 {REOPEN_NOW.index(name) + 1} 個）", fg=BLUE_FG, bg=BLUE_BG)])
        else:
            left_rows.append([cell(i, fg=GRAY, center=True), cell(name, fg=GRAY if name in ABSENT else None)])
    for i, name in enumerate(REOPEN_NOW, 1):
        kind = reopen_mark(name)
        if kind == "added":
            right_rows.append([cell(i, fg=GREEN_FG, center=True), cell(f"{name}（新增，沒有分譜）", fg=GREEN_FG, bg=GREEN_BG)])
        elif kind == "moved":
            right_rows.append([cell(i, fg=BLUE_FG, center=True), cell(f"{name}（原本第 {REOPEN_BEFORE.index(name) + 1} 個）", fg=BLUE_FG, bg=BLUE_BG)])
        else:
            right_rows.append([cell(i, fg=GRAY, center=True), cell(name)])
    split = QSplitter(Qt.Horizontal)
    for title, rows in (("專案裡的（上次存檔時）", left_rows), ("譜庫現在", right_rows)):
        box = QGroupBox(title)
        QVBoxLayout(box).addWidget(table(["#", "聲部"], rows, stretch=1))
        split.addWidget(box)
    return dialog_page(REOPEN_TITLE, [
        REOPEN_INTRO, split,
        label(REOPEN_APPLIED, color=GRAY),
        label("套用後分譜跟著自己的聲部移動；「烽火操_中音嗩吶.pdf」沒有對應的聲部，會移到未分組。", color=AMBER_FG, wrap=True),
    ], [btn("維持專案裡的"), btn("套用譜庫的清單", primary=True)], width=820)


def reopen_b():
    changes = QListWidget()
    for text, fg in (
        (f"新增「排鼓」（第 {REOPEN_NOW.index('排鼓') + 1} 個）：這個專案沒有它的分譜", GREEN_FG),
        ("移除「中音嗩吶」：它的分譜「烽火操_中音嗩吶.pdf」會移到未分組", RED_FG),
        (f"「古箏」從第 {REOPEN_BEFORE.index('古箏') + 1} 個移到第 {REOPEN_NOW.index('古箏') + 1} 個：分譜跟著移動", BLUE_FG),
    ):
        it = QListWidgetItem(text)
        it.setForeground(QColor(fg))
        changes.addItem(it)
    changes.setMaximumHeight(100)
    rows = []
    serial = 0
    for name in REOPEN_NOW:
        kind = reopen_mark(name)
        bg = {"added": GREEN_BG, "moved": BLUE_BG}.get(kind)
        if name in ABSENT:
            rows.append([cell(""), cell(name, fg=GRAY, italic=True), cell("本專案沒有這份分譜", fg=GRAY, italic=True)])
            continue
        serial += 1
        f = cell("（沒有分譜）", fg=AMBER_FG, bg=bg) if kind == "added" else cell(file_for(name), bg=bg)
        rows.append([cell(f"{serial:02d}", fg=GRAY, center=True, bg=bg), cell(name, bg=bg), f])
    rows.append([cell("", bg=RED_BG), cell("（沒有聲部）", fg=RED_FG, bg=RED_BG), cell(f"{file_for('中音嗩吶')} → 移到未分組", fg=RED_FG, bg=RED_BG)])
    return dialog_page(REOPEN_TITLE, [
        REOPEN_INTRO, label("變動", bold=True), changes, label(REOPEN_APPLIED, color=GRAY),
        label("套用後的配對", bold=True), table(["序號", "聲部", "分譜檔"], rows, stretch=2),
    ], [btn("維持專案裡的"), btn("套用譜庫的清單", primary=True)], width=760)


def reopen_c():
    def ghosts(rows):
        out = []
        names = [v[0] for v in VOICES]
        for name in REOPEN_NOW:
            kind = reopen_mark(name)
            if kind == "added":
                out.append([cell(f"+ {name}（譜庫新增）", fg=GREEN_FG, bg=GREEN_BG), cell("1", fg=GREEN_FG, bg=GREEN_BG), cell("打擊", fg=GREEN_FG, bg=GREEN_BG)])
                continue
            row = rows[names.index(name)]
            if kind == "moved":
                row = [cell(f"{name}（從第 {REOPEN_BEFORE.index(name) + 1} 個移來）", fg=BLUE_FG, bg=BLUE_BG), row[1], row[2]]
            out.append(row)
            if name == "高音嗩吶":
                out.append([cell("中音嗩吶（譜庫已移除）", fg=RED_FG, bg=RED_BG, strike=True), cell("1", fg=RED_FG, bg=RED_BG, strike=True), cell("吹管", fg=RED_FG, bg=RED_BG, strike=True)])
        return out

    left = QWidget()
    left.setFixedWidth(450)
    lay = QVBoxLayout(left)
    lay.setContentsMargins(4, 4, 4, 4)
    lay.addWidget(label("編制表", bold=True))
    lay.addWidget(banner("譜庫的聲部清單變了：新增 1、移除 1、移動 1。人數的變動已套用。表格裡標色的是還沒套用的變動。",
                         [btn("套用"), btn("維持")], color=BLUE_FG, bg=BLUE_BG))
    lay.addWidget(label(link_text(), color=GRAY))
    lay.addWidget(ensemble_table_a(ghosts=ghosts, unsynced={}, counts={"二胡 I": (6, 10)}), 1)
    tab = QWidget()
    tl = QVBoxLayout(tab)
    tl.setContentsMargins(4, 4, 4, 4)
    group_fields(tl)
    tl.addWidget(banner("套用前，分譜仍照專案裡的聲部清單配對。", color=BLUE_FG, bg=BLUE_BG))
    classic_file_list(tl)
    return main_shell(left, tab, "烽火操（譜庫有變動）")


# --- 畫面五：存檔時譜庫已被別人改過 ---------------------------------------------------


CONFLICTS = [c for c in SAVE_CHANGES if c[1] != c[3]]
CONFLICT_TITLE = "譜庫裡有 3 項已經被別人改過"
CONFLICT_INTRO = (f"專案檔已經存好了。寫進譜庫前重讀了一次，發現下面 {len(CONFLICTS)} 項在你開啟專案之後被別人改過。"
                  f"選好每一項要用哪一邊，按「寫入譜庫」後跟其他 {len(SAVE_CHANGES) - len(CONFLICTS)} 項改動一起寫入。")
CONFLICT_BUTTONS = lambda: [btn("先不寫入（標成未同步）"), btn("寫入譜庫", primary=True)]


def conflict_a():
    rows = []
    for item, before, mine, theirs in CONFLICTS:
        group = QButtonGroup()
        a, b = QRadioButton(), QRadioButton()
        group.addButton(a)
        group.addButton(b)
        a._group = group
        rows.append([cell(item, bold=True), cell(before, fg=GRAY), cell(mine, fg=BLUE_FG), cell(theirs, fg=RED_FG), centered(a), centered(b)])
    return dialog_page(CONFLICT_TITLE, [
        CONFLICT_INTRO,
        table(["項目", "原本", "你改成", "譜庫現在", "用我的", "用譜庫的"], rows, stretch=0),
        hbox(btn("全部用我的"), btn("全部用譜庫的"), None, label("每一項都要選", color=GRAY)),
    ], CONFLICT_BUTTONS(), width=860, grow=False)


def conflict_b():
    body = QWidget()
    v = QVBoxLayout(body)
    step = label("", color=GRAY)
    title = label("", bold=True, size=18)
    before = label("", color=GRAY)
    mine, theirs = QPushButton(), QPushButton()
    for b in (mine, theirs):
        b.setCheckable(True)
        b.setMinimumHeight(90)
        b.setStyleSheet("QPushButton { font-size: 16px; min-height: 90px; } QPushButton:checked { background: #4338CA; color: #eef2ff; }")
    choice = QButtonGroup(body)
    choice.addButton(mine)
    choice.addButton(theirs)
    progress = label("", color=GRAY)
    state = {"i": 0, "picked": {}}

    def show(i):
        state["i"] = i
        item, b, m, th = CONFLICTS[i]
        step.setText(f"第 {i + 1} 項，共 {len(CONFLICTS)} 項")
        title.setText(item)
        before.setText(f"你開啟專案時是：{b}")
        mine.setText(f"用我的\n{m}")
        theirs.setText(f"用譜庫現在的\n{th}")
        choice.setExclusive(False)
        mine.setChecked(state["picked"].get(i) == "mine")
        theirs.setChecked(state["picked"].get(i) == "theirs")
        choice.setExclusive(True)
        progress.setText(f"已決定 {len(state['picked'])}／{len(CONFLICTS)}")

    def pick(which):
        state["picked"][state["i"]] = which
        if state["i"] + 1 < len(CONFLICTS):
            show(state["i"] + 1)
        else:
            show(state["i"])

    mine.clicked.connect(lambda: pick("mine"))
    theirs.clicked.connect(lambda: pick("theirs"))
    for w in (step, title, before):
        v.addWidget(w)
    v.addLayout(hbox(mine, theirs))
    v.addLayout(hbox(btn("← 上一項", slot=lambda: show(max(0, state["i"] - 1))), None, progress,
                     None, btn("下一項 →", slot=lambda: show(min(len(CONFLICTS) - 1, state["i"] + 1)))))
    show(0)
    return dialog_page(CONFLICT_TITLE, [CONFLICT_INTRO, body], CONFLICT_BUTTONS(), width=720, grow=False)


def conflict_c():
    rows = []
    for item, before, mine, theirs in SAVE_CHANGES:
        if before == theirs:
            rows.append([cell(item), cell(before, fg=GRAY), cell(mine, fg=BLUE_FG), cell(theirs, fg=GRAY), cell("沒有衝突，會寫入", fg=GREEN_FG)])
            continue
        combo = QComboBox()
        combo.addItems(["請選擇…", f"用我的（{mine}）", f"用譜庫的（{theirs}）"])
        combo.setStyleSheet(f"background: {RED_BG}; color: {RED_FG};")
        rows.append([cell(item, bg=RED_BG, bold=True), cell(before, fg=GRAY, bg=RED_BG), cell(mine, fg=BLUE_FG, bg=RED_BG),
                     cell(theirs, fg=RED_FG, bg=RED_BG), combo])
    return dialog_page("寫入譜庫前確認", [
        f"這次存檔要寫進譜庫的 {len(SAVE_CHANGES)} 項改動：{len(SAVE_CHANGES) - len(CONFLICTS)} 項沒有衝突，{len(CONFLICTS)} 項在你開啟專案之後被別人改過，要你決定。專案檔已經存好了。",
        table(["項目", "原本", "你改成", "譜庫現在", "處理"], rows, stretch=0),
    ], CONFLICT_BUTTONS(), width=980, grow=False)


# --- 畫面六：連線時兩邊都已有編制 -----------------------------------------------------


LINK_TITLE = "「烽火操」與譜庫都已經有編制"
LINK_INTRO = f"要連到譜庫的 {link_text()}。兩邊的編制不一樣，整張表用其中一份；預設用譜庫的。選「用群組的」會在下次存檔時覆寫譜庫。"


def pairing_rows(voices):
    """依位置配對：群組現有的分譜第 i 份配給第 i 個聲部；檔名跟聲部對不上的標紅"""
    rows = []
    for i, name in enumerate(voices):
        f = GROUP_FILES[i] if i < len(GROUP_FILES) else None
        if f is None:
            rows.append([cell(f"{i + 1:02d}", fg=GRAY, center=True), cell(name), cell("（沒有分譜）", fg=AMBER_FG)])
        elif f != file_for(name):
            rows.append([cell(f"{i + 1:02d}", fg=RED_FG, center=True, bg=RED_BG), cell(name, bg=RED_BG),
                         cell(f"{f}　檔名看起來不是這個聲部", fg=RED_FG, bg=RED_BG)])
        else:
            rows.append([cell(f"{i + 1:02d}", fg=GRAY, center=True), cell(name), cell(f)])
    return rows


def side_diff(name, rows_self, rows_other):
    """這一列跟另一邊比：只在這邊、人數不同，或相同"""
    other = {r[0]: r for r in rows_other}
    me = {r[0]: r for r in rows_self}[name]
    if name not in other:
        return "只在這邊"
    if (me[1], me[2]) != (other[name][1], other[name][2]):
        return f"人數不同（另一邊 {fmt_count(other[name][1], other[name][2])}）"
    return None


def side_rows(rows_self, rows_other):
    out = []
    for name, lo, hi, sec in rows_self:
        diff = side_diff(name, rows_self, rows_other)
        bg = None if diff is None else (GREEN_BG if diff == "只在這邊" else AMBER_BG)
        out.append([cell(name, bg=bg, tip=diff), cell(fmt_count(lo, hi), bg=bg, tip=diff), cell(sec, fg=GRAY, bg=bg)])
    return out


def link_a():
    split = QSplitter(Qt.Horizontal)
    radios = []
    for title, mine, other in (("用譜庫的（預設）", VOICES, GROUP_SIDE), ("用群組的", GROUP_SIDE, VOICES)):
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        r = QRadioButton(title)
        radios.append(r)
        bl.addWidget(r)
        bl.addWidget(table(["聲部", "人數", "聲部組"], side_rows(mine, other), stretch=0))
        split.addWidget(box)
    pair_box = QGroupBox(f"套用後的配對（群組已有 {len(GROUP_FILES)} 份分譜）")
    pl = QVBoxLayout(pair_box)
    holder = {"t": None}

    def refresh():
        voices = [v[0] for v in (VOICES if radios[0].isChecked() else GROUP_SIDE)]
        if holder["t"]:
            holder["t"].deleteLater()
        holder["t"] = table(["序號", "聲部", "分譜檔"], pairing_rows(voices), stretch=2)
        pl.addWidget(holder["t"])

    for r in radios:
        r.toggled.connect(lambda on: on and refresh())
    radios[0].setChecked(True)
    outer = QSplitter(Qt.Horizontal)
    outer.addWidget(split)
    outer.addWidget(pair_box)
    outer.setStretchFactor(0, 3)
    outer.setStretchFactor(1, 2)
    return dialog_page(LINK_TITLE, [
        LINK_INTRO, label("綠底：只在這一邊．黃底：人數不同", color=GRAY), outer,
    ], [btn("取消"), btn("確定", primary=True)], width=1180)


def link_b():
    group = {r[0]: r for r in GROUP_SIDE}
    cat = {r[0]: r for r in VOICES}
    order = [v[0] for v in VOICES]
    order.insert(order.index("打擊 2") + 1, "排鼓")
    pairs = {row[1].text(): row[2] for row in pairing_rows([v[0] for v in VOICES])}
    rows = []
    for name in order:
        g, c = group.get(name), cat.get(name)
        if g and c and (g[1], g[2]) == (c[1], c[2]):
            diff, bg = cell("相同", fg=GRAY), None
        elif g and c:
            diff, bg = cell("人數不同", fg=AMBER_FG), AMBER_BG
        elif g:
            diff, bg = cell("只在群組", fg=GREEN_FG), GREEN_BG
        else:
            diff, bg = cell("只在譜庫", fg=BLUE_FG), BLUE_BG
        pair = pairs.get(name)
        if pair is None:
            pair = cell("（用譜庫的就沒有這個聲部）", fg=GRAY)
        else:
            pair = pair.clone()
        rows.append([cell(name, bg=bg), cell(fmt_count(g[1], g[2]) if g else "—", bg=bg),
                     cell(fmt_count(c[1], c[2]) if c else "—", bg=bg), diff, pair])
    return dialog_page(LINK_TITLE, [
        LINK_INTRO,
        table(["聲部", "群組的人數", "譜庫的人數", "差異", "用譜庫的時，配到的分譜"], rows, stretch=4),
    ], [btn("取消"), btn("用群組的"), btn("用譜庫的（預設）", primary=True)], width=1000)


def link_c():
    body = QWidget()
    v = QVBoxLayout(body)
    v.setContentsMargins(0, 0, 0, 0)
    use_cat, use_grp = QPushButton("用譜庫的（預設）"), QPushButton("用群組的")
    seg = QButtonGroup(body)
    for b in (use_cat, use_grp):
        b.setCheckable(True)
        b.setMinimumWidth(200)
        b.setStyleSheet("QPushButton:checked { background: #4338CA; color: #eef2ff; font-weight: bold; }")
        seg.addButton(b)
    v.addLayout(hbox(None, use_cat, use_grp, None))
    summary = label("", color=GRAY, wrap=True)
    v.addWidget(summary)
    holder = {"t": None}

    def refresh():
        mine, other = (VOICES, GROUP_SIDE) if use_cat.isChecked() else (GROUP_SIDE, VOICES)
        names = [r[0] for r in mine]
        pairing = pairing_rows(names)
        rows = []
        only, differ = 0, 0
        for i, (name, lo, hi, sec) in enumerate(mine):
            diff = side_diff(name, mine, other)
            only += diff == "只在這邊"
            differ += bool(diff) and diff != "只在這邊"
            mark = cell(diff or "", fg=GREEN_FG if diff == "只在這邊" else AMBER_FG)
            rows.append([pairing[i][0], cell(name), cell(fmt_count(lo, hi)), cell(sec, fg=GRAY), mark, pairing[i][2]])
        missing = [r for r in other if r[0] not in names]
        wrong = sum(1 for r in pairing if "看起來不是" in r[2].text())
        summary.setText(f"跟另一份相比：多 {only} 個聲部、少 {len(missing)} 個（{'、'.join(r[0] for r in missing)}）、人數不同 {differ} 個．"
                        f"依位置配對後，{wrong} 份分譜的檔名跟聲部對不上。")
        if holder["t"]:
            holder["t"].deleteLater()
        holder["t"] = table(["序號", "聲部", "人數", "聲部組", "跟另一份比", "配到的分譜"], rows, stretch=5)
        v.addWidget(holder["t"], 1)

    use_cat.toggled.connect(lambda _on: refresh())
    use_cat.setChecked(True)
    return dialog_page(LINK_TITLE, [LINK_INTRO, body], [btn("取消"), btn("確定", primary=True)], width=1000)


# --- 畫面清單與切換列 -----------------------------------------------------------

SCREENS = [
    ("main", "主視窗的編制表", "群組「烽火操」連到譜庫；豎琴標成本專案沒有；二胡 II 的人數與時長存檔時斷網、未同步", [
        ("A", "左欄編制表：取代樂器表，分譜清單不變", main_a),
        ("B", "配對一張表：聲部、人數與分譜同一列", main_b),
        ("C", "群組裡的「編制」分頁：樂器表留在左欄", main_c),
    ]),
    ("catalog", "譜庫視窗的版本頁", "人民音樂出版社 × 烽火操：三個聲部缺檔，資料夾裡有四個沒登記的檔（揚琴已登記，指定給它會提示覆蓋）", [
        ("A", "兩欄：編制表＋沒登記的檔，按鈕指定", catalog_a),
        ("B", "一張表：每個聲部的「登記的檔」是下拉選單", catalog_b),
        ("C", "從檔案出發：每個檔選要登記給哪個聲部", catalog_c),
    ]),
    ("sections", "聲部組對照", "全社團共用；排鼓、雲鑼、Harp、Celesta 在譜庫裡出現但還沒歸類", [
        ("A", "一張平表：樂團類型｜聲部名稱｜聲部組", sections_a),
        ("B", "分欄拖拉：每個聲部組一欄", sections_b),
    ]),
    ("reopen", "開啟專案：聲部清單變了", "譜庫新增排鼓、移除中音嗩吶、古箏往前移；二胡 I 的人數也改了（直接套用）", [
        ("A", "兩欄對照：專案裡的｜譜庫現在", reopen_a),
        ("B", "變動清單＋套用後的配對", reopen_b),
        ("C", "不跳對話框：群組裡的提示列，表格內標色", reopen_c),
    ]),
    ("conflict", "存檔：譜庫已被別人改過", "七項改動裡三項衝突：二胡 II 的人數、時長、你刪掉的打擊 2 被別人改了人數", [
        ("A", "衝突表：每列選用我的或用譜庫的", conflict_a),
        ("B", "一次一項：兩個大按鈕", conflict_b),
        ("C", "全部改動總覽，衝突的標紅要選", conflict_c),
    ]),
    ("link", "連線：兩邊都有編制", "群組沒有豎琴、多一個排鼓，二胡 I 與大提琴人數不同；群組已有 23 份分譜", [
        ("A", "並排兩張表＋配對預覽", link_a),
        ("B", "一張合併差異表", link_b),
        ("C", "切換檢視：選一邊看結果與配對", link_c),
    ]),
]


class SwitcherBar(QFrame):
    """浮在視窗底部的黃色切換列，明顯不屬於被評估的設計"""

    def __init__(self, host):
        super().__init__(host)
        self.setObjectName("protoBar")
        self.setStyleSheet(
            "QFrame#protoBar { background: #f5c518; border-radius: 16px; }"
            "QFrame#protoBar QLabel { color: #111; }"
            "QFrame#protoBar QPushButton { background: #111; color: #f5c518; border: none; border-radius: 12px;"
            " padding: 4px 14px; font-weight: bold; }"
            "QFrame#protoBar QComboBox { background: #fff7cc; color: #111; }"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 6, 14, 6)
        v.setSpacing(2)
        row = QHBoxLayout()
        row.addWidget(label("PROTOTYPE", bold=True, color="#111"))
        self.screen_combo = QComboBox()
        self.screen_combo.addItems([s[1] for s in SCREENS])
        self.screen_combo.currentIndexChanged.connect(lambda i: host.show_variant(i, 0))
        row.addWidget(self.screen_combo)
        row.addWidget(btn("←", slot=lambda: host.step(-1)))
        self.variant_label = label("", bold=True, color="#111")
        row.addWidget(self.variant_label, 1)
        row.addWidget(btn("→", slot=lambda: host.step(1)))
        v.addLayout(row)
        self.scenario = label("", color="#333", size=12)
        v.addWidget(self.scenario)


class Host(QMainWindow):
    def __init__(self):
        super().__init__()
        holder = QWidget()
        self._lay = QVBoxLayout(holder)
        self._lay.setContentsMargins(0, 0, 0, 78)
        self.setCentralWidget(holder)
        self._page = None
        self._at = (0, 0)
        self.bar = SwitcherBar(self)

    def show_variant(self, si, vi):
        key, name, scenario, variants = SCREENS[si]
        vkey, vname, build = variants[vi]
        if self._page is not None:
            self._lay.removeWidget(self._page)
            self._page.hide()
            self._page.setParent(None)
            self._page.deleteLater()
        self._page = build()
        self._lay.addWidget(self._page)
        self._at = (si, vi)
        self.bar.screen_combo.blockSignals(True)
        self.bar.screen_combo.setCurrentIndex(si)
        self.bar.screen_combo.blockSignals(False)
        self.bar.variant_label.setText(f"{vkey}（{vname}）　{vi + 1}/{len(variants)}")
        self.bar.scenario.setText(f"情境：{scenario}　　--screen={key} --variant={vkey}")
        self.setWindowTitle(f"PROTOTYPE 編制表與差異畫面 — {name} {vkey}")
        self._place_bar()

    def step(self, delta):
        si, vi = self._at
        n = len(SCREENS[si][3])
        self.show_variant(si, (vi + delta) % n)

    def _place_bar(self):
        w = min(self.width() - 32, 1000)
        self.bar.setFixedWidth(w)
        self.bar.adjustSize()
        self.bar.move((self.width() - w) // 2, self.height() - self.bar.height() - 10)
        self.bar.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_bar()


class ArrowKeys(QObject):
    """左右鍵換變體；焦點在輸入框、表格、下拉選單時不攔"""

    def __init__(self, host):
        super().__init__()
        self._host = host

    def eventFilter(self, obj, event):
        if event.type() != QEvent.KeyPress or event.key() not in (Qt.Key_Left, Qt.Key_Right) or event.modifiers():
            return False
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QAbstractSpinBox, QTextEdit, QAbstractItemView, QComboBox)):
            return False
        self._host.step(-1 if event.key() == Qt.Key_Left else 1)
        return True


def main():
    parser = argparse.ArgumentParser(description="編制表與差異畫面的原型")
    parser.add_argument("--screen", default=SCREENS[0][0], choices=[s[0] for s in SCREENS])
    parser.add_argument("--variant", default="A")
    parser.add_argument("--shots", help="每個變體截一張圖存到這個資料夾後結束")
    parser.add_argument("--font", help="另外載入的字型檔（例如沒有中文字型的環境）")
    args = parser.parse_args()
    set_locale(LOCALE_ZH_TW)
    app = QApplication(sys.argv[:1])
    if args.font:
        QFontDatabase.addApplicationFont(args.font)
        families = QFontDatabase.applicationFontFamilies(0)
        if families:
            f = app.font()
            f.setFamily(families[0])
            app.setFont(f)
    font = app.font()
    font.setPixelSize(14)
    app.setFont(font)
    from main import _apply_dark_theme
    # 不用 main._AppStyle：PySide6 6.12 在 Python 3.11 以前，Python 覆寫的 pixelMetric 被 C++ 呼叫時
    # 會少算 None 的參考計數，畫面切換幾次就以 none_dealloc 崩潰；它只調子選單重疊，原型用不到
    app.setStyle(QStyleFactory.create("Fusion"))
    _apply_dark_theme(app)
    host = Host()
    host.resize(1280, 860)
    si = next(i for i, s in enumerate(SCREENS) if s[0] == args.screen)
    vi = max(0, next((i for i, v in enumerate(SCREENS[si][3]) if v[0] == args.variant.upper()), 0))
    host.show_variant(si, vi)
    host.show()
    if args.shots:
        host.resize(1280, 1000)
        os.makedirs(args.shots, exist_ok=True)
        for si, (key, _n, _s, variants) in enumerate(SCREENS):
            for vi, (vkey, *_rest) in enumerate(variants):
                host.show_variant(si, vi)
                for _ in range(3):
                    app.processEvents()
                host.grab().save(os.path.join(args.shots, f"{si + 1}-{key}-{vkey}.png"))
        return
    keys = ArrowKeys(host)
    app.installEventFilter(keys)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

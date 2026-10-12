# -*- coding: utf-8 -*-
"""
PROTOTYPE（拋棄式，只留在 prototype/publish-screens 分支，不進 develop）

回答 wayfinder ticket「發譜、撤回與收回畫面的原型」（#63）：發譜預覽、壓縮比較、發譜進行中與結果、
撤回、收回、收回提醒要長什麼樣子。行為已在「發譜的收網」（#54）與「平台的收網」（#57）定案。
六個畫面，各有三個結構不同的變體；資料全是假的，按鈕不連 HuaXia、不寫入任何地方。

    python src/ui/prototype_publish.py
    python src/ui/prototype_publish.py --screen=compare --variant=B
    python src/ui/prototype_publish.py --shots=<資料夾>    # 每個變體截一張圖後結束

底部黃色切換列：左邊選畫面，箭頭（或鍵盤左右鍵，焦點不在表格或輸入框時）換變體。
畫面裡的黃色按鈕是原型用的模擬控制（例如「壓縮完成」「下一階段」），不屬於被評估的設計。
"""
import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDialog, QFrame,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenuBar, QProgressBar, QPushButton, QRadioButton, QScrollArea, QSplitter,
    QStackedWidget, QStyleFactory, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

# --- 顏色（配合主程式的深色主題）--------------------------------------------

AMBER_BG, AMBER_FG = "#5b4613", "#f2c14e"     # 未同步、要確認
GRAY = "#7a7f93"                               # 不動、說明
GREEN_BG, GREEN_FG = "#1d4a2f", "#7ddc9a"      # 新增、完成
RED_BG, RED_FG = "#5a2129", "#ff8a8a"          # 刪除、失敗、警告
BLUE_BG, BLUE_FG = "#253c63", "#8fb8ff"        # 取代、覆寫
PROTO = "background: #f5c518; color: #111; font-weight: bold; border: none; border-radius: 4px; padding: 4px 10px;"

# --- 假資料 ---------------------------------------------------------------

HX_PIECES = ["長城隨想－烽火操（劉文金）", "茉莉花（江蘇民歌）", "春節序曲（李煥之）",
             "豫北敘事曲（劉文金）", "秦‧兵馬俑（彭修文）"]

FENGHUO_VOICES = ["梆笛", "曲笛", "高音笙", "中音笙", "高音嗩吶", "中音嗩吶", "定音鼓", "打擊 1", "打擊 2", "排鼓",
                  "揚琴", "柳琴", "琵琶", "中阮", "大阮", "古箏", "二胡獨奏", "高胡", "二胡 I", "二胡 II",
                  "中胡", "大提琴", "低音提琴"]
CHUNJIANG_VOICES = ["簫", "曲笛", "笙", "琵琶", "揚琴", "古箏", "中阮", "大阮", "高胡", "二胡 I", "二胡 II",
                    "中胡", "大提琴", "低音提琴"]
MOLI_VOICES = ["梆笛", "曲笛", "笙", "揚琴", "柳琴", "琵琶", "中阮", "大阮", "高胡", "二胡 I", "二胡 II", "中胡",
               "大提琴", "低音提琴"]

# 聲部的變動：泠靈的聲部、HuaXia 現在、變動、發譜後、決定（None＝不用決定；"check:文字:預設"；"combo:選項|選項"）、標記
FENGHUO_DIFF = [
    ("二胡 I", "二胡1", "名稱相近", "對到「二胡1」", "combo:對到「二胡1」|另建「二胡 I」", "confirm"),
    ("排鼓", "（沒有）", "HuaXia 沒有", "建立：1 人、打擊", None, "new"),
    ("二胡 II", "6–8 人", "人數不同", "5–8 人", "check:覆寫:1", "unsynced"),
    ("中胡", "4–6 人", "人數不同", "4–8 人", "check:覆寫:1", ""),
    ("大提琴", "拉弦", "聲部組不同", "低音", "check:覆寫:1", ""),
    ("豎琴", "（沒有）", "本專案沒有這份分譜", "不建", "check:在 HuaXia 建:0", "absent"),
    ("雲鑼", "1 人", "只在 HuaXia", "不動", None, "gray"),
]


def _sizes(voices, seed, big=()):
    rnd = random.Random(seed)
    return {v: (dict(big)[v] if v in dict(big) else round(rnd.uniform(0.4, 2.8), 1)) for v in voices}


# 其他人放的檔：聲部 → 說明
FENGHUO_OTHERS = {"二胡1": "二胡1_弓法.pdf（林詠晴，聲部長）", "琵琶": "琵琶_指法.pdf（陳昱安，網頁上傳）"}

GROUPS = [
    dict(name="烽火操", new=False, piece=0, voices=FENGHUO_VOICES, diff=FENGHUO_DIFF, same=16,
         sizes=_sizes(FENGHUO_VOICES + ["總譜"], 1, (("大提琴", 21.3), ("總譜", 34.2))),
         replaced=set(FENGHUO_VOICES) - {"排鼓"} | {"總譜"}, others=FENGHUO_OTHERS),
    dict(name="春江花月夜", new=True, title="春江花月夜", composer="秦鵬章 編", kind="絲竹", voices=CHUNJIANG_VOICES,
         diff=[(v, "（沒有）", "新曲目", "建立", None, "new") for v in CHUNJIANG_VOICES], same=0,
         sizes=_sizes(CHUNJIANG_VOICES + ["總譜"], 2, (("低音提琴", 20.6),)), replaced=set(), others={}),
    dict(name="茉莉花", new=False, piece=1, voices=MOLI_VOICES, diff=[], same=14,
         sizes=_sizes(MOLI_VOICES + ["總譜"], 3), replaced=set(MOLI_VOICES) | {"總譜"}, others={}),
]
# 超過 20MB 的檔：(群組, 聲部) → (壓縮後大小, 狀態)
COMPRESS = {("烽火操", "大提琴"): (8.7, "done"), ("烽火操", "總譜"): (14.8, "running"),
            ("春江花月夜", "低音提琴"): (7.9, "done")}

EVENTS = [
    ("期末公演", "2027-01-10（日）", "中正堂", True,
     "應出席者會變成排進這 3 首的人：目前 41 人；春江花月夜還沒排人，排人前不算"),
    ("校慶音樂會", "2026-11-21（六）", "活動中心", False,
     "已掛 2 首（豫北敘事曲、春節序曲）；勾選後再加這 3 首，應出席者增加 6 人"),
]


def file_name(group, voice):
    return f"{group}_{voice.replace(' ', '')}.pdf"


# --- 小工具 -------------------------------------------------------------------


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


def line(text="", width=None, placeholder=None):
    w = QLineEdit(text)
    if width:
        w.setFixedWidth(width)
    if placeholder:
        w.setPlaceholderText(placeholder)
    return w


def btn(text, danger=False, primary=False, slot=None, proto=False, small=False):
    w = QPushButton(text)
    if danger:
        w.setStyleSheet("background-color: #c0392b; color: white;")
    if primary:
        w.setStyleSheet("QPushButton { background-color: #4338CA; color: #eef2ff; font-weight: bold; }"
                        "QPushButton:disabled { background-color: #2e3040; color: #6e7282; }")
    if proto:
        w.setStyleSheet(PROTO)
    if small:
        w.setStyleSheet(w.styleSheet() + "padding: 2px 8px; min-height: 16px;")
    if slot:
        w.clicked.connect(slot)
    return w


def combo(items, index=0):
    w = QComboBox()
    w.addItems(items)
    w.setCurrentIndex(index)
    return w


def check(text, checked=False):
    w = QCheckBox(text)
    w.setChecked(checked)
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
            grow = isinstance(w, (QLineEdit, QProgressBar)) and w.maximumWidth() > 10000
            lay.addWidget(w, 1 if grow else 0)
    return lay


def vbox_widget(*items, margins=(0, 0, 0, 0), spacing=6):
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(*margins)
    v.setSpacing(spacing)
    for it in items:
        if it is None:
            v.addStretch()
        elif isinstance(it, QHBoxLayout):
            v.addLayout(it)
        elif isinstance(it, str):
            v.addWidget(label(it, wrap=True))
        else:
            v.addWidget(it, 1 if isinstance(it, (QTableWidget, QTreeWidget, QScrollArea, QSplitter, QStackedWidget,
                                                 QTextEdit, QTabWidget)) else 0)
    return w


def cell(text, fg=None, bg=None, italic=False, strike=False, bold=False, tip=None, center=False):
    it = QTableWidgetItem(str(text))
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
    has_widgets = any(isinstance(v, (QWidget, QHBoxLayout)) for row in rows for v in row)
    tbl.verticalHeader().setDefaultSectionSize(38 if has_widgets else 28)
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            if v is None:
                continue
            if isinstance(v, QHBoxLayout):
                holder = QWidget()
                v.setContentsMargins(4, 0, 4, 0)
                holder.setLayout(v)
                tbl.setCellWidget(r, c, holder)
            elif isinstance(v, QWidget):
                tbl.setCellWidget(r, c, v)
            else:
                tbl.setItem(r, c, v if isinstance(v, QTableWidgetItem) else cell(v))
    hdr = tbl.horizontalHeader()
    for c in range(len(headers)):
        hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
    if stretch is not None:
        hdr.setSectionResizeMode(stretch, QHeaderView.Stretch)
    return tbl


def tree(headers):
    tw = QTreeWidget()
    tw.setHeaderLabels(headers)
    tw.setUniformRowHeights(False)
    tw.header().setSectionResizeMode(QHeaderView.ResizeToContents)
    tw.header().setStretchLastSection(True)
    return tw


def titem(parent, texts, fg=None, bold=False, bg=None):
    it = QTreeWidgetItem(parent, [str(x) for x in texts])
    for c in range(len(texts)):
        if fg:
            it.setForeground(c, QColor(fg))
        if bg:
            it.setBackground(c, QColor(bg))
        if bold:
            f = it.font(c)
            f.setBold(True)
            it.setFont(c, f)
    return it


def put(tw, it, col, w):
    """把元件放進樹狀表的一格，列高跟著元件"""
    it.setSizeHint(col, QSize(-1, max(34, w.sizeHint().height() + 4)))
    tw.setItemWidget(it, col, w)


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


def scroll(widget):
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setWidget(widget)
    return sa


def dialog_page(title, body_items, buttons, width=1300, grow=True):
    """模擬對話框：深色背景上一個置中的框"""
    page = QFrame()
    page.setObjectName("backdrop")
    page.setStyleSheet("QFrame#backdrop { background: #0d0e13; }")
    outer = QVBoxLayout(page)
    frame = QFrame()
    frame.setObjectName("dlg")
    frame.setStyleSheet("QFrame#dlg { background: #16171e; border: 1px solid #4a4c63; border-radius: 8px; }")
    frame.setMaximumWidth(width)
    v = QVBoxLayout(frame)
    v.setContentsMargins(16, 12, 16, 12)
    v.addWidget(label(title, bold=True, size=16))
    for w in body_items:
        if w is None:
            v.addStretch()
        elif isinstance(w, QHBoxLayout):
            v.addLayout(w)
        elif isinstance(w, str):
            v.addWidget(label(w, wrap=True))
        else:
            v.addWidget(w, 1 if isinstance(w, (QTableWidget, QTreeWidget, QSplitter, QTabWidget, QStackedWidget,
                                               QScrollArea, QTextEdit)) else 0)
    if buttons:
        v.addLayout(hbox(*buttons) if None in buttons else hbox(None, *buttons))
    outer.addWidget(frame, 1 if grow else 0)
    if not grow:
        outer.addStretch()
    return page


def popup(parent, title, widget, size=(1200, 820)):
    """原型裡從某個按鈕打開另一個畫面"""
    dlg = QDialog(parent.window())
    dlg.setWindowTitle(title)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(widget)
    dlg.resize(*size)
    dlg.exec()


def main_shell(content_top=None, status_right=None, body=None, bottom=None):
    """主視窗外框（只放到看得出是主視窗）"""
    page = QWidget()
    v = QVBoxLayout(page)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)
    mb = QMenuBar()
    for name in ("檔案", "編輯", "匯入", "譜庫", "發譜", "工具", "檢視"):
        mb.addMenu(name)
    v.addWidget(mb)
    inner = QWidget()
    bv = QVBoxLayout(inner)
    bv.setContentsMargins(6, 6, 6, 6)
    if content_top is not None:
        bv.addWidget(content_top)
    splitter = QSplitter(Qt.Horizontal)
    left = QListWidget()
    for name in ("梆笛", "曲笛", "高音笙", "中音笙", "高音嗩吶", "…"):
        left.addItem(name)
    left.setMaximumWidth(260)
    splitter.addWidget(left)
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "未分組")
    for g in GROUPS:
        tabs.addTab(body if (body is not None and g["name"] == "烽火操") else
                    label(f"（群組「{g['name']}」的分譜清單）", color=GRAY), g["name"])
    tabs.setCurrentIndex(1)
    splitter.addWidget(tabs)
    splitter.setStretchFactor(1, 1)
    bv.addWidget(splitter, 1)
    if bottom is not None:
        bv.addWidget(bottom)
    status = hbox(label("就緒", color=GRAY), None)
    if status_right is not None:
        status.addWidget(status_right)
    preview = btn("預覽重新命名")
    status.addWidget(preview)
    bv.addLayout(status)
    v.addWidget(inner, 1)
    return page


# --- 共用：發譜預覽的零件 ---------------------------------------------------------


def precheck_line():
    return label("專案已存檔・已登入 HuaXia（suikann，幹部）・HuaXia 發譜函式相符", color=GRAY)


def piece_editor(g, compact=False):
    """群組對到哪首 HuaXia 曲目：挑選現有或新建"""
    pick = QRadioButton("挑選現有")
    new = QRadioButton("新建")
    pieces = combo(HX_PIECES, g.get("piece", 0))
    title = line(g.get("title", g["name"]), width=150)
    composer = line(g.get("composer", ""), width=130)
    kind = combo(["合奏", "絲竹"], 1 if g.get("kind") == "絲竹" else 0)
    new_fields = QWidget()
    nf = hbox("曲名", title, "作曲家", composer, "類型", kind, margins=(0, 0, 0, 0))
    new_fields.setLayout(nf)

    def sync():
        pieces.setVisible(pick.isChecked())
        new_fields.setVisible(new.isChecked())
    pick.toggled.connect(sync)
    (new if g["new"] else pick).setChecked(True)
    sync()
    w = QWidget()
    w.setLayout(hbox(pick, new, pieces, new_fields, None, margins=(0, 0, 0, 0)))
    return w


def decision_widget(spec):
    if spec is None:
        return None
    kind, rest = spec.split(":", 1)
    if kind == "combo":
        return combo(rest.split("|"))
    text, default = rest.rsplit(":", 1)
    return centered(check(text, default == "1"))


def centered(widget):
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(4, 0, 4, 0)
    lay.addWidget(widget)
    lay.addStretch()
    return holder


def diff_cells(row):
    """（聲部、HuaXia 現在、變動、發譜後）四格，依標記上色"""
    voice, hx, change, after, _spec, mark = row
    fg = {"new": GREEN_FG, "confirm": AMBER_FG, "gray": GRAY, "absent": GRAY}.get(mark)
    after_cell = cell(after, fg=fg)
    if mark == "unsynced":
        after_cell = cell(f"{after}（未同步）", fg=AMBER_FG, bg=AMBER_BG, tip="這個值存檔時沒寫進譜庫，譜庫裡還是舊的")
    return [cell(voice, fg=GRAY if mark == "gray" else None), cell(hx, fg=GRAY), cell(change, fg=fg), after_cell]


def voice_diff_table(g):
    rows = [diff_cells(r) + [decision_widget(r[4])] for r in g["diff"]]
    tbl = table(["聲部", "HuaXia 現在", "變動", "發譜後", "決定"], rows, stretch=3)
    return tbl


def compress_cell(g, voice, parent_getter=None):
    """超過 20MB 的檔：壓縮狀態加開啟、比較、不發這份"""
    key = (g["name"], voice)
    if key not in COMPRESS:
        return None
    after, state = COMPRESS[key]
    if state == "running":
        bar = QProgressBar()
        bar.setValue(62)
        bar.setFormat("壓縮中 %p%")
        bar.setFixedWidth(160)
        return hbox(bar, None)
    b_cmp = btn("比較", small=True)
    b_cmp.clicked.connect(lambda: popup(b_cmp, "比較壓縮前後", compare_a()))
    return hbox(label(f"→ {after} MB", color=GREEN_FG), btn("開啟", small=True), b_cmp,
                btn("不發這份", small=True), None)


def file_rows(g):
    rows = []
    for v in g["voices"] + ["總譜"]:
        size = g["sizes"][v]
        hx_voice = "二胡1" if v == "二胡 I" else v
        replaced = cell("取代 10/05 泠靈發的", fg=BLUE_FG) if v in g["replaced"] \
            else cell("（新）", fg=GREEN_FG)
        other = g["others"].get(hx_voice)
        rows.append((v, file_name(g["name"], v), size, replaced, other, compress_cell(g, v)))
    return rows


def file_table(g):
    rows = []
    for v, name, size, replaced, other, comp in file_rows(g):
        big = size > 20
        rows.append([cell(v), cell(name), cell(f"{size} MB", fg=RED_FG if big else None, bold=big), replaced,
                     cell(f"不動：{other}", fg=AMBER_FG) if other else cell(""), comp])
    tbl = table(["聲部", "這次發的檔", "大小", "取代（泠靈發的那份）", "其他人放的檔（不動）", "壓縮（超過 20MB）"], rows)
    tbl.horizontalHeader().setSectionResizeMode(5, QHeaderView.Fixed)
    tbl.setColumnWidth(5, 330)
    return tbl


def group_summary(g):
    changes = sum(1 for r in g["diff"] if r[4] is not None)
    files = len(g["voices"]) + 1
    if g["new"]:
        return f"新建曲目・{len(g['diff'])} 個聲部・{files} 份檔"
    return f"{changes} 項要決定・{files} 份檔" if changes else f"聲部一致・{files} 份檔"


def piece_text(g):
    return f"新建「{g['title']}」" if g["new"] else HX_PIECES[g["piece"]]


def events_widget():
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.addWidget(label("掛到哪幾場公演（本學期、還沒結束的公演，可複選）", bold=True))
    for name, date, place, checked, note in EVENTS:
        v.addWidget(check(f"{name}　{date}　{place}", checked))
        v.addWidget(label(note, color=GRAY, wrap=True))
    v.addWidget(label("不掛任何公演也可以發，但公演結束後不會提醒收回。", color=GRAY, wrap=True))
    return w


class CompressStatus(QWidget):
    """背景壓縮的狀態與發譜鈕：壓縮完成前不能按發譜"""

    def __init__(self, publish_text="發譜"):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.text = label("背景壓縮：烽火操_總譜.pdf（3 份裡第 3 份）", color=AMBER_FG)
        self.bar = QProgressBar()
        self.bar.setValue(62)
        self.bar.setFixedWidth(180)
        self.cancel = btn("取消壓縮")
        self.done = btn("（原型）壓縮完成", proto=True, slot=self.finish)
        self.publish = btn(publish_text, primary=True)
        self.publish.setEnabled(False)
        self.publish.setToolTip("壓縮完成才能發譜")
        for w in (self.text, self.bar, self.cancel, self.done):
            lay.addWidget(w)
        lay.addStretch()
        lay.addWidget(btn("取消"))
        lay.addWidget(self.publish)

    def finish(self):
        self.text.setText("壓縮完成：3 份，共省下 45.0 MB")
        self.text.setStyleSheet(f"color: {GREEN_FG};")
        for w in (self.bar, self.cancel, self.done):
            w.hide()
        self.publish.setEnabled(True)


# --- 畫面一：發譜預覽 -------------------------------------------------------------


def preview_a():
    """主從：左邊群組清單，右邊選到的群組分成曲目、聲部、檔案三段"""
    nav = QListWidget()
    nav.setMinimumWidth(250)
    nav.setMaximumWidth(300)
    pages = QStackedWidget()
    for g in GROUPS:
        it = QListWidgetItem(f"{g['name']} → {piece_text(g)}\n{group_summary(g)}")
        if any(r[5] in ("confirm", "absent") for r in g["diff"]):
            it.setForeground(QColor(AMBER_FG))
        nav.addItem(it)
        piece_box = QGroupBox("HuaXia 曲目")
        piece_box.setLayout(QVBoxLayout())
        piece_box.layout().addWidget(piece_editor(g))
        voice_box = QGroupBox(f"聲部（另有 {g['same']} 個聲部一致）" if g["same"] else "聲部")
        voice_box.setLayout(QVBoxLayout())
        if g["diff"]:
            voice_box.layout().addWidget(voice_diff_table(g))
        else:
            voice_box.layout().addWidget(label("與 HuaXia 一致，不改聲部。", color=GRAY))
        file_box = QGroupBox("分譜與總譜")
        file_box.setLayout(QVBoxLayout())
        file_box.layout().addWidget(file_table(g))
        page = vbox_widget(piece_box, voice_box, file_box)
        if g["diff"]:
            page.layout().setStretch(1, 2)
        page.layout().setStretch(2, 3)
        pages.addWidget(page)
    nav.addItem(QListWidgetItem("公演場次（全部群組）\n已選 1 場"))
    pages.addWidget(vbox_widget(events_widget(), None))
    nav.currentRowChanged.connect(pages.setCurrentIndex)
    nav.setCurrentRow(0)
    split = QSplitter(Qt.Horizontal)
    split.addWidget(nav)
    split.addWidget(pages)
    split.setStretchFactor(1, 1)
    return dialog_page("發譜預覽：3 個群組", [precheck_line(), split, CompressStatus()], [])


def preview_b():
    """精靈：曲目 → 聲部 → 檔案 → 公演 → 確認，每一步列出全部群組"""
    steps = ["1 曲目", "2 聲部", "3 檔案", "4 公演", "5 確認"]
    head = QHBoxLayout()
    step_labels = []
    for i, s in enumerate(steps):
        lb = label(s)
        step_labels.append(lb)
        head.addWidget(lb)
        if i < len(steps) - 1:
            head.addWidget(label("›", color=GRAY))
    head.addStretch()
    stack = QStackedWidget()

    # 1 曲目
    rows = [[cell(g["name"], bold=True), piece_editor(g)] for g in GROUPS]
    t1 = table(["群組", "對到 HuaXia 的曲目"], rows, stretch=1)
    stack.addWidget(vbox_widget(t1, label("新建的曲目在發譜一開始先記下編號並自動存檔，重發不會重複建。", color=GRAY)))

    # 2 聲部
    tw = tree(["聲部", "HuaXia 現在", "變動", "發譜後", "決定"])
    for g in GROUPS:
        root = titem(tw, [f"{g['name']}（{group_summary(g)}）", "", "", "", ""], bold=True)
        if not g["diff"]:
            titem(root, ["聲部一致，不改", "", "", "", ""], fg=GRAY)
        for r in g["diff"]:
            it = QTreeWidgetItem(root, [c.text() for c in diff_cells(r)] + [""])
            for c, src in enumerate(diff_cells(r)):
                it.setForeground(c, src.foreground())
                if src.background().style() != Qt.NoBrush:
                    it.setBackground(c, src.background())
            w = decision_widget(r[4])
            if w is not None:
                put(tw, it, 4, w)
        if g["same"] and g["diff"]:
            titem(root, [f"其他 {g['same']} 個聲部一致", "", "", "", ""], fg=GRAY)
    tw.expandAll()
    stack.addWidget(vbox_widget(tw))

    # 3 檔案
    tw3 = tree(["聲部", "這次發的檔", "大小", "取代", "其他人放的檔", "壓縮"])
    for g in GROUPS:
        rows = file_rows(g)
        n_rep = sum(1 for r in rows if r[0] in g["replaced"])
        root = titem(tw3, [f"{g['name']}：{len(rows)} 份（取代 {n_rep}、新 {len(rows) - n_rep}）", "", "", "", "", ""],
                     bold=True)
        for v, name, size, replaced, other, comp in rows:
            it = QTreeWidgetItem(root, [v, name, f"{size} MB", replaced.text(), f"不動：{other}" if other else "", ""])
            it.setForeground(3, replaced.foreground())
            if other:
                it.setForeground(4, QColor(AMBER_FG))
            if size > 20:
                it.setForeground(2, QColor(RED_FG))
            if comp is not None:
                holder = QWidget()
                holder.setLayout(comp)
                put(tw3, it, 5, holder)
        root.setExpanded(g["name"] == "烽火操")
    stack.addWidget(vbox_widget(tw3))

    # 4 公演
    stack.addWidget(vbox_widget(events_widget(), None))

    # 5 確認
    summary = QTextEdit()
    summary.setReadOnly(True)
    summary.setHtml(
        "<h3>這次發譜會做的事</h3>"
        "<p><b>烽火操 → 長城隨想－烽火操</b><br>建立聲部：排鼓<br>覆寫：二胡 II 人數 6–8 → 5–8（未同步）、"
        "中胡人數 4–6 → 4–8、大提琴聲部組 拉弦 → 低音<br>二胡 I 對到 HuaXia 的「二胡1」<br>"
        "傳 24 份、取代 23 份泠靈發的；二胡1、琵琶各有其他人放的檔，不動</p>"
        "<p><b>春江花月夜 → 新建「春江花月夜」（秦鵬章 編，絲竹）</b><br>建立 14 個聲部；傳 15 份</p>"
        "<p><b>茉莉花 → 茉莉花（江蘇民歌）</b><br>聲部不改；傳 15 份、取代 15 份</p>"
        "<p><b>公演</b>：期末公演 2027-01-10</p>"
        f"<p style='color:{GRAY}'>發完記進譜庫的發譜紀錄；連錯曲目時可以從發譜紀錄撤回。</p>")
    stack.addWidget(vbox_widget(summary))

    status = CompressStatus()
    back = btn("上一步")
    nxt = btn("下一步")

    def go(i):
        stack.setCurrentIndex(i)
        for j, lb in enumerate(step_labels):
            lb.setStyleSheet("font-weight: bold; color: #eef2ff; background: #4338CA; padding: 3px 8px; border-radius: 4px;"
                             if j == i else f"color: {GRAY};")
        back.setEnabled(i > 0)
        nxt.setVisible(i < len(steps) - 1)
        status.publish.setVisible(i == len(steps) - 1)
    back.clicked.connect(lambda: go(stack.currentIndex() - 1))
    nxt.clicked.connect(lambda: go(stack.currentIndex() + 1))
    status.layout().insertWidget(status.layout().count() - 1, back)
    status.layout().insertWidget(status.layout().count() - 1, nxt)
    go(1)
    return dialog_page("發譜：3 個群組", [precheck_line(), head, stack, status], [])


def preview_c():
    """一張總表：所有會寫進 HuaXia 的變動，依群組分層；可只看要確認的"""
    tw = tree(["項目", "HuaXia 現在", "發譜後", "決定"])
    need = []

    def add_decision(it, spec):
        w = decision_widget(spec)
        if w is not None:
            put(tw, it, 3, w)
            need.append(it)

    for g in GROUPS:
        root = titem(tw, [g["name"], "", piece_text(g), ""], bold=True)
        pc = titem(root, ["曲目", "", piece_text(g), ""])
        put(tw, pc, 3, piece_editor(g))
        if g["new"]:
            need.append(pc)
        voices = titem(root, [f"聲部：{len(g['diff'])} 項變動，{g['same']} 個一致" if g["diff"] else "聲部：一致", "", "", ""])
        for r in g["diff"]:
            voice, hx, change, after, spec, mark = r
            fg = {"new": GREEN_FG, "confirm": AMBER_FG, "gray": GRAY, "absent": GRAY}.get(mark)
            it = titem(voices, [f"{voice}　{change}", hx, after + ("（未同步）" if mark == "unsynced" else ""), ""], fg=fg)
            if mark == "unsynced":
                it.setBackground(2, QColor(AMBER_BG))
                it.setForeground(2, QColor(AMBER_FG))
            add_decision(it, spec)
        rows = file_rows(g)
        n_rep = sum(1 for r in rows if r[0] in g["replaced"])
        files = titem(root, [f"分譜與總譜：傳 {len(rows)} 份", f"泠靈發的 {n_rep} 份",
                             f"取代 {n_rep}、新 {len(rows) - n_rep}", ""])
        for v, name, size, replaced, other, comp in rows:
            fg = RED_FG if size > 20 else None
            it = titem(files, [f"{v}　{size} MB", "10/05 泠靈發的" if v in g["replaced"] else "（沒有）",
                               name, ""], fg=fg)
            if comp is not None:
                holder = QWidget()
                holder.setLayout(comp)
                put(tw, it, 3, holder)
                need.append(it)
            if other:
                titem(it, [f"其他人放的：{other}", "", "不動", ""], fg=AMBER_FG)
        for it in (root, pc, voices):
            it.setExpanded(True)
        files.setExpanded(False)
    ev_root = titem(tw, ["公演場次", "", "", ""], bold=True)
    for name, date, place, checked, note in EVENTS:
        it = titem(ev_root, [f"{name}　{date}", "", note, ""])
        put(tw, it, 3, centered(check("掛上", checked)))
    ev_root.setExpanded(True)
    tw.header().setSectionResizeMode(2, QHeaderView.Interactive)
    tw.setColumnWidth(2, 300)
    tw.header().setSectionResizeMode(0, QHeaderView.Interactive)
    tw.setColumnWidth(0, 300)

    only = check(f"只看要決定的（{len(need)} 項）")

    def filt(on):
        def walk(item):
            visible_child = False
            for i in range(item.childCount()):
                visible_child |= walk(item.child(i))
            show = (not on) or item in need or visible_child or item.parent() is None
            item.setHidden(not show)
            if on and visible_child:
                item.setExpanded(True)
            return show and item.parent() is not None
        for i in range(tw.topLevelItemCount()):
            walk(tw.topLevelItem(i))
    only.toggled.connect(filt)
    return dialog_page("發譜預覽：所有會寫進 HuaXia 的變動",
                       [precheck_line(), hbox(only, None, label("黃字＝要確認・藍字＝取代・綠字＝新增・灰字＝不動", color=GRAY)),
                        tw, CompressStatus()], [])


# --- 畫面二：壓縮比較 -------------------------------------------------------------

_PAGES = {}


def score_page(seed, page, title):
    """畫一頁假的掃描分譜（灰階、五線譜、指法小字與鉛筆記號）；原檔 2480 寬"""
    key = (seed, page)
    if key in _PAGES:
        return _PAGES[key]
    W, H = 2480, 3508
    img = QImage(W, H, QImage.Format_RGB32)
    img.fill(QColor(250, 250, 248))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    rnd = random.Random(seed * 100 + page)
    for _ in range(2500):     # 掃描雜點
        g = rnd.randint(180, 235)
        p.fillRect(rnd.randint(0, W), rnd.randint(0, H), 3, 3, QColor(g, g, g))
    f = QFont()
    f.setPixelSize(74)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor(20, 20, 20))
    if page == 1:
        p.drawText(QRect(0, 110, W, 110), Qt.AlignCenter, title)
    f.setPixelSize(40)
    f.setBold(False)
    p.setFont(f)
    p.drawText(QRect(0, H - 120, W, 60), Qt.AlignCenter, f"— {page} —")
    small = QFont()
    small.setPixelSize(30)
    dyn = QFont()
    dyn.setPixelSize(46)
    dyn.setItalic(True)
    dyn.setBold(True)
    top = 330 if page == 1 else 220
    for s in range(9):
        y0 = top + s * 345
        p.setPen(QPen(QColor(25, 25, 25), 4))
        for k in range(5):
            p.drawLine(170, y0 + k * 26, W - 170, y0 + k * 26)
        for bx in range(170, W - 160, 535):
            p.drawLine(bx, y0, bx, y0 + 104)
        p.drawLine(W - 170, y0, W - 170, y0 + 104)
        x = 290
        while x < W - 220:
            if (x - 170) % 535 < 70:
                x += 70
                continue
            step = rnd.randint(-3, 11)
            ny = y0 + 104 - step * 13
            p.setPen(QPen(QColor(20, 20, 20), 4))
            p.setBrush(QColor(20, 20, 20))
            p.drawEllipse(QPointF(x, ny), 17, 12)
            p.drawLine(QPointF(x + 16, ny), QPointF(x + 16, ny - 92))
            if step < 0 or step > 8:
                p.drawLine(QPointF(x - 28, ny), QPointF(x + 28, ny))
            if rnd.random() < 0.35:   # 指法數字（降解析度時最先糊掉）
                p.setFont(small)
                p.drawText(QPointF(x - 8, y0 - 22), str(rnd.randint(1, 4)))
            x += rnd.choice([75, 95, 115, 140])
        if rnd.random() < 0.5:
            p.setFont(dyn)
            p.drawText(QPointF(rnd.randint(200, 1800), y0 + 175), rnd.choice(["mf", "p", "f", "sfz", "mp"]))
        if rnd.random() < 0.3:        # 鉛筆記號
            p.setPen(QPen(QColor(120, 120, 120), 6))
            p.setBrush(Qt.NoBrush)
            cx = rnd.randint(400, 2000)
            p.drawEllipse(QPointF(cx, y0 + 50), 90, 70)
    p.end()
    img = img.convertToFormat(QImage.Format_Grayscale8)
    _PAGES[key] = img
    return img


def compressed(img, dpi):
    """原檔當作 600 dpi，降到指定 dpi"""
    key = (img.cacheKey(), dpi)
    if key not in _PAGES:
        _PAGES[key] = img.scaledToWidth(int(img.width() * dpi / 600), Qt.SmoothTransformation)
    return _PAGES[key]


# 獨立壓縮的批次（發譜預覽裡按「比較」也開同一個畫面，只是只有一份）
BATCH = [
    ("烽火操_大提琴.pdf", "大提琴　烽火操", 11, 6, 21.3, 8.7, 300),
    ("烽火操_總譜.pdf", "烽火操　總譜", 12, 9, 34.2, 14.8, 300),
    ("春江花月夜_低音提琴.pdf", "低音提琴　春江花月夜", 13, 5, 20.6, 7.9, 200),
]


class PageCanvas(QWidget):
    """一頁，100% 時寬 900 像素；可另給第二張圖畫成左右分割"""

    BASE = 900

    def __init__(self, img, img2=None, zoom=1.0):
        super().__init__()
        self.img, self.img2, self.split = img, img2, 0.5
        self.set_zoom(zoom)

    def set_zoom(self, z):
        self.z = z
        w = int(self.BASE * z)
        self.setFixedSize(w, int(w * 1.4145))
        self.update()

    def set_images(self, img, img2=None):
        self.img, self.img2 = img, img2
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        r = QRectF(self.rect())
        if self.img2 is None:
            p.drawImage(r, self.img)
            return
        x = self.width() * self.split
        p.save()
        p.setClipRect(QRectF(0, 0, x, self.height()))
        p.drawImage(r, self.img)
        p.restore()
        p.save()
        p.setClipRect(QRectF(x, 0, self.width() - x, self.height()))
        p.drawImage(r, self.img2)
        p.restore()
        p.setPen(QPen(QColor("#e0457b"), 3))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))

    def mousePressEvent(self, e):
        self.mouseMoveEvent(e)

    def mouseMoveEvent(self, e):
        if self.img2 is not None:
            self.split = max(0.0, min(1.0, e.position().x() / self.width()))
            self.update()


ZOOMS = [("符合寬度", None), ("100%", 1.0), ("200%", 2.0), ("400%", 4.0)]


class CompareBase(QWidget):
    """三個變體共用的外框：左邊批次清單，上方翻頁與縮放，下方留下／不要"""

    def __init__(self, title):
        super().__init__()
        self.fi, self.page = 0, 1
        self.files = QListWidget()
        self.files.setMaximumWidth(280)
        self.state = ["待確認"] * len(BATCH)
        self._fill_files()
        self.files.currentRowChanged.connect(self._pick)
        self.page_label = label("")
        self.zoom = combo([z[0] for z in ZOOMS], 2)
        self.zoom.currentIndexChanged.connect(lambda _i: self.refresh())
        self.info = label("", color=GRAY)
        self.tools = hbox(btn("‹ 上一頁", slot=lambda: self._turn(-1)), self.page_label,
                          btn("下一頁 ›", slot=lambda: self._turn(1)), "縮放", self.zoom, None, self.info)
        self.view_holder = QVBoxLayout()
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addLayout(self.tools)
        rv.addLayout(self.view_holder, 1)
        rv.addLayout(hbox(btn("用電腦的 PDF 閱讀器開啟壓縮後的檔"), None,
                          btn("不要這份（保留原檔）", slot=lambda: self._decide("不要")),
                          btn("留下壓縮後的", primary=True, slot=lambda: self._decide("留下"))))
        split = QSplitter(Qt.Horizontal)
        split.addWidget(vbox_widget(label("獨立壓縮：3 份", bold=True), self.files,
                                    label("全部確認後才輸出到「（壓縮）」資料夾；發譜預覽按「比較」時只有那一份。",
                                          color=GRAY, wrap=True)))
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        self.body = dialog_page(title, [split],
                                [label("2 份待確認", color=AMBER_FG), None, btn("取消"), btn("輸出", primary=True)],
                                width=1500)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.body)

    def _fill_files(self):
        self.files.blockSignals(True)
        self.files.clear()
        for i, (name, _t, _s, pages, before, after, dpi) in enumerate(BATCH):
            it = QListWidgetItem(f"{name}\n{before} → {after} MB（{dpi} dpi）・{self.state[i]}")
            it.setForeground(QColor({"留下": GREEN_FG, "不要": RED_FG}.get(self.state[i], AMBER_FG)))
            self.files.addItem(it)
        self.files.setCurrentRow(self.fi)
        self.files.blockSignals(False)

    def _pick(self, i):
        if i >= 0:
            self.fi, self.page = i, 1
            self.refresh()

    def _turn(self, d):
        pages = BATCH[self.fi][3]
        self.page = max(1, min(pages, self.page + d))
        self.refresh()

    def _decide(self, what):
        self.state[self.fi] = what
        self.fi = min(self.fi + 1, len(BATCH) - 1)
        self._fill_files()
        self.refresh()

    def images(self):
        name, title, seed, pages, before, after, dpi = BATCH[self.fi]
        orig = score_page(seed, self.page, title)
        return orig, compressed(orig, dpi)

    def zoom_value(self, avail_width):
        z = ZOOMS[self.zoom.currentIndex()][1]
        return z if z else max(0.2, (avail_width - 30) / PageCanvas.BASE)

    def refresh(self):
        name, title, seed, pages, before, after, dpi = BATCH[self.fi]
        self.page_label.setText(f"第 {self.page} 頁／共 {pages} 頁")
        self.info.setText(f"原檔 600 dpi・{before} MB　　壓縮後 {dpi} dpi・{after} MB")


def sync_scroll(a, b):
    for bar_a, bar_b in ((a.horizontalScrollBar(), b.horizontalScrollBar()),
                         (a.verticalScrollBar(), b.verticalScrollBar())):
        bar_a.valueChanged.connect(bar_b.setValue)
        bar_b.valueChanged.connect(bar_a.setValue)


class CompareA(CompareBase):
    """左右並排：同一頁、同一縮放，捲動同步"""

    def __init__(self):
        super().__init__("比較壓縮前後：左右並排")
        orig, comp = self.images()
        self.left, self.right = PageCanvas(orig), PageCanvas(comp)
        self.sa_l, self.sa_r = QScrollArea(), QScrollArea()
        for sa, canvas, title in ((self.sa_l, self.left, "原檔"), (self.sa_r, self.right, "壓縮後")):
            sa.setWidget(canvas)
            sa.setAlignment(Qt.AlignHCenter)
        sync_scroll(self.sa_l, self.sa_r)
        row = QHBoxLayout()
        row.addWidget(vbox_widget(label("原檔", bold=True), self.sa_l))
        row.addWidget(vbox_widget(label("壓縮後", bold=True), self.sa_r))
        self.view_holder.addLayout(row)
        self.refresh()

    def refresh(self):
        super().refresh()
        if not hasattr(self, "left"):
            return
        orig, comp = self.images()
        self.left.set_images(orig)
        self.right.set_images(comp)
        z = self.zoom_value(max(300, self.sa_l.viewport().width()))
        self.left.set_zoom(z)
        self.right.set_zoom(z)
        for sa in (self.sa_l, self.sa_r):   # 放大時先看左上角第一行（指法小字）
            sa.horizontalScrollBar().setValue(int(sa.horizontalScrollBar().maximum() * 0.08))
            sa.verticalScrollBar().setValue(int(sa.verticalScrollBar().maximum() * 0.05))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if ZOOMS[self.zoom.currentIndex()][1] is None and hasattr(self, "left"):
            self.refresh()


class CompareB(CompareBase):
    """單一大畫面加分割線：滑鼠拖分割線，左邊原檔、右邊壓縮後"""

    def __init__(self):
        super().__init__("比較壓縮前後：分割線")
        orig, comp = self.images()
        self.canvas = PageCanvas(orig, comp)
        self.sa = QScrollArea()
        self.sa.setWidget(self.canvas)
        self.sa.setAlignment(Qt.AlignHCenter)
        self.view_holder.addLayout(hbox(label("◀ 原檔", bold=True), None,
                                        label("在頁面上按住拖動分割線", color=GRAY), None, label("壓縮後 ▶", bold=True)))
        self.view_holder.addWidget(self.sa, 1)
        self.refresh()

    def refresh(self):
        super().refresh()
        if not hasattr(self, "canvas"):
            return
        orig, comp = self.images()
        self.canvas.set_images(orig, comp)
        self.canvas.set_zoom(self.zoom_value(max(400, self.sa.viewport().width())))
        self.sa.horizontalScrollBar().setValue(int(self.sa.horizontalScrollBar().maximum() * 0.3))
        self.sa.verticalScrollBar().setValue(int(self.sa.verticalScrollBar().maximum() * 0.05))


class Overview(PageCanvas):
    """縮圖：點哪裡，兩個放大鏡就看哪裡"""

    def __init__(self, img, on_pick):
        super().__init__(img, zoom=0.42)
        self.focus = QPointF(0.25, 0.12)
        self.on_pick = on_pick

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setPen(QPen(QColor("#e0457b"), 2))
        w, h = 0.22 * self.width(), 0.22 * self.width() * Loupe.ASPECT
        p.drawRect(QRectF(self.focus.x() * self.width() - w / 2, self.focus.y() * self.height() - h / 2, w, h))

    def mouseMoveEvent(self, e):
        self.focus = QPointF(e.position().x() / self.width(), e.position().y() / self.height())
        self.update()
        self.on_pick(self.focus)


class Loupe(QWidget):
    """放大鏡：把頁面上一小塊放大顯示"""

    ASPECT = 0.42

    def __init__(self, img):
        super().__init__()
        self.img, self.focus, self.span = img, QPointF(0.25, 0.12), 0.22
        self.setMinimumSize(500, 210)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        W, H = self.img.width(), self.img.height()
        sw = self.span * W
        sh = sw * self.height() / max(1, self.width())
        src = QRectF(self.focus.x() * W - sw / 2, self.focus.y() * H - sh / 2, sw, sh)
        p.drawImage(QRectF(self.rect()), self.img, src)


class CompareC(CompareBase):
    """縮圖加兩個放大鏡：點縮圖選位置，上下對照同一塊"""

    def __init__(self):
        super().__init__("比較壓縮前後：放大鏡")
        orig, comp = self.images()
        self.zoom.setEnabled(False)
        self.overview = Overview(orig, self._focus)
        self.loupe_o, self.loupe_c = Loupe(orig), Loupe(comp)
        self.span = combo(["放大 2 倍", "放大 4 倍", "放大 8 倍"], 1)
        self.span.currentIndexChanged.connect(self._span)
        right = vbox_widget(hbox(label("原檔", bold=True), None, self.span), self.loupe_o,
                            label("壓縮後", bold=True), self.loupe_c)
        right.layout().setStretch(1, 1)
        right.layout().setStretch(3, 1)
        row = QHBoxLayout()
        row.addWidget(vbox_widget(label("點或拖動選位置", color=GRAY), self.overview, None))
        row.addWidget(right, 1)
        self.view_holder.addLayout(row)
        self._span(1)
        self.refresh()

    def _span(self, i):
        for lp in (self.loupe_o, self.loupe_c):
            lp.span = [0.45, 0.22, 0.11][i]
            lp.update()

    def _focus(self, pt):
        for lp in (self.loupe_o, self.loupe_c):
            lp.focus = pt
            lp.update()

    def refresh(self):
        super().refresh()
        if not hasattr(self, "overview"):
            return
        orig, comp = self.images()
        self.overview.set_images(orig)
        self.loupe_o.img, self.loupe_c.img = orig, comp
        self.loupe_o.update()
        self.loupe_c.update()


def compare_a():
    return CompareA()


def compare_b():
    return CompareB()


def compare_c():
    return CompareC()


# --- 畫面三：發譜進行中與結果 -------------------------------------------------------

# 每個階段：標題、可否取消、各群組（傳檔、切換、清舊檔）、全體三段的進度
RUN_STATES = [
    ("傳檔中", True, {
        "烽火操": ("24/24", "等待", "—"), "春江花月夜": ("7/15", "等待", "—"), "茉莉花": ("0/15", "等待", "—")},
     (31, 54), None, None),
    ("切換中", False, {
        "烽火操": ("24/24", "切換中", "—"), "春江花月夜": ("13/15・2 份失敗", "不切換", "—"), "茉莉花": ("15/15", "等待", "—")},
     (52, 54), "running", None),
    ("清舊檔中", True, {
        "烽火操": ("24/24", "完成", "9/23"), "春江花月夜": ("13/15・2 份失敗", "不切換", "—"), "茉莉花": ("15/15", "完成", "0/15")},
     (52, 54), "done", (9, 38)),
    ("完成", False, {
        "烽火操": ("24/24", "完成", "23/23"), "春江花月夜": ("13/15・2 份失敗", "不切換", "—"), "茉莉花": ("15/15", "完成", "15/15")},
     (52, 54), "done", (38, 38)),
    ("傳檔時取消", False, {
        "烽火操": ("24/24", "不切換", "—"), "春江花月夜": ("7/15", "不切換", "—"), "茉莉花": ("0/15", "不切換", "—")},
     (31, 54), None, None),
]
FAILED_NOTE = "春江花月夜沒發成：春江花月夜_琵琶.pdf、春江花月夜_總譜.pdf 傳檔失敗（連線逾時）。已傳的 13 份留著，重新發譜時跳過。"
CANCEL_NOTE = "已取消，HuaXia 上沒有任何改變。已傳的 31 份團員看不到，重新發譜時跳過。"


class RunBase(QWidget):
    """三個變體共用：原型用的「上一階段／下一階段」按鈕"""

    def __init__(self):
        super().__init__()
        self.si = 0
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(0, 0, 0, 0)
        self.body = None

    def wrap(self, page):
        """畫面加上原型用的模擬按鈕（放上面，免得被切換列擋住）"""
        name = RUN_STATES[self.si][0]
        return vbox_widget(hbox(label(f"模擬階段：{name}", color="#111", bg="#f5c518"),
                                btn("上一階段", proto=True, slot=lambda: self.go(-1)),
                                btn("下一階段", proto=True, slot=lambda: self.go(1)), None, margins=(6, 4, 6, 0)), page)

    def go(self, d):
        self.si = (self.si + d) % len(RUN_STATES)
        self.render()

    def set_body(self, w):
        if self.body is not None:
            self.outer.removeWidget(self.body)
            self.body.hide()
            self.body.deleteLater()
        self.body = w
        self.outer.insertWidget(0, w, 1)

    def cancel_button(self):
        name, can_cancel, *_ = RUN_STATES[self.si]
        if name in ("完成", "傳檔時取消"):
            return btn("關閉", primary=True)
        b = btn("取消")
        b.setEnabled(can_cancel)
        if name == "切換中":
            b.setToolTip("切換一瞬間完成，不能取消")
        if name == "清舊檔中":
            b.setToolTip("取消時沒刪的舊檔留著，下次發譜時清掉")
        return b

    def result_banner(self):
        name = RUN_STATES[self.si][0]
        if name == "完成":
            return banner("發成 2 個群組（烽火操、茉莉花），沒發成 1 個。" + FAILED_NOTE,
                          [btn("重新發譜沒發成的群組")], color=RED_FG, bg=RED_BG)
        if name == "傳檔時取消":
            return banner(CANCEL_NOTE, [btn("重新發譜")])
        return None


def fixed(w, width=80):
    w.setFixedWidth(width)
    return w


def progress(value, total, fmt=None):
    bar = QProgressBar()
    bar.setMaximum(total)
    bar.setValue(value)
    if fmt:
        bar.setFormat(fmt)
    return bar


class RunA(RunBase):
    """三條進度條：傳檔、切換、清舊檔，下面一行目前在做什麼"""

    def __init__(self):
        super().__init__()
        self.render()

    def render(self):
        name, _c, groups, up, sw, cl = RUN_STATES[self.si]
        rows = []
        rows.append(hbox(fixed(label("1 傳檔", bold=True)), progress(*up, "%v/%m 份")))
        sw_bar = progress(1 if sw == "done" else 0, 1, {"done": "完成", "running": "切換中…"}.get(sw, "等待"))
        if sw == "running":
            sw_bar.setMaximum(0)
        rows.append(hbox(fixed(label("2 切換", bold=True)), sw_bar))
        rows.append(hbox(fixed(label("3 清舊檔", bold=True)), progress(*(cl or (0, 38)), "%v/%m 份" if cl else "等待")))
        now = {"傳檔中": "正在傳：春江花月夜_中阮.pdf（同時 4 份）", "切換中": "把 2 個群組一次寫進 HuaXia",
               "清舊檔中": "刪除被取代的舊檔：烽火操_揚琴.pdf"}.get(name, "")
        items = [label(f"發譜：{name}", bold=True, size=15)] + rows + [label(now, color=GRAY)]
        res = self.result_banner()
        if res:
            items.append(res)
        page = dialog_page("發譜", [vbox_widget(*items, spacing=10)], [None, self.cancel_button()], width=760, grow=False)
        self.set_body(self.wrap(page))


class RunB(RunBase):
    """群組表：每個群組一列，三段各一欄；以群組為單位切換一目了然"""

    def __init__(self):
        super().__init__()
        self.render()

    def render(self):
        name, _c, groups, up, sw, cl = RUN_STATES[self.si]
        rows = []
        for g in GROUPS:
            u, s, c = groups[g["name"]]
            fail = "失敗" in u
            rows.append([cell(g["name"], bold=True), cell(piece_text(g)),
                         cell(u, fg=RED_FG if fail else (GREEN_FG if u.split("/")[0] == u.split("/")[-1] else None)),
                         cell(s, fg={"完成": GREEN_FG, "不切換": RED_FG if fail or name == "傳檔時取消" else GRAY,
                                     "切換中": AMBER_FG}.get(s)),
                         cell(c, fg=GREEN_FG if c.split("/")[0] == c.split("/")[-1] and c != "—" else None)])
        tbl = table(["群組", "HuaXia 曲目", "1 傳檔", "2 切換", "3 清舊檔"], rows, stretch=1)
        tbl.setFixedHeight(28 * 4 + 20)
        items = [hbox(label(f"發譜：{name}", bold=True, size=15), None, label(f"傳檔 {up[0]}/{up[1]}", color=GRAY)),
                 tbl, label("有檔沒傳上去的群組不切換，其他群組照常。", color=GRAY)]
        res = self.result_banner()
        if res:
            items.append(res)
        page = dialog_page("發譜", [vbox_widget(*items, spacing=10)], [None, self.cancel_button()], width=900, grow=False)
        self.set_body(self.wrap(page))


RUN_LOG = [
    ["21:40:02　寫入發譜紀錄：開始發譜", "21:40:03　清掉這次涉及曲目的孤兒檔：1 份",
     "21:40:05　傳檔：烽火操 24/24", "21:40:31　傳檔：春江花月夜 7/15 …"],
    ["21:40:48　傳檔失敗：春江花月夜_琵琶.pdf（連線逾時，重試 3 次）",
     "21:40:52　傳檔失敗：春江花月夜_總譜.pdf（連線逾時，重試 3 次）", "21:41:05　傳檔：茉莉花 15/15",
     "21:41:06　切換：烽火操、茉莉花（春江花月夜有檔沒傳上去，不切換）…"],
    ["21:41:07　切換完成：建立聲部 1 個、覆寫 3 項、登記 39 份、掛上期末公演",
     "21:41:08　清舊檔：9/38 …"],
    ["21:41:20　清舊檔：38/38", "21:41:21　寫入發譜紀錄：發成 2 個群組，沒發成 1 個"],
]


class RunC(RunBase):
    """不擋主視窗：狀態列一條總進度，展開是逐行紀錄；完成時跳出結果"""

    def __init__(self):
        super().__init__()
        self.render()

    def render(self):
        name, _c, groups, up, sw, cl = RUN_STATES[self.si]
        done = up[0] + (cl[0] if cl else 0)
        total = up[1] + 38
        log = QTextEdit()
        log.setReadOnly(True)
        lines = []
        if name == "傳檔時取消":
            lines = RUN_LOG[0] + ["21:40:33　使用者取消：沒有任何群組切換"]
        else:
            for chunk in RUN_LOG[: self.si + 1]:
                lines += chunk
        log.setPlainText("\n".join(lines))
        panel = QGroupBox("發譜紀錄（這次）")
        pl = QVBoxLayout(panel)
        pl.addWidget(log)
        res = self.result_banner()
        if res:
            pl.addWidget(res)
        status = QWidget()
        status.setLayout(hbox(label(f"發譜：{name}", color=AMBER_FG if name not in ("完成",) else GREEN_FG),
                              progress(done, total, "%v/%m"), self.cancel_button(), margins=(0, 0, 0, 0)))
        status.setMinimumWidth(520)
        top = banner("發譜中：這 3 個群組的分譜、編制與命名先鎖住，其他群組照常可用。", color=BLUE_FG, bg=BLUE_BG) \
            if name not in ("完成", "傳檔時取消") else None
        panel.setMaximumHeight(260)
        shell = main_shell(content_top=top, status_right=status, bottom=panel)
        self.set_body(self.wrap(shell))


def run_a():
    return RunA()


def run_b():
    return RunB()


def run_c():
    return RunC()


# --- 畫面四：撤回 ---------------------------------------------------------------

# 發譜紀錄：時間、操作者、動作、群組 → 曲目
LOG = [
    ("2026-10-12 21:40", "suikann", "發譜", [("烽火操", "長城隨想－烽火操"), ("茉莉花", "茉莉花")], "沒發成：春江花月夜"),
    ("2026-10-08 20:15", "suikann", "發譜", [("烽火操", "長城隨想－烽火操")], ""),
    ("2026-10-05 22:10", "suikann", "發譜", [("烽火操", "豫北敘事曲"), ("茉莉花", "茉莉花")], ""),
    ("2026-09-28 20:03", "林詠晴", "發譜", [("豫北敘事曲", "豫北敘事曲"), ("春節序曲", "春節序曲")], ""),
    ("2026-06-20 19:30", "王語彤", "收回", [("—", "長城隨想－烽火操"), ("—", "春節序曲")], "上學期"),
]
# 撤回 10/05 那次「烽火操 → 豫北敘事曲」（連錯曲目）會發生的事
WITHDRAW = dict(
    files=[file_name("烽火操", v) for v in FENGHUO_VOICES] + ["烽火操_總譜.pdf"],
    gone=["烽火操_揚琴.pdf", "烽火操_柳琴.pdf"],
    delete_voices=["排鼓", "二胡獨奏"],
    keep_voices=[("高胡", "已排 2 人")],
    not_back=["被取代的舊檔 18 份不會回來", "二胡 II 人數 6–8 → 5–8 不改回", "大提琴聲部組 拉弦 → 低音 不改回",
              "場次不動：豫北敘事曲仍掛在校慶音樂會"],
    # 連錯時新建了曲目的情況（本情境沒有）：撤回後留下一首空曲目，可能已掛上場次，由幹部在網頁刪除
    leftover="撤回後豫北敘事曲只剩 9/28 放的 3 份；被 10/05 取代的 18 份不會回來，要從它的專案或譜庫重新發譜。",
)


def log_list(selectable=True):
    tbl = table(["時間", "操作者", "動作", "群組 → HuaXia 曲目", "備註"],
                [[cell(t_), cell(who), cell(act, fg=GRAY if act != "發譜" else None),
                  cell("、".join(f"{g} → {p}" for g, p in pairs)), cell(note, fg=GRAY)]
                 for t_, who, act, pairs, note in LOG], stretch=3)
    tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
    tbl.setSelectionMode(QAbstractItemView.SingleSelection)
    tbl.selectRow(2)
    return tbl


def withdraw_effects(parent_tree=None):
    """撤回的影響：會撤掉、會刪、不會改回"""
    tw = parent_tree or tree(["", "說明"])
    files = titem(tw, [f"撤掉的檔：{len(WITHDRAW['files']) - len(WITHDRAW['gone'])} 份", "這次放上豫北敘事曲、現在還在的"], fg=RED_FG,
                  bold=True)
    for f in WITHDRAW["files"]:
        if f in WITHDRAW["gone"]:
            titem(files, [f, "已被 10/08 的發譜取代，不動"], fg=GRAY)
        else:
            titem(files, [f, ""])
    voices = titem(tw, ["刪掉的聲部", "那次新建、還沒排人的"], fg=RED_FG, bold=True)
    for v in WITHDRAW["delete_voices"]:
        titem(voices, [v, ""])
    for v, why in WITHDRAW["keep_voices"]:
        titem(voices, [f"{v}（不刪）", f"那次新建，但{why}"], fg=GRAY)
    keep = titem(tw, ["不會改回", ""], fg=GRAY, bold=True)
    for s in WITHDRAW["not_back"]:
        titem(keep, [s, ""], fg=GRAY)
    voices.setExpanded(True)
    keep.setExpanded(True)
    return tw


def catalog_log_shell(content):
    """譜庫視窗的「發譜紀錄」分頁"""
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "曲目")
    tabs.addTab(QWidget(), "聲部組對照")
    tabs.addTab(content, "發譜紀錄")
    tabs.setCurrentIndex(2)
    return vbox_widget(hbox(label("譜庫：華夏國樂社譜庫", bold=True), None, label("已連線（suikann）", color=GRAY)), tabs,
                       margins=(6, 6, 6, 6))


def withdraw_a():
    """兩欄：左邊發譜紀錄挑一次，右邊勾群組、下面列出影響"""
    log = log_list()
    groups = QGroupBox("2026-10-05 22:10 的發譜，撤回哪幾個群組")
    gl = QVBoxLayout(groups)
    gl.addWidget(check("烽火操 → 豫北敘事曲", True))
    gl.addWidget(check("茉莉花 → 茉莉花", False))
    right = vbox_widget(groups, withdraw_effects(),
                        banner(WITHDRAW["leftover"]),
                        hbox(None, btn("撤回 1 個群組", danger=True)))
    split = QSplitter(Qt.Horizontal)
    split.addWidget(vbox_widget(label("發譜紀錄", bold=True), log))
    split.addWidget(right)
    split.setSizes([620, 620])
    return catalog_log_shell(split)


def withdraw_b():
    """一棵樹：發譜紀錄 › 群組 › 影響，勾群組就撤回"""
    tw = tree(["發譜紀錄", "說明"])
    for t_, who, act, pairs, note in LOG:
        root = titem(tw, [f"{t_}　{who}　{act}", note], fg=GRAY if act != "發譜" else None, bold=act == "發譜")
        if act != "發譜":
            continue
        for g, p in pairs:
            it = titem(root, [f"{g} → {p}", ""])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked if (t_.startswith("2026-10-05") and g == "烽火操") else Qt.Unchecked)
            if t_.startswith("2026-10-05") and g == "烽火操":
                inner = QTreeWidget()
                withdraw_effects(inner)
                for i in range(inner.topLevelItemCount()):
                    top = inner.takeTopLevelItem(0)
                    it.addChild(top)
                it.setExpanded(True)
                root.setExpanded(True)
    return catalog_log_shell(vbox_widget(
        hbox(label("勾選要撤回的群組，展開看會撤掉什麼。", color=GRAY), None),
        tw, banner(WITHDRAW["leftover"]),
        hbox(label("已勾 1 個群組", color=AMBER_FG), None, btn("撤回", danger=True))))


def withdraw_c():
    """從 HuaXia 曲目出發：挑曲目，看它歷次發譜的時間軸，挑一次撤回"""
    pick = combo(HX_PIECES, 3)
    timeline = QListWidget()
    entries = [
        ("2026-10-05 22:10　suikann　烽火操 → 豫北敘事曲", "放了 24 份、新建 3 個聲部；22 份還在", True),
        ("2026-09-28 20:03　林詠晴　豫北敘事曲 → 豫北敘事曲", "放了 21 份；10/05 取代了 18 份，3 份還在", False),
        ("2026-03-02 21:00　王語彤　豫北敘事曲 → 豫北敘事曲", "已在 2026-06-20 收回", False),
    ]
    for head, sub, sel in entries:
        it = QListWidgetItem(f"{head}\n{sub}")
        if "收回" in sub:
            it.setForeground(QColor(GRAY))
        timeline.addItem(it)
    timeline.setCurrentRow(0)
    timeline.setMaximumHeight(170)
    effects = withdraw_effects()
    return catalog_log_shell(vbox_widget(
        hbox("HuaXia 曲目", pick, None, label("現在：29 份樂譜，其中 22 份是 10/05 放的", color=GRAY)),
        label("這首曲目的發譜", bold=True), timeline,
        label("撤回 10/05 的那次", bold=True), effects,
        banner(WITHDRAW["leftover"]),
        hbox(None, btn("撤回這次", danger=True))))


# --- 畫面五：收回 ---------------------------------------------------------------

# 可收回的曲目：曲目、公演、檔（檔名、放的人、聲部）、還掛在未結束公演
RECALL = [
    ("長城隨想－烽火操", "期末公演 2026-06-14（已結束）",
     [(file_name("烽火操", v), "suikann（泠靈）", v) for v in FENGHUO_VOICES[:20]]
     + [("二胡1_弓法.pdf", "林詠晴（網頁）", "二胡1"), ("琵琶_指法.pdf", "陳昱安（網頁）", "琵琶")], None),
    ("春節序曲", "期末公演 2026-06-14（已結束）",
     [(f"春節序曲_{v}.pdf", "王語彤（泠靈）", v) for v in MOLI_VOICES] + [("春節序曲_高胡_改.pdf", "林詠晴（網頁）", "高胡")],
     None),
    ("茉莉花（江蘇民歌）", "期末公演 2026-06-14（已結束）",
     [(file_name("茉莉花", v), "suikann（泠靈）", v) for v in MOLI_VOICES], "校慶音樂會 2026-11-21"),
]


def others_first(files):
    return sorted(files, key=lambda f: "網頁" not in f[1])


def recall_rows(piece):
    name, concert, files, still = piece
    mine = sum(1 for f in files if "泠靈" in f[1])
    return mine, len(files) - mine


def recall_a():
    """曲目勾選清單＋下面一張檔案表"""
    tw = tree(["曲目", "公演", "樂譜", "注意"])
    for p in RECALL:
        name, concert, files, still = p
        mine, others = recall_rows(p)
        it = titem(tw, [name, concert, f"{len(files)} 份（泠靈發的 {mine}、其他人放的 {others}）",
                        f"還掛在{still}" if still else ""])
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(0, Qt.Unchecked if still else Qt.Checked)
        if still:
            it.setForeground(3, QColor(RED_FG))
    tw.setMaximumHeight(150)
    rows = []
    for name, concert, files, still in RECALL[:2]:
        for f, who, v in others_first(files):
            rows.append([cell(name), cell(v), cell(f), cell(who, fg=AMBER_FG if "網頁" in who else None)])
    tbl = table(["曲目", "聲部", "樂譜", "放的人"], rows, stretch=2)
    return catalog_log_shell(vbox_widget(
        label("收回：刪掉曲目在 HuaXia 上的所有樂譜，包含別人放的。聲部、曲目、場次與排人都留著。", color=GRAY, wrap=True),
        tw, label("將刪除的樂譜（已勾 2 首，共 37 份）", bold=True), tbl,
        hbox(None, btn("取消"), btn("收回 2 首", danger=True))))


def recall_b():
    """一首一頁：大字寫刪多少份，逐首決定收回、這首不收回或跳過"""
    stack = QStackedWidget()
    pager = label("")

    def page_for(i, p):
        name, concert, files, still = p
        mine, others = recall_rows(p)
        lst = QListWidget()
        for f, who, v in others_first(files):
            it = QListWidgetItem(f"{f}　—　{who}")
            if "網頁" in who:
                it.setForeground(QColor(AMBER_FG))
            lst.addItem(it)
        items = [label(name, bold=True, size=20), label(concert, color=GRAY),
                 label(f"刪除 {len(files)} 份樂譜", bold=True, size=26, color=RED_FG),
                 label(f"泠靈發的 {mine} 份、其他人放的 {others} 份（黃字）", color=GRAY)]
        if still:
            items.append(banner(f"這首還掛在{still}，還沒結束。收回後團員在那場之前就拿不到譜。", color=RED_FG, bg=RED_BG))
        items.append(lst)
        return vbox_widget(*items)

    for i, p in enumerate(RECALL):
        stack.addWidget(page_for(i, p))

    def go(i):
        stack.setCurrentIndex(i % len(RECALL))
        pager.setText(f"第 {stack.currentIndex() + 1} 首／共 {len(RECALL)} 首")
    go(0)
    return catalog_log_shell(vbox_widget(
        hbox(label("收回", bold=True, size=16), None, pager),
        stack,
        hbox(btn("跳過", slot=lambda: go(stack.currentIndex() + 1)), btn("這首不收回"), None,
             btn("收回這首", danger=True, slot=lambda: go(stack.currentIndex() + 1)))))


def recall_c():
    """依公演分：已結束的公演 › 曲目 › 樂譜；掛在未結束公演的曲目另列"""
    tw = tree(["公演 › 曲目 › 樂譜", "放的人", "說明"])
    ended = titem(tw, ["期末公演 2026-06-14（已結束）", "", "3 首"], bold=True)
    for p in RECALL:
        name, concert, files, still = p
        mine, others = recall_rows(p)
        it = titem(ended, [name, "", f"{len(files)} 份（其他人放的 {others}）" + (f"・還掛在{still}" if still else "")],
                   fg=RED_FG if still else None)
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(0, Qt.Unchecked if still else Qt.Checked)
        for f, who, v in others_first(files)[:6] + ([("…", "", "")] if len(files) > 6 else []):
            titem(it, [f, who, ""], fg=AMBER_FG if "網頁" in who else GRAY if f == "…" else None)
        it.setExpanded(not still and name.startswith("長城"))
    ended.setExpanded(True)
    upcoming = titem(tw, ["校慶音樂會 2026-11-21（未結束）", "", "這場的曲目不能在這裡收回"], fg=GRAY, bold=True)
    titem(upcoming, ["茉莉花（江蘇民歌）", "", "也掛在期末公演，勾它會在這場前刪掉樂譜"], fg=RED_FG)
    upcoming.setExpanded(True)
    return catalog_log_shell(vbox_widget(
        hbox(label("勾選要收回的曲目", color=GRAY), None, check("只看還沒收回的", True)),
        tw, hbox(label("已勾 2 首、37 份", color=AMBER_FG), None, btn("收回", danger=True))))


# --- 畫面六：收回提醒 -------------------------------------------------------------

REMIND = [("長城隨想－烽火操", "期末公演 2026-06-14 結束", 22), ("春節序曲", "期末公演 2026-06-14 結束", 15),
          ("茉莉花（江蘇民歌）", "期末公演 2026-06-14 結束", 14)]


def reminder_a():
    """開泠靈時跳對話框：每首一列，各自收回、之後再說、這首不收回"""
    rows = []
    for name, why, n in REMIND:
        rows.append([cell(name, bold=True), cell(why, fg=GRAY), cell(f"{n} 份"),
                     hbox(btn("收回…", small=True), btn("之後再說", small=True), btn("這首不收回", small=True))])
    tbl = table(["曲目", "", "樂譜", ""], rows, stretch=1)
    note = label("收回前會再向 HuaXia 查一次，還掛在未結束公演上的會標出來。「之後再說」下次開泠靈再提醒；"
                 "「這首不收回」記進發譜紀錄，不再提醒。", color=GRAY, wrap=True)
    tbl.setFixedHeight(28 + 38 * 3 + 6)
    return dialog_page("3 首曲目的公演已結束，還沒收回", [tbl, note],
                       [btn("全部之後再說"), None, btn("全部收回…", danger=True)], width=900, grow=False)


def reminder_b():
    """主視窗頂端提示列：不擋操作，按「查看」開收回畫面"""
    top = banner("3 首曲目的公演已結束，還沒收回：長城隨想－烽火操、春節序曲、茉莉花。",
                 [btn("查看並收回"), btn("之後再說")])
    return main_shell(content_top=top, body=label("（群組內容）", color=GRAY))


def reminder_c():
    """狀態列的徽章：點開是一張小清單，不開就不打擾"""
    pop = QFrame()
    pop.setObjectName("pop")
    pop.setStyleSheet("QFrame#pop { background: #24262f; border: 1px solid #4a4c63; border-radius: 6px; }")
    pv = QVBoxLayout(pop)
    pv.addWidget(label("公演已結束、還沒收回", bold=True))
    for name, why, n in REMIND:
        pv.addLayout(hbox(label(f"{name}　{n} 份", wrap=False), None, btn("收回…", small=True),
                          btn("不收回", small=True)))
        pv.addWidget(label(why, color=GRAY))
    pv.addLayout(hbox(None, btn("之後再說")))
    pop.setFixedWidth(560)
    badge = btn("待收回 3")
    badge.setStyleSheet(f"background: {RED_BG}; color: {RED_FG}; font-weight: bold; border-radius: 10px; padding: 3px 12px;")
    body = vbox_widget(None, hbox(None, pop), margins=(0, 0, 8, 0))
    return main_shell(status_right=badge, body=body)


# --- 畫面清單與切換列 -----------------------------------------------------------

SCREENS = [
    ("preview", "發譜預覽", "3 個群組：烽火操連到現有曲目（7 項聲部變動）、春江花月夜新建曲目、茉莉花一致；"
                          "3 份超過 20MB，總譜還在壓縮", [
        ("A", "主從：左邊群組清單，右邊曲目、聲部、檔案三段", preview_a),
        ("B", "精靈：曲目 → 聲部 → 檔案 → 公演 → 確認，每步列全部群組", preview_b),
        ("C", "一張總表：所有會寫進 HuaXia 的變動，可只看要決定的", preview_c),
    ]),
    ("compare", "壓縮比較", "獨立壓縮 3 份（發譜預覽按「比較」開同一畫面）；放大看指法小字最容易看出差別", [
        ("A", "左右並排：同頁同縮放、捲動同步", compare_a),
        ("B", "分割線：一張大圖，拖線切換原檔與壓縮後", compare_b),
        ("C", "放大鏡：點縮圖選位置，上下對照同一小塊", compare_c),
    ]),
    ("run", "發譜進行中與結果", "黃色按鈕切換階段：傳檔中 → 切換中 → 清舊檔中 → 完成（春江花月夜沒發成）→ 傳檔時取消", [
        ("A", "三條進度條：傳檔、切換、清舊檔", run_a),
        ("B", "群組表：每個群組一列，三段各一欄", run_b),
        ("C", "不擋主視窗：狀態列總進度＋逐行紀錄", run_c),
    ]),
    ("withdraw", "撤回", "10/05 把烽火操連錯到豫北敘事曲；撤回那次的烽火操", [
        ("A", "兩欄：發譜紀錄挑一次，右邊勾群組看影響", withdraw_a),
        ("B", "一棵樹：發譜紀錄 › 群組 › 影響", withdraw_b),
        ("C", "從曲目出發：挑曲目，看它歷次發譜", withdraw_c),
    ]),
    ("recall", "收回", "上學期期末公演的 3 首；茉莉花還掛在 11/21 的校慶音樂會", [
        ("A", "曲目勾選＋一張檔案表", recall_a),
        ("B", "一首一頁，逐首決定", recall_b),
        ("C", "依公演分組：已結束 › 曲目 › 樂譜", recall_c),
    ]),
    ("reminder", "收回提醒", "開泠靈時讀發譜紀錄：3 首曲目的公演都已結束、還沒收回（開泠靈時不連 HuaXia）", [
        ("A", "跳對話框：每首各自決定", reminder_a),
        ("B", "主視窗頂端提示列，不擋操作", reminder_b),
        ("C", "狀態列徽章，點開才看清單", reminder_c),
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
        self.scenario = label("", color="#333", size=12, wrap=True)
        v.addWidget(self.scenario)


class Host(QMainWindow):
    def __init__(self):
        super().__init__()
        holder = QWidget()
        self._lay = QVBoxLayout(holder)
        self._lay.setContentsMargins(0, 0, 0, 92)
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
        self.setWindowTitle(f"PROTOTYPE 發譜、撤回與收回畫面 — {name} {vkey}")
        self._place_bar()

    def step(self, delta):
        si, vi = self._at
        n = len(SCREENS[si][3])
        self.show_variant(si, (vi + delta) % n)

    def _place_bar(self):
        w = min(self.width() - 32, 1100)
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
        if focus is not None and focus.window() is not self._host:
            return False
        self._host.step(-1 if event.key() == Qt.Key_Left else 1)
        return True


def main():
    parser = argparse.ArgumentParser(description="發譜、撤回與收回畫面的原型")
    parser.add_argument("--screen", default=SCREENS[0][0], choices=[s[0] for s in SCREENS])
    parser.add_argument("--variant", default="A")
    parser.add_argument("--shots", help="每個變體截一張圖存到這個資料夾後結束")
    parser.add_argument("--font", help="另外載入的字型檔（例如沒有中文字型的環境）")
    args = parser.parse_args()
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
    # 不用 main._AppStyle：PySide6 6.12 在 Python 3.11 以前會因它覆寫的 pixelMetric 崩潰（見 #60），原型用不到它
    app.setStyle(QStyleFactory.create("Fusion"))
    _apply_dark_theme(app)
    host = Host()
    host.resize(1400, 900)
    si = next(i for i, s in enumerate(SCREENS) if s[0] == args.screen)
    vi = max(0, next((i for i, v in enumerate(SCREENS[si][3]) if v[0] == args.variant.upper()), 0))
    host.show_variant(si, vi)
    host.show()
    if args.shots:
        os.makedirs(args.shots, exist_ok=True)
        for si, (key, _n, _s, variants) in enumerate(SCREENS):
            for vi, (vkey, *_rest) in enumerate(variants):
                host.show_variant(si, vi)
                for _ in range(3):
                    app.processEvents()
                if hasattr(host._page, "refresh"):
                    host._page.refresh()
                    app.processEvents()
                host.grab().save(os.path.join(args.shots, f"{si + 1}-{key}-{vkey}.png"))
        return
    keys = ArrowKeys(host)
    app.installEventFilter(keys)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

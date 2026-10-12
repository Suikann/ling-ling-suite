# -*- coding: utf-8 -*-
"""
PROTOTYPE（拋棄式，只留在 prototype/deposit-screens 分支，不進 develop）

回答 wayfinder ticket「存入、撤銷存入與清除本機檔案畫面的原型」（#65）：存入譜庫預覽、撤銷存入、
改連時的編制清單、清除本機檔案預覽與確認框、譜庫錯誤訊息要長什麼樣子。行為已在
「存入譜庫後刪除本機檔案與發譜讀檔的衝突」（#53）與「譜庫的收網」（#55）定案。
六個畫面，各有二到三個結構不同的變體；資料全是假的，按鈕不連 Drive、不寫入任何地方。

    python src/ui/prototype_deposit.py
    python src/ui/prototype_deposit.py --screen=undo --variant=B
    python src/ui/prototype_deposit.py --shots=<資料夾>    # 每個變體截一張圖後結束

底部黃色切換列：左邊選畫面，箭頭（或鍵盤左右鍵，焦點不在表格或輸入框時）換變體。
畫面裡的黃色控制項是原型用的模擬控制（例如「模擬：有未存檔的改動」、錯誤類別），不屬於被評估的設計。
"""
import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenuBar, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QStackedWidget, QStyleFactory, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

# --- 顏色（配合主程式的深色主題）--------------------------------------------

AMBER_BG, AMBER_FG = "#5b4613", "#f2c14e"     # 要確認、不能清除
GRAY = "#7a7f93"                               # 不動、跳過、說明
GREEN_BG, GREEN_FG = "#1d4a2f", "#7ddc9a"      # 新上傳、可清除
RED_BG, RED_FG = "#5a2129", "#ff8a8a"          # 移除、刪掉、失敗、永久刪除
BLUE_BG, BLUE_FG = "#253c63", "#8fb8ff"        # 更新、還原
PROTO = "background: #f5c518; color: #111; font-weight: bold; border: none; border-radius: 4px; padding: 4px 10px;"

# --- 假資料 ---------------------------------------------------------------

CATALOG = "華夏國樂社譜庫"
PROJECT_DIR = "~/社團/2026 秋季公演/輸出"

FENGHUO_VOICES = ["梆笛", "曲笛", "高音笙", "中音笙", "高音嗩吶", "中音嗩吶", "定音鼓", "打擊 1", "打擊 2", "排鼓",
                  "揚琴", "柳琴", "琵琶", "中阮", "大阮", "古箏", "二胡獨奏", "高胡", "二胡 I", "二胡 II",
                  "中胡", "大提琴", "低音提琴"]
CHUNJIANG_VOICES = ["簫", "曲笛", "笙", "琵琶", "揚琴", "古箏", "中阮", "大阮", "高胡", "二胡 I", "二胡 II",
                    "中胡", "大提琴", "低音提琴"]
MOLI_VOICES = ["梆笛", "曲笛", "笙", "揚琴", "柳琴", "琵琶", "中阮", "大阮", "高胡", "二胡 I", "二胡 II", "中胡",
               "大提琴", "低音提琴"]


def file_name(group, voice):
    return f"{group}_{voice.replace(' ', '')}.pdf"


def sizes_for(names, seed):
    rnd = random.Random(seed)
    return {n: round(rnd.uniform(0.4, 2.8), 1) for n in names}


# 存入預覽：每份檔的動作
ACT = {"new": ("新上傳", GREEN_FG), "update": ("更新內容", BLUE_FG), "move": ("搬移", BLUE_FG),
       "skip": ("跳過", GRAY), "resumed": ("跳過", GRAY)}


def _deposit_files(group, voices, special, seed, resumed=0):
    sz = sizes_for(voices + ["總譜"], seed)
    rows = []
    for i, v in enumerate(["總譜"] + voices):
        act, why = special.get(v, ("resumed", "上次中斷前已存入") if 0 < i <= resumed else ("skip", "內容相同"))
        local = ("打擊/" if v == "打擊 2" else "") + file_name(group, v)
        rows.append(dict(voice=v, local=local, act=act, why=why, size=sz[v]))
    return rows


DEPOSIT = [
    dict(name="烽火操", link="長城隨想 › 劉文金原版 › 第四樂章 烽火操", folder="長城隨想/劉文金原版",
         files=_deposit_files("烽火操", FENGHUO_VOICES, {
             "大提琴": ("update", "內容不同"),
             "二胡 I": ("update", "內容不同，改名：烽火操_二胡1.pdf → 烽火操_二胡I.pdf"),
             "打擊 2": ("move", "內容相同，搬到子資料夾「打擊」"),
             "排鼓": ("new", "新聲部"),
             "總譜": ("new", "第一次存入總譜"),
         }, 1)),
    dict(name="春江花月夜", link="春江花月夜 › 秦鵬章 編 › 整首", folder="春江花月夜/秦鵬章 編",
         files=_deposit_files("春江花月夜", CHUNJIANG_VOICES,
                              {v: ("new", "") for v in ["總譜"] + CHUNJIANG_VOICES[9:]}, 2, resumed=9)),
    dict(name="茉莉花", link=None, folder=None, files=[]),
]


def act_count(g):
    out = {}
    for f in g["files"]:
        key = "skip" if f["act"] == "resumed" else f["act"]
        out[key] = out.get(key, 0) + 1
    return out


def act_summary(g):
    if not g["link"]:
        return "沒連到譜庫，不存入"
    c = act_count(g)
    names = [("new", "新上傳"), ("update", "更新"), ("move", "搬移"), ("skip", "跳過")]
    return "・".join(f"{n} {c[k]}" for k, n in names if c.get(k))


def deposit_totals():
    up = [f for g in DEPOSIT for f in g["files"] if f["act"] in ("new", "update")]
    mv = [f for g in DEPOSIT for f in g["files"] if f["act"] == "move"]
    return f"上傳 {len(up)} 份（{sum(f['size'] for f in up):.1f} MB），搬移 {len(mv)} 份"


# 撤銷存入：10/11 那次把豫北敘事曲連錯到長城隨想的第三樂章
DEPOSIT_LOG = [
    ("2026-10-11 21:04", "存入", "烽火操、春江花月夜、豫北敘事曲", "48 份"),
    ("2026-10-03 14:22", "存入", "春江花月夜", "15 份"),
    ("2026-09-27 10:15", "撤銷存入", "茉莉花（09-26 那次）", "14 份"),
    ("2026-09-26 22:40", "存入", "茉莉花、春節序曲", "29 份"),
]
UNDO_WHO = "suikann・SUIKANN-PC"
UNDO_GROUPS = [("烽火操", "長城隨想 › 劉文金原版 › 第四樂章"), ("春江花月夜", "春江花月夜 › 秦鵬章 編 › 整首"),
               ("豫北敘事曲", "長城隨想 › 劉文金原版 › 第三樂章")]
UNDO_PLACE = "長城隨想 › 劉文金原版 › 第三樂章"

# 聲部、現在譜庫裡、撤銷後、原因、類別
UNDO_KINDS = [("remove", "丟進 Drive 垃圾桶", RED_FG, RED_BG), ("restore", "還原存入前的內容", BLUE_FG, BLUE_BG),
              ("lost", "無法還原", AMBER_FG, AMBER_BG), ("keep", "不動", GRAY, None)]
UNDO_FILES = (
    [(v, file_name("豫北敘事曲", v), f"第三樂章_{v.replace(' ', '')}.pdf（存入前的內容）", "那次蓋掉了原本的檔", "restore")
     for v in ["梆笛", "曲笛", "高音笙", "揚琴", "二胡 I"]]
    + [(v, file_name("豫北敘事曲", v), "（沒有檔）", "那次新上傳的", "remove")
       for v in ["柳琴", "琵琶", "小鈸", "大阮", "低音提琴"]]
    + [("中阮", file_name("豫北敘事曲", "中阮"), "維持現狀", "舊內容已被 Drive 刪除", "lost"),
       ("二胡 II", file_name("豫北敘事曲", "二胡 II"), "維持現狀", "10/12 又存入過", "keep")]
)
UNDO_DOWNLOAD = "豫北敘事曲的本機檔案已清除：先下載 12 份回原位並核對，成功後才動譜庫。"

# 編制清單（撤銷存入與改連共用）：聲部、那次寫的、譜庫現在、撤掉後、類別
INST_UNDO = [
    ("柳琴", "新增聲部", "1 人", "刪掉這個聲部", "del"),
    ("琵琶", "新增聲部", "2 人", "刪掉這個聲部", "del"),
    ("小鈸", "新增聲部", "1 人", "刪掉這個聲部", "del"),
    ("二胡 I", "人數 6–8 → 4–6", "4–6 人", "改回 6–8 人", "restore"),
    ("梆笛", "人數 1 → 2", "2 人", "改回 1 人", "restore"),
    ("二胡 II", "人數 6–8 → 5–8", "5–7 人", "不動：10/12 其他電腦改過", "keep"),
]
RELINK_FROM = "長城隨想 › 劉文金原版 › 第三樂章"
RELINK_TO = "長城隨想 › 劉文金原版 › 第四樂章 烽火操"
INST_RELINK = [
    ("排鼓", "新增聲部", "1 人", "刪掉這個聲部", "del"),
    ("二胡 II", "人數 6–8 → 5–8", "5–8 人", "改回 6–8 人", "restore"),
    ("大提琴", "人數 3–4 → 2–4", "2–4 人", "改回 3–4 人", "restore"),
    ("中胡", "人數 4–6 → 4–8", "4–6 人", "不動：10/09 其他電腦改過", "keep"),
]
INST_COLORS = {"del": RED_FG, "restore": BLUE_FG, "keep": GRAY}

# 清除本機檔案：每份檔的狀態
STATUS = {"ok": ("可清除", GREEN_FG), "diff": ("跟譜庫裡的不一樣，請先重新存入", AMBER_FG),
          "notin": ("還沒存入譜庫", AMBER_FG), "missing": ("譜庫裡沒有", RED_FG)}


def _clear_files(group, voices, special, seed):
    sz = sizes_for(voices + ["總譜"], seed)
    return [(file_name(group, v), special.get(v, "ok"), sz[v]) for v in ["總譜"] + voices]


CLEAR = [
    dict(name="烽火操", files=_clear_files("烽火操", FENGHUO_VOICES, {"大提琴": "diff", "排鼓": "notin"}, 1)),
    dict(name="春江花月夜", files=_clear_files("春江花月夜", CHUNJIANG_VOICES, {"古箏": "missing"}, 2)),
    dict(name="茉莉花", unlinked=True, files=_clear_files("茉莉花", MOLI_VOICES, {v: "notin" for v in MOLI_VOICES + ["總譜"]}, 3)),
    dict(name="豫北敘事曲", cleared=12, files=[]),
]
MISSING_WHY = "Drive 上的檔已在垃圾桶"
CLEAR_FOLDERS = [f"{PROJECT_DIR}/春江花月夜", f"{PROJECT_DIR}/烽火操/打擊"]


def clear_ok(g):
    return [f for f in g["files"] if f[1] == "ok"]


def clear_summary(groups):
    files = [f for g in groups for f in clear_ok(g)]
    return len(files), sum(f[2] for f in files)


# 譜庫錯誤的分類：鍵、模擬控制上的名稱、標題、要做什麼、按鈕
ERRORS = [
    ("offline", "斷網", "連不上網路", "檢查網路後再試一次。", ["重試"]),
    ("relogin", "要重新登入 Google", "Google 登入已失效", "重新登入 Google 後再試一次。", ["重新登入 Google"]),
    ("perm", "沒有權限", "沒有權限", "共用帳號可能改了密碼，或檔案沒有分享給這個帳號。", ["重新登入 Google"]),
    ("notfound", "找不到", "找不到譜庫", "試算表 ID 可能填錯，或資料夾被刪了。", ["開啟譜庫設定"]),
    ("quota", "達到用量上限", "Google 暫時不接受請求", "已達到 Google 的用量上限，過幾分鐘再試。", ["重試"]),
    ("other", "其他", "譜庫讀寫失敗", "發生預期外的錯誤。", ["重試"]),
]
RAW = {
    "offline": "httplib2.error.ServerNotFoundError: Unable to find the server at www.googleapis.com",
    "relogin": "google.auth.exceptions.RefreshError: ('invalid_grant: Token has been expired or revoked.')",
    "perm": "HttpError 403 when requesting …/spreadsheets/1AbC…/values:batchGet returned \"The caller does not have permission\"",
    "notfound": "HttpError 404 when requesting …/spreadsheets/1AbC… returned \"Requested entity was not found.\"",
    "quota": "HttpError 429 when requesting …/spreadsheets/1AbC…:batchUpdate returned \"Quota exceeded for quota metric 'Write requests'\"（已自動重試 5 次）",
    "other": "HttpError 500 when requesting …/spreadsheets/1AbC…:batchUpdate returned \"Internal error encountered.\"",
}


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



def tint(it, col, color, bold=False):
    it.setForeground(col, QColor(color))
    if bold:
        f = it.font(col)
        f.setBold(True)
        it.setFont(col, f)


def fit_height(tbl, rows, row_h=28, cap=None):
    h = 30 + rows * row_h
    tbl.setFixedHeight(min(h, cap) if cap else h)
    return tbl


def menu_bar(*names):
    mb = QMenuBar()
    for name in names:
        m = mb.addMenu(name)
        if name == "說明":
            m.addAction("使用說明")
            m.addAction("開啟紀錄檔資料夾")
            m.addAction("關於泠靈")
    return mb


def main_shell(content_top=None, body=None, status=None):
    """主視窗外框（只放到看得出是主視窗）"""
    page = QWidget()
    v = QVBoxLayout(page)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)
    v.addWidget(menu_bar("檔案", "編輯", "匯入", "譜庫", "發譜", "工具", "檢視", "說明"))
    inner = QWidget()
    bv = QVBoxLayout(inner)
    bv.setContentsMargins(6, 6, 6, 6)
    splitter = QSplitter(Qt.Horizontal)
    left = QListWidget()
    for name in ("梆笛", "曲笛", "高音笙", "中音笙", "高音嗩吶", "…"):
        left.addItem(name)
    left.setMaximumWidth(260)
    splitter.addWidget(left)
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "未分組")
    for name in ("烽火操", "春江花月夜", "茉莉花", "豫北敘事曲"):
        if name == "烽火操":
            holder = vbox_widget(*(x for x in (content_top, body) if x is not None), None, margins=(4, 4, 4, 4))
            tabs.addTab(holder, name)
        else:
            tabs.addTab(label(f"（群組「{name}」的分譜清單）", color=GRAY), name)
    tabs.setCurrentIndex(1)
    splitter.addWidget(tabs)
    splitter.setStretchFactor(1, 1)
    bv.addWidget(splitter, 1)
    bv.addLayout(hbox(label(status or "就緒", color=GRAY), None, btn("預覽重新命名")))
    v.addWidget(inner, 1)
    return page


def catalog_shell(content, tab="存入紀錄", menus=True, top=None):
    """譜庫視窗"""
    tabs = QTabWidget()
    for name in ("曲目", "聲部組對照", "發譜紀錄", "存入紀錄"):
        tabs.addTab(content if name == tab else label(f"（{name}）", color=GRAY), name)
    tabs.setCurrentIndex(["曲目", "聲部組對照", "發譜紀錄", "存入紀錄"].index(tab))
    items = [hbox(label(f"譜庫：{CATALOG}", bold=True), None, label("已登入 Google（社團共用帳號）", color=GRAY))]
    if top is not None:
        items.append(top)
    items.append(tabs)
    body = vbox_widget(*items, margins=(6, 6, 6, 6))
    if not menus:
        return body
    return vbox_widget(menu_bar("檔案", "編輯", "說明"), body)


def proto_toggle(text, slot):
    b = btn(text, proto=True)
    b.setCheckable(True)
    b.toggled.connect(slot)
    return b


# --- 畫面一：存入譜庫預覽 --------------------------------------------------------


class DepositShell(QWidget):
    """存入預覽的外框：未存檔時的提示與「存入譜庫」按鈕；中間由各變體決定"""

    def __init__(self, body, resume_banner=True):
        super().__init__()
        self.unsaved = banner("專案有未存檔的改動，存檔後才能存入。", [btn("存檔")], color=RED_FG, bg=RED_BG)
        self.unsaved.hide()
        self.go = btn("存入譜庫", primary=True)
        items = [hbox(label("已核對：本機檔案與譜庫的內容指紋（MD5）", color=GRAY), None,
                      proto_toggle("模擬：有未存檔的改動", self.set_unsaved)), self.unsaved]
        if resume_banner:
            items.append(banner("上次存入在「春江花月夜」中斷，已存 9 份。這次只存剩下的。"))
        items += [body, hbox(label(deposit_totals(), color=GRAY), None)]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(dialog_page("存入譜庫", items, [btn("取消"), self.go]))

    def set_unsaved(self, on):
        self.unsaved.setVisible(on)
        self.go.setEnabled(not on)


def deposit_a():
    """群組 › 檔案的樹；跳過的收成一行"""
    tw = tree(["群組 › 檔案", "動作", "譜庫裡的位置"])
    for g in DEPOSIT:
        if not g["link"]:
            titem(tw, [g["name"], act_summary(g), ""], fg=GRAY, bold=True)
            continue
        it = titem(tw, [g["name"], act_summary(g), g["link"]], bold=True)
        skips = [f for f in g["files"] if f["act"] in ("skip", "resumed")]
        for f in g["files"]:
            if f in skips:
                continue
            name, color = ACT[f["act"]]
            sub = titem(it, [f["local"], f"{name}" + (f"：{f['why']}" if f["why"] else ""), f"{g['folder']}/{f['local']}"])
            tint(sub, 1, color)
        if skips:
            whys = sorted({f["why"] for f in skips})
            sk = titem(it, [f"另 {len(skips)} 份跳過", "、".join(whys), ""], fg=GRAY)
            for f in skips:
                titem(sk, [f["local"], f["why"], f"{g['folder']}/{f['local']}"], fg=GRAY)
        it.setExpanded(True)
    return DepositShell(tw)


def deposit_b():
    """依動作分段：新上傳、更新、跳過、不存入；跳過的段落預設收起"""
    secs = []

    def section(title, color, rows, headers, collapsed=False):
        tbl = fit_height(table(headers, rows, stretch=len(headers) - 1), len(rows), cap=360)
        head = hbox(label(title, bold=True, color=color), None)
        if collapsed:
            tbl.hide()
            t = btn("顯示", small=True)
            t.clicked.connect(lambda: (tbl.setVisible(not tbl.isVisible()), t.setText("收起" if tbl.isVisible() else "顯示")))
            head.addWidget(t)
        secs.extend([head, tbl])

    def pick(acts):
        return [(g, f) for g in DEPOSIT for f in g["files"] if f["act"] in acts]

    new = pick(("new",))
    section(f"新上傳　{len(new)} 份・{sum(f['size'] for _g, f in new):.1f} MB", GREEN_FG,
            [[g["name"], f["local"], f"{g['folder']}/{f['local']}"] for g, f in new], ["群組", "檔案", "譜庫裡的位置"])
    upd = pick(("update", "move"))
    section(f"更新譜庫裡已登記的那份　{len(upd)} 份", BLUE_FG,
            [[g["name"], f["local"], cell(f["why"], fg=BLUE_FG)] for g, f in upd], ["群組", "檔案", "變動"])
    res = pick(("resumed",))
    section(f"上次中斷前已存入，跳過　{len(res)} 份", GRAY,
            [[g["name"], f["local"], "春江花月夜在 10/11 21:04 那次存到一半"] for g, f in res], ["群組", "檔案", ""], True)
    same = pick(("skip",))
    section(f"內容相同，跳過　{len(same)} 份", GRAY,
            [[g["name"], f["local"], ""] for g, f in same], ["群組", "檔案", ""], True)
    secs.append(label("不存入：茉莉花（沒連到譜庫）", bold=True, color=GRAY))
    secs.append(None)
    return DepositShell(scroll(vbox_widget(*secs, spacing=8)), resume_banner=False)


def deposit_c():
    """主從：左邊群組與摘要，右邊本機 → 譜庫的對照"""
    lst = QListWidget()
    lst.setMaximumWidth(300)
    stack = QStackedWidget()
    for g in DEPOSIT:
        QListWidgetItem(f"{g['name']}\n{act_summary(g)}", lst)
        if not g["link"]:
            stack.addWidget(vbox_widget(label("這個群組沒連到譜庫，不存入。", color=GRAY),
                                        hbox(btn("連到譜庫…"), None), None))
            continue
        rows = []
        for f in g["files"]:
            name, color = ACT[f["act"]]
            dim = f["act"] in ("skip", "resumed")
            rows.append([cell(f["voice"], fg=GRAY if dim else None), cell(f["local"], fg=GRAY if dim else None),
                         cell("→", fg=GRAY, center=True), cell(f"{g['folder']}/{f['local']}", fg=GRAY if dim else None),
                         cell(name + (f"（{f['why']}）" if f["why"] else ""), fg=color)])
        tbl = table(["聲部", f"本機（{PROJECT_DIR}/{g['name']}）", "", "譜庫（Drive）", "動作"], rows, stretch=3)
        only = check("只看要上傳或搬移的", True)

        def filt(on, tbl=tbl, files=g["files"]):
            for r, f in enumerate(files):
                tbl.setRowHidden(r, on and f["act"] in ("skip", "resumed"))
        only.toggled.connect(filt)
        filt(True)
        stack.addWidget(vbox_widget(hbox(label(g["link"], bold=True), None, only), tbl))
    lst.currentRowChanged.connect(stack.setCurrentIndex)
    lst.setCurrentRow(0)
    split = QSplitter(Qt.Horizontal)
    split.addWidget(lst)
    split.addWidget(stack)
    split.setStretchFactor(1, 1)
    return DepositShell(split)


# --- 共用：編制清單（撤銷存入與改連用同一套）----------------------------------------


def inst_table(rows, place):
    tbl = table(["聲部", "那次寫的", "譜庫現在", "撤掉後"],
                [[r[0], cell(r[1], fg=GRAY), r[2], cell(r[3], fg=INST_COLORS[r[4]])] for r in rows], stretch=3)
    fit_height(tbl, len(rows))
    return tbl


def inst_block(rows, place, checked=False, text=None):
    """勾選框＋編制清單；不勾時清單變暗"""
    n = sum(1 for r in rows if r[4] != "keep")
    box = check(text or f"一起撤掉這個群組寫進「{place.split(' › ')[-1]}」的編制（{n} 項）", checked)
    tbl = inst_table(rows, place)
    tbl.setEnabled(checked)
    box.toggled.connect(tbl.setEnabled)
    return vbox_widget(box, tbl, None)


def inst_sentence(rows):
    dels = [r[0] for r in rows if r[4] == "del"]
    res = [f"{r[0]} {r[3].replace('改回 ', '')}" for r in rows if r[4] == "restore"]
    keep = [f"{r[0]}（{r[3].replace('不動：', '')}）" for r in rows if r[4] == "keep"]
    parts = []
    if dels:
        parts.append(f"刪掉 {len(dels)} 個那次新增的聲部：{'、'.join(dels)}")
    if res:
        parts.append(f"人數改回：{'、'.join(res)}")
    if keep:
        parts.append(f"不動：{'、'.join(keep)}")
    return "；".join(parts) + "。"


# --- 畫面二：撤銷存入 ------------------------------------------------------------


def undo_kind(key):
    return next(k for k in UNDO_KINDS if k[0] == key)


def undo_a():
    """存入紀錄 → 撤銷對話框：左邊勾群組，右邊逐聲部「現在 → 撤銷後 → 原因」，下面是編制"""
    picks = []
    for name, place in UNDO_GROUPS:
        picks += [check(name, name == "豫北敘事曲"), label(place, color=GRAY)]
    groups = vbox_widget(*picks, None, spacing=4)
    groups.setMaximumWidth(330)
    strip = QHBoxLayout()
    for key, name, fg, bg in UNDO_KINDS:
        n = sum(1 for r in UNDO_FILES if r[4] == key)
        strip.addWidget(label(f"{name} {n}", color=fg, bg=bg or "#24262f"))
    strip.addStretch()
    rows = []
    for voice, now, after, why, key in UNDO_FILES:
        _k, name, fg, bg = undo_kind(key)
        rows.append([cell(voice), cell(now), cell(after, fg=fg), cell(f"{name}：{why}", fg=fg)])
    tbl = table(["聲部", "現在譜庫裡", "撤銷後", "原因"], rows, stretch=3)
    right = vbox_widget(label(f"豫北敘事曲 → {UNDO_PLACE}", bold=True), strip,
                        banner(UNDO_DOWNLOAD, color=BLUE_FG, bg=BLUE_BG), tbl,
                        inst_block(INST_UNDO, UNDO_PLACE))
    split = QSplitter(Qt.Horizontal)
    split.addWidget(vbox_widget(label("勾選要撤銷的群組", color=GRAY), groups))
    split.addWidget(right)
    split.setStretchFactor(1, 1)
    return dialog_page(f"撤銷存入：{DEPOSIT_LOG[0][0]}（{UNDO_WHO}）",
                       [split, label("移除的檔放進 Drive 垃圾桶，30 天內可救回。", color=GRAY)],
                       [btn("取消"), btn("撤銷存入", danger=True)])


def undo_b():
    """一欄到底：上面一排勾群組，下面依結果分段，每段一句白話標題"""
    picks = QHBoxLayout()
    picks.addWidget(label("撤銷哪些群組：", color=GRAY))
    for name, place in UNDO_GROUPS:
        picks.addWidget(check(f"{name}（{place.split(' › ')[-1]}）", name == "豫北敘事曲"))
    picks.addStretch()
    secs = [picks, label("先下載回本機", bold=True, color=BLUE_FG), label(UNDO_DOWNLOAD, wrap=True)]
    titles = {"remove": "丟進 Drive 垃圾桶：那次新上傳的", "restore": "改回存入前的內容：那次蓋掉了原本的檔",
              "lost": "無法還原：舊內容已被 Drive 刪除，維持現狀", "keep": "不動：之後又存入過"}
    for key, _name, fg, _bg in UNDO_KINDS:
        files = [r for r in UNDO_FILES if r[4] == key]
        secs.append(label(f"{titles[key]}（{len(files)} 份）", bold=True, color=fg))
        secs.append(label("、".join(r[1] for r in files), wrap=True))
    box = check(f"一起撤掉這個群組寫進「第三樂章」的編制")
    sent = label(inst_sentence(INST_UNDO), wrap=True, color=GRAY)
    box.toggled.connect(lambda on: sent.setStyleSheet("" if on else f"color: {GRAY};"))
    secs += [label("編制", bold=True), box, sent, None]
    return dialog_page(f"撤銷存入：{DEPOSIT_LOG[0][0]}（{UNDO_WHO}）", [scroll(vbox_widget(*secs, spacing=8))],
                       [btn("取消"), btn("撤銷存入", danger=True)], width=1000)


def undo_c():
    """不開對話框：存入紀錄分頁是一棵樹（紀錄 › 群組 › 檔案），右邊是摘要與編制"""
    tw = tree(["存入紀錄 › 群組 › 聲部", "撤銷後", "原因"])
    for i, (when, kind, what, n) in enumerate(DEPOSIT_LOG):
        top = titem(tw, [f"{when}　{kind}　{what}（{n}，{UNDO_WHO}）", "", ""],
                    fg=GRAY if kind != "存入" else None, bold=i == 0)
        top.setFirstColumnSpanned(True)
        if i != 0:
            continue
        for name, place in UNDO_GROUPS:
            g = titem(top, [f"{name} → {place}", "", ""])
            g.setFirstColumnSpanned(True)
            g.setFlags(g.flags() | Qt.ItemIsUserCheckable)
            g.setCheckState(0, Qt.Checked if name == "豫北敘事曲" else Qt.Unchecked)
            if name != "豫北敘事曲":
                titem(g, ["（展開看各聲部）", "", ""], fg=GRAY)
                continue
            for voice, now, after, why, key in UNDO_FILES:
                _k, kname, fg, _bg = undo_kind(key)
                titem(g, [f"{voice}　{now}", after, f"{kname}：{why}"], fg=fg)
            g.setExpanded(True)
        top.setExpanded(True)
    tw.header().setSectionResizeMode(0, QHeaderView.Interactive)
    tw.setColumnWidth(0, 300)
    counts = [label("勾了 1 個群組：豫北敘事曲", bold=True)]
    for key, name, fg, _bg in UNDO_KINDS:
        counts.append(label(f"{name}　{sum(1 for r in UNDO_FILES if r[4] == key)} 份", color=fg))
    side = vbox_widget(*counts, label(UNDO_DOWNLOAD, wrap=True, color=BLUE_FG), label("編制", bold=True),
                       inst_block(INST_UNDO, UNDO_PLACE), None, hbox(None, btn("撤銷勾選的群組", danger=True)))
    side.setMinimumWidth(460)
    split = QSplitter(Qt.Horizontal)
    split.addWidget(tw)
    split.addWidget(side)
    split.setStretchFactor(0, 1)
    return catalog_shell(split)


# --- 畫面三：改連時的編制清單 ------------------------------------------------------


def relink_a():
    """改連對話框裡直接列出：選新的連線，下面是原本那處留下的編制，預設勾選"""
    pick = hbox(label("曲目"), combo(["長城隨想（劉文金）"]), label("版本"), combo(["劉文金原版"]),
                label("樂章"), combo(["第三樂章（現在的連線）", "第四樂章 烽火操"], 1), None)
    return dialog_page("改連：烽火操", [
        pick,
        label(f"原本的「{RELINK_FROM}」有這個群組寫的編制，之後沒人改過：", wrap=True),
        inst_block(INST_RELINK, RELINK_FROM, checked=True, text="一起撤掉（存檔時寫入譜庫）"),
        label("按「改連」後照常進入連線的差異表。", color=GRAY), None,
    ], [btn("取消"), btn("改連", primary=True)], width=1000)


def relink_b():
    """改連完成後另跳一個確認框，兩個按鈕二選一"""
    n = sum(1 for r in INST_RELINK if r[4] != "keep")
    return dialog_page(f"要撤掉原本那處的 {n} 項編制嗎？", [
        label(f"烽火操已改連到「{RELINK_TO}」。原本的「{RELINK_FROM}」還留著這個群組寫的編制：", wrap=True),
        inst_table(INST_RELINK, RELINK_FROM),
        label("撤掉的話，存檔時寫入譜庫。", color=GRAY),
    ], [btn("留著"), btn("撤掉", danger=True)], width=900, grow=False)


def relink_c():
    """不跳框：群組頂端提示列，按「查看」展開清單"""
    n = sum(1 for r in INST_RELINK if r[4] != "keep")
    tbl = inst_table(INST_RELINK, RELINK_FROM)
    tbl.hide()
    look = btn("查看")
    look.clicked.connect(lambda: tbl.setVisible(not tbl.isVisible()))
    top = vbox_widget(banner(f"改連後，原本的「第三樂章」還留著這個群組寫的 {n} 項編制。",
                             [look, btn("撤掉（存檔時寫入）"), btn("留著")]), tbl)
    top.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
    return main_shell(content_top=top, body=label(f"（編制表：已連到 {RELINK_TO}）", color=GRAY),
                      status="未存檔")


# --- 畫面四：清除本機檔案預覽 ------------------------------------------------------


class ClearShell(QWidget):
    """清除預覽的外框：底部合計與「清除…」；中間由各變體決定，勾選變動時呼叫 update"""

    def __init__(self, body, chosen):
        super().__init__()
        self._chosen = chosen
        self.total = label("", color=AMBER_FG)
        go = btn("清除…", danger=True, slot=lambda: popup(self, "清除本機檔案", confirm_a(), (720, 320)))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(dialog_page("清除本機檔案", [
            label("已核對整個專案：本機檔案與譜庫的內容指紋（MD5）一致才能清除。", color=GRAY),
            body, self.total], [btn("取消"), go]))
        self.update_total()

    def update_total(self, *_):
        n, mb = clear_summary(self._chosen())
        self.total.setText(f"勾了 {len(self._chosen())} 個群組：永久刪除 {n} 份（{mb:.1f} MB），其餘的留在本機")


def clear_a():
    """群組 › 檔案的樹：群組可勾，不能清除的排前面，可清除的收成一行"""
    tw = tree(["群組 › 檔案", "狀態", "大小"])
    items = []
    for g in CLEAR:
        if g.get("cleared"):
            titem(tw, [g["name"], f"已清除，只在譜庫（{g['cleared']} 份）", ""], fg=GRAY, bold=True)
            continue
        ok = clear_ok(g)
        it = titem(tw, [g["name"], "沒連到譜庫" if g.get("unlinked") else f"可清除 {len(ok)}／{len(g['files'])} 份",
                        f"{sum(f[2] for f in ok):.1f} MB" if ok else ""], bold=True)
        if ok:
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked)
            items.append((it, g))
        else:
            tint(it, 0, GRAY)
        bad = [f for f in g["files"] if f[1] != "ok"]
        for name, st, size in bad[:6]:
            text, color = STATUS[st]
            sub = titem(it, [name, text + (f"（{MISSING_WHY}）" if st == "missing" else ""), f"{size} MB"])
            tint(sub, 1, color)
        if len(bad) > 6:
            titem(it, [f"…另 {len(bad) - 6} 份", "", ""], fg=GRAY)
        if ok:
            o = titem(it, [f"可清除 {len(ok)} 份", "", ""], fg=GREEN_FG)
            for name, _st, size in ok:
                titem(o, [name, "可清除", f"{size} MB"], fg=GRAY)
        it.setExpanded(not g.get("unlinked"))
    shell = ClearShell(tw, lambda: [g for it, g in items if it.checkState(0) == Qt.Checked])
    tw.itemChanged.connect(shell.update_total)
    return shell


def clear_b():
    """依狀態分段：可以清除（每群組一列可勾）／不能清除（逐份列原因與處理按鈕）／已清除"""
    boxes = []
    rows = []
    for g in CLEAR:
        ok = clear_ok(g)
        if not ok:
            continue
        b = check(g["name"], True)
        boxes.append((b, g))
        rest = len(g["files"]) - len(ok)
        rows.append([b, f"{len(ok)} 份", f"{sum(f[2] for f in ok):.1f} MB",
                     cell(f"其餘 {rest} 份不能清除，見下方" if rest else "", fg=GRAY)])
    can = fit_height(table(["群組", "可清除", "大小", ""], rows, stretch=3), len(rows), row_h=38)
    bad_rows = []
    for g in CLEAR:
        if g.get("unlinked"):
            bad_rows.append([g["name"], cell(f"整個群組 {len(g['files'])} 份", fg=GRAY),
                             cell("沒連到譜庫，還沒存入", fg=AMBER_FG), btn("連到譜庫…", small=True)])
            continue
        for name, st, _size in g["files"]:
            if st == "ok":
                continue
            text, color = STATUS[st]
            act = btn("存入譜庫…", small=True) if st in ("diff", "notin") else btn("開啟譜庫視窗", small=True)
            bad_rows.append([g["name"], name, cell(text + (f"（{MISSING_WHY}）" if st == "missing" else ""), fg=color), act])
    bad = fit_height(table(["群組", "檔案", "原因", ""], bad_rows, stretch=2), len(bad_rows), row_h=38)
    done = [g for g in CLEAR if g.get("cleared")]
    body = scroll(vbox_widget(
        label("可以清除", bold=True, color=GREEN_FG), can,
        label("不能清除，會留在本機", bold=True, color=AMBER_FG), bad,
        label("已清除，只在譜庫：" + "、".join(f"{g['name']}（{g['cleared']} 份）" for g in done), color=GRAY),
        None, spacing=8))
    shell = ClearShell(body, lambda: [g for b, g in boxes if b.isChecked()])
    for b, _g in boxes:
        b.toggled.connect(shell.update_total)
    return shell


def clear_c():
    """群組總表（每種狀態一欄）＋下方明細，預設只看不能清除的"""
    groups = [g for g in CLEAR if not g.get("cleared")]
    boxes = []
    rows = []
    for g in groups:
        c = {k: sum(1 for f in g["files"] if f[1] == k) for k in STATUS}
        b = check("", c["ok"] > 0)
        b.setEnabled(c["ok"] > 0)
        boxes.append((b, g))
        ok = clear_ok(g)
        rows.append([b, cell(g["name"], bold=True), cell(c["ok"] or "", fg=GREEN_FG, center=True),
                     cell(c["diff"] or "", fg=AMBER_FG, center=True), cell(c["notin"] or "", fg=AMBER_FG, center=True),
                     cell(c["missing"] or "", fg=RED_FG, center=True), f"{sum(f[2] for f in ok):.1f} MB" if ok else ""])
    rows.append([None, cell("豫北敘事曲", fg=GRAY), cell("已清除，只在譜庫", fg=GRAY), None, None, None, None])
    summary = fit_height(table(["", "群組", "可清除", "跟譜庫不一樣", "還沒存入", "譜庫裡沒有", "可釋放"], rows, stretch=1),
                         len(rows), row_h=39)
    summary.setSelectionBehavior(QAbstractItemView.SelectRows)
    detail_title = label("", bold=True)
    only = check("只看不能清除的", True)
    detail = table(["檔案", "狀態", "大小"], [], stretch=1)

    def show(r):
        if r >= len(groups):
            return
        g = groups[r]
        files = [f for f in g["files"] if not (only.isChecked() and f[1] == "ok")]
        detail.setRowCount(len(files))
        for i, (name, st, size) in enumerate(files):
            text, color = STATUS[st]
            detail.setItem(i, 0, cell(name))
            detail.setItem(i, 1, cell(text + (f"（{MISSING_WHY}）" if st == "missing" else ""), fg=color))
            detail.setItem(i, 2, cell(f"{size} MB"))
        detail_title.setText(f"{g['name']}　{len(files)} 份")
    summary.currentCellChanged.connect(lambda r, *_: show(r))
    only.toggled.connect(lambda _on: show(max(summary.currentRow(), 0)))
    summary.selectRow(0)
    show(0)
    body = vbox_widget(summary, hbox(detail_title, None, only), detail)
    shell = ClearShell(body, lambda: [g for b, g in boxes if b.isChecked()])
    for b, _g in boxes:
        b.toggled.connect(shell.update_total)
    return shell


# --- 畫面五：清除的確認框 ----------------------------------------------------------


def _confirm_numbers():
    groups = [g for g in CLEAR if clear_ok(g)]
    n, mb = clear_summary(groups)
    return groups, n, mb


def confirm_a():
    """一般確認框：一句結果、一句怎麼找回"""
    groups, n, mb = _confirm_numbers()
    return dialog_page(f"永久刪除 {n} 份檔案？", [
        label(f"{'、'.join(g['name'] for g in groups)} 的 {n} 份（{mb:.1f} MB）會永久刪除，不進資源回收桶。"
              f"清空的資料夾一併刪除。", wrap=True),
        label("要找回，在群組裡按「從譜庫下載回來」。", wrap=True),
    ], [btn("取消"), btn("永久刪除", danger=True)], width=640, grow=False)


def confirm_b():
    """列出會刪的資料夾，勾「我知道這是永久刪除」才能按"""
    groups, n, mb = _confirm_numbers()
    go = btn(f"永久刪除 {n} 份", danger=True)
    go.setEnabled(False)
    know = check("我知道這是永久刪除，不進資源回收桶")
    know.toggled.connect(go.setEnabled)
    folders = QListWidget()
    for f in CLEAR_FOLDERS:
        folders.addItem(f)
    folders.setFixedHeight(84)
    return dialog_page("清除本機檔案", [
        label(f"永久刪除 {'、'.join(g['name'] for g in groups)} 的 {n} 份（{mb:.1f} MB）。", wrap=True, bold=True),
        label("清空後一併刪除的資料夾：", color=GRAY), folders,
        label("要找回，在群組裡按「從譜庫下載回來」。", wrap=True),
        know,
    ], [btn("取消"), go], width=760, grow=False)


def confirm_c():
    """不另開對話框：清除預覽底部的按鈕按兩次，第二次才刪"""
    groups, n, mb = _confirm_numbers()
    warn = label("", color=RED_FG, wrap=True)
    go = btn(f"清除 {n} 份…", danger=True)
    state = {"armed": False}

    def press():
        if state["armed"]:
            warn.setText("（原型：這裡會開始刪除）")
            return
        state["armed"] = True
        go.setText("再按一次：永久刪除")
        warn.setText(f"{n} 份（{mb:.1f} MB）會永久刪除，不進資源回收桶。要找回，在群組裡按「從譜庫下載回來」。")
    go.clicked.connect(press)
    reset = btn("取消", slot=lambda: (state.update(armed=False), go.setText(f"清除 {n} 份…"), warn.setText("")))
    return dialog_page("清除本機檔案", [label("（清除預覽，見上一個畫面）", color=GRAY, italic=True), None, warn],
                       [reset, go])


# --- 畫面六：譜庫的錯誤訊息 --------------------------------------------------------


class ErrorDemo(QWidget):
    """上方黃色下拉選單切換錯誤類別（模擬控制），下方依變體呈現"""

    def __init__(self, build):
        super().__init__()
        self._build = build
        self._page = None
        pick = QComboBox()
        pick.addItems([f"模擬：{e[1]}" for e in ERRORS])
        pick.setStyleSheet(PROTO)
        pick.currentIndexChanged.connect(self.render)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.addLayout(hbox(None, pick, margins=(6, 4, 6, 0)))
        self.render(0)

    def render(self, i):
        if self._page is not None:
            self._page.setParent(None)
            self._page.deleteLater()
        self._page = self._build(ERRORS[i])
        self._lay.addWidget(self._page, 1)


DEPOSIT_OP = "存入譜庫：春江花月夜，已存 9／15 份。重按「存入譜庫」會補齊。"


def details_box(err):
    key = err[0]
    text = QTextEdit()
    text.setReadOnly(True)
    text.setPlainText(f"2026-10-12 21:16:05　存入譜庫　春江花月夜_中阮.pdf\n{RAW[key]}")
    text.setFixedHeight(80)
    text.hide()
    toggle = btn("詳細資料", small=True)
    toggle.clicked.connect(lambda: text.setVisible(not text.isVisible()))
    return toggle, text


def error_a():
    """對話框：標題是類別，一句怎麼辦，詳細資料可展開；「開啟紀錄檔資料夾」在左下"""
    def build(err):
        key, _n, title, hint, actions = err
        toggle, text = details_box(err)
        return dialog_page(title, [label(DEPOSIT_OP, color=GRAY), label(hint),
                                   hbox(toggle, None), text],
                           [btn("開啟紀錄檔資料夾"), None, *[btn(a, primary=True) for a in actions], btn("關閉")],
                           width=640, grow=False)
    return ErrorDemo(build)


def error_b():
    """就地的提示列：失敗的地方（這裡是譜庫視窗）頂端紅色一列；紀錄檔只在「說明」選單"""
    def build(err):
        key, _n, title, hint, actions = err
        top = banner(f"讀不到譜庫｜{title}：{hint}", [btn(a) for a in actions] + [btn("詳細資料")],
                     color=RED_FG, bg=RED_BG)
        empty = vbox_widget(None, label("曲目清單讀不到，不是譜庫裡沒有曲目。", color=GRAY), None)
        lay = QHBoxLayout()
        lay.addStretch()
        lay.addWidget(empty)
        lay.addStretch()
        holder = QWidget()
        holder.setLayout(lay)
        return catalog_shell(holder, tab="曲目", top=top)
    return ErrorDemo(build)


def error_c():
    """結果清單：批次動作做完列出每份的結果，失敗的寫類別；底部「開啟紀錄檔資料夾」"""
    def build(err):
        key, _n, title, hint, actions = err
        g = DEPOSIT[1]
        rows = []
        for i, f in enumerate(g["files"]):
            if i < 9 or f["act"] == "resumed":
                rows.append([g["name"], f["local"], cell("已存入", fg=GREEN_FG)])
            else:
                rows.append([g["name"], f["local"], cell(f"沒存入：{title}", fg=RED_FG)])
        failed = sum(1 for r in rows if r[2].text().startswith("沒存入"))
        tbl = table(["群組", "檔案", "結果"], rows, stretch=2)
        return dialog_page("存入沒完成", [
            banner(f"{len(rows) - failed} 份已存入，{failed} 份沒存入。{title}：{hint}", color=RED_FG, bg=RED_BG),
            tbl, label("重按「存入譜庫」只存沒存入的。", color=GRAY)],
            [btn("開啟紀錄檔資料夾"), None, *[btn(a, primary=True) for a in actions], btn("關閉")])
    return ErrorDemo(build)


# --- 畫面清單與切換列 -----------------------------------------------------------

SCREENS = [
    ("deposit", "存入譜庫預覽", "烽火操：新上傳 2、更新 2（其一改名）、搬移 1；春江花月夜：上次中斷已存 9 份；"
                            "茉莉花沒連到譜庫。黃色按鈕模擬未存檔", [
        ("A", "樹：群組 › 檔案，跳過的收成一行", deposit_a),
        ("B", "依動作分段：新上傳、更新、跳過（收起）、不存入", deposit_b),
        ("C", "主從：左邊群組摘要，右邊本機 → 譜庫對照", deposit_c),
    ]),
    ("undo", "撤銷存入", "10/11 那次把豫北敘事曲連錯到長城隨想的第三樂章；本機已清除；撤銷這個群組", [
        ("A", "對話框：左邊勾群組，右邊逐聲部「現在 → 撤銷後 → 原因」，下面是編制表", undo_a),
        ("B", "對話框：一欄到底，依結果分段的白話標題，編制寫成一句話", undo_b),
        ("C", "不開對話框：存入紀錄分頁是一棵樹，右邊摘要", undo_c),
    ]),
    ("relink", "改連時的編制清單", "烽火操從第三樂章改連到第四樂章；第三樂章留著這個群組寫的 3 項編制，另 1 項別人改過", [
        ("A", "改連對話框裡直接列出，預設勾選「一起撤掉」", relink_a),
        ("B", "改連後另跳確認框：留著／撤掉", relink_b),
        ("C", "不跳框：群組頂端提示列，按「查看」展開", relink_c),
    ]),
    ("clear", "清除本機檔案預覽", "烽火操 2 份不能清除、春江花月夜 1 份譜庫裡沒有、茉莉花沒連到譜庫、豫北敘事曲已清除", [
        ("A", "樹：群組可勾，不能清除的排前面", clear_a),
        ("B", "依狀態分段：可以清除／不能清除（附處理按鈕）／已清除", clear_b),
        ("C", "群組總表（每種狀態一欄）＋下方明細", clear_c),
    ]),
    ("confirm", "清除的確認框", "清除烽火操、春江花月夜的可清除檔案", [
        ("A", "一般確認框", confirm_a),
        ("B", "列出會刪的資料夾，勾「我知道」才能按", confirm_b),
        ("C", "不另開框：按鈕按兩次", confirm_c),
    ]),
    ("error", "譜庫的錯誤訊息", "黃色下拉選單切換六種錯誤類別", [
        ("A", "對話框：類別標題＋一句怎麼辦，紀錄檔在左下", error_a),
        ("B", "就地提示列：失敗的視窗頂端紅色一列，紀錄檔只在「說明」選單", error_b),
        ("C", "結果清單：逐份列結果，底部開紀錄檔", error_c),
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
        self.setWindowTitle(f"PROTOTYPE 存入、撤銷存入與清除畫面 — {name} {vkey}")
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
    parser = argparse.ArgumentParser(description="存入、撤銷存入與清除本機檔案畫面的原型")
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
        os._exit(0)  # PySide6 6.12 在 Python 3.14 關閉時清理物件會崩潰，截完圖直接結束
    keys = ArrowKeys(host)
    app.installEventFilter(keys)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

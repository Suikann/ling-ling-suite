# -*- coding: utf-8 -*-
"""
常數定義

定義模板變數、預設值與應用程式路徑。
"""
import os
from dataclasses import dataclass
from typing import List, Tuple

APP_NAME = "LingLingSuite"
APP_DISPLAY_NAME = "泠靈小工具"
APP_VERSION = "1.0.0"
APPDATA_DIR = os.path.join(os.environ.get("APPDATA", ""), APP_NAME)
UNDO_DIR = os.path.join(APPDATA_DIR, "undo")
REDO_DIR = os.path.join(APPDATA_DIR, "redo")
BACKUP_DIR = os.path.join(APPDATA_DIR, "backups")
PROJECT_EXTENSION = ".llproj"
DEFAULT_MASTER_TEMPLATE = "{序號}-{曲名}-{樂器}.pdf"
DEFAULT_MASTER_TEMPLATE_EN = "{Number}-{PieceName}-{Instrument}.pdf"
DEFAULT_SUBFOLDER_TEMPLATE = "{曲名} - 第{樂章編號}樂章"
DEFAULT_SUBFOLDER_TEMPLATE_EN = "{PieceName} - Movement {MovementNum}"


@dataclass(frozen=True)
class TemplateVariable:
    """模板變數定義"""
    name: str
    name_en: str
    level: str
    description: str


TEMPLATE_VARIABLES: List[TemplateVariable] = [
    TemplateVariable("序號", "Number", "逐檔不同", "樂器在樂器表中的位置，自動產生，零填充"),
    TemplateVariable("樂器", "Instrument", "逐檔不同", "樂器表，依排序對應"),
    TemplateVariable("曲名", "PieceName", "群組層級", "從檔名共同部分自動偵測，使用者可覆寫"),
    TemplateVariable("樂章編號", "MovementNum", "群組層級", "使用者輸入"),
    TemplateVariable("樂章名稱", "MovementName", "群組層級", "使用者輸入"),
    TemplateVariable("作曲家", "Composer", "群組層級", "使用者輸入"),
    TemplateVariable("曲種", "Genre", "群組層級", "使用者輸入，例如交響曲、協奏曲"),
]

VARIABLE_NAMES: List[str] = [v.name for v in TEMPLATE_VARIABLES]
VARIABLE_NAMES_EN: List[str] = [v.name_en for v in TEMPLATE_VARIABLES]
ALL_VARIABLE_NAMES: List[str] = VARIABLE_NAMES + VARIABLE_NAMES_EN


@dataclass(frozen=True)
class InstrumentPreset:
    """預設編制表"""
    name: str
    name_en: str
    instruments: tuple


INSTRUMENT_PRESETS: List[InstrumentPreset] = [
    InstrumentPreset(
        name="管弦樂團",
        name_en="Orchestra",
        instruments=(
            "Flute 1", "Flute 2",
            "Oboe 1", "Oboe 2",
            "Clarinet in Bb 1", "Clarinet in Bb 2",
            "Bassoon 1", "Bassoon 2",
            "Horn in F 1", "Horn in F 2", "Horn in F 3", "Horn in F 4",
            "Trumpet in Bb 1", "Trumpet in Bb 2",
            "Trombone 1", "Trombone 2", "Bass Trombone",
            "Tuba",
            "Timpani",
            "Percussion",
            "Violin I", "Violin II",
            "Viola",
            "Violoncello",
            "Contrabass",
        ),
    ),
    InstrumentPreset(
        name="管樂團",
        name_en="Concert Band",
        instruments=(
            "Piccolo",
            "Flute 1", "Flute 2",
            "Oboe",
            "English Horn",
            "Clarinet in Bb 1", "Clarinet in Bb 2", "Clarinet in Bb 3",
            "Bass Clarinet",
            "Bassoon",
            "Alto Saxophone 1", "Alto Saxophone 2",
            "Tenor Saxophone",
            "Baritone Saxophone",
            "Trumpet in Bb 1", "Trumpet in Bb 2", "Trumpet in Bb 3",
            "Horn in F 1", "Horn in F 2", "Horn in F 3", "Horn in F 4",
            "Trombone 1", "Trombone 2", "Bass Trombone",
            "Euphonium",
            "Tuba",
            "String Bass",
            "Timpani",
            "Percussion 1", "Percussion 2",
        ),
    ),
    InstrumentPreset(
        name="弦樂團",
        name_en="String Orchestra",
        instruments=(
            "Violin I", "Violin II",
            "Viola",
            "Violoncello",
            "Contrabass",
        ),
    ),
    InstrumentPreset(
        name="國樂合奏",
        name_en="Chinese Orchestra",
        instruments=(
            # 吹管
            "梆笛", "曲笛", "新笛",
            "高音笙", "中音笙", "低音笙",
            "高音嗩吶", "中音嗩吶", "次中音嗩吶", "低音嗩吶",
            # 彈撥
            "柳琴",
            "琵琶",
            "中阮", "大阮",
            "揚琴",
            "古箏",
            # 打擊
            "打擊 I", "打擊 II",
            # 弦樂
            "高胡",
            "二胡 I", "二胡 II",
            "中胡",
            "大提琴",
            "低音提琴",
        ),
    ),
    InstrumentPreset(
        name="絲竹室內樂",
        name_en="Silk and Bamboo Ensemble",
        instruments=(
            # 吹管
            "曲笛", "簫", "笙",
            # 彈撥
            "琵琶", "中阮", "揚琴", "古箏",
            # 弦樂
            "二胡", "中胡",
        ),
    ),
]

SECTION_KEYWORDS: List[Tuple[str, str, List[str]]] = [
    ("吹管", "Winds", [
        "笛", "曲笛", "梆笛", "新笛", "簫", "笙", "嗩吶", "管子",
    ]),
    ("彈撥", "Plucked Strings", [
        "琵琶", "阮", "柳琴", "揚琴", "箏", "三弦",
    ]),
    ("木管", "Woodwinds", [
        "flute", "piccolo", "oboe", "english horn", "cor anglais",
        "clarinet", "bassoon", "contrabassoon", "saxophone", "sax",
    ]),
    ("銅管", "Brass", [
        "horn", "trumpet", "cornet", "trombone", "tuba", "euphonium",
    ]),
    ("打擊", "Percussion", [
        "timpani", "percussion", "xylophone", "marimba", "vibraphone",
        "glockenspiel", "drum", "打擊", "鼓", "鑼", "鈸",
    ]),
    ("鍵盤", "Keyboard", [
        "piano", "keyboard", "organ", "celesta", "harpsichord", "harp",
    ]),
    ("弦樂", "Strings", [
        "violin", "viola", "violoncello", "cello", "contrabass",
        "double bass", "string bass", "胡", "高胡", "二胡", "中胡",
        "大提琴", "低音提琴",
    ]),
]

SECTION_NAMES_EN = {
    section_zh: section_en
    for section_zh, section_en, _ in SECTION_KEYWORDS
}

DEFAULT_HEADCOUNT = 1


def detect_instrument_section(instrument_name: str) -> str:
    """根據樂器名稱自動偵測所屬聲部組

    Args:
        instrument_name: 樂器名稱

    Returns:
        聲部組名稱（中文），偵測不到時回傳「其他」
    """
    name_lower = instrument_name.lower()
    for section_zh, _section_en, keywords in SECTION_KEYWORDS:
        for kw in keywords:
            if kw.lower() in name_lower:
                return section_zh
    return "其他"

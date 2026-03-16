# -*- coding: utf-8 -*-
"""
常數定義

定義模板變數、預設值與應用程式路徑。
"""
import os
from dataclasses import dataclass
from typing import List

APP_NAME = "LingLingSuite"
APP_DISPLAY_NAME = "泠靈小工具"
APP_VERSION = "1.0.0"
APPDATA_DIR = os.path.join(os.environ.get("APPDATA", ""), APP_NAME)
UNDO_DIR = os.path.join(APPDATA_DIR, "undo")
PROJECT_EXTENSION = ".llproj"
DEFAULT_MASTER_TEMPLATE = "{序號}. {樂器} - {曲名}.pdf"
DEFAULT_MASTER_TEMPLATE_EN = "{Number}. {Instrument} - {PieceName}.pdf"
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
            "梆笛", "曲笛", "新笛",
            "高音笙", "中音笙",
            "高音嗩吶", "中音嗩吶",
            "高胡", "二胡 I", "二胡 II",
            "中胡",
            "琵琶",
            "中阮", "大阮",
            "古箏",
            "揚琴",
            "大提琴",
            "低音提琴",
            "打擊",
        ),
    ),
    InstrumentPreset(
        name="絲竹室內樂",
        name_en="Silk and Bamboo Ensemble",
        instruments=(
            "曲笛",
            "簫",
            "笙",
            "二胡",
            "中胡",
            "琵琶",
            "中阮",
            "揚琴",
            "古箏",
        ),
    ),
]

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

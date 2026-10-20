# -*- coding: utf-8 -*-
"""
紀錄檔的一筆

譜庫的讀寫失敗與重要動作（存入譜庫、撤銷存入、清除本機檔案、下載、發譜）在紀錄檔各寫一筆。
一筆只有固定的欄位：時間、動作、曲名、檔名、路徑、錯誤類別與原始訊息，沒有放其他資料的地方，
所以權杖、密碼與 HuaXia 傳回的團員資料不會被寫進去；原始訊息裡看起來像權杖或密碼的部分換成 ***。
提示列的「詳細資料」與紀錄檔用同一個 text()，兩邊讀起來一樣。

使用範例：
    from core.log_entry import LogEntry
    entry = LogEntry("存入譜庫", piece_title="春江花月夜", file_name="春江花月夜_中阮.pdf")
    entry.text()
"""
import datetime
import re
from dataclasses import dataclass, field

# 原始訊息裡要遮掉的權杖與密碼：Authorization 標頭、Google 的存取權杖（ya29.）與續期權杖（1//），以及「鍵: 值」形式的祕密
_SECRET_PATTERNS = (
    (re.compile(r"(Bearer\s+)\S+", re.IGNORECASE), r"\1***"),
    (re.compile(r"ya29\.[\w.\-]+"), "***"),
    (re.compile(r"1//[\w\-]+"), "***"),
    (
        re.compile(
            r"""((?:access_token|refresh_token|id_token|client_secret|password|passwd|token)["']?\s*[:=]\s*["']?)[^\s"',&}]+""",
            re.IGNORECASE,
        ),
        r"\1***",
    ),
)


# 紀錄的時間寫法：紀錄檔的一筆，以及譜庫的編制寫入紀錄、存入紀錄的時間欄（字串比較即先後）
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def now() -> datetime.datetime:
    """現在的本機時間，精確到秒（紀錄檔一筆的時間）"""
    return datetime.datetime.now().replace(microsecond=0)


def now_text() -> str:
    """現在的本機時間，照紀錄的寫法（TIME_FORMAT）"""
    return now().strftime(TIME_FORMAT)


def redact(message: str) -> str:
    """把訊息裡看起來像權杖或密碼的部分換成 ***

    Args:
        message: 原始訊息

    Returns:
        遮掉祕密後的訊息
    """
    for pattern, replacement in _SECRET_PATTERNS:
        message = pattern.sub(replacement, message)
    return message


@dataclass(frozen=True)
class LogEntry:
    """紀錄檔的一筆：動作成功時沒有錯誤類別與原始訊息"""

    action: str
    piece_title: str = ""
    file_name: str = ""
    path: str = ""
    category: str = ""
    message: str = ""
    time: datetime.datetime = field(default_factory=now)

    def text(self) -> str:
        """寫進紀錄檔與「詳細資料」的內容

        第一行是時間、動作、曲名與檔名（全形空白分隔，空的略過）；有路徑時第二行是路徑；
        失敗時最後是「錯誤類別：原始訊息」，原始訊息遮掉權杖與密碼。
        """
        heading = "　".join(
            part for part in (self.time.strftime(TIME_FORMAT), self.action, self.piece_title, self.file_name)
            if part
        )
        lines = [heading]
        if self.path:
            lines.append(self.path)
        if self.category or self.message:
            lines.append("：".join(part for part in (self.category, redact(self.message)) if part))
        return "\n".join(lines)

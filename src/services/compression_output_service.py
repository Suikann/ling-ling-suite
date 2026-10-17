# -*- coding: utf-8 -*-
"""
壓縮輸出位置

決定壓縮結果另存到哪裡、擋下會蓋掉原檔的位置，並把結果交到那裡。
輸出的檔名不加後綴，所以原檔所在的資料夾（含子資料夾）一律不能當輸出位置。

使用範例：
    output = CompressionOutputService(FileService())
    target = output.default_target("D:/Symphony/Flute.pdf")  # D:/Symphony（壓縮）/Flute.pdf
    if not output.is_blocked(source, target):
        output.deliver(result, target)
"""
import os

from core.constants import COMPRESSED_FOLDER_SUFFIX, COMPRESSED_FOLDER_SUFFIX_EN, CompressionStatus
from core.locale import localized
from core.paths import is_inside, same_path
from services.compression_service import CompressionResult
from services.file_service import FileService


class CompressionOutputService:
    """壓縮結果的另存位置規則與交出"""

    def __init__(self, file_service: FileService):
        """
        Args:
            file_service: 檔案服務
        """
        self._files = file_service

    @staticmethod
    def default_target(source: str) -> str:
        """預設的另存位置：原資料夾旁的「<原資料夾名>（壓縮）」，檔名不變

        Args:
            source: 原檔路徑

        Returns:
            另存的檔案路徑
        """
        folder = os.path.dirname(os.path.abspath(source))
        suffix = localized(COMPRESSED_FOLDER_SUFFIX, COMPRESSED_FOLDER_SUFFIX_EN)
        return os.path.join(folder + suffix, os.path.basename(source))

    @staticmethod
    def is_blocked(source: str, target: str) -> bool:
        """另存位置會不會蓋掉原檔：是原檔、或在原檔的資料夾（含子資料夾）裡就擋下

        Args:
            source: 原檔路徑
            target: 另存的檔案路徑

        Returns:
            要擋下時為 True
        """
        folder = os.path.dirname(os.path.abspath(source))
        return same_path(target, source) or is_inside(target, folder)

    def deliver(self, result: CompressionResult, target: str) -> None:
        """把壓縮結果交到 target：不到上限的原檔原樣複製，有壓縮檔的複製壓縮檔；目的地已有檔案時覆蓋

        暫存的壓縮檔留著，由呼叫端以 CompressionService.discard 清掉。

        Args:
            result: 壓縮結果，狀態是不到上限、或帶有壓縮檔
            target: 另存的檔案路徑；父資料夾不存在時建立

        Raises:
            ValueError: target 被擋下（見 is_blocked），或結果沒有可交出的檔案
            OSError: 複製失敗
        """
        if self.is_blocked(result.source, target):
            raise ValueError(f"另存位置會蓋掉原檔，拒絕交出：{target}")
        if result.status is CompressionStatus.SMALL_ENOUGH:
            source = result.source
        elif result.output_path is not None:
            source = result.output_path
        else:
            raise ValueError(f"沒有可交出的檔案：{result.source}")
        self._files.create_directory(os.path.dirname(os.path.abspath(target)))
        self._files.copy_file(source, target)

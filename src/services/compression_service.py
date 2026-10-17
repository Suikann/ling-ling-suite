# -*- coding: utf-8 -*-
"""
PDF 壓縮服務

把一份 PDF 壓到大小上限以下：先無損，不夠再依序降到 300、200 dpi，到上限以下就停。
壓縮檔寫在暫存資料夾，原檔永遠不動；交到輸出位置由呼叫端決定（見 compression_output_service）。
PDF 內容的讀寫在 pdf_service，這裡只決定級數與結果，檔案存取經過 FileService。

使用範例：
    service = CompressionService(FileService())
    result = service.compress("Flute.pdf")
    if result.output_path:
        ...  # 交出 result.output_path 後呼叫 service.discard(result)
"""
import os
import uuid
from dataclasses import dataclass
from typing import Optional

from core.constants import (
    COMPRESS_SIZE_LIMIT, TEMP_DIR, CompressionLevel, CompressionProblem, CompressionStatus,
)
from services import pdf_service
from services.file_service import FileService

# 降解析度時重新編碼的 JPEG 品質（暫訂，待以真實的分譜掃描檔校正）
JPEG_QUALITY = 75


@dataclass(frozen=True)
class CompressionResult:
    """一份 PDF 的壓縮結果

    Attributes:
        source: 原檔路徑
        status: 結果
        original_size: 原檔大小（位元組）
        level: 壓縮檔用了哪一級；沒有壓縮檔時為 None
        compressed_size: 壓縮檔大小（位元組）；沒有壓縮檔時為 None
        output_path: 暫存資料夾裡的壓縮檔；沒有壓縮檔時為 None
        problem: 壓縮失敗或無法壓縮的原因；其他結果為 None
        detail: 原因的補充說明（程式庫的錯誤訊息、頁碼或數值），沒有時為空字串
    """
    source: str
    status: CompressionStatus
    original_size: int
    level: Optional[CompressionLevel] = None
    compressed_size: Optional[int] = None
    output_path: Optional[str] = None
    problem: Optional[CompressionProblem] = None
    detail: str = ""


class CompressionService:
    """把一份 PDF 壓到大小上限以下，壓縮檔留在暫存資料夾"""

    def __init__(
        self, file_service: FileService, temp_dir: str = TEMP_DIR, size_limit: int = COMPRESS_SIZE_LIMIT,
    ):
        """
        Args:
            file_service: 檔案服務
            temp_dir: 暫存資料夾，壓縮檔寫在它底下各自的子資料夾
            size_limit: 大小上限（位元組）
        """
        self._files = file_service
        self._temp_dir = temp_dir
        self._size_limit = size_limit

    def compress(self, source: str) -> CompressionResult:
        """壓縮一份 PDF

        Args:
            source: 原檔路徑（不改動）

        Returns:
            壓縮結果
        """
        original_size = self._files.file_size(source)
        if original_size <= self._size_limit:
            return CompressionResult(source, CompressionStatus.SMALL_ENOUGH, original_size)
        defect = pdf_service.open_defect(source)
        if defect is not None:
            return CompressionResult(
                source, CompressionStatus.UNCOMPRESSIBLE, original_size, problem=defect.problem, detail=defect.detail,
            )
        folder = os.path.join(self._temp_dir, uuid.uuid4().hex)
        self._files.create_directory(folder)
        output = os.path.join(folder, os.path.basename(source))
        for level in CompressionLevel:
            self._write_level(source, output, level)
            size = self._files.file_size(output)
            if size <= self._size_limit:
                break
        status = CompressionStatus.COMPRESSED if size <= self._size_limit else CompressionStatus.STILL_TOO_LARGE
        return CompressionResult(
            source, status, original_size,
            level=level, compressed_size=size, output_path=output,
        )

    @staticmethod
    def _write_level(source: str, output: str, level: CompressionLevel) -> None:
        """以 level 這一級壓縮 source，寫到 output（覆蓋前一級的嘗試）"""
        if level is CompressionLevel.LOSSLESS:
            pdf_service.save_lossless(source, output)
        else:
            pdf_service.save_downsampled(source, output, level.value, JPEG_QUALITY)

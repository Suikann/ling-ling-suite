# -*- coding: utf-8 -*-
"""
PDF 壓縮服務

把一份 PDF 壓到大小上限以下：先無損，不夠再依序降到 300、200 dpi，到上限以下就停。
壓縮檔寫在暫存資料夾，最後一個候選經過三層檢查（見 pdf_service.check_compressed）才留下，
沒通過就判為壓縮失敗、丟掉壓縮檔，不改試下一級；原檔永遠不動。
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
from typing import Callable, NamedTuple, Optional

from core.constants import (
    COMPRESS_SIZE_LIMIT, TEMP_DIR, CompressionLevel, CompressionProblem, CompressionStatus,
)
from services import pdf_service
from services.file_service import FileService

# 降解析度時重新編碼的 JPEG 品質（暫訂，待以真實的分譜掃描檔校正）
JPEG_QUALITY = 75
# 壓縮檢查：與原檔各以這個 dpi 算繪比較，每頁 PSNR 不得低於門檻（dB，暫訂，待以真實的分譜掃描檔校正）
CHECK_RENDER_DPI = 150
PSNR_THRESHOLD = 30


class CompressionProgress(NamedTuple):
    """壓縮進度：正在試哪一級、是壓縮還是檢查，已完成幾頁

    每一級開始壓縮、開始檢查時各回報一次 page 為 0，之後每完成一頁回報一次（無損一次存完，不逐頁回報）。
    """
    level: CompressionLevel
    checking: bool
    page: int
    page_count: int


class _Cancelled(Exception):
    """使用者取消壓縮；在頁與頁之間拋出，中止 PDF 的處理"""


@dataclass(frozen=True)
class CompressionResult:
    """一份 PDF 的壓縮結果

    Attributes:
        source: 原檔路徑
        status: 結果
        original_size: 原檔大小（位元組）
        level: 壓縮檔用了哪一級；壓縮失敗時是失敗的那一級；不到上限、無法壓縮或取消時為 None
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

    @property
    def size_limit(self) -> int:
        """大小上限（位元組）"""
        return self._size_limit

    def compress(
        self, source: str, should_cancel: Callable[[], bool] = lambda: False,
        on_progress: Callable[[CompressionProgress], None] = lambda progress: None,
    ) -> CompressionResult:
        """壓縮一份 PDF

        可以在背景執行緒呼叫；取消在頁與頁之間生效，取消後暫存資料夾裡什麼都不留。

        Args:
            source: 原檔路徑（不改動）
            should_cancel: 頁與頁之間呼叫，回傳 True 就取消
            on_progress: 回報進度（見 CompressionProgress），在呼叫 compress 的執行緒上呼叫

        Returns:
            壓縮結果
        """
        original_size = self._files.file_size(source)
        if original_size <= self._size_limit:
            return CompressionResult(source, CompressionStatus.SMALL_ENOUGH, original_size)
        page_count, defect = pdf_service.probe_pdf(source)
        if defect is not None:
            return CompressionResult(
                source, CompressionStatus.UNCOMPRESSIBLE, original_size, problem=defect.problem, detail=defect.detail,
            )
        folder = os.path.join(self._temp_dir, uuid.uuid4().hex)
        try:
            result = self._compress_into(source, original_size, folder, page_count, should_cancel, on_progress)
        except _Cancelled:
            self._files.discard_directory(folder)
            return CompressionResult(source, CompressionStatus.CANCELLED, original_size)
        except BaseException:
            self._files.discard_directory(folder)
            raise
        if result.output_path is None:
            self._files.discard_directory(folder)
        return result

    def clear_leftovers(self) -> None:
        """清空暫存資料夾：上次執行留下的壓縮檔（當機、或壓縮中關閉程式）；啟動時取得單一實例鎖後呼叫"""
        self._files.discard_directory(self._temp_dir)

    def discard(self, result: CompressionResult) -> None:
        """丟掉結果在暫存資料夾裡的壓縮檔（交出之後、或使用者不要這份時）；沒有壓縮檔時什麼都不做"""
        if result.output_path is not None:
            self.discard_output(result.output_path)

    def discard_output(self, output_path: str) -> None:
        """丟掉暫存資料夾裡的一份壓縮檔（以 CompressionResult.output_path 指定；發譜用完交出的壓縮檔時）"""
        self._files.discard_directory(os.path.dirname(output_path))

    def _compress_into(
        self, source: str, original_size: int, folder: str, page_count: int,
        should_cancel: Callable[[], bool], on_progress: Callable[[CompressionProgress], None],
    ) -> CompressionResult:
        """在 folder 裡依序試各級，最後一個候選檢查通過才留下（folder 由呼叫端清掉）

        Raises:
            _Cancelled: 使用者取消
        """
        def stepper(level: CompressionLevel, checking: bool) -> Callable[[int], None]:
            """回報這一階段開始，並回傳逐頁回報進度、檢查取消的函式"""
            def step(page: int):
                """回報這一階段做到第 page 頁；使用者要求取消時拋出 _Cancelled"""
                on_progress(CompressionProgress(level, checking, page, page_count))
                if should_cancel():
                    raise _Cancelled()
            step(0)
            return step

        self._files.create_directory(folder)
        output = os.path.join(folder, os.path.basename(source))
        for level in CompressionLevel:
            step = stepper(level, checking=False)
            try:
                self._write_level(source, output, level, step)
            except _Cancelled:
                raise
            except Exception as e:
                return CompressionResult(
                    source, CompressionStatus.FAILED, original_size, level=level,
                    problem=CompressionProblem.WRITE_FAILED, detail=str(e),
                )
            size = self._files.file_size(output)
            if size <= self._size_limit:
                break
        defect = pdf_service.check_compressed(
            source, output, PSNR_THRESHOLD, CHECK_RENDER_DPI, stepper(level, checking=True),
        )
        if defect is not None:
            return CompressionResult(
                source, CompressionStatus.FAILED, original_size, level=level,
                problem=defect.problem, detail=defect.detail,
            )
        status = CompressionStatus.COMPRESSED if size <= self._size_limit else CompressionStatus.STILL_TOO_LARGE
        return CompressionResult(
            source, status, original_size, level=level, compressed_size=size, output_path=output,
        )

    @staticmethod
    def _write_level(source: str, output: str, level: CompressionLevel, on_page: Callable[[int], None]) -> None:
        """以 level 這一級壓縮 source，寫到 output（覆蓋前一級的嘗試）；降解析度時每完成一頁呼叫 on_page"""
        if level is CompressionLevel.LOSSLESS:
            pdf_service.save_lossless(source, output)
        else:
            pdf_service.save_downsampled(source, output, level.value, JPEG_QUALITY, on_page)

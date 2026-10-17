# -*- coding: utf-8 -*-
"""
壓縮 PDF 的使用者互動

選原檔與另存位置、把壓縮進度寫成狀態列文字、交出結果並告訴使用者結果；
壓縮本身在 services/compression_service.py，另存位置的規則在 services/compression_output_service.py。
"""
from typing import Optional, Tuple

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from core.constants import PDF_EXTENSION, CompressionLevel, CompressionProblem, CompressionStatus
from core.locale import t
from services.compression_output_service import CompressionOutputService
from services.compression_service import CompressionProgress, CompressionResult
from services.file_service import FileService
from ui.widgets import ask_save_path

_LEVEL_KEYS = {
    CompressionLevel.LOSSLESS: "compress.level.lossless",
    CompressionLevel.DPI_300: "compress.level.dpi_300",
    CompressionLevel.DPI_200: "compress.level.dpi_200",
}

_PROBLEM_KEYS = {
    CompressionProblem.ENCRYPTED: "compress.problem.encrypted",
    CompressionProblem.UNREADABLE: "compress.problem.unreadable",
    CompressionProblem.WRITE_FAILED: "compress.problem.write_failed",
    CompressionProblem.OUTPUT_UNREADABLE: "compress.problem.output_unreadable",
    CompressionProblem.REPAIRED: "compress.problem.repaired",
    CompressionProblem.PAGE_COUNT: "compress.problem.page_count",
    CompressionProblem.RENDER_ERROR: "compress.problem.render_error",
    CompressionProblem.WARNINGS: "compress.problem.warnings",
    CompressionProblem.CHANNELS: "compress.problem.channels",
    CompressionProblem.PSNR: "compress.problem.psnr",
}


def level_text(level: CompressionLevel) -> str:
    """級數的顯示名稱"""
    return t(_LEVEL_KEYS[level])


def problem_text(result: CompressionResult) -> str:
    """壓縮失敗或無法壓縮的原因，帶上補充說明"""
    text = t(_PROBLEM_KEYS[result.problem])
    return f"{text}（{result.detail}）" if result.detail else text


def size_text(size: int) -> str:
    """檔案大小的顯示文字（MB，與大小上限同一種單位）"""
    return f"{size / 1000 / 1000:.1f} MB"


def ask_paths(
    parent: QWidget, output: CompressionOutputService, file_service: FileService,
) -> Optional[Tuple[str, str]]:
    """詢問要壓縮的 PDF 與另存位置；另存位置沒打副檔名時補上，在原檔的資料夾裡時提示並再問一次

    Args:
        parent: 對話框的父元件
        output: 另存位置的規則
        file_service: 檔案服務（確認補上副檔名後的檔名是否已有檔案）

    Returns:
        （原檔，另存位置）；任一個對話框按了取消時為 None
    """
    source, _ = QFileDialog.getOpenFileName(
        parent, t("compress.select_source"), "", f"{t('filedialog.pdf_files')} (*.pdf)",
    )
    if not source:
        return None
    suggested = output.default_target(source)
    while True:
        target = ask_save_path(
            parent, t("compress.select_target"), suggested, t("filedialog.pdf_files"), PDF_EXTENSION, file_service,
        )
        if not target:
            return None
        if not output.is_blocked(source, target):
            return source, target
        QMessageBox.warning(parent, t("dialog.warning"), t("compress.target_blocked"))


def describe_progress(name: str, progress: CompressionProgress) -> str:
    """壓縮進度的狀態列文字

    Args:
        name: 正在壓縮的檔名
        progress: 壓縮進度

    Returns:
        「壓縮中」或「檢查中」，帶級數與頁數
    """
    key = "compress.progress.checking" if progress.checking else "compress.progress.compressing"
    return t(key, name=name, level=level_text(progress.level), page=progress.page, count=progress.page_count)


def deliver_and_report(
    parent: QWidget, output: CompressionOutputService, result: CompressionResult, target: str, size_limit: int,
) -> bool:
    """交出壓縮結果並以提示框告訴使用者：級數與前後大小，或失敗原因

    已取消的結果不交出、不提示。暫存的壓縮檔由呼叫端丟掉。

    Args:
        parent: 提示框的父元件
        output: 另存位置的規則與交出
        result: 壓縮結果
        target: 另存位置
        size_limit: 壓縮時的大小上限（位元組），寫進提示

    Returns:
        是否已把檔案交到 target
    """
    status = result.status
    if status is CompressionStatus.CANCELLED:
        return False
    if status is CompressionStatus.FAILED:
        QMessageBox.critical(parent, t("dialog.error"), t("compress.failed", reason=problem_text(result)))
        return False
    if status is CompressionStatus.UNCOMPRESSIBLE:
        QMessageBox.critical(parent, t("dialog.error"), t("compress.uncompressible", reason=problem_text(result)))
        return False
    try:
        output.deliver(result, target)
    except (OSError, ValueError) as e:
        QMessageBox.critical(parent, t("dialog.error"), t("compress.deliver_failed", error=e))
        return False
    limit = size_text(size_limit)
    if status is CompressionStatus.SMALL_ENOUGH:
        QMessageBox.information(parent, t("compress.done.title"), t("compress.done.small_enough", limit=limit, path=target))
    elif status is CompressionStatus.STILL_TOO_LARGE:
        QMessageBox.warning(parent, t("compress.done.title"), t(
            "compress.done.still_too_large", limit=limit, path=target,
            before=size_text(result.original_size), after=size_text(result.compressed_size),
        ))
    else:
        QMessageBox.information(parent, t("compress.done.title"), t(
            "compress.done.compressed", path=target, level=level_text(result.level),
            before=size_text(result.original_size), after=size_text(result.compressed_size),
        ))
    return True

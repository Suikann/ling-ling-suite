# -*- coding: utf-8 -*-
"""
壓縮 PDF 的使用者互動

選要壓縮的 PDF（多選同一資料夾的檔、或整個資料夾）與輸出位置、確認覆蓋，
把壓縮進度寫成狀態列文字，交出後逐份告訴使用者結果；交出前的逐份比較在 ui/compression_compare.py。
單一檔另存成一批只有一份；壓縮一份在 services/compression_service.py，
批次、輸出位置的規則與交出在 services/compression_output_service.py。
"""
import os
from typing import Dict, Optional, Sequence

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from core.constants import PDF_EXTENSION, CompressionLevel, CompressionProblem, CompressionStatus
from core.locale import t
from services.compression_output_service import BatchProgress, CompressionBatch, CompressionOutputService
from services.compression_service import CompressionResult
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


def ask_files(parent: QWidget, output: CompressionOutputService, file_service: FileService) -> Optional[CompressionBatch]:
    """多選同一資料夾裡要壓縮的 PDF，再問輸出位置；只選一份時改問另存的檔名

    Args:
        parent: 對話框的父元件
        output: 批次與輸出位置的規則
        file_service: 檔案服務（另存時確認補上副檔名後的檔名是否已有檔案）

    Returns:
        這一批；任一個對話框按了取消、或不同意覆蓋時為 None
    """
    sources, _ = QFileDialog.getOpenFileNames(
        parent, t("compress.select_source"), "", f"{t('filedialog.pdf_files')} (*.pdf)",
    )
    if not sources:
        return None
    if len(sources) == 1:
        return _ask_single_target(parent, output, file_service, sources[0])
    root = output.input_root(sources)
    location = _ask_output_folder(parent, output, root)
    if location is None:
        return None
    return _confirm_overwrite(parent, output, output.plan(root, sources, location))


def ask_folder(parent: QWidget, output: CompressionOutputService) -> Optional[CompressionBatch]:
    """選一個資料夾，連同子資料夾裡的 PDF 一起壓縮，再問輸出位置

    Args:
        parent: 對話框的父元件
        output: 批次與輸出位置的規則

    Returns:
        這一批；任一個對話框按了取消、資料夾裡沒有 PDF、或不同意覆蓋時為 None
    """
    root = QFileDialog.getExistingDirectory(parent, t("compress.select_folder"))
    if not root:
        return None
    try:
        sources = output.collect_folder(root)
    except OSError as e:
        QMessageBox.critical(parent, t("dialog.error"), t("compress.folder_unreadable", error=e))
        return None
    if not sources:
        QMessageBox.information(parent, t("dialog.info"), t("compress.no_pdfs"))
        return None
    location = _ask_output_folder(parent, output, root)
    if location is None:
        return None
    return _confirm_overwrite(parent, output, output.plan(root, sources, location))


def _ask_single_target(
    parent: QWidget, output: CompressionOutputService, file_service: FileService, source: str,
) -> Optional[CompressionBatch]:
    """詢問單一檔的另存位置；沒打副檔名時補上，在原檔的資料夾裡時提示並再問一次

    已有檔案時由另存對話框確認取代，這裡不再問一次覆蓋。
    """
    root = output.input_root([source])
    suggested = output.default_target(source)
    while True:
        target = ask_save_path(
            parent, t("compress.select_target"), suggested, t("filedialog.pdf_files"), PDF_EXTENSION, file_service,
        )
        if not target:
            return None
        if not output.is_blocked(root, target):
            return output.plan_single(source, target)
        QMessageBox.warning(parent, t("dialog.warning"), t("compress.target_blocked"))


def _ask_output_folder(parent: QWidget, output: CompressionOutputService, root: str) -> Optional[str]:
    """以預設的輸出位置詢問要不要開始，或改選別的資料夾；選到輸入的資料夾裡時提示並再問一次

    Returns:
        輸出位置的資料夾；按了取消時為 None
    """
    location = output.default_output(root)
    while True:
        box = QMessageBox(QMessageBox.Question, t("compress.title"), t("compress.output.message", path=location),
                          parent=parent)
        start = box.addButton(t("compress.output.start"), QMessageBox.AcceptRole)
        change = box.addButton(t("compress.output.change"), QMessageBox.ActionRole)
        box.addButton(t("compress.output.cancel"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is start:
            return location
        if box.clickedButton() is not change:
            return None
        chosen = QFileDialog.getExistingDirectory(parent, t("compress.select_output"), os.path.dirname(location))
        if not chosen:
            continue
        if not output.is_blocked(root, chosen):
            return chosen
        QMessageBox.warning(parent, t("dialog.warning"), t("compress.output.blocked"))


def _confirm_overwrite(
    parent: QWidget, output: CompressionOutputService, batch: CompressionBatch,
) -> Optional[CompressionBatch]:
    """輸出位置已有同名檔時，列出所有會被覆蓋的檔名一次確認

    Returns:
        沒有會被覆蓋的檔、或同意覆蓋時為 batch；不同意時為 None
    """
    replaced = output.overwritten(batch)
    if not replaced:
        return batch
    names = "\n".join(job.name for job in replaced)
    answer = QMessageBox.question(parent, t("compress.title"), t("compress.overwrite.message", names=names))
    return batch if answer == QMessageBox.Yes else None


def describe_progress(progress: BatchProgress) -> tuple:
    """一批壓縮進度的狀態列文字與進度條

    Args:
        progress: 一批的壓縮進度

    Returns:
        （狀態列文字，已完成數，總數）：文字帶第幾份、共幾份，進度條是這一份目前這一階段的頁數
    """
    name = os.path.basename(progress.source)
    step = progress.step
    if step is None:
        return t("compress.progress.starting", index=progress.index, total=progress.total, name=name), 0, 1
    key = "compress.progress.checking" if step.checking else "compress.progress.compressing"
    text = t(
        key, index=progress.index, total=progress.total, name=name,
        level=level_text(step.level), page=step.page, count=step.page_count,
    )
    return text, step.page, step.page_count


def _result_line(name: str, result: CompressionResult, error: Optional[OSError], limit: str) -> str:
    """一份結果在報告裡的那一行"""
    status = result.status
    if error is not None:
        return t("compress.line.deliver_failed", name=name, error=error)
    if status is CompressionStatus.FAILED:
        return t("compress.line.failed", name=name, reason=problem_text(result))
    if status is CompressionStatus.UNCOMPRESSIBLE:
        return t("compress.line.uncompressible", name=name, reason=problem_text(result))
    if status is CompressionStatus.SMALL_ENOUGH:
        return t("compress.line.small_enough", name=name, limit=limit)
    before, after = size_text(result.original_size), size_text(result.compressed_size)
    if status is CompressionStatus.STILL_TOO_LARGE:
        return t("compress.line.still_too_large", name=name, limit=limit, before=before, after=after)
    return t("compress.line.compressed", name=name, level=level_text(result.level), before=before, after=after)


def report(
    parent: QWidget, batch: CompressionBatch, results: Sequence[CompressionResult],
    errors: Dict[str, OSError], size_limit: int,
) -> None:
    """以提示框逐份告訴使用者結果：級數與前後大小，或沒有輸出的原因

    有任何一份仍超過、壓縮失敗、無法壓縮或交不出去時以警告呈現。

    Args:
        parent: 提示框的父元件
        batch: 這一批
        results: 交出的那幾份的壓縮結果（比較畫面留下的）
        errors: 交出時複製失敗的原檔路徑對應到錯誤
        size_limit: 壓縮時的大小上限（位元組），寫進提示
    """
    limit = size_text(size_limit)
    names = {job.source: job.name for job in batch.jobs}
    lines = [_result_line(names[r.source], r, errors.get(r.source), limit) for r in results]
    fine = {CompressionStatus.SMALL_ENOUGH, CompressionStatus.COMPRESSED}
    problem = bool(errors) or any(r.status not in fine for r in results)
    text = t("compress.done.message", path=batch.output, lines="\n".join(lines))
    if problem:
        QMessageBox.warning(parent, t("compress.done.title"), text)
    else:
        QMessageBox.information(parent, t("compress.done.title"), text)

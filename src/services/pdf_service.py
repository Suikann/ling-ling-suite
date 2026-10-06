# -*- coding: utf-8 -*-
"""
PDF 工具服務

提供 PDF 的頁數查詢、頁面擷取（分割模組用來寫出分譜）、分段旋轉與縮圖算繪。
"""
import os
from typing import List, Tuple

from PyPDF2 import PdfReader, PdfWriter

from core.paths import same_path


def get_page_count(pdf_path: str) -> int:
    """取得 PDF 檔案的總頁數

    Args:
        pdf_path: PDF 檔案路徑

    Returns:
        總頁數
    """
    reader = PdfReader(pdf_path)
    return len(reader.pages)


def extract_pages(
    pdf_path: str,
    page_indices: List[int],
    output_path: str,
) -> str:
    """從 PDF 擷取指定頁面（0-based）至新檔案

    Args:
        pdf_path: 來源 PDF 檔案路徑
        page_indices: 要擷取的頁面索引清單（從 0 開始）
        output_path: 輸出檔案路徑

    Returns:
        輸出檔案路徑
    """
    if same_path(output_path, pdf_path):
        raise ValueError(f"輸出路徑與來源相同，拒絕覆蓋來源：{pdf_path}")
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    total = len(reader.pages)
    for idx in page_indices:
        if 0 <= idx < total:
            writer.add_page(reader.pages[idx])
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "wb") as f:
        writer.write(f)
    return output_path


def render_page_thumbnails(pdf_path: str, max_width: int = 160) -> List:
    """將 PDF 各頁面算繪為 PIL Image 縮圖

    Args:
        pdf_path: PDF 檔案路徑
        max_width: 縮圖最大寬度（像素）

    Returns:
        PIL Image 物件清單，每頁一張
    """
    try:
        import fitz
    except ImportError as err:
        raise ImportError(
            "缺少 PyMuPDF 套件。請執行 pip install PyMuPDF 安裝。",
        ) from err
    from PIL import Image

    doc = fitz.open(pdf_path)
    thumbnails = []
    for page in doc:
        scale = max_width / page.rect.width
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        thumbnails.append(img)
    doc.close()
    return thumbnails


def render_single_page(pdf_path: str, page_index: int, max_width: int = 600):
    """算繪單一頁面為較大的 PIL Image

    Args:
        pdf_path: PDF 檔案路徑
        page_index: 頁面索引（從 0 開始）
        max_width: 輸出最大寬度（像素）

    Returns:
        PIL Image 物件
    """
    try:
        import fitz
    except ImportError as err:
        raise ImportError(
            "缺少 PyMuPDF 套件。請執行 pip install PyMuPDF 安裝。",
        ) from err
    from PIL import Image

    doc = fitz.open(pdf_path)
    page = doc[page_index]
    scale = max_width / page.rect.width
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return img


def rotate_pdf_sections(
    pdf_path: str,
    sections: List[Tuple[int, int, int]],
    output_path: str,
) -> str:
    """依區段旋轉 PDF 頁面

    Args:
        pdf_path: 來源 PDF 檔案路徑
        sections: 區段清單，每項為 (起始頁0based, 結束頁0based, 角度)
        output_path: 輸出檔案路徑

    Returns:
        輸出檔案路徑
    """
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    page_angles = {}
    for start, end, angle in sections:
        if angle == 0:
            continue
        for p in range(start, end + 1):
            page_angles[p] = angle
    for i, page in enumerate(reader.pages):
        if i in page_angles:
            page.rotate(page_angles[i])
        writer.add_page(page)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "wb") as f:
        writer.write(f)
    return output_path

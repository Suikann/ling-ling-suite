# -*- coding: utf-8 -*-
"""
PDF 工具服務

提供 PDF 檔案的頁面分割、旋轉與縮圖算繪功能。
"""
import os
from typing import List, Tuple

from PyPDF2 import PdfReader, PdfWriter


def get_page_count(pdf_path: str) -> int:
    """取得 PDF 檔案的總頁數

    Args:
        pdf_path: PDF 檔案路徑

    Returns:
        總頁數
    """
    reader = PdfReader(pdf_path)
    return len(reader.pages)


def split_pdf(
    pdf_path: str,
    ranges: List[Tuple[int, int]],
    output_dir: str,
    name_pattern: str = "",
) -> List[str]:
    """依指定頁面範圍分割 PDF

    Args:
        pdf_path: 來源 PDF 檔案路徑
        ranges: 頁面範圍清單，每項為 (起始頁, 結束頁)，從 1 開始
        output_dir: 輸出資料夾路徑
        name_pattern: 輸出檔名模式，含 {index} 與 {pages} 變數；
                      空字串時使用預設 "原檔名_part{index}.pdf"

    Returns:
        產生的檔案路徑清單
    """
    reader = PdfReader(pdf_path)
    total_pages = len(reader.pages)
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    os.makedirs(output_dir, exist_ok=True)
    output_files = []
    for idx, (start, end) in enumerate(ranges, 1):
        start_idx = max(0, start - 1)
        end_idx = min(total_pages, end)
        if start_idx >= end_idx:
            continue
        writer = PdfWriter()
        for page_num in range(start_idx, end_idx):
            writer.add_page(reader.pages[page_num])
        if name_pattern:
            filename = name_pattern.replace(
                "{index}", str(idx),
            ).replace(
                "{pages}", f"{start}-{end}",
            )
            if not filename.lower().endswith(".pdf"):
                filename += ".pdf"
        else:
            filename = f"{base_name}_part{idx}.pdf"
        output_path = os.path.join(output_dir, filename)
        with open(output_path, "wb") as f:
            writer.write(f)
        output_files.append(output_path)
    return output_files


def split_pdf_every_n_pages(
    pdf_path: str,
    n: int,
    output_dir: str,
) -> List[str]:
    """每 N 頁分割一個檔案

    Args:
        pdf_path: 來源 PDF 檔案路徑
        n: 每個檔案的頁數
        output_dir: 輸出資料夾路徑

    Returns:
        產生的檔案路徑清單
    """
    total = get_page_count(pdf_path)
    ranges = []
    for start in range(1, total + 1, n):
        end = min(start + n - 1, total)
        ranges.append((start, end))
    return split_pdf(pdf_path, ranges, output_dir)


def split_pdf_into_single_pages(
    pdf_path: str,
    output_dir: str,
) -> List[str]:
    """將 PDF 分割為逐頁獨立檔案

    Args:
        pdf_path: 來源 PDF 檔案路徑
        output_dir: 輸出資料夾路徑

    Returns:
        產生的檔案路徑清單
    """
    return split_pdf_every_n_pages(pdf_path, 1, output_dir)


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


def rotate_pdf(
    pdf_path: str,
    angle: int,
    page_ranges: List[Tuple[int, int]],
    output_path: str,
) -> str:
    """旋轉 PDF 指定頁面

    Args:
        pdf_path: 來源 PDF 檔案路徑
        angle: 旋轉角度，90、180 或 270（順時針）
        page_ranges: 要旋轉的頁面範圍清單（從 1 開始），
                     空清單表示旋轉所有頁面
        output_path: 輸出檔案路徑

    Returns:
        輸出檔案路徑
    """
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    total = len(reader.pages)
    pages_to_rotate = set()
    if page_ranges:
        for start, end in page_ranges:
            for p in range(max(1, start), min(total, end) + 1):
                pages_to_rotate.add(p)
    else:
        pages_to_rotate = set(range(1, total + 1))
    for i, page in enumerate(reader.pages):
        if (i + 1) in pages_to_rotate:
            page.rotate(angle)
        writer.add_page(page)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "wb") as f:
        writer.write(f)
    return output_path

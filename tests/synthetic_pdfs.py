# -*- coding: utf-8 -*-
"""
以 PyMuPDF 產生的合成 PDF，測壓縮服務與壓縮檢查

頁面是邊長 SIDE_INCHES 英寸的正方形，滿版放一張影像，影像的有效 dpi 即其像素寬度除以 SIDE_INCHES。
掃描影像是淡灰底加顆粒雜訊、五條譜線與一排音符頭，像真實的掃描分譜一樣壓不太小；
乾淨影像沒有雜訊，無損壓縮就能壓得很小。
"""
import hashlib
import io
from typing import List

import pymupdf
from PIL import Image, ImageDraw

SIDE_INCHES = 2
POINTS_PER_INCH = 72


def _score_image(dpi: int, grain: bool) -> Image.Image:
    """一頁譜面的 8-bit 灰階影像"""
    side = SIDE_INCHES * dpi
    if grain:
        image = Image.effect_noise((side, side), 40).point(lambda v: min(255, 200 + v // 4))
    else:
        image = Image.new("L", (side, side), 245)
    draw = ImageDraw.Draw(image)
    for i in range(5):
        y = int(side * (0.2 + 0.1 * i))
        draw.rectangle([side * 0.05, y, side * 0.95, y + max(1, dpi // 150)], fill=0)
    for i in range(10):
        x = int(side * (0.1 + 0.08 * i))
        draw.ellipse([x, side * 0.3, x + dpi * 0.08, side * 0.3 + dpi * 0.06], fill=0)
    return image


def _encode(image: Image.Image, fmt: str) -> bytes:
    """把影像編碼成 fmt 格式（JPEG 以品質 90）的位元組"""
    buffer = io.BytesIO()
    image.save(buffer, fmt, **({"quality": 90} if fmt == "JPEG" else {}))
    return buffer.getvalue()


def _save_pages(path: str, images: List[bytes], deflate: bool, **save_options) -> str:
    """把每張影像放成一頁正方形頁面，存成 PDF 並回傳路徑"""
    doc = pymupdf.open()
    side = SIDE_INCHES * POINTS_PER_INCH
    for data in images:
        page = doc.new_page(width=side, height=side)
        page.insert_image(page.rect, stream=data)
    doc.save(path, deflate=deflate, **save_options)
    doc.close()
    return path


def scanned_pdf(path: str, dpi: int, pages: int = 1, jpeg: bool = False) -> str:
    """掃描分譜：每頁一張有顆粒的 8-bit 灰階影像；jpeg 為 True 時影像以 JPEG 存，否則以 Flate 壓縮"""
    images = [_encode(_score_image(dpi, grain=True), "JPEG" if jpeg else "PNG") for _ in range(pages)]
    return _save_pages(path, images, deflate=True)


def uncompressed_pdf(path: str, dpi: int) -> str:
    """影像串流沒有壓縮的乾淨譜面（無損壓縮就能壓得很小）"""
    return _save_pages(path, [_encode(_score_image(dpi, grain=False), "PNG")], deflate=False)


def bilevel_pdf(path: str, dpi: int) -> str:
    """黑白 1-bit 影像，串流不壓縮"""
    image = _score_image(dpi, grain=False).point(lambda v: 255 if v > 128 else 0).convert("1")
    return _save_pages(path, [_encode(image, "PNG")], deflate=False)


def encrypted_pdf(path: str, dpi: int) -> str:
    """要密碼才打得開的掃描分譜"""
    images = [_encode(_score_image(dpi, grain=True), "PNG")]
    return _save_pages(
        path, images, deflate=True,
        encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner",
    )


def mislabeled_jpeg_pdf(path: str, correct_path: str = "") -> str:
    """三通道的 JPEG 卻宣告為灰階（PyMuPDF #5164 產出的那種壞檔）

    Args:
        path: 輸出路徑
        correct_path: 不是空字串時，另存一份宣告正確的同一個檔到這裡

    Returns:
        path
    """
    image = Image.new("RGB", (400, 400), (250, 240, 230))
    data = _encode(image, "JPEG")
    side = SIDE_INCHES * POINTS_PER_INCH
    with pymupdf.open() as doc:
        page = doc.new_page(width=side, height=side)
        page.insert_image(page.rect, stream=data)
        if correct_path:
            doc.save(correct_path)
        doc.xref_set_key(page.get_images(full=True)[0][0], "ColorSpace", "/DeviceGray")
        doc.save(path)
    return path


def image_pixels(path: str) -> List[tuple]:
    """各頁影像資源的（寬像素、高像素、每色版位元數），依頁序

    頁面資源裡的每個影像名稱各算一次：同一張影像以兩個名稱留在頁面上（例如換掉影像時留下的別名）會算兩次，
    PyMuPDF 存檔時把別名併回原影像或另存一份都看得出來。
    """
    doc = pymupdf.open(path)
    try:
        return [tuple(img[2:5]) for page in doc for img in page.get_images(full=True)]
    finally:
        doc.close()


def image_dpis(path: str) -> List[float]:
    """各頁影像的有效 dpi（像素寬度除以頁寬英寸），依頁序"""
    return [width / SIDE_INCHES for width, _, _ in image_pixels(path)]


def file_digest(path: str) -> str:
    """檔案內容的雜湊，確認原檔沒被改動"""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

# -*- coding: utf-8 -*-
"""
PDF 工具服務

提供 PDF 的頁數查詢、頁面擷取（分割模組用來寫出分譜）、分段旋轉與縮圖算繪。
"""
import io
import math
import os
import re
from typing import List, NamedTuple, Optional, Tuple

from PyPDF2 import PdfReader, PdfWriter

from core.constants import CompressionProblem
from core.paths import same_path

POINTS_PER_INCH = 72


class PdfDefect(NamedTuple):
    """PDF 打不開或沒通過壓縮檢查的原因

    Attributes:
        problem: 原因種類
        detail: 補充說明（程式庫的錯誤訊息、頁碼或數值），沒有時為空字串
    """
    problem: CompressionProblem
    detail: str = ""


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
        import pymupdf
    except ImportError as err:
        raise ImportError(
            "缺少 PyMuPDF 套件。請執行 pip install PyMuPDF 安裝。",
        ) from err
    from PIL import Image

    doc = pymupdf.open(pdf_path)
    thumbnails = []
    for page in doc:
        scale = max_width / page.rect.width
        mat = pymupdf.Matrix(scale, scale)
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
        import pymupdf
    except ImportError as err:
        raise ImportError(
            "缺少 PyMuPDF 套件。請執行 pip install PyMuPDF 安裝。",
        ) from err
    from PIL import Image

    doc = pymupdf.open(pdf_path)
    page = doc[page_index]
    scale = max_width / page.rect.width
    mat = pymupdf.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return img


def open_defect(pdf_path: str) -> Optional[PdfDefect]:
    """PDF 能不能拿來壓縮：要密碼才打得開、或本身就壞到打不開時回報原因

    Args:
        pdf_path: PDF 檔案路徑

    Returns:
        打不開的原因；打得開時為 None
    """
    import pymupdf
    try:
        with pymupdf.open(pdf_path, filetype="pdf") as doc:
            if doc.needs_pass:
                return PdfDefect(CompressionProblem.ENCRYPTED)
            if doc.page_count == 0:
                return PdfDefect(CompressionProblem.UNREADABLE, "0 pages")
    except Exception as e:
        return PdfDefect(CompressionProblem.UNREADABLE, str(e))
    return None


def save_lossless(pdf_path: str, output_path: str) -> None:
    """無損壓縮：清掉沒用到的物件、壓縮沒有壓過的串流、物件收進 object stream，另存到 output_path

    不傳 compression_effort（傳 1 或 True 時幾乎不壓），不用 clean、linear。

    Args:
        pdf_path: 來源 PDF 檔案路徑（不改動）
        output_path: 輸出檔案路徑，不能是來源
    """
    import pymupdf
    _refuse_source(pdf_path, output_path)
    with pymupdf.open(pdf_path) as doc:
        doc.save(output_path, garbage=3, deflate=True, use_objstms=1)


def save_downsampled(pdf_path: str, output_path: str, dpi: int, jpeg_quality: int) -> None:
    """把 8-bit 影像降到 dpi（以 Lanczos 重取樣、存成 JPEG），另存到 output_path

    1-bit、ImageMask 與帶 SMask 的影像不動；有效 dpi 已不高於目標的影像不重新編碼。
    有效 dpi 以影像在各頁放得最大的那一處計算。不用 Document.rewrite_images（PyMuPDF #5164、#4896）。

    Args:
        pdf_path: 來源 PDF 檔案路徑（不改動）
        output_path: 輸出檔案路徑，不能是來源
        dpi: 目標 dpi
        jpeg_quality: JPEG 品質（1 到 95）
    """
    import pymupdf
    _refuse_source(pdf_path, output_path)
    with pymupdf.open(pdf_path) as doc:
        placements = _largest_placements(doc)
        done = set()
        for page in doc:
            for xref in _page_image_xrefs(page):
                if xref in done or xref not in placements:
                    continue
                done.add(xref)
                jpeg = _downsampled_jpeg(doc, xref, placements[xref], dpi, jpeg_quality)
                if jpeg is not None:
                    page.replace_image(xref, stream=jpeg)
        doc.save(output_path, garbage=4, deflate=True, use_objstms=1)


def _page_image_xrefs(page) -> List[int]:
    """頁面用到的影像 xref（不含 SMask）"""
    return [img[0] for img in page.get_images(full=True)]


def _largest_placements(doc) -> dict:
    """各張可以降解析度的 8-bit 影像，在所有頁面中放得最大處的長邊（點）

    Returns:
        {xref: 長邊點數}；1-bit、ImageMask、帶 SMask 與沒有放在頁面上的影像不列入
    """
    placements = {}
    for page in doc:
        for img in page.get_images(full=True):
            xref, smask, bpc = img[0], img[1], img[4]
            if bpc != 8 or smask or doc.xref_get_key(xref, "ImageMask")[1] == "true":
                continue
            for rect in page.get_image_rects(xref):
                side = max(rect.width, rect.height)
                if side > placements.get(xref, 0):
                    placements[xref] = side
    return placements


def _downsampled_jpeg(doc, xref: int, placed_points: float, dpi: int, jpeg_quality: int) -> Optional[bytes]:
    """把一張影像降到 dpi 後編成 JPEG

    Args:
        doc: PDF 文件
        xref: 影像 xref
        placed_points: 影像放在頁面上的長邊（點）
        dpi: 目標 dpi
        jpeg_quality: JPEG 品質

    Returns:
        JPEG 位元組；有效 dpi 已不高於目標、或色彩空間無法處理時為 None
    """
    import pymupdf
    from PIL import Image
    pix = pymupdf.Pixmap(doc, xref)
    longest = max(pix.width, pix.height)
    target_longest = round(placed_points / POINTS_PER_INCH * dpi)
    if target_longest <= 0 or longest <= target_longest:
        return None
    if pix.alpha:
        return None
    if pix.n == 4:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    mode = {1: "L", 3: "RGB"}.get(pix.n)
    if mode is None:
        return None
    image = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    scale = target_longest / longest
    size = (max(1, round(pix.width * scale)), max(1, round(pix.height * scale)))
    buffer = io.BytesIO()
    image.resize(size, Image.LANCZOS).save(buffer, "JPEG", quality=jpeg_quality)
    return buffer.getvalue()


def check_compressed(
    original_path: str, compressed_path: str, psnr_threshold: float, render_dpi: int,
) -> Optional[PdfDefect]:
    """檢查壓縮檔有沒有壞：結構、影像通道、畫面三層依序檢查，回報第一個沒通過的

    1. 結構：開得起來、不需修復、頁數與原檔相同、逐頁算繪不出錯、MuPDF 沒有警告
    2. 影像通道：每張 /DCTDecode 影像的 JPEG 通道數與 /ColorSpace 宣告的一致
    3. 畫面：與原檔各以 render_dpi 算繪，每頁的 PSNR 都不低於門檻

    Args:
        original_path: 原檔路徑
        compressed_path: 壓縮檔路徑
        psnr_threshold: PSNR 門檻（dB）
        render_dpi: 比較畫面時算繪的 dpi

    Returns:
        第一個沒通過的原因；全部通過時為 None
    """
    import pymupdf
    pymupdf.TOOLS.mupdf_warnings(reset=True)
    try:
        doc = pymupdf.open(compressed_path, filetype="pdf")
    except Exception as e:
        return PdfDefect(CompressionProblem.OUTPUT_UNREADABLE, str(e))
    with doc, pymupdf.open(original_path) as original:
        if doc.is_repaired:
            return PdfDefect(CompressionProblem.REPAIRED)
        if doc.page_count != original.page_count:
            return PdfDefect(CompressionProblem.PAGE_COUNT, f"{original.page_count} → {doc.page_count}")
        worst = None
        for index in range(doc.page_count):
            try:
                compressed_image = _render_rgb(doc[index], render_dpi)
            except Exception as e:
                return PdfDefect(CompressionProblem.RENDER_ERROR, f"{index + 1}: {e}")
            warnings = pymupdf.TOOLS.mupdf_warnings(reset=True)
            if warnings:
                return PdfDefect(CompressionProblem.WARNINGS, f"{index + 1}: {warnings}")
            psnr = _psnr(_render_rgb(original[index], render_dpi), compressed_image)
            pymupdf.TOOLS.mupdf_warnings(reset=True)
            if worst is None or psnr < worst[1]:
                worst = (index + 1, psnr)
        mismatch = _jpeg_channel_mismatch(doc)
        if mismatch:
            return PdfDefect(CompressionProblem.CHANNELS, mismatch)
        if worst is not None and worst[1] < psnr_threshold:
            return PdfDefect(CompressionProblem.PSNR, f"{worst[0]}: {worst[1]:.1f} dB")
    return None


def _render_rgb(page, dpi: int):
    """以 dpi 把頁面算繪成 RGB 的 PIL Image"""
    import pymupdf
    from PIL import Image
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _psnr(reference, image) -> float:
    """兩張同尺寸 RGB 影像的 PSNR（dB）；尺寸不同時為 0，完全相同時為無限大"""
    from PIL import ImageChops, ImageStat
    if reference.size != image.size:
        return 0.0
    squares = ImageStat.Stat(ImageChops.difference(reference, image)).sum2
    mse = sum(squares) / (reference.width * reference.height * len(squares))
    return math.inf if mse == 0 else 10 * math.log10(255 ** 2 / mse)


# /ColorSpace 宣告的色版數；ICCBased 讀它的 /N，其他（Indexed、Separation 等）不檢查
_COLORSPACE_COMPONENTS = {
    "/DeviceGray": 1, "/CalGray": 1, "/DeviceRGB": 3, "/CalRGB": 3, "/Lab": 3, "/DeviceCMYK": 4,
}


def _jpeg_channel_mismatch(doc) -> str:
    """找出 JPEG 通道數與 /ColorSpace 宣告不一致的影像

    Returns:
        第一張不一致的影像說明（xref 與兩邊的通道數）；都一致時為空字串
    """
    from PIL import Image
    for xref in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(xref, "Subtype")[1] != "/Image" or doc.xref_get_key(xref, "Filter")[1] != "/DCTDecode":
                continue
        except Exception:
            continue
        declared = _declared_components(doc, xref)
        if declared is None:
            continue
        try:
            actual = len(Image.open(io.BytesIO(doc.xref_stream_raw(xref))).getbands())
        except Exception as e:
            return f"xref {xref}: {e}"
        if actual != declared:
            return f"xref {xref}: {declared} → {actual}"
    return ""


def _declared_components(doc, xref: int) -> Optional[int]:
    """影像 /ColorSpace 宣告的色版數；無法判定時為 None"""
    kind, value = doc.xref_get_key(xref, "ColorSpace")
    if kind == "xref":
        value = doc.xref_object(int(value.split()[0]), compressed=True)
    value = value.strip()
    if value in _COLORSPACE_COMPONENTS:
        return _COLORSPACE_COMPONENTS[value]
    icc = re.match(r"\[\s*/ICCBased\s+(\d+)\s+\d+\s+R", value)
    if icc:
        kind, n = doc.xref_get_key(int(icc.group(1)), "N")
        return int(n) if kind == "int" else None
    family = re.match(r"\[\s*(/\w+)", value)
    if family and family.group(1) in ("/CalGray", "/CalRGB", "/Lab"):
        return _COLORSPACE_COMPONENTS[family.group(1)]
    return None


def _refuse_source(pdf_path: str, output_path: str) -> None:
    """輸出路徑與來源相同時拒絕，來源不會被覆蓋"""
    if same_path(output_path, pdf_path):
        raise ValueError(f"輸出路徑與來源相同，拒絕覆蓋來源：{pdf_path}")


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

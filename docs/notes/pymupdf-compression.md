# PyMuPDF 降解析度壓縮查證

查證 #50 的四個問題，用來支撐 #46 定下的壓縮級數：先無損壓縮，不夠再依序降到 300 dpi、200 dpi。

- 查證日期：2026-10-10
- 一手來源：PyMuPDF 官方文件原始檔（`docs/*.rst`，main 分支）、PyMuPDF `changes.txt`、PyPI 版本清單、MuPDF 原始碼（依版本標籤比對）、MuPDF `mutool clean` 文件、PyMuPDF 的 GitHub issue。完整清單見文末〈來源〉。
- 實測環境：WSL2、Intel i5-12400F、Python 3.10。PyMuPDF 1.24.0、1.24.1、1.26.0、1.26.1、1.26.3、1.28.2 各裝一個 venv。PyPI 上最新版是 1.28.2（2026-08-06）。
- 測試資料是自己產生的合成掃描分譜（見〈3. 掃描分譜的效果與速度〉），不是真實掃描檔，數字只能看相對關係。

## 結論

1. **無損**：`doc.save(out, garbage=3, deflate=True, use_objstms=1)`，等同 `doc.ez_save(out)`。`use_objstms` 要 1.24.1 以上，現在的 `PyMuPDF>=1.24.0` 不夠。對已經壓縮過的掃描檔幾乎沒效果（實測 0%～1.2%），只有影像串流沒壓縮時才明顯（實測 −42%）。
2. **降解析度**：`Document.rewrite_images` 從 1.26.1 起才有。它預設的縮放方法 `FZ_SUBSAMPLE_AVERAGE` 只能以 2 的次方縮小，到不了指定的 dpi（400 dpi 目標 300 時完全不縮；600 dpi 目標 200 時停在 300）。要準確降到 300、200 dpi，必須自己組 `PdfImageRewriterOptions` 並改用 `FZ_SUBSAMPLE_BICUBIC`。
3. **`rewrite_images` 沒有一個版本能直接用**：1.26.3 到最新的 1.28.2 都有未修的 PyMuPDF #5164。灰階影像的色彩空間若是 ICCBased，改寫成 JPEG 後實際是 RGB 三通道，`/ColorSpace` 卻仍宣告一通道，輸出是壞檔，而且重新開檔、算繪、PyPDF2 都不報錯。1.26.1 沒有 #5164，卻有另外兩個 1.28.0 才修的 bug：#4896（改寫後以 `garbage>=2` 存檔會弄壞字型）、#4918（同一影像被多頁共用時 segfault）。
4. **掃描分譜**：600 dpi 灰階降到 300 dpi JPEG（品質 75）後，檔案剩原本的 1%～2%；來源 400 dpi 以上時，再降到 200 dpi 又少 39%～49%。每頁約 0.1～0.4 秒；600 dpi 灰階的峰值記憶體 173～340 MB。黑白 1-bit 掃描（CCITT G4）本來就很小；降解析度時 MuPDF 會用半色調抖動轉回 1-bit，譜線變成點線，檔案還常常變大。黑白影像應該排除在降解析度之外。
5. **驗證**：結構檢查（重新開檔、`is_repaired`、頁數、逐頁算繪、`TOOLS.mupdf_warnings()`）抓得到截斷、串流損壞與 #4896，抓不到 #5164。要再加兩道檢查：JPEG 實際通道數是否等於宣告的色彩空間；與原檔的算繪結果比對。

### 對規則的建議

規則可以照用，但要補上下面幾點：

- 降解析度只處理 8-bit 灰階或彩色影像；1-bit 影像、`ImageMask`、帶 `SMask` 的影像都不動。
- 縮放要用 bicubic（MuPDF）或 Lanczos（Pillow），才會準確落在 300、200 dpi。
- 有效 dpi 不高於門檻的影像不重新編碼，避免已經是 300 dpi 的 JPEG 多失真一次。
- 實作路線二選一（比較見〈2. 降解析度〉）：
  - **A：Pillow 重取樣搭配 `Page.replace_image`**。`replace_image` 從 1.21.0 起就有，實測 1.24.0 與 1.28.2 的輸出一致，完全避開 `rewrite_images` 的 bug，但要自己判斷影像種類。版本下限 1.24.1（為了 `use_objstms`）。
  - **B：`rewrite_images(options=...)`**。版本下限訂 1.28.0，改寫前先把 ICCBased 一通道的灰階影像改標成 `/DeviceGray` 來繞過 #5164（實測有效），並保留 JPEG 通道數檢查。等上游修好 #5164 再拿掉這個繞道。

## 1. 無損壓縮

### `Document.save` 的相關參數

- `garbage`：0～4。1 移除沒有被引用的物件；2 再壓縮 xref 表；3 再合併重複的物件；4 再比對 stream 內容找出重複，比較慢。
- `deflate`：只壓縮沒有 `/Filter` 的 stream。已經壓縮過的影像（DCT、Flate、CCITT）原樣複製，不會重壓；沒壓縮的 1-bit 點陣圖會自動改成 CCITT G4（MuPDF `pdf-write.c` 的 `copystream`）。值 1 是 Flate，2 是 Brotli；文件警告 Brotli 是實驗性功能、多數閱讀器不支援，不要用。
- `deflate_images`、`deflate_fonts`：只在 `deflate=0` 時有意義，單獨對影像或字型 stream 開壓縮（`pdf-write.c`：`do_deflate = opts->do_compress ? opts->do_compress : 1`）。
- `use_objstms`：把非 stream 的物件定義打包進 object stream，再由 `deflate` 一起壓縮。文件標示「new in v1.24.0」，但 1.24.0 的 `save` 簽章沒有這個參數（實測）；changelog 把「Support ObjStm Compression」記在 1.24.1，1.24.1 實測也有。以 1.24.1 為準。
- `compression_effort`：整數，0 為預設，1 最省力，100 最費力。實測用 Flate 時 100 與 0 的結果相同。傳 1 時 139 MB 的未壓縮檔幾乎沒被壓縮（輸出 139.23 MB）；傳 `True` 也等於 1，文件特別警告過這個陷阱。不要傳這個參數。
- `ez_save`（1.18.11 起）：就是 `save`，只是預設值換成 `garbage=3, deflate=1, use_objstms=True`。
- 不要動的參數：
  - `clean`：會改寫內容 stream；1.28.0 的 `clean_contents(sanitize=True)` 有掉字形位置的回歸（#5054，changelog 記在尚未發佈的 2.0 修正）。
  - `linear`：1.26.0 起傳 `True` 直接拋例外。
  - `expand`、`incremental`：文件〈File size reduction〉也建議保持預設。

### 效果（實測，PyMuPDF 1.28.2，四頁 A4）

| 輸入 | 原始 | `garbage=3, deflate=True, use_objstms=1` | 時間 |
| --- | --- | --- | --- |
| 600 dpi 灰階，影像未壓縮 | 139.22 MB | 80.88 MB（−42%） | 4.8 s |
| 600 dpi 灰階，Flate | 80.88 MB | 80.88 MB（0%） | 0.07 s |
| 400 dpi、300 dpi 灰階 JPEG | 6.45 MB、4.15 MB | 不變 | 0.01 s 以下 |
| 600 dpi 1-bit（Flate、CCITT、ImageMask） | 0.53 MB、0.26 MB、0.26 MB | −0.1%～−1.2% | 0.01 s 以下 |

- `garbage=4` 比 3 只多省 400 bytes，多花 0.5 秒。
- 官方文件以文字、向量為主的 1.8 MB 檔示範，`use_objstms` 加 Flate 可以壓到 903 KB；掃描檔的體積幾乎全在影像串流，打包物件省不了多少。
- 無損這一級很便宜（大多不到 0.1 秒），值得先試，但對常見的掃描檔幾乎都不夠，實際上會直接進入 300 dpi 這一級。

## 2. 降解析度

### `Document.rewrite_images`

**版本**：changelog 在 1.26.1 寫「New method `Document.rewrite_images()`」。實測 1.26.0 沒有這個方法（但已經有低階的 `pymupdf.mupdf.pdf_rewrite_images` 與 `PdfImageRewriterOptions`），1.26.1 有。文件該節沒有標版本。1.26.2 沒有發佈到 PyPI（changelog 寫「Skipped」）。

**簽章**：`rewrite_images(dpi_threshold=None, dpi_target=0, quality=0, lossy=True, lossless=True, bitonal=True, color=True, gray=True, set_to_gray=False, options=None)`

**底層運作**（MuPDF `pdf_rewrite_images`，讀 1.26.2 與 1.28.2 原始碼）：

- 分兩輪走過每一頁的內容，包括註解與表單外觀。
- 第一輪計算每張影像的有效 dpi：像素寬 × 72 ÷ 影像在頁面上的寬（pt），x、y 取較小值；同一張影像出現多次時取最小值。dpi 依影像在頁面上的實際大小計算，不看影像檔內記錄的 dpi。
- 第二輪把有效 dpi 大於 `threshold` 的影像縮到 `to`，再依類別重新編碼。
- 影像依實際像素分類（`classify_pixmap`）：灰階影像若只有 0 與 255 兩種值，會被當成黑白（bitonal）處理。

**高階參數的實際行為**（讀 1.28.2 的 Python 原始碼）：

- 灰階與彩色影像，不論原本是無損還是有損，一律改成 JPEG（`FZ_RECOMPRESS_JPEG`）；黑白影像改成 CCITT（`FZ_RECOMPRESS_FAX`）；縮放方法固定是 `FZ_SUBSAMPLE_AVERAGE`。
- 文件說 `quality=0` 是「不改品質」，實際上 MuPDF 收到 `"0"` 會改用預設品質 75（`fz_recompress_image_as_jpeg`：`if (q == 0) q = 75`）。實測 `quality=0` 與 75 的輸出大小相同。
- `dpi_target >= dpi_threshold` 會拋 `ValueError`，所以 threshold 必須大於 target。
- 有給 `options` 時，除了 `set_to_gray` 以外的參數都被忽略。

**縮放方法**（MuPDF `resample()`）：

- `FZ_SUBSAMPLE_AVERAGE`：只做 2 的次方縮小（`fz_subsample_pixmap`），一直減半到「再減半就會低於目標」為止，結果落在 target 與 2 × target 之間。實測：
  - 600 dpi 目標 300：得 300.2 dpi（來源略高於 600，剛好能減半一次）。
  - 600 dpi 目標 200：也停在 300.2 dpi。
  - 400 dpi 目標 300：完全不縮，只重新編碼。
  - 400 dpi 目標 200：得 200.1 dpi。
- `FZ_SUBSAMPLE_BICUBIC`：用 `fz_scale_pixmap` 縮到準確尺寸，實測得 300.0 與 200.0 dpi。
- 要照規則「降到 300 dpi」「降到 200 dpi」，必須用 `options` 把 `*_subsample_method` 設成 `FZ_SUBSAMPLE_BICUBIC`。

**不縮的影像也會重新編碼**：只要某類別的 `*_recompress_method` 不是 `FZ_RECOMPRESS_NEVER`，有效 dpi 低於 threshold 的影像也會被重新編碼一次。例如 300 dpi 的 JPEG 在「300 dpi」這一級會以品質 75 重壓（實測 −24%，多失真一次）。設成 `NEVER` 則整個類別連縮放都跳過，沒辦法做到只縮大圖、不碰小圖。

**「變小才換」**：

- MuPDF 拿新影像的 `fz_image_size` 和原 stream 的 `/Length` 比較，新的沒有比較小就保留原圖。1.27.2 起可以用 `options.recompress_when = FZ_RECOMPRESS_WHEN_ALWAYS` 關掉這項比較。
- 實測：129 KB 的 Flate 1-bit 頁轉成 64 KB 的 CCITT 時被擋下，改成 `ALWAYS` 才換掉。
- 讀原始碼推得的原因：`fz_image_size` 算的是緩衝區容量（`image.c` 的 `buffer->cap`），不是實際長度；CCITT 編碼器先配置「原始 1-bit 大小 ÷ 8」的容量（`encode-fax.c`），所以 CCITT 的結果常被誤判為不夠小。

**黑白影像縮小**：先以灰階縮放，再用 `fz_default_halftone` 半色調轉回 1-bit，然後做 CCITT 編碼。實測 600 dpi 降到 200 dpi 後譜線變成點線、符桿斷斷續續（與原檔比 PSNR 26.7 dB）；多數情況還會被上一點的大小比較擋下，根本沒有換掉。

**JBIG2**：最新的 `mutool clean` 文件列出 `jbig2` 重壓方式，但 PyMuPDF 1.28.2 的 binding 沒有 `FZ_RECOMPRESS_JBIG2` 常數，未驗證。

### 已知 bug

| 問題 | 影響版本 | 狀態 | 實測 |
| --- | --- | --- | --- |
| #5164：灰階或 CMYK 影像改寫成 JPEG 時寫成 RGB 三通道，`/ColorSpace` 不變 | 1.26.3～1.28.2；PyMuPDF main 分支（2.0）用的 MuPDF 1.28.5 與 MuPDF master 原始碼仍是同一段 | 2026-10-07 開立，未修 | 1.26.3、1.28.2 重現，1.26.1 正常 |
| #4896：`rewrite_images` 後以 `garbage>=2` 存檔，字型的 xref 錯亂 | 1.27.x 以前 | 1.28.0 修正 | 用 issue 附檔測：1.26.1、1.26.3 重現（抽出的文字不同，PSNR 13.1 dB），1.28.2 正常 |
| #4918：同一影像 xref 被多頁共用時 segfault | 1.27.x 以前 | 1.28.0 修正 | 未實測 |
| #4720：`rewrite_images` 記憶體洩漏 | 1.26.5 以前 | 1.26.6 修正 | 未實測 |
| ImageMask 影像改寫：MuPDF 1.27.2 起才把 `imagemask` 帶到新影像並把顏色反轉回來 | PyMuPDF 1.27.2 以前 | MuPDF 1.27.2 修改 | 沒看到錯誤輸出：1.26.1 完全沒處理 ImageMask，1.28.2 被大小比較擋下 |

#5164 的成因（讀原始碼）：

- MuPDF 1.26.3 起，`fz_new_buffer_from_pixmap_as_jpeg`（`output-jpeg.c`）會把不是 `fz_device_gray`、`fz_device_rgb`、`fz_device_cmyk` 這三個內建色彩空間的像素（例如 ICCBased 灰階）一律轉成 RGB 再編碼。
- 改寫器卻用 `fz_colorspace_is_gray` 判定它是灰階，沿用原本的色彩空間。
- 來源是 `/DeviceGray` 時不受影響（實測）。PyMuPDF 自己的 `insert_image` 插入灰階 PNG 或 JPEG 時，就會寫成 `[/ICCBased N=1]`，所以這不是罕見情況。
- issue 說 MuPDF 算繪看不出異狀、macOS Preview 會整頁全黑、Poppler 偏暗。本次的合成分譜在 MuPDF 1.28.2 算繪出來也是錯亂的橫紋（PSNR 約 15 dB）。

路線 B 的繞道：改寫前把一通道的 ICCBased 灰階影像改標成 `/DeviceGray`。這會捨棄該影像的 ICC 描述檔，對黑白掃描的譜影響可以忽略。

```python
M = pymupdf.mupdf
for page in doc:
    for xref, *_ in page.get_images(full=True):
        if declared_components(doc, xref) == 1 and doc.xref_get_key(xref, "BitsPerComponent")[1] == "8":
            doc.xref_set_key(xref, "ColorSpace", "/DeviceGray")
o = M.PdfImageRewriterOptions()
for cat in ("gray_lossless", "gray_lossy", "color_lossless", "color_lossy"):
    setattr(o, cat + "_image_recompress_method", M.FZ_RECOMPRESS_JPEG)
    setattr(o, cat + "_image_recompress_quality", "75")
    setattr(o, cat + "_image_subsample_method", M.FZ_SUBSAMPLE_BICUBIC)
    setattr(o, cat + "_image_subsample_threshold", target + 10)
    setattr(o, cat + "_image_subsample_to", target)
# bitonal_* 保持預設的 FZ_RECOMPRESS_NEVER：黑白影像不動
doc.rewrite_images(options=o)
doc.save(tmp_path, garbage=3, deflate=True, use_objstms=1)
```

`declared_components` 見〈4. 驗證輸出沒有損壞〉。實測這段在 1.28.2 上：ICCBased 灰階的 600 dpi 輸入降到 300 dpi 得 1.37 MB（與 `/DeviceGray` 版相同），通道數一致、PSNR 48.2 dB；CCITT 黑白輸入原樣保留。

### 替代做法

**Pillow 重取樣搭配 `Page.replace_image(xref, stream=...)`**（`replace_image` 從 1.21.0 起）：

- 流程：用 `pymupdf.Pixmap(doc, xref)` 解碼影像；從 `page.get_image_info(xrefs=True)` 的 bbox 算有效 dpi；以 Pillow `resize(..., Image.LANCZOS)` 縮到準確 dpi；存成單通道 JPEG 後替換。
- `replace_image` 的做法是在頁面上多插一張圖、再把它複製到原本的 xref，頁面資源仍然掛著那張重複的圖。存檔要用 `garbage=4` 才會合併掉：實測 600 dpi 降到 300 dpi 的四頁檔，`garbage=3` 是 3.72 MB，`garbage=4` 是 1.86 MB。
- 必須跳過 1-bit（`BitsPerComponent 1`）、`ImageMask`、帶 `SMask` 的影像。實測沒有跳過時，0.26 MB 的 CCITT 檔被轉成 JPEG 後變成 2.45 MB。
- 只處理有效 dpi 高於門檻的影像，低於門檻的不重壓。
- 實測 1.24.0 與 1.28.2 的輸出一致（600 dpi 降到 200 dpi：1.12 MB，PSNR 44.5 dB），不受 #5164、#4896 影響。Pillow 已經在 `requirements.txt` 裡。
- `Pixmap.tobytes("jpg")`（1.22.0 起）也能編 JPEG，但它走的正是 #5164 那段 `fz_new_buffer_from_pixmap_as_jpeg`，ICCBased 灰階會被轉成 RGB；替換後的影像本身前後一致，只是變成三通道、檔案較大。這點是讀原始碼推得，未實測。用 Pillow 編碼可以避開。

**其他**：

- `pymupdf.Pixmap(source, width, height)` 可以任意縮放（寬高不是整數時會多出 alpha 通道）；`Pixmap.shrink(n)` 只能除以 2 的 n 次方。兩者都能取代 Pillow 做縮放，未實測。
- 整頁算繪（`page.get_pixmap(dpi=...)`）再組成新 PDF：一定能降到指定 dpi，但文字層、向量內容、註解都會變成點陣，不建議拿來壓縮。

## 3. 掃描分譜的效果與速度

合成資料：A4、每份四頁的小提琴分譜（五線譜、符頭、符桿、表情記號、標題文字），用 numpy 與 Pillow 產生，再以 `insert_image` 鋪滿整頁。

- 灰階：輕微模糊、紙色漸層、高斯雜訊（紙 σ=2.5、墨 σ=4）。有 600 dpi（未壓縮、Flate 兩種）、400 dpi JPEG（品質 85）、300 dpi JPEG（品質 85）。
- 黑白：600 dpi 1-bit，有 Flate、CCITT G4、ImageMask（CCITT）三種，含少量雜點。
- 灰階的數字取自改標 `/DeviceGray` 的版本，避開 #5164。

### 灰階

PyMuPDF 1.28.2，`rewrite_images(options=...)`，BICUBIC，threshold 為 target + 10，最後 `save(garbage=3, deflate=True, use_objstms=1)`。單位 MB，括號內是整份四頁從開檔、改寫到存檔的時間。

| 輸入 | 原始 | 無損 | 300 dpi，q75 | 200 dpi，q75 | 300 dpi，q85 | 200 dpi，q85 |
| --- | --- | --- | --- | --- | --- | --- |
| 600 dpi 未壓縮 | 139.22 | 80.88（4.8 s） | 1.37（0.9 s） | 0.83（0.5 s） | 1.98 | 1.04 |
| 600 dpi Flate | 80.88 | 80.88（0.07 s） | 1.37（1.6 s） | 0.83（1.2 s） | 1.98 | 1.04 |
| 400 dpi JPEG | 6.45 | 6.45 | 1.77（0.9 s） | 0.90（0.6 s） | 2.73 | 1.24 |
| 300 dpi JPEG | 4.15 | 4.15 | 3.16（只重壓，0.8 s） | 1.03（0.4 s） | 3.49 | 1.45 |

- 與原檔各以 150 dpi 算繪比較：300 dpi 的輸出 PSNR 43.6 dB 以上，200 dpi 的 39.0～42.6 dB。
- 600 dpi 降到 300 dpi 能縮掉 98% 以上，主要來自改用 JPEG。同樣降到 300 dpi 但保持無損（`FZ_RECOMPRESS_LOSSLESS`，也就是 Flate）是 14.62 MB，200 dpi 是 5.57 MB。來源本來就是 JPEG 時，改成 Flate 會變大，被「變小才換」擋下，檔案不變。
- 高階 API 預設的 `AVERAGE`：600 dpi 不論目標 300 或 200，都得到 300.2 dpi、1.42 MB；400 dpi 目標 300 時不縮，只重壓成 4.90 MB。
- 峰值記憶體（`/proc/self/status` 的 `VmHWM`）：600 dpi 灰階改寫 173～340 MB；只做無損 48～100 MB。
- 速度：每頁約 0.1～0.4 秒，大半花在解碼原圖。
- 照 #46 的 20 MB 門檻，四頁 600 dpi 灰階譜在 300 dpi 這一級就只剩 1.4 MB；200 dpi 這一級大概只有頁數很多或彩色的檔案才會用到。

### 黑白 1-bit

PyMuPDF 1.28.2，條件同上，`bitonal_*` 設 `FZ_RECOMPRESS_FAX`。單位 MB。

| 輸入 | 原始 | 無損 | 300 dpi | 200 dpi |
| --- | --- | --- | --- | --- |
| 600 dpi Flate | 0.53 | 0.53 | 不變（被大小比較擋下） | 0.35，半色調讓譜線變成點線（PSNR 26.7 dB） |
| 600 dpi CCITT G4 | 0.26 | 0.26 | 不變 | 不變 |
| 600 dpi ImageMask | 0.26 | 0.26 | 不變 | 不變 |

合成頁面比較簡單，一頁 CCITT 約 65 KB，真實分譜會大上幾倍。黑白掃描檔靠降解析度壓不下來，也不應該降。

### Pillow 搭配 `replace_image`

PyMuPDF 1.28.2，LANCZOS、JPEG 品質 75、`save(garbage=4, deflate=True)`。來源是原本的 ICCBased 灰階版本。

| 輸入 | 300 dpi | 200 dpi |
| --- | --- | --- |
| 600 dpi Flate | 1.86 MB（2.0 s） | 1.12 MB（1.9 s） |
| 400 dpi JPEG | 2.33 MB（0.8 s） | 1.19 MB（0.7 s） |
| 300 dpi JPEG | 不動（4.15 MB） | 1.34 MB（0.4 s） |

- PSNR 42.0～48.1 dB，JPEG 通道數全部一致。
- 比 `rewrite_images` 大約 30%～36%：Pillow 的 LANCZOS 比較銳利，JPEG 編碼器也不同。要更小可以調低品質。

## 4. 驗證輸出沒有損壞

輸出先寫到暫存檔，全部檢查通過才以 `os.replace` 搬到正式位置；任何一項失敗就丟棄並回報。#46 已經定好壓縮不覆蓋原檔。

| 檢查 | 做法 | 截斷（一半、尾端 1 KB） | 空檔 | JPEG 串流中段被改 | #4896 字型錯亂 | #5164 通道數不符 |
| --- | --- | --- | --- | --- | --- | --- |
| 結構 | 見下方 | 抓到 | 抓到 | 抓到 | 抓到 | 漏掉 |
| PyPDF2 | `PdfReader(path, strict=True)`，比對頁數 | 抓到（`EOF marker not found`） | 抓到 | 漏掉 | 漏掉 | 漏掉 |
| JPEG 通道數 | 見下方 | 不適用 | 不適用 | 不適用 | 不適用 | 抓到 |
| 與原檔算繪比對 | 原檔與輸出各算繪成 150 dpi 灰階，算 PSNR | 不適用 | 不適用 | 不適用 | 抓到（13.1 dB） | 抓到（約 15 dB） |

結構檢查：

1. 先 `pymupdf.TOOLS.reset_mupdf_warnings()`。
2. `pymupdf.open(path)` 不拋例外。
3. `doc.is_repaired` 為 `False`。文件說明：開檔時因為結構問題而被修復才會是 `True`（1.18.2 起）。
4. `doc.page_count` 等於原檔頁數。
5. 逐頁 `page.get_pixmap(dpi=36)` 不拋例外，這一步會解碼所有影像。
6. `pymupdf.TOOLS.mupdf_warnings()` 為空字串。

實測各種損壞被哪一步抓到：截斷檔 `is_repaired=True`、頁數 0、有 `cannot find startxref` 警告；空檔在開檔時拋例外；JPEG 串流被改在算繪時出現 `jpeg error` 警告；#4896 出現 `cannot find ExtGState resource` 警告。

JPEG 通道數檢查：對每張 `/DCTDecode` 影像，用 Pillow 讀 `doc.xref_stream_raw(xref)` 的 bands 數，和 `/ColorSpace` 宣告的通道數比對。

```python
def declared_components(doc, xref):
    kind, value = doc.xref_get_key(xref, "ColorSpace")
    if kind == "name":
        return {"/DeviceGray": 1, "/DeviceRGB": 3, "/DeviceCMYK": 4}.get(value)
    if kind == "xref":
        value = doc.xref_object(int(value.split()[0]), compressed=True)
    m = re.search(r"/ICCBased\s*(\d+) 0 R", value)
    return int(doc.xref_get_key(int(m.group(1)), "N")[1]) if m else None
```

補充：

- 算繪比對的門檻：正常的 300、200 dpi 輸出在 150 dpi 下都在 39 dB 以上，壞檔是 13～16 dB，黑白半色調是 26.7 dB。門檻可以先訂 30 dB，這只依據合成資料，未用真實掃描檔驗證。用 72 dpi 比對時，正常輸出只有 29～31 dB，太接近門檻，不建議。
- 以 150 dpi 比對一頁（原檔與輸出各算繪一次）實測 0.08～0.32 秒，原檔解析度越高越久。
- `Document.save(raise_on_repair=True)`（PyPI 上 1.27.1 起；文件寫 1.27.0，但 PyPI 沒有 1.27.0）只在存檔過程觸發修復時拋例外，不能取代上面的檢查。

## 5. 對現有程式的附帶發現

- `requirements.txt` 的 `PyMuPDF>=1.24.0` 不夠用：`use_objstms` 要 1.24.1；走路線 B 要 1.28.0。
- `src/services/pdf_service.py` 用 `import fitz`。changelog 1.28.2 寫「Output warning when legacy `fitz` module is imported」，實測 1.28.2 匯入時會印出 `The fitz API is deprecated and will be removed in future`。`import pymupdf` 從 1.24.3 起可用。

## 6. 查無或未驗證

- 真實掃描分譜的數字：本次只有合成資料。彩色、JBIG2、含 OCR 文字層的真實掃描檔效果未測。
- 彩色影像降解析度的結果未測。#5164 說 RGB 不受影響、CMYK 受影響。
- JBIG2 重壓：MuPDF 文件有 `jbig2` 選項，PyMuPDF 1.28.2 沒有對應常數，未測。
- #4918、#4720 未實測，只依 changelog。
- 用 `Pixmap(source, width, height)`、`Pixmap.shrink` 縮放未測。`Pixmap.tobytes("jpg")` 會把 ICCBased 灰階轉成 RGB，是讀原始碼推得，未實測。
- Windows 上的速度與記憶體未測，本次在 WSL2 上執行。
- PSNR 門檻 30 dB 只依據合成資料。
- PyMuPDF 2.0（changelog 已列出，PyPI 尚未發佈）是否修了 #5164：changelog 沒有提到，它用的 MuPDF 1.28.5 與 MuPDF master 原始碼仍是同一段，推斷沒有修。
- 「變小才換」誤判 CCITT 大小的原因是讀原始碼推得，現象有實測。

## 來源

PyMuPDF 文件與版本資訊：

- [docs/document.rst](https://github.com/pymupdf/PyMuPDF/blob/main/docs/document.rst)：`save`、`ez_save`、`rewrite_images`、`recolor`、`repair`、`is_repaired`
- [docs/compressing-files.rst](https://github.com/pymupdf/PyMuPDF/blob/main/docs/compressing-files.rst)：`use_objstms`、`deflate`、`compression_effort`、`garbage` 的範例與數字
- [docs/page.rst](https://github.com/pymupdf/PyMuPDF/blob/main/docs/page.rst)：`replace_image`、`get_pixmap`
- [docs/pixmap.rst](https://github.com/pymupdf/PyMuPDF/blob/main/docs/pixmap.rst)：縮放建構子、`shrink`、`tobytes`
- [docs/tools.rst](https://github.com/pymupdf/PyMuPDF/blob/main/docs/tools.rst)、[docs/app3.rst](https://github.com/pymupdf/PyMuPDF/blob/main/docs/app3.rst)：`mupdf_warnings`、修復訊息範例
- [changes.txt](https://github.com/pymupdf/PyMuPDF/blob/main/changes.txt)：1.24.1、1.24.3、1.26.0、1.26.1、1.26.6、1.27.1、1.28.0、1.28.2、2.0 各節
- [PyPI 版本清單](https://pypi.org/project/PyMuPDF/#history)

MuPDF：

- [source/pdf/pdf-image-rewriter.c](https://github.com/ArtifexSoftware/mupdf/blob/1.28.2/source/pdf/pdf-image-rewriter.c)：與 1.26.2、1.27.1、1.27.2 版比對
- [source/fitz/output-jpeg.c](https://github.com/ArtifexSoftware/mupdf/blob/1.28.2/source/fitz/output-jpeg.c)：與 1.26.2、1.26.3 版比對
- [source/pdf/pdf-write.c](https://github.com/ArtifexSoftware/mupdf/blob/1.28.2/source/pdf/pdf-write.c)
- [source/fitz/image.c](https://github.com/ArtifexSoftware/mupdf/blob/1.28.2/source/fitz/image.c)
- [source/fitz/encode-fax.c](https://github.com/ArtifexSoftware/mupdf/blob/1.28.2/source/fitz/encode-fax.c)
- [mutool clean 文件](https://mupdf.readthedocs.io/en/latest/tools/mutool-clean.html)

PyMuPDF issue：

- [#5164](https://github.com/pymupdf/PyMuPDF/issues/5164)：灰階、CMYK 改寫成 RGB JPEG
- [#4896](https://github.com/pymupdf/PyMuPDF/issues/4896)：改寫後 `garbage>=2` 弄壞字型
- [#4918](https://github.com/pymupdf/PyMuPDF/issues/4918)：共用影像 segfault
- [#4720](https://github.com/pymupdf/PyMuPDF/issues/4720)：記憶體洩漏

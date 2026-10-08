# CODING_STANDARDS.md

寫或改 `src/`、`tests/` 的程式碼時照這份做：動手前查「動手前」兩項，改完逐項核對「完成條件」，全部成立才算改完。

## 分層

`src/` 分三層，每件事放哪一層、怎麼跨層以這張表為準：

| 層 | 放什麼 | 界線 |
|----|--------|------|
| `ui/` | 使用者互動與顯示：讀取使用者的意圖，交給 `Project` 的編輯操作或 services，再顯示結果 | 判斷規則（能不能做、算出什麼、怎麼搬）放 `core/` 或 `services/`；檔案存取經過 services |
| `services/` | 碰磁碟與外部 API 的動作：搬移與重新命名、分割、PDF、匯入、專案檔、工作區、Google | 不匯入 `ui/` 與 PySide6；檔案系統操作經過建構時注入的 `FileService` |
| `core/` | 純邏輯與資料：資料模型、命名、偵測、路徑比對、常數、介面字串 | 不匯入 `ui/`、`services/` 與 PySide6（`tests/test_project.py` 驗證 `core.models`），不做檔案 I/O |

- **注入**：services 用到的 `FileService`、其他服務與檔案位置都由建構子傳入，預設值取自 `core/constants.py`。測試傳入替身（例如 `tests/failing_writes.py`）或臨時目錄，全域常數與私有成員維持原樣
- 可以直接呼叫作業系統檔案 API 與 PDF 程式庫的，只有 `services/file_service.py` 本身、PDF 內容的讀寫（`services/pdf_service.py`）與單一實例鎖（`services/instance_lock.py`）

## 常數與資料

- 跨模組共用的常數放 `core/constants.py`，譜庫的放 `core/catalog_constants.py`，以 `from core.constants import X` 匯入；只屬於一個模組（含它對外介面）的常數留在該模組頂端，例如 `core/naming.py` 的 `SCORE_SLOT` 與模組私用、`_` 開頭的常數
- 同一個值只定義一次
- 會增加的清單與對照寫成常數資料，程式從常數讀取，函式裡不另列一份：變數選單、命名與語言轉換都讀 `TEMPLATE_VARIABLES`；重新命名問題（`RenameProblem`）的阻擋與訊息都查 `RENAME_PROBLEM_RULES`

## 格式與 docstring

- 換行用 LF（repo 沒有 .gitattributes 代為轉換）
- 函式與方法內，敘述之間最多空一行
- docstring 一律用繁體中文，格式照下例（方法與函式用 Google Style）：

```python
# -*- coding: utf-8 -*-
"""
模組標題

詳細描述（可選）。

使用範例：
    from xxx import xxx
    result = xxx.method()
"""


class ClassName:
    """類別簡短描述"""

    def method_name(self, param1: str, param2: int) -> Dict:
        """簡短描述

        Args:
            param1: 參數描述
            param2: 參數描述

        Returns:
            返回值描述
        """
```

- 複雜的方法與函式寫 `Args:`、`Returns:` 區塊；簡單的（例如 getter、setter）寫單行
- 模組 docstring 是該檔用途的唯一說明（`docs/architecture/modules.md` 不逐檔列出）

## 動手前

1. 找已有的入口：先看 [`docs/architecture/modules.md`](docs/architecture/modules.md) 的唯一入口，再以要做的事的關鍵字 `grep -rn` `src/core/`、`src/services/` 與 `core/constants.py`；已有的就沿用
2. 依「分層」表決定新程式碼放哪一層

## 完成條件

1. **測試**：`python -m pytest tests/ -v` 全數通過；新增或改變的行為有測試釘住
2. **分層**：以下三條指令都沒有輸出：
   - `grep -rln PySide6 src/core src/services`
   - `grep -rnE "^\s*(from|import) (ui|services)\b" src/core`
   - `grep -rnE "^\s*(from|import) ui\b" src/services`

   而且 `git diff HEAD -- src` 新增的行裡，直接碰檔案系統的呼叫（`open(`、`os.remove`、`os.rename`、`os.replace`、`os.makedirs`、`os.path.isfile`、`os.path.isdir`、`shutil.`）只落在「分層」列出的三個檔；`ui/` 新增的程式碼只讀取畫面、呼叫編輯操作或 services、顯示結果
3. **常數**：新常數照「常數與資料」放置；`grep -rnE "^_?<名稱>\s*=" src` 只有一筆定義
4. **唯一入口**：新程式碼經過 `modules.md` 列的唯一入口；以新函式的關鍵呼叫 `grep -rn` `src/`，沒有另一處做同一件事
5. **介面字串**：新的 `t("…")` 鍵在 `core/locale.py` 的 zh_TW 與 en 都有。`tests/test_locale.py` 只掃得到字面值的鍵，以 f-string 或變數組出的鍵要自己對照
6. **Docstring**：新增或改動的模組、類別、方法與函式都有照上方格式的 docstring；首行一句話說得完它做的事（說不完就拆）；模組的職責改了，模組 docstring 跟著改
7. **文件**：改了 `docs/architecture/*.md` 描述的行為，在同一個 commit 更新那份文件（哪份文件管哪些程式碼，見 `CLAUDE.md` 的 Reference docs）；新增或搬動唯一入口時更新 `modules.md`；用詞照 `GLOSSARY.md`
8. **換行**：`git diff HEAD --name-only | xargs grep -l $'\r'` 沒有輸出

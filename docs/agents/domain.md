# Domain docs

engineering skill 探索程式碼前，應該如何讀本 repo 的領域文件。

## 探索前先讀

- 根目錄的 **`CONTEXT.md`**；或
- 根目錄的 **`CONTEXT-MAP.md`**（若存在）：它指向每個 context 各自的 `CONTEXT.md`，讀與主題相關的那幾份
- **`docs/adr/`**：讀碰到的區域相關的 ADR。多 context 的 repo 另看 `src/<context>/docs/adr/`

任一檔案不存在時**靜靜略過**，不要指出缺少、不要主動建議先建。`/domain-modeling`（經 `/grill-with-docs` 與 `/improve-codebase-architecture` 進入）會在詞彙或決策真的定案時才懶惰建立。

## 檔案結構

單一 context（多數 repo）：

    /
    ├── CONTEXT.md
    ├── docs/adr/
    │   ├── 0001-....md
    │   └── 0002-....md
    └── src/

多 context（根目錄有 `CONTEXT-MAP.md`）：

    /
    ├── CONTEXT-MAP.md
    ├── docs/adr/                          ← 全系統決策
    └── src/
        ├── ordering/
        │   ├── CONTEXT.md
        │   └── docs/adr/                  ← 該 context 的決策
        └── billing/
            ├── CONTEXT.md
            └── docs/adr/

## 用詞彙表的詞

輸出提到領域概念時（issue 標題、重構提案、假設、測試名），用 `CONTEXT.md` 定義的詞，不要飄到詞彙表明列的 _Avoid_ 同義詞。

需要的概念不在詞彙表裡是個訊號：不是你在發明專案沒有的語言（重新考慮），就是真的有缺口（記下來給 `/domain-modeling`）。

## 標出 ADR 衝突

輸出與既有 ADR 牴觸時明講，不要默默蓋過：

> _Contradicts ADR-0007 (event-sourced orders), but worth reopening because…_

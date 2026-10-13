# drive.file 與桌面 Picker 的實測

回答 [#72](https://github.com/Suikann/ling-ling-suite/issues/72) 的四個測試項目。判定標準在 [#68](https://github.com/Suikann/ling-ling-suite/issues/68)：只有第 1 項沒過才退回範圍 A（保留 `drive`），其餘沒過仍走 C、改入口。

腳本是同目錄的 `drive_file_picker_probe.py`。它：

- 自己接瀏覽器的回呼，取 `picked_file_ids`（`run_local_server` 不回傳重新導向網址，見 `google-oauth-verification.md` 第 4 節）。
- 每次取得或載入權杖都向 `tokeninfo` 查實際範圍；帶了 `drive.file` 以外的 Drive 或 Sheets 範圍就中止，避免同一個用戶端先前授權過的 `drive` 混進來，讓結果失真。
- 每次 API 呼叫記下成功或失敗、HTTP 狀態碼與 Google 的 `reason`，附加到 `~/.cache/lingling-picker-test/log.jsonl`；`report` 整理成表格。授權帳號的 email 只印在終端機，不寫進紀錄。

## 前置

1. **Cloud Console**：在泠靈用的那個 Cloud 專案啟用 Google Picker API（API 和服務 → 程式庫 → 搜尋「Google Picker API」→ 啟用）。
2. **用戶端 JSON**：下載泠靈用的那個「電腦版應用程式」用戶端的 JSON，存成 repo 根目錄的 `client_secret.json`（已在 `.gitignore`）。第 3 項要測「泠靈以舊範圍建的試算表」，所以必須是建立舊譜庫試算表的同一個 Cloud 專案。
3. **測試資料夾樹**：用譜庫帳號在 Drive **網頁**手動建立，不要經過泠靈：

   ```
   泠靈實測 Picker/
     作曲家甲/曲目甲/版本甲/   上傳兩份 PDF
     作曲家乙/曲目乙/版本乙/   上傳一份 PDF
   ```

   記下三個 ID：根資料夾（網址 `folders/` 後面那段）、`版本甲`、`版本甲` 裡的一份 PDF（右鍵 → 分享 → 複製連結，`/file/d/` 後面那段）。
4. **舊譜庫試算表**：記下 ID（網址 `/d/` 與 `/edit` 之間）。確認它**不在**測試根資料夾底下，否則第 1 步選資料夾會連帶影響第 3 項。
   - 測試帳號沒有舊試算表時，先跑 `old-sheet` 用舊範圍建一份。
5. 在 repo 根目錄、以 `.venv` 的 Python 執行；下文的 `probe` 代表 `python docs/notes/drive_file_picker_probe.py`。每個指令加 `--step <編號>`，紀錄才分得出是哪一步。

## 步驟

順序有意義：第 3 項與基準線必須在任何 Picker 選取**之前**做。

| 步驟 | 指令 | 你要做的 | 對應項目 |
| --- | --- | --- | --- |
| A | `probe --step A login` | 用譜庫帳號登入。截圖每一個畫面（有沒有「Google 尚未驗證」、同意畫面顯示的名稱與範圍文字） | 4 |
| B | `probe --step 3a sheet <試算表ID>` | 無 | 3（前段） |
| C | `probe --step base tree <根ID> --expect <PDF ID>` | 無。預期失敗；若列得出來，表示有別的權限混進來，停下回報 | 4（403 或 404） |
| D | `probe --step pick pick` | 在 Picker 裡**只選根資料夾**「泠靈實測 Picker」，按選取。截圖 Picker 畫面 | 1 |
| E | `probe --step 1 tree <根ID> --expect <PDF ID>` | 對照終端機印出的樹：根底下六個子資料夾、三份 PDF 是否都在 | **1（決定方向）** |
| F | `probe --step 1w write <版本甲ID> <PDF ID>` | 無。腳本會建子資料夾、上傳、改名、換內容、移動手動的 PDF，再全部復原 | 1（補充：寫入） |
| G | 到 Drive 網頁：在 `版本甲` 放一份新 PDF；在 `曲目甲` 底下新建 `版本甲二`，放一份 PDF | 手動 | 2 |
| H | `probe --step 2 tree <根ID> --refresh` | 對照：G 新放的兩份 PDF 與 `版本甲二` 是否出現 | 2 |
| I | 只在 B 失敗時：`probe --step 3b-pick pick --sheet`，在 Picker 選舊譜庫試算表；再 `probe --step 3b sheet <試算表ID>` | 在 Picker 選試算表 | 3（後段） |
| J | `probe report` | 把輸出連同截圖、E 與 H 的對照結果交給 agent | 全部 |

第 F 步會讓那份手動 PDF 在 Drive 多一個內容相同的舊版，不影響檔案本身。

## 判定

- **第 1 項**：E 列出全部資料夾與三份 PDF，且 `files.get(media)` 讀得到內容 → 過，走 C。缺任何一項 → 退回 A。F 的寫入動作若失敗，記下哪一個；它不在 #68 的判定標準裡，但會影響走 C 時改名、收集檔案、存入能不能做到手動上傳的檔。
- **第 2 項**：H 看得到 G 新放的檔 → 版本頁「登記既有的檔」維持原設計；看不到 → 改開 Picker 挑檔。
- **第 3 項**：B 成功 → 舊試算表不用補選；B 失敗、I 成功 → 改範圍後用 Picker 補選一次；兩者都失敗 → 回報，需要重新討論。
- **第 4 項**：A 的截圖與 C 的狀態碼、`reason` 直接記錄。

## 收尾

- 刪掉 `~/.cache/lingling-picker-test/`（裡面有只帶 `drive.file` 的明文權杖）。
- 測試資料夾樹可留可刪。
- 除非範圍檢查要求，不必到 Google 帳戶移除泠靈的存取權；移除後泠靈原本的登入會失效，要重新登入。範圍檢查要求移除時，第 3 項的結果要註明「是在移除存取權之後測的」。

## 結果

2026-10-13 15:14 至 15:39（UTC+8），Python 3.13.13、google-auth 2.58.0、google-auth-oauthlib 1.4.1、google-api-python-client 2.200.0、oauthlib 3.3.1。Cloud 專案 `ling-ling-suite` 處於「測試中」。每一步的範圍檢查都只有 `drive.file`。

**結論：第 1 項沒過，退回 A（保留 `drive`）。**

| 項目 | 結果 |
| --- | --- |
| 1 | 沒過。譜庫帳號 `hxsheetmusic` 在 15:19 從網頁建了 `泠靈實測 Picker/朱雲嵩`、`泠靈實測 Picker/顧冠仁`；15:23 用 Picker 只選根資料夾，`picked_file_ids` 帶回根資料夾的 ID。之後 `files.get` 根資料夾成功，但 `'<根ID>' in parents` 的 `files.list` 回 0 筆；15:39 換新的 access token 重跑仍是 0 筆，排除授權生效延遲。選資料夾只授權那個資料夾本身，不及於選取前已在裡面的子項目 |
| 2 | 未測：退回 A 後 `drive` 看得到全部檔案，不影響決定 |
| 3 | 過（附帶）。以舊範圍（`spreadsheets`、`drive`）經 Sheets API 建立的試算表，換成只有 `drive.file` 的權杖後不經 Picker 就能 `files.get`、`spreadsheets.get`、`values.get`、新增並刪除工作表。測試帳號是 `nccucmusic`（測試使用者，不是譜庫帳號）；推論帳號不影響結論，未以譜庫帳號重測 |
| 4 | 存取被拒：`files.get` 回 **404 `notFound`**，不是 403；`files.list` 不報錯，只回 0 筆。這次基準線是用 `nccucmusic` 測的，它對該資料夾本來就沒有權限，所以 404 分不出是 `drive.file` 擋下還是一般的無權限；選取後的 `files.list` 回 0 筆（不報錯）則是 `hxsheetmusic` 自己的資料夾。同意畫面：測試中、只要求 `drive.file` 時，不在測試使用者名單的帳號被擋（403 `access_denied`，「已封鎖存取權：『泠靈小工具』未完成 Google 驗證程序」）；在名單上的帳號**沒有**「Google 尚未驗證」警告頁，直接進同意畫面，顯示名稱「泠靈小工具」、範圍文字「查看、編輯、建立及刪除您透過這個應用程式使用的特定 Google 雲端硬碟檔案」，並提示看不到隱私權政策與服務條款連結。未經驗證的正式版的畫面要等切換正式版才看得到 |

其他觀察：

- 桌面 Picker 的重新導向帶回 `code`、`iss`、`picked_file_ids`、`scope`、`state`；沒經過 Picker 時沒有 `picked_file_ids`。
- 瀏覽器同時登入多個 Google 帳號時，授權容易落到別的帳號（本次步驟 A、B、C 落在 `nccucmusic`）。泠靈的登入應帶 `login_hint` 或在登入後顯示帳號。

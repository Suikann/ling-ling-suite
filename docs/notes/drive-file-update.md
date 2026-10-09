# 調查：Drive 更新同一檔案內容的行為

- 調查日期：2026-10-10
- 對應 ticket：#48（地圖 #43；來源是 #45「存入譜庫」一節的 Q45）
- 範圍：Google Drive API v3 的 `files.update` 上傳新內容、`revisions` 資源、`keepForever`、resumable upload、權限範圍，以及 google-api-python-client 的用法
- 方法：只讀官方文件與原始碼（來源見文末，附各頁的「Last updated」日期），**沒有實際呼叫 API**。標「推論」的是由官方文字推得、但官方沒有直接寫明的結論；標「查無」的是官方文件找不到答案的問題。

## 結論摘要

| 問題 | 答案 |
|---|---|
| 檔案 ID 會不會變 | 不會。`files.update` 對同一個 `fileId` 上傳新內容，檔案 ID 不變，新內容成為新的 head revision。[S1][S2] |
| 舊內容保留規則 | 二進位檔（PDF 屬此類）的舊 revision 預設約保留 30 天；若該檔已有 100 個非 `keepForever` 的 revision，再上傳新版時可能提早清除。標成 `keepForever` 的不會被自動清除，每個檔案最多 200 個。head revision 永遠不會被自動清除。[S3][S4][S12] |
| `keepForever` 怎麼用 | 上傳時帶 `keepRevisionForever=true`，或事後用 `revisions.update` 把 `keepForever` 設為 `true`。[S6][S3] |
| 會不會佔配額 | `keepForever` 的 revision 會佔擁有者的配額；`quotaBytesUsed` 的定義只計 head 與 `keepForever` 的 revision，**推論**一般的舊 revision 不佔。[S3][S7][S14] |
| 能不能列出、還原 | 能列出（`revisions.list`）。API **沒有**「還原」方法；做法是先把舊 revision 設為 `keepForever`、下載（`revisions.get` 加 `alt=media`），再用 `files.update` 傳回去成為新的 head。[S4][S5][S9] |
| 大檔怎麼傳 | `uploadType=resumable`：第一個請求用 `PATCH` 開 session，之後用 `PUT` 傳內容；session URI 一週後失效。Python 用 `MediaFileUpload(..., resumable=True)` 搭配 `next_chunk()`。[S8][S15][S17] |
| 權限範圍 | 目前的 `https://www.googleapis.com/auth/drive` 已涵蓋 `files.update` 與全部 `revisions` 方法，不必新增。[S6][S5] |
| 對分譜工作表 `drive_file_id` 的影響 | 更新同一檔案：`drive_file_id` 不變。刪掉舊檔、上傳新檔：產生新 ID，必須回寫工作表，否則該列指向已刪除的檔案。詳見第 7 節。 |

## 1. 檔案 ID 會不會改變

不會。

- 官方對檔案 ID 的定義：「A unique, opaque ID for each file. File IDs are stable throughout the life of the file, even if the file name changes.」[S1]
- 對二進位檔（blob）的 revision 定義：「A new blob can be uploaded as a new revision, which becomes the new head revision of that file.」[S2]
- `files.update` 的路徑參數就是 `fileId`，上傳內容走 `PATCH https://www.googleapis.com/upload/drive/v3/files/{fileId}`，說明是「Updates a file's metadata, content, or both.」，且「This method supports patch semantics.」[S6]

會跟著變的是檔案資源上的這幾個欄位（皆為 output only）：`headRevisionId`（「The ID of the file's head revision. This is currently only available for files with binary content in Google Drive.」）、`md5Checksum`、`version`（「A monotonically increasing version number for the file.」）、`modifiedTime`。[S7]

因為是 patch 語意，請求的 `body` 不帶 `name` 就不改檔名；要同時改檔名，可在同一個請求的 metadata 帶 `name`（multipart 或 resumable 都能帶 metadata）。[S6][S8]

## 2. 舊內容以 revision 保留的規則

PDF 在 Drive 的分類是 blob（「A file type that contains raw binary or text content (such as images, videos, and PDFs)」）[S1]，以下規則都針對 blob。

**保留天數與數量**

- 「Any blob file revision, other than the head revision, that's not designated as "Keep Forever" is purgeable. Purgeable revisions are typically preserved for 30 days, but can be purged earlier if a file has 100 revisions that aren't designated as "Keep Forever" and a new revision is uploaded.」[S2][S3]
- `keepForever` 欄位的說明：「If not set, the revision will be automatically purged 30 days after newer content is uploaded.」[S4]
- 「The head revision is never auto-purged.」[S3]
- Drive 說明中心的使用者版說法：「A version might be permanently deleted after 30 days or if there are 100 newer versions.」[S12]

注意用詞是「typically」：30 天是通常情況，不是保證的下限。

**`keepForever`**

- 「A blob file revision can be set to "Keep Forever" meaning the revision cannot be automatically purged. Up to 200 revisions can be set to "Keep Forever" and they count towards your storage limit.」[S3]
- `keepForever` 欄位只適用二進位檔：「This field is only applicable to files with binary content in Drive.」，每個檔案最多 200 個。[S4]
- 兩種設定方式：
  1. 上傳當下：`files.update` 的查詢參數 `keepRevisionForever`，「Whether to set the keepForever field in the new head revision. … Only 200 revisions for the file can be kept forever. If the limit is reached, try deleting pinned revisions.」[S6]
  2. 事後：`revisions.update`（`PATCH .../files/{fileId}/revisions/{revisionId}`，patch 語意），在 body 設 `{"keepForever": true}`。[S3][S5]
- 「Once a blob file revision is set to "Keep Forever", it can only be downloaded or deleted.」[S3] 這句暗示設為 `true` 後不能再改回 `false`，但官方沒有明說，未實測。
- API v2 的對應欄位叫 `pinned`。[S3]

**刪除 revision**

- `revisions.delete` 永久刪除一個 revision；只限二進位檔，且「the last remaining revision of the binary file, can't be deleted.」`keepForever` 的 revision 可以刪。[S3][S5]

## 3. revision 會不會佔用配額

- `keepForever` 的 revision 會佔：「they count towards your storage limit」[S3]；說明中心也寫「pinned file versions all use your cloud storage」[S14]。
- 檔案的 `quotaBytesUsed` 定義：「The number of storage quota bytes used by the file. This includes the head revision as well as previous revisions with keepForever enabled.」[S7]
- **推論**：既然 `quotaBytesUsed` 只列 head 與 `keepForever` 的 revision，一般（可被清除的）舊 revision 不計入配額。官方沒有一句話正面寫「不計」，所以列為推論。
- 配額算在誰身上：「Files in shared folders only consume the cloud storage quota of the original file owner, never the recipient.」[S13]；用別的帳號上傳新版本時「the original owner remains the same」[S12]。#45 決定譜庫用社團共用帳號，配額就算在那個帳號。
- 垃圾桶裡的檔案也佔配額：「Your total cloud storage also counts items left in your Drive Trash」[S13]（和第 7 節「刪掉舊檔」的做法有關）。

## 4. 能不能用 API 列出並還原舊的 revision

**列出：可以。**

- `GET https://www.googleapis.com/drive/v3/files/{fileId}/revisions`，`pageSize` 未指定時最多回 200 筆，上限 1000，用 `nextPageToken` 翻頁。[S5]
- 預設只回 `id`、`mimeType`、`kind`、`modifiedTime`，要其他欄位（如 `keepForever`、`size`、`md5Checksum`、`originalFilename`）得用 `fields` 指定。[S3]
- 警告：「The list of revisions returned by this method might be incomplete for files with a large revision history, including frequently edited Google Docs, Sheets, and Slides. Older revisions might be omitted from the response」[S5]。舉例是 Google 文件類，但措辭沒有排除二進位檔。
- 存取 revision 歷史需要 owner、organizer、fileOrganizer 或 writer 角色。[S3]

**還原：API 沒有現成方法。**

- `revisions` 資源只有 `delete`、`get`、`list`、`update` 四個方法 [S4]，沒有 restore 或 revert。
- 要拿回舊內容，得先下載：「You can only download blob file content revisions that are marked as "Keep Forever". If you want to download a revision, set it to "Keep Forever" first.」下載方式是 `revisions.get` 加 `alt=media`。[S9][S3]
- 因此「還原」的可行流程（由上述文件組合而成，**未實測**）：
  1. `revisions.list` 找到目標 revision。
  2. 若它還不是 `keepForever`，用 `revisions.update` 設為 `true`（必須在它被清除前，也就是約 30 天內）。
  3. `revisions.get_media` 下載內容。
  4. `files.update` 把內容傳回同一個 `fileId`，成為新的 head revision；檔案 ID 不變，原本較新的那份變成一般的舊 revision。

```python
revs = service.revisions().list(
    fileId=file_id,
    fields="nextPageToken, revisions(id, modifiedTime, keepForever, size, md5Checksum)",
).execute()
service.revisions().update(
    fileId=file_id, revisionId=rev_id, body={"keepForever": True},
).execute()
request = service.revisions().get_media(fileId=file_id, revisionId=rev_id)
# 用 googleapiclient.http.MediaIoBaseDownload 寫到本機，再走第 5 節的 files.update
```

Python 用戶端確實有 `revisions().get_media(fileId, revisionId, acknowledgeAbuse=None)` 這個方法。[S16]

## 5. 大檔怎麼用 resumable upload

**選哪一種上傳方式** [S8]

- `uploadType=media`／`multipart`：適用 5 MB 以下。
- `uploadType=resumable`：適用大於 5 MB、或網路容易中斷的情況；「Resumable uploads are also a good choice for most applications because they also work for small files at a minimal cost of one additional HTTP request per upload.」
- `files.update` 的上傳上限是 5,120 GB。[S6]

**HTTP 流程** [S8]

1. 開 session：對 `/upload` URI 送請求並帶 `uploadType=resumable`；「To update an existing file, use PATCH.」也就是 `PATCH https://www.googleapis.com/upload/drive/v3/files/{fileId}?uploadType=resumable`，body 可放 JSON metadata（例如新檔名）。成功回 `200 OK`，`Location` 標頭是 session URI。「A resumable session URI expires after one week.」
2. 傳內容：之後一律用 `PUT` 打 session URI（「Use PUT for all subsequent requests for a resumable upload once the request has started.」）。分塊上傳時，每塊大小須為 256 KB 的倍數（最後一塊除外），以 `Content-Range` 標示位置。
3. 中斷續傳：送空的 `PUT` 加 `Content-Range: */<總長度>` 查詢進度；`308 Resume Incomplete` 依回應的 `Range` 標頭接著傳，`200`／`201` 代表已完成，`404` 代表 session 過期、必須從頭開始。
4. 錯誤處理：5xx 續傳或重試；403 rate limit 重試；「For any 4xx errors (including 403) during a resumable upload, restart the upload.」

**google-api-python-client 寫法**

- `files().update(fileId, …, keepRevisionForever=None, media_body=None, media_mime_type=None, …)` 有 `media_body` 與 `keepRevisionForever` 參數。[S16]
- 「To use resumable media you must use a MediaFileUpload object and flag it as a resumable upload. You then repeatedly call next_chunk() on the googleapiclient.http.HttpRequest object until the upload is complete.」[S15]
- 原始碼細節 [S17]：
  - `MediaFileUpload` 的預設 `chunksize` 是 `DEFAULT_CHUNK_SIZE = 100 * 1024 * 1024`（100 MiB），只在 `resumable=True` 時使用，傳 `-1` 表示一次傳完。
  - 開 session 的第一個請求用該 API 方法本身的 HTTP 動詞（`files.update` 即 `PATCH`），之後的內容用 `PUT`，與 HTTP 流程一致。
  - `next_chunk(num_retries=N)` 與 `execute(num_retries=N)` 都會以隨機化的指數退避重試；`execute()` 遇到 resumable 請求時內部就是反覆呼叫 `next_chunk()`。
  - session URI 存在 `HttpRequest` 物件裡，程式結束就沒了；要跨程式重啟續傳得自行保存，這部分官方文件沒有範例。
- 可重試的狀態碼：「404 Not Found (must restart upload)」、500、502、503、504，重試須用指數退避。[S15]

```python
from googleapiclient.http import MediaFileUpload

media = MediaFileUpload(local_path, mimetype="application/pdf", resumable=True)
request = service.files().update(
    fileId=drive_file_id,
    body={"name": new_name},   # 可省略；patch 語意，不帶就不改檔名
    media_body=media,
    keepRevisionForever=False,
    fields="id, name, headRevisionId, md5Checksum, version",
)
response = None
while response is None:
    status, response = request.next_chunk(num_retries=3)
    if status:
        progress = status.progress()  # 0.0 到 1.0，可接進度列
```

## 6. 需要哪個權限範圍

目前 `catalog_constants.py:78-81` 申請 `spreadsheets` 與 `drive` 兩個範圍。

各方法接受的範圍（任一即可）：

| 方法 | 接受的範圍 | 來源 |
|---|---|---|
| `files.update` | `drive`、`drive.appdata`、`drive.file`、`drive.metadata`、`drive.scripts` | [S6] |
| `revisions.list`、`revisions.get` | `drive`、`drive.appdata`、`drive.file`、`drive.meet.readonly`、`drive.metadata`、`drive.metadata.readonly`、`drive.photos.readonly`、`drive.readonly` | [S5] |
| `revisions.update`、`revisions.delete` | `drive`、`drive.appdata`、`drive.file` | [S5] |

結論：現有的 `drive` 已涵蓋全部，不必新增範圍。

相關事實（供日後縮小範圍時參考，本 ticket 不做決定）：

- `drive` 屬於 restricted 範圍（「View and manage all your Drive files.」）；`drive.file` 是 non-sensitive，但只涵蓋「Create new Drive files, or modify existing files, that you open with an app or that the user shares with an app while using the Google Picker API or the app's file picker.」[S11]
- **推論**：譜庫現有功能（`drive_service.py` 的 `list_subfolders`、`list_pdfs_in_folder`、`scan_library_folder`、`rename_file`、`move_file`）處理的是使用者在 Drive 網頁放進去的資料夾與 PDF，不是泠靈建立的檔案，改成只用 `drive.file` 會失去這些檔案的存取權。所以現有功能仍需要 `drive`。

## 7. 「更新同一檔案」與「刪掉舊檔、上傳新檔」的比較

分譜工作表的欄位是 `id, edition_id, movement_id, instrument_name, sort_order, status, drive_file_id, file_name, notes`（`catalog_constants.py:67-70`）。譜庫畫面比對 Drive 上的 PDF 與分譜列時，先用 `drive_file_id`、找不到再用 `file_name`（`catalog_window.py:798-799, 861`）；按下狀態下拉選單時，比對不到既有列就新增一列（`catalog_window.py:907-918`）。

| 面向 | 更新同一檔案（`files.update`） | 刪掉舊檔、上傳新檔（`files.delete` 或移到垃圾桶，加 `files.create`） |
|---|---|---|
| `drive_file_id` | 不變 [S1]，工作表不必改這一欄 | 新檔有新 ID，**必須**回寫；漏寫時該列指向已刪除的檔案 |
| 現有比對邏輯 | 不受影響 | ID 比對不到，退回用檔名；檔名也變了（例如群組改名）就變成孤兒列，之後改狀態會再新增一列，造成重複 |
| 舊內容 | 成為舊 revision，約 30 天／100 版內可取回；`keepRevisionForever=true` 可永久保留（每檔 200 個，佔配額） [S3][S6] | `files.delete`：「Permanently deletes a file owned by the user without moving it to the trash.」無法復原 [S10]。移到垃圾桶：30 天內可還原，期間佔配額 [S10][S13] |
| 舊版與新版的關聯 | 同一檔案的 revision 歷史 | 沒有關聯，是兩個不相干的檔案 |
| 擁有者 | 不變：「If you upload a new version of a file owned by someone else, the original owner remains the same.」[S12] | 新檔的擁有者是上傳的帳號；別人的帳號放進共用資料夾的新檔佔上傳者的配額 [S13] |
| 誰能做 | 需要 writer 以上的角色（**推論**：上傳內容即編輯檔案） | 移到垃圾桶限擁有者：「Only the file owner can trash a file」；`files.delete` 也只刪「a file owned by the user」[S10]（共用雲端硬碟另有規則） |
| 共用設定 | 同一個檔案資源，檔案層級的共用設定保留（**推論**：ACL 屬於檔案，不屬於 revision [S1]） | 檔案層級的共用設定隨舊檔消失：「After you delete a file, anyone you've shared the file with loses access to it.」[S10]；從資料夾繼承的存取權對新檔仍有效 [S18] |
| 中途失敗 | 只有一個 API 呼叫；上傳沒完成時 ID 照舊（**推論**：官方沒寫未完成的 resumable upload 不會產生 revision） | 兩步驟之間可能失敗：先刪後傳失敗，Drive 上沒有檔案；先傳後刪失敗，留下兩份；回寫工作表失敗，ID 失準。另見 #45 記錄的「試算表寫入沒有鎖」 |

結論：就 `drive_file_id` 而言，更新同一檔案只需在存入時確認該列已有 `drive_file_id`，有就 `files.update`，沒有才 `files.create` 並寫入新 ID；刪掉舊檔、上傳新檔則每次都要回寫 ID，且舊內容無法用 Drive 的版本機制取回。#45 Q45 想要的「更新同一個 Drive 檔案的內容、保留舊版本」在 API 層面可行，限制是預設只保留約 30 天，要永久保留就得用 `keepForever` 並承擔配額與每檔 200 個的上限。

## 查無／未驗證

- 上傳與現有 head 完全相同的內容時，是否仍會新增 revision：查無。
- 刪除 head revision 後，前一個 revision 是否自動成為 head（能否當作「還原」的替代做法）：查無。官方只說最後一個 revision 不能刪。
- `keepForever` 設為 `true` 後能否改回 `false`：官方只說「it can only be downloaded or deleted」，未明說，未實測。
- 一般（非 `keepForever`）舊 revision 在被清除前是否計入配額：官方沒有正面敘述，第 3 節的結論是由 `quotaBytesUsed` 定義推得。
- `files.update` 後檔案層級的共用權限是否保留：沒有直接敘述，依 ACL 屬於檔案資源推論。
- `revisions.list` 對 PDF 是否也可能漏列舊 revision：官方警告沒有排除二進位檔。
- 用 `files.update` 對一個已被手動刪除或移到垃圾桶的 `fileId` 上傳時回什麼錯誤：未查。
- 以上所有行為都沒有用實際帳號呼叫 API 驗證。

## 來源

Google Drive API v3 官方文件（developers.google.com）：

- [S1] Files and folders overview：https://developers.google.com/workspace/drive/api/guides/about-files （Last updated 2026-09-03）
- [S2] Changes and revisions overview：https://developers.google.com/workspace/drive/api/guides/change-overview （2026-09-03）
- [S3] Manage file revisions：https://developers.google.com/workspace/drive/api/guides/manage-revisions （2026-09-03）
- [S4] REST Resource: revisions：https://developers.google.com/workspace/drive/api/reference/rest/v3/revisions （2025-07-23）
- [S5] revisions.list：https://developers.google.com/workspace/drive/api/reference/rest/v3/revisions/list （2026-07-07）；revisions.get／update／delete：同路徑下的 `get`、`update`、`delete`（皆 2025-08-13）
- [S6] Method: files.update：https://developers.google.com/workspace/drive/api/reference/rest/v3/files/update （2026-03-20）
- [S7] REST Resource: files：https://developers.google.com/workspace/drive/api/reference/rest/v3/files （2026-07-14）
- [S8] Upload file data：https://developers.google.com/workspace/drive/api/guides/manage-uploads （2026-09-03）
- [S9] Download and export files：https://developers.google.com/workspace/drive/api/guides/manage-downloads （2026-09-03）
- [S10] Trash or delete files and folders：https://developers.google.com/workspace/drive/api/guides/delete （2026-09-03）；Method: files.delete：https://developers.google.com/workspace/drive/api/reference/rest/v3/files/delete （2026-05-21）
- [S11] Choose Google Drive API scopes：https://developers.google.com/workspace/drive/api/guides/api-specific-auth （2026-09-03）
- [S18] Manage folders with limited and expansive access：https://developers.google.com/workspace/drive/api/guides/limited-expansive-access （2026-09-03）

Google Drive 說明中心（support.google.com）：

- [S12] Check activity & file versions：https://support.google.com/drive/answer/2409045
- [S13] How your Google storage works：https://support.google.com/drive/answer/9312312
- [S14] Understand your computer and Google storage when using Drive for desktop：https://support.google.com/drive/answer/17196458

google-api-python-client：

- [S15] Media Upload：https://googleapis.github.io/google-api-python-client/docs/media.html
- [S16] drive_v3 files／revisions 方法說明：https://googleapis.github.io/google-api-python-client/docs/dyn/drive_v3.files.html 、https://googleapis.github.io/google-api-python-client/docs/dyn/drive_v3.revisions.html
- [S17] `googleapiclient/http.py` 原始碼，main 分支上最後修改該檔的 commit `ecb50949dce33f06624ec9a5acd8e3d1cacb1d17`（2026-09-09）：https://github.com/googleapis/google-api-python-client/blob/ecb50949dce33f06624ec9a5acd8e3d1cacb1d17/googleapiclient/http.py

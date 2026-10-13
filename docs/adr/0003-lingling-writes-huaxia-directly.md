---
status: accepted
date: 2026-10-13
---

# 泠靈直傳 HuaXia

HuaXia 是社團既有的網站（Supabase 加 GitHub Pages），團員在上面讀譜、幹部在上面排人，由它自己的維護者管理，不屬於本 repo。編制與分譜的定本在泠靈與譜庫（ADR 0002）；HuaXia 的 `piece_parts.needed` 是排人與缺額用的，幹部可以在網頁上調整，與樂譜要求的人數是不同的概念。我們決定：泠靈以幹部自己的 Discord 帳號登入 HuaXia，從桌面程式直接寫入它的 Supabase（`pieces`、`piece_parts`、`scores` 與 Storage bucket `scores`），編制只從泠靈流向 HuaXia，不經 HuaXia 網頁的「匯入編制表」與「批次上傳」。泠靈的 HuaXia 用戶端直接打 Supabase 的 REST API（Auth、PostgREST、Storage），不用 `supabase-py`。

依據的版本：Supabase Auth `v2.197.0`、HuaXia main `5cf811d`。

## Considered Options

- 經 HuaXia 網頁：泠靈產出貼進「匯入編制表」的文字，分譜由譜務在網頁批次上傳。當初排除它，是因為網頁原有的這兩項功能不好用。之後定案的發譜行為都建立在直接寫入上：一次發譜全成或全不成；重發時只取代同聲部、泠靈發的那份（網頁上傳的 Storage 路徑是時間戳，分不出誰發的）；撤回、收回與發譜紀錄要知道每次發了哪些檔、掛了哪些場次。現在改回網頁，等於放棄這些功能。
- `supabase-py`：多 25 個套件（含用不到的 `realtime`、`websockets`），也不提供本機回呼伺服器。它預設把整個 session 連同 access token 存起來，自動 refresh 跑在背景執行緒，要配合「安全儲存只存 refresh token」與 PySide6 的主執行緒，兩者都得改寫，省下的只有 refresh token 輪替。直接打 REST 用的 `requests` 已在相依樹裡，不增加套件。
- 回呼網址用固定埠並請維護者在 Supabase 後台登記（第一輪的決定）：`localhost` 必須登記，維護者忘了或 HuaXia 重建專案就全社團登不進去，固定埠也可能被別的程式占用；`127.0.0.1` 固定埠加備援登記，在現行邏輯下登記不起作用。

## Consequences

- **三段式發譜**：一次發譜的資料庫寫入（新建曲目、建聲部、改人數與聲部組、登記新樂譜、撤掉舊樂譜的登記、掛場次）交給 HuaXia 新增的發譜函式一次完成。Storage 的檔案包不進資料庫交易，所以發譜分三段：傳檔（先不登記）、切換（呼叫函式）、清舊檔。函式以呼叫者身分執行，HuaXia 的 RLS 照常生效，不是幹部就寫不進去。HuaXia 沒有這個函式或版本不符時，發譜整個不能用，畫面說明要請維護者處理哪個合併請求。
- **對 HuaXia 的修改**：以合併請求提出，只放泠靈需要的項目，逐項附理由：

  | 項目 | 理由 |
  |---|---|
  | 發譜函式，明寫 `grant execute ... to authenticated` | 讓一次發譜的資料庫寫入全成或全不成；Supabase 自 2026-10-30 起不再自動授權新物件 |
  | 發譜函式的測試（`tests/schema.test.mjs`） | HuaXia 改資料表時由它自己的測試發現破壞 |
  | 泠靈依賴說明文件，`CLAUDE.md` 加一行指向 | 接手的人改資料表前知道泠靈依賴哪些資料表、欄位、意義與發譜函式的介面，也包括經 `rpc` 呼叫 `is_officer()`、單檔上限 20MB |
  | `docs/SETUP.md` 寫明開啟 Allow manual linking（後台設定由維護者自己點） | 沒開的話「連結 Discord」不能用，泠靈的提示指向死路 |
  | 網頁讀出連結 Discord 回來時的 `identity_already_exists` 並顯示原因 | 現在按了沒反應 |
  | `docs/MAINTENANCE.md`「同一個人變成兩個帳號」補上刪除位置，註明「拒絕」不會刪帳號 | 維護者照泠靈的提示處理時有一份能照做的說明 |

  後三項修的是 HuaXia 本來就有的缺口（網頁的 Discord 登入同樣會多建帳號）；泠靈多開了一個登入入口，這三項是泠靈提示的後盾。
- **刻意不提的修改**：`is_officer()` 不另加 `grant execute`，因為 HuaXia 自己的 RLS policy 也呼叫它，重建專案時失去權限會先讓 HuaXia 網頁壞掉，修好網頁就修好了泠靈。`scores` bucket 不加 `file_size_limit`：20MB 是 HuaXia 的政策，泠靈在送出前自己擋，寫進資料庫只會讓 HuaXia 放寬上限時多改一處。
- **回呼依賴沒寫進文件的行為**：Discord 登入的回呼網址是 `http://127.0.0.1:<隨機埠>/`，不請維護者登記。Supabase Auth 對 loopback IP 主機直接放行、不比對放行名單（`internal/utilities/request.go:118-122`，v2.171.0 起；RFC 8252 §7.3 也這樣要求），但官方文件沒有承諾。Supabase 若改成 IP 主機也比對放行名單，Supabase 會把瀏覽器導回 HuaXia 首頁、不報錯，全社團都登不進去。泠靈等瀏覽器 5 分鐘後結束，請使用者回報；處理方式是泠靈改版，並請維護者在後台登記回呼網址。
- **refresh token 輪替要自己處理**：Supabase 的 refresh token 只能用一次，重用舊的會撤銷整個 session。泠靈同一時間只能有一個 refresh，換到新的 refresh token 立刻寫回安全儲存。網路請求放在 QThread。

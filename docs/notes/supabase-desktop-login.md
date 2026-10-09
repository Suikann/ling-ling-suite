# Supabase 桌面登入與 API 能力查證

對應 issue #47，供「泠靈直傳 HuaXia」ADR 與發譜規格引用。查證日期 2026-10-10。HuaXia 的程式位置以 main `b84079b` 為準，寫成 `HuaXia:<檔案>:<行>`。

## 來源版本

| 來源 | 釘選版本 | 說明 |
|---|---|---|
| `supabase/auth`（Supabase Auth，舊名 GoTrue） | tag `v2.197.0` | HuaXia 專案正在跑的版本：2026-10-10 以 publishable key 呼叫 `GET /auth/v1/health` 回傳 `"version":"v2.197.0"` |
| `supabase/supabase` 官方文件原始檔 | commit `eeae602` | `apps/docs/content/guides/` 下的 `.mdx`，下文簡稱「文件」 |
| `supabase/storage` | commit `f31599e` | Storage 伺服器原始碼 |
| `supabase/supabase-js` | commit `b9753a1` | `packages/core/auth-js`，官方 JS 用戶端，當作參考實作 |
| PyPI `supabase` 系列 | `2.32.0` | `supabase`、`supabase-auth`、`postgrest`、`storage3` 等，裝在暫存 venv 量測 |
| PostgREST 文件原始檔 | commit `f7abddf` | `docs/references/` |
| PostgreSQL 文件 | 18 | `ddl-priv.html`、`sql-createfunction.html` |
| RFC 8252 | — | OAuth 2.0 for Native Apps |
| Discord 開發者文件 | — | `docs.discord.com/developers/topics/oauth2` |

另外對 HuaXia 正式環境做了三次唯讀請求，只帶網頁上公開的 publishable key（`HuaXia:config.js:4-5`）：`GET /auth/v1/health`、`GET /auth/v1/settings`、`GET /rest/v1/rpc/is_officer`，另以 `GET /rest/v1/rpc/discord_ids` 對照。

## 摘要

1. 流程是：瀏覽器開 `GET /auth/v1/authorize?provider=discord&redirect_to=…&code_challenge=…&code_challenge_method=s256`，回呼收到 `?code=`，再 `POST /auth/v1/token?grant_type=pkce` 換 token。授權碼從呼叫 authorize 起算 300 秒內有效，只能兌換一次。access token 預設 3600 秒；refresh token 不會過期但只能用一次，每次 refresh 都會換新的。重複使用舊 token 會讓整個 session 失效，有兩個例外：重用間隔內（hosted 預設 10 秒），以及「目前有效 token 的上一代」。
2. 回呼網址如果用 `127.0.0.1` 或 `[::1]`，**任何埠都會被接受，不必登記到 Redirect URLs**：v2.197.0 的程式碼裡，IP 型主機在比對允許清單之前就直接判定，只要是 loopback 就放行。這項行為官方文件沒有寫。用 `localhost` 就要登記，例如 `http://localhost:53682/**`。萬用字元的 `*` 依文件語意可以涵蓋埠號，但沒有官方範例，本次也沒有實測。網址不合格時退回 Site URL，也就是 HuaXia 的 GitHub Pages，泠靈收不到授權碼。
3. 可以呼叫。`is_officer()` 在 public schema，沒有被 revoke；Postgres 預設把函式的 EXECUTE 授予 PUBLIC。實測匿名 `GET /rest/v1/rpc/is_officer` 回 `200 false`，被 revoke 的 `discord_ids` 則回 `401 42501`。登入後帶 `Authorization: Bearer <access_token>` 呼叫，回傳裸 JSON 布林值。
4. `schema.sql:865` 建 bucket 只給了 `id`、`name`、`public`，`file_size_limit` 為 null，所以只受專案層級的全域上限約束。Free 方案全域上限不能超過 50 MB，HuaXia 實際設定值查無。20 MB 只有 HuaXia 前端在擋，伺服器不擋。超過上限時回 HTTP 413。
5. `supabase-py` 2.32.0 會在泠靈現有的相依套件之外多帶 26 個套件（Python 3.11 以上是 25 個），安裝後約 17.5 MB，其中 pydantic 就佔 8.3 MB。它不提供本機回呼伺服器，自動 refresh 走 `threading.Timer` 背景執行緒，預設把整個 session 存在記憶體。直接打 REST 則只需要 `requests`，它已經在泠靈的相依樹裡（經由 `google-api-core`），不增加任何套件。

---

## 1. Discord＋PKCE 的桌面登入流程

### 步驟

1. 泠靈產生 `code_verifier`，再算出 `code_challenge = BASE64URL(SHA256(code_verifier))`，method 用 `s256`。伺服器檢查 challenge 長度在 43–128 之間，字元限 `[A-Za-z0-9._~-]`（`supabase/auth@v2.197.0:internal/api/pkce.go:14-15,21-31`）。method 不分大小寫，只接受 `s256` 和 `plain`（`internal/models/flow_state.go:69-77`）。官方 JS 用戶端送的是小寫 `s256`（`supabase-js@b9753a1:packages/core/auth-js/src/lib/helpers.ts:508`）。
2. 泠靈只在 loopback 介面上開一個 HTTP 監聽（RFC 8252 §8.3：「Clients should listen on the loopback network interface only」），並用**系統瀏覽器**開啟授權網址。RFC 8252 §8.12 規定原生程式「MUST NOT use embedded user-agents to perform authorization requests」，所以不能用 PySide6 WebEngine 內嵌登入。
3. 授權網址：

   ```
   GET https://<ref>.supabase.co/auth/v1/authorize
       ?provider=discord
       &redirect_to=<URL 編碼後的本機回呼網址>
       &code_challenge=<challenge>
       &code_challenge_method=s256
       [&scopes=<額外的 Discord scope>]
   ```

   伺服器讀取的參數見 `internal/api/external.go:43-46`；路由見 `internal/api/api.go:226`；參數組法和官方 JS 用戶端一致（`GoTrueClient.ts:5694-5737`）。Discord provider 預設已帶 `email`、`identify` 兩個 scope（`internal/api/provider/discord.go:41-46`），泠靈不必另外指定。Supabase 會建立一筆 flow state，拿它的 id 當 OAuth `state`，然後 302 轉到 Discord（`external.go:123-134`）。
4. Discord 那一端只認得 Supabase 自己的 callback（`https://<ref>.supabase.co/auth/v1/callback`）。HuaXia 已經登記過（`HuaXia:docs/SETUP.md:22-23`），本機回呼網址完全不經過 Discord，因此不需要動 Discord 的設定。Discord 文件寫的是：「`redirect_uri` is whatever URL you registered when creating your application」。
5. 使用者同意後，Supabase 把瀏覽器轉到 `redirect_to?code=<auth_code>`（`internal/api/external.go:278-283`、`internal/api/verify.go:535-544`）。失敗時同一個網址的 query 會帶 `error`、`error_code`、`error_description`（`external.go:821-845`）。PKCE 的結果放在 query 而不是 fragment，本機 HTTP 伺服器讀得到。implicit flow 把 token 放在 `#` fragment，伺服器端收不到，這也是桌面程式必須用 PKCE 的原因之一。
6. 兌換授權碼：

   ```
   POST https://<ref>.supabase.co/auth/v1/token?grant_type=pkce
   apikey: <publishable key>
   Content-Type: application/json

   {"auth_code": "<code>", "code_verifier": "<verifier>"}
   ```

   見 `internal/api/token.go:31-34,54,214-289`、`api.go:268`。官方 JS 用戶端的寫法相同（`GoTrueClient.ts:2058-2070`）。回應欄位有 `access_token`、`token_type`、`expires_in`、`expires_at`、`refresh_token`、`user`，另外 `provider_token` 是 Discord 的 token（`internal/tokens/service.go:111-122`）。
7. 更新：`POST /auth/v1/token?grant_type=refresh_token`，body 為 `{"refresh_token": "<token>"}`（`token.go:50`；JS 用戶端見 `GoTrueClient.ts:4790`）。

### 有效期

| 項目 | 有效期 | 根據 |
|---|---|---|
| 授權碼／flow state | 從呼叫 `/authorize` 起算 300 秒（OAuth 以 flow state 的建立時間計），兌換一次後刪除 | `internal/conf/configuration.go:26,1274-1275`、`internal/models/flow_state.go:210-214`、`token.go:241,276`；文件 `auth/sessions/pkce-flow.mdx:22`：「the code has a validity of 5 minutes and can only be exchanged for an access token once」 |
| access token（JWT） | 預設 3600 秒，可在後台調整 | `configuration.go:1141`；文件 `auth/sessions.mdx:72`。**HuaXia 的實際設定查無**，應以回應的 `expires_in`／`expires_at` 為準，不要寫死 |
| refresh token | 不會過期，但只能用一次 | 文件 `auth/sessions.mdx:15`：「refresh tokens never expire but can only be used once」 |
| session | 預設沒有期限。限時、閒置逾時、單一 session 三項設定只有 Pro 以上方案能開 | 文件 `auth/sessions.mdx:11,44`。HuaXia 用免費方案（`HuaXia:docs/SETUP.md:12`），所以這三項都開不了 |

### refresh token 的輪替規則

- 輪替預設開啟（`configuration.go:902`，`default:"true"`）。每次 refresh 都會發一個新的 refresh token，舊的作廢。
- 有兩種舊 token 重用是允許的（文件 `auth/sessions.mdx:87-96`）：
  - 在重用間隔內再次使用。hosted 預設 10 秒（文件 `sessions.mdx:89`）；開源版設定的預設值是 0（`example.env`）。
  - 使用的是「目前有效 token 的上一代」。這時伺服器回傳目前有效的那一個，不另發新的（`internal/tokens/service.go:382-391`）。用來處理用戶端沒收到或沒存下上一次 refresh 結果的情況。
- 其他情況的重用一律視為疑似竊用：撤銷整個 token 家族或登出整個 session，並回錯誤 `refresh_token_already_used`「Invalid Refresh Token: Already Used」（`service.go:395-407,562-576`）。
- 對泠靈的影響：
  - 每次 refresh 成功就要立刻把新 token 寫進 keyring。
  - 同一時間只能有一個 refresh 在進行。
  - 因為有「上一代」例外，refresh 之後、寫入之前當掉，下次啟動還能救回一次；超過這個範圍就只能重新登入。

### 補充

- HuaXia 網頁用 `createClient(url, key)`，沒有傳任何選項（`HuaXia:js/core.js:11-12`），而 supabase-js 的預設 `flowType` 是 `'implicit'`（`supabase-js:packages/core/supabase-js/src/lib/constants.ts:44`），所以網頁走的是 implicit flow。泠靈走 PKCE 不會影響網頁。兩邊各自是獨立的 session，泠靈登入也不會把網頁登出，因為沒有開單一 session 限制。
- `GET /auth/v1/settings` 回傳 `"discord":true`、`"disable_signup":false`：Discord 登入已開啟，第一次登入的 Discord 使用者會自動建立帳號。

## 2. Redirect URLs 允許清單與本機回呼

### 伺服器的判定順序

v2.197.0 的 `IsRedirectURLValid`（`internal/utilities/request.go:94-139`）依序判定：

1. 主機、scheme 與 Site URL 相同，而且埠也相同：放行。如果主機是 `localhost`、`127.0.0.1`、`::1`、`0.0.0.0` 或 `::`，就不比對埠（`request.go:106-113`、`internal/utilities/url_validator.go:72-89`；2026-06-01 的 PR #2479，依 RFC 8252 §7.3）。HuaXia 的 Site URL 是 GitHub Pages，這一條用不上。
2. 主機是純數字（十進位 IP）：拒絕。
3. **主機是 IP 字面值：只要是 loopback 就放行，否則拒絕，完全不看允許清單**（`request.go:118-122`）。這條規則在 v2.171.0（2025-04-14，PR #1984）加入。
4. 其餘情況才用允許清單比對，比對前會先去掉 `#` fragment（`request.go:128-136`）。

### 本機回呼網址的寫法

- **`127.0.0.1` 或 `[::1]`**：依上面第 3 步，`http://127.0.0.1:<任意埠>/<任意路徑>` 不必登記就會被接受。單元測試 `request_test.go` 裡有「valid loopback IPv4 address」`http://127.0.0.1:12345/path` 和 IPv6 `http://[0:0:0:0:0:0:0:1]:12345/path` 兩個案例；不過那份測試的允許清單剛好也含 `http://*:12345/*`，所以測試本身不能單獨證明這條規則，結論是從程式碼本身讀出來的。這也符合 RFC 8252：§7.3 要求「The authorization server MUST allow any port to be specified at the time of the request for loopback IP redirect URIs」，§8.3 寫「the use of localhost is NOT RECOMMENDED」。**這項行為在官方文件 `auth/redirect-urls.mdx` 裡沒有寫**，屬於程式碼層面的事實，不是文件承諾的介面。
- **`localhost`**：不是 IP，必須比對允許清單。固定埠的寫法例如 `http://localhost:53682/**`，或者精確寫成 `http://localhost:53682/callback`。依文件的範例表（`auth/redirect-urls.mdx:53-58`），`/*` 不會比中多層路徑，也不會比中結尾的 `/`。`*` 可以比中 query 參數：測試案例「* respects parameters」是 `http://localhost:8000/*` 比中 `http://localhost:8000/path?param=1`。
- **萬用字元能不能涵蓋埠**：允許清單用 `gobwas/glob` 編譯，分隔字元只有 `.` 和 `/`（`configuration.go:1248`、`glob.MustCompile(uri, '.', '/')`）。`*` 的定義是「matches any sequence of non-separator characters」（文件 `redirect-urls.mdx:36,43`；`gobwas/glob@v0.2.3:glob.go:20`）。`:` 和數字都不是分隔字元，因此 `http://localhost:*/**` 依語意會比中任何埠。**這是推論：官方沒有這種範例，原始碼測試也沒有這種案例，本次沒有實測。** 文件另外建議正式環境「setting the exact redirect URL path」（`redirect-urls.mdx:47`）。

### 不在清單上會怎樣

`GetReferrer`（`request.go:75-89`）依序嘗試 `redirect_to` 參數、`Referer` header，兩者都不合格就回傳 **Site URL**；文件 `redirect-urls.mdx:24` 也把 Site URL 稱為「default redirect URL」。桌面程式叫出系統瀏覽器時不會帶 `Referer`，所以使用者會被導到 HuaXia 首頁，網址附上 `?code=…`。

HuaXia 網頁走 implicit flow，瀏覽器裡也沒有 code verifier。supabase-js 只有在 storage 裡找得到 verifier 時才會把 `?code=` 當成 PKCE 回呼處理（`GoTrueClient.ts:4007-4026`），所以網頁會忽略這個 code。泠靈的本機伺服器什麼都收不到，Supabase 也不會顯示錯誤，flow state 300 秒後就過期。泠靈必須自己設逾時，並在畫面上提示。

## 3. 已登入使用者經 PostgREST `rpc` 呼叫 `is_officer()`

- **能不能呼叫**：可以。
  - PostgREST 的規則是「Every function in the exposed schema and accessible by the active database role is executable under the `/rpc` prefix」（PostgREST `docs/references/api/functions.rst:8`）。
  - Supabase 預設會暴露 public schema（文件 `api/using-custom-schemas.mdx:7`）。
  - 執行權限：PostgreSQL 建立函式時預設把「`EXECUTE` privilege for functions and procedures」授予 PUBLIC（PostgreSQL 18 `ddl-priv.html`）。Supabase 文件也寫「By default, any role can run a database function」（`database/functions.mdx:459`）。
  - HuaXia 只對 `discord_ids` 做了 revoke（`HuaXia:supabase/schema.sql:630`），`is_officer()` 沒有（定義在 `schema.sql:431-434`）。
- **實測（2026-10-10）**：只帶 publishable key、以匿名身分 `GET /rest/v1/rpc/is_officer`，回 `HTTP 200`，body 為 `false`。同樣方式呼叫被 revoke 的 `discord_ids`，回 `HTTP 401`，`{"code":"42501",…,"message":"permission denied for function discord_ids"}`。這證實函式有暴露，權限機制也和 schema 寫的一致。匿名能執行，代表權限來自 PUBLIC 或 Supabase 的預設授權；`authenticated` 角色同樣適用。
- **呼叫方式**：

  ```
  POST https://<ref>.supabase.co/rest/v1/rpc/is_officer
  apikey: <publishable key>
  Authorization: Bearer <使用者的 access_token>
  Content-Type: application/json

  {}
  ```

  因為函式是 `stable`，也可以用 GET（PostgREST `docs/references/transactions.rst:58-80`：POST 呼叫 STABLE 函式時是 READ ONLY）。回傳的是裸 JSON 布林值，PostgREST 會偵測純量函式並照此組回應（`functions.rst:336-339`）。publishable key 要放在 `apikey` header，不能放在 `Authorization: Bearer`，因為它不是 JWT（文件 `getting-started/api-keys.mdx:351`）。使用者身分由 Bearer 帶的 JWT 決定（同檔 `:80`）。
- **結果的意義**：`is_officer()` 是 `security definer`，以 JWT 的 `auth.uid()` 判斷。使用者的 `profiles.status = 'active'`，而且有 `admin` 或 `officer` 角色，才會回 true（`schema.sql:421-434`）。
  - 新的 Discord 使用者會由觸發器建立 `status = 'pending'` 的 profile，而且沒有任何角色，結果是 false（`schema.sql:646-666`）。
  - Supabase 會把相同 email 的身分自動連到同一個使用者（文件 `auth/auth-identity-linking.mdx:23`）。如果幹部在 HuaXia 用的是 Email 帳號，而 Discord 帳號的 email 不同又沒有連結過，用泠靈登入就會產生一個新的 pending 帳號。HuaXia 的維護文件記錄過這種狀況：「同一個人變成兩個帳號」，處理方式是在「我的設定」按「連結 Discord」（`HuaXia:docs/MAINTENANCE.md:40`、`js/views/me.js:14-15`）。泠靈顯示「不是幹部」時，訊息應該把這個可能一起提出來。
- **2026-10-30 的平台變更**：Supabase 會在所有既有專案上取消 public schema 新物件的自動授權，既有物件保留原本的權限（Supabase discussion #45329；文件 `api/securing-your-api.mdx:33-35`）。`is_officer()` 已經存在，不受影響。用 `create or replace` 重跑 schema 也不會改變權限：PostgreSQL 文件寫「the ownership and permissions of the function do not change」。風險在**重建專案**：照 `SETUP.md` 在新專案重跑 `schema.sql` 時，資料表沒有明確的 grant，連 HuaXia 網頁本身都會壞。函式會不會受影響則查無定論：公告附的 SQL 只 revoke 資料表和 sequence，但建立專案時的勾選項名稱寫的是「tables and functions」。如果要保險，可以在對 HuaXia 的合併請求裡加上 `grant execute on function public.is_officer() to authenticated;`。

## 4. Storage bucket `scores` 的單檔上限

- **bucket 層級**：`schema.sql:865` 是 `insert into storage.buckets (id, name, public) values ('scores', 'scores', false) on conflict (id) do nothing;`，沒有給 `file_size_limit` 和 `allowed_mime_types`。這兩欄的預設值都是 null；`file_size_limit` 是 bigint，單位為位元組（`supabase/storage@f31599e:migrations/tenant/0013-add-bucket-custom-limits.sql`、`0014-use-bytes-for-max-size.sql`）。
- **bucket 為 null 時**：Storage 只有在 bucket 上限是數字時才取它和全域上限的較小值，否則直接用全域上限（`src/storage/uploader.ts:702-717`、`src/storage/limits.ts:57-71`）。
- **專案層級**：全域上限在後台 Storage Settings 設定。「For Free projects, the limit can't exceed 50 MB」，Pro 以上最高 500 GB（文件 `storage/uploads/file-limits.mdx:11-18`）。bucket 上限不能高於全域上限（同檔 `:26`）。HuaXia 用免費方案，伺服器端的上限最多 50 MB；**實際設定值查無**，這個值不經由公開 API 提供，要到後台才看得到。
- **超過上限**：回 HTTP 413，`"error": "Payload too large"`，訊息「The object exceeded the maximum allowed size」（`src/internal/errors/codes.ts:349-356`）。
- **20 MB 由誰把關**：只有 HuaXia 前端（`HuaXia:js/views/pieces.js:82-87`）。伺服器端沒有 20 MB 限制，所以泠靈必須自己擋，#44 的決定已經這樣寫。如果要讓伺服器也擋，可以在 HuaXia 的合併請求裡加 `update storage.buckets set file_size_limit = 20971520 where id = 'scores';`。只改 insert 沒有用，因為有 `on conflict do nothing`，已經存在的 bucket 不會被更新。不要把 `allowed_mime_types` 限定成 PDF，因為網頁也接受圖片（`pieces.js:101`）。
- **上傳方式**：一般上傳最多可以傳 5 GB，但文件說它「ideal for small files that are not larger than 6MB」，超過 6 MB 建議改用 TUS 續傳上傳，可靠度較高（文件 `storage/uploads/standard-uploads.mdx:11,17`）。20 MB 以內用一般上傳可行。
- **不能覆寫**：upsert 除了 INSERT，還需要 SELECT 和 UPDATE policy（文件 `storage/security/access-control.mdx:29`）。HuaXia 對 `scores` 的物件只有 select、insert、delete 三個 policy（`schema.sql:866-873`），沒有 update，所以無法覆寫同一個路徑。這和 #44 Q11 的決定「先上傳新檔，成功後再刪除舊檔」一致。HuaXia 網頁產生的路徑本身也帶時間戳（`pieces.js:92`）。
- **端點**：上傳是 `POST /storage/v1/object/scores/<path>`，`storage3` 用 multipart 的 `file` 欄位；刪除是 `DELETE /storage/v1/object/scores`，body 為 `{"prefixes": ["<path>", …]}`（`storage3` 2.32.0 `_sync/file_api.py:368-381,505-581`）。

## 5. Python 端：`supabase-py` 還是直接打 REST

### 相依套件與體積

量測方式：在暫存 venv（Linux x86_64、CPython 3.10、pip 26.2.1）裡，用 `pip install --dry-run --report` 分別解析泠靈的 `requirements.txt`，以及「`requirements.txt` 加 `supabase`」，比對兩邊的差異；再實際安裝 `supabase==2.32.0`，依各套件的 RECORD 加總檔案大小。

- 泠靈現有的相依閉包有 37 個套件。加上 `supabase` 之後**多出 26 個**，既有套件的版本都沒有變動：
  - `supabase`、`supabase-auth`、`postgrest`、`storage3`、`realtime`、`supabase-functions`
  - `pydantic`、`pydantic-core`、`typing-inspection`、`annotated-types`
  - `httpx`、`httpcore`、`h11`、`h2`、`hpack`、`hyperframe`、`anyio`
  - `websockets`、`yarl`、`multidict`、`propcache`
  - `pyjwt`、`deprecation`、`packaging`、`strenum`、`exceptiongroup`
- 泠靈要求 Python 3.11 以上（README.md:68）。`exceptiongroup` 只在 3.11 以下才需要（`anyio` 的條件相依），所以實際是 25 個。
- 安裝後合計約 **17.5 MB**，含 `.pyc`。最大的幾個：`pydantic-core` 5.1 MB、`pydantic` 3.2 MB、`multidict` 1.5 MB、`websockets` 1.1 MB、`anyio` 1.0 MB。`realtime` 和 `websockets` 是即時訂閱功能用的，泠靈用不到，但 `supabase` 套件固定會裝（`supabase` 的 requires_dist 把五個子套件釘在 `==2.32.0`）。
- PyInstaller 打包後實際增加多少**沒有量測**：打包會把 `.pyc` 壓進 PYZ，增加的量應該小於 17.5 MB，但沒有數據。
- 直接打 REST 的話，`requests` 2.34.2 已經在泠靈的相依閉包裡：`google-api-core` 要求 `requests>=2.33.0`，`google-auth-oauthlib` 經由 `requests-oauthlib` 也要求它。**不增加任何套件**。泠靈若直接 import `requests`，應該在 `requirements.txt` 明列。

### `supabase-py` 實際能幫上的部分

- PKCE：`sign_in_with_oauth()` 只負責組網址，並把 verifier 存進 storage；`exchange_code_for_session()` 送出 `grant_type=pkce`（`supabase_auth/_sync/gotrue_client.py:438-457,1165-1203`）。`supabase` 的 `ClientOptions` 預設 `flow_type = "pkce"`（`supabase/lib/client_options.py:65`）；`supabase-auth` 單獨使用時預設是 `"implicit"`（`gotrue_client.py:111`）。**它沒有提供本機回呼伺服器**，泠靈無論如何都得自己寫。
- session 儲存：同步用戶端預設用 `SyncMemoryStorage`（`client_options.py:118-119`）。開啟 `persist_session`（預設 True）時，會把整個 session 序列化成 JSON 寫進 storage，裡面包含 access token 和 refresh token（`gotrue_client.py:1090-1103`）。#46 的決定是「keyring 只存 refresh token」，要用 `supabase-py` 就得自己寫 storage adapter 把欄位過濾掉。
- 自動 refresh：用 `threading.Timer`，而且設成 daemon（`supabase_auth/timer.py:2,29-31`、`gotrue_client.py:1105-1129`）。refresh 和 `on_auth_state_change` 的回呼都在**背景執行緒**上執行，在 PySide6 裡碰 UI 之前必須先用 Signal 轉回主執行緒。

### 在 PySide6 事件迴圈下的用法

- **同步呼叫放到 worker 執行緒**：`supabase-py` 的同步用戶端和 `requests` 都是阻塞 I/O，不能在 GUI 執行緒上呼叫。泠靈已經有可以沿用的模式：`src/ui/catalog_window.py:41-54` 的 `_Worker(QThread)`，結果用 `finished`／`error` Signal 傳回。現有的 Google 登入 `flow.run_local_server(port=0)`（`src/services/google_auth_service.py:111`）是從設定對話框直接呼叫的（`src/ui/catalog_settings_dialog.py:108`），也就是在 GUI 執行緒上阻塞。Discord 登入不應該照抄這個寫法。
- **async 用戶端**：需要一個 asyncio 事件迴圈和 Qt 共存。PySide6 內建的 QtAsyncio 頁面寫著「This module is currently in technical preview」，目前只涵蓋 asyncio 的事件迴圈基礎設施，不涵蓋 transports、protocols、network connections、sockets 等使用者層 API（`doc.qt.io/qtforpython-6/PySide6/QtAsyncio/index.html`）。`httpx` 的 async 模式靠 socket 連線，因此推論它無法在 QtAsyncio 上執行，本次未實測。第三方的 `qasync` 也沒有查證。
- **泠靈需要的 REST 端點不多**：
  - `/auth/v1/token`，兩種 grant：`pkce`、`refresh_token`
  - `/auth/v1/logout`（`api.go:275`）
  - `/rest/v1/rpc/is_officer`
  - `/rest/v1/<table>` 的 select、insert、update：`pieces`、`piece_parts`、`scores`、`event_pieces` 等
  - `/storage/v1/object/scores/…` 的上傳與刪除

  每個請求都是 `apikey` 加上 `Authorization: Bearer` 兩個 header。

### 取捨

| 面向 | `supabase-py` 2.32.0 | 直接打 REST（`requests`） |
|---|---|---|
| 新增套件 | 25–26 個，約 17.5 MB（安裝後） | 0 |
| 本機回呼伺服器 | 要自己寫 | 要自己寫 |
| PKCE 組網址、兌換 | 有 | 約幾十行 |
| refresh 與 token 儲存 | 有，但用背景 Timer、預設存整個 session，要改寫 storage 才符合 #46 | 要自己寫；時機和儲存完全由泠靈控制 |
| 執行緒模型 | 同步 API 放 QThread；Timer 回呼在背景執行緒 | 同步 API 放 QThread |
| 版本風險 | 子套件互相釘死版本；pydantic 2 的二進位套件要跟著 Python 版本走 | `requests` 已經由 Google 套件約束 |

研究者的判讀（不是查證到的事實，留給 ADR 決定）：泠靈只用到少數幾個端點，`supabase-py` 省下的主要是 PKCE 和 refresh 的幾十行程式。代價是多 25 個套件，而且 token 儲存和執行緒模型都得反過來改寫，才能配合 #46 和 PySide6。直接打 REST、包成一個 HuaXia 用戶端模組，是比較省的選項。

---

## 對 #44 既有決定的影響

#44 Q20、Q31、S1 的決定是：本機 http 回呼用固定埠，並請 HuaXia 維護者在後台登記一次回呼網址。依本次查證：

- 回呼網址如果用 `http://127.0.0.1:<埠>/…`，HuaXia 目前跑的 v2.197.0 **不需要登記，埠也不必固定**，而且 RFC 8252 本來就建議這種寫法。但這項行為只存在於程式碼，官方文件沒有寫，Supabase 將來可能改動。
- 有三種做法可以選：
  1. 用 `127.0.0.1` 加隨機埠，不登記。
  2. 用 `127.0.0.1` 加固定埠，同時請維護者登記 `http://127.0.0.1:<埠>/**` 當備援。不過在現行邏輯下，IP 主機根本不會比對允許清單，這筆登記目前沒有作用；只有 Supabase 改成「IP 主機也比對清單」時才會發揮效用。
  3. 用 `localhost` 加固定埠，並且必須登記。
- 這是 ADR 要做的決定，本文件只陳述事實。

## 查無或未驗證

- HuaXia 後台的實際設定：JWT 有效期、refresh token 重用間隔、輪替是否被關掉、Redirect URLs 清單現有內容、Storage 全域檔案上限。這些都要到後台才看得到，沒有公開 API。
- `localhost:*` 這類埠號萬用字元能不能比中：依 glob 語意推論可以，沒有官方範例，也沒有實測。
- loopback IP 不必登記的行為：只有原始碼，官方文件沒有承諾，也沒有用真實帳號走完整個登入流程實測。
- 重建專案時，新專案的預設權限會不會也取消函式的 EXECUTE：查無定論。
- `supabase-py` 用在 PyInstaller 打包後實際增加的體積：未量測。
- `httpx` async 在 QtAsyncio 或 `qasync` 上能不能用：未實測。

## 來源連結

- Supabase Auth v2.197.0：
  - <https://github.com/supabase/auth/blob/v2.197.0/internal/utilities/request.go#L75-L139>
  - <https://github.com/supabase/auth/blob/v2.197.0/internal/api/external.go#L37-L136>
  - <https://github.com/supabase/auth/blob/v2.197.0/internal/api/token.go#L31-L289>
  - <https://github.com/supabase/auth/blob/v2.197.0/internal/tokens/service.go#L382-L576>
  - <https://github.com/supabase/auth/blob/v2.197.0/internal/conf/configuration.go#L1248>
  - <https://github.com/supabase/auth/blob/v2.197.0/internal/models/flow_state.go#L210-L214>
- Supabase Auth 相關 PR：
  - <https://github.com/supabase/auth/pull/1984>
  - <https://github.com/supabase/auth/pull/2479>
- Supabase 文件原始檔（commit `eeae602`）：<https://github.com/supabase/supabase/tree/eeae6027d5dc658932c6bd496a8c13e0277b7fdb/apps/docs/content/guides>
  - `auth/sessions.mdx`
  - `auth/sessions/pkce-flow.mdx`
  - `auth/redirect-urls.mdx`
  - `auth/auth-identity-linking.mdx`
  - `database/functions.mdx`
  - `api/securing-your-api.mdx`
  - `api/using-custom-schemas.mdx`
  - `getting-started/api-keys.mdx`
  - `storage/uploads/file-limits.mdx`
  - `storage/uploads/standard-uploads.mdx`
  - `storage/security/access-control.mdx`
- Supabase 預設權限變更公告：<https://github.com/orgs/supabase/discussions/45329>
- Supabase Storage：
  - <https://github.com/supabase/storage/blob/f31599e188d7c9c854b1daa76a11b41c6d179462/src/storage/uploader.ts#L702-L717>
  - <https://github.com/supabase/storage/blob/f31599e188d7c9c854b1daa76a11b41c6d179462/src/internal/errors/codes.ts#L349-L356>
- supabase-js auth-js：<https://github.com/supabase/supabase-js/blob/b9753a1e3a8ad867b5c1dbefaf9a5a41cd4ce147/packages/core/auth-js/src/GoTrueClient.ts>
- PostgREST 文件：
  - <https://github.com/PostgREST/postgrest/blob/f7abddf57fd9177608175f6758f1f28d8d94690b/docs/references/api/functions.rst>
  - <https://github.com/PostgREST/postgrest/blob/f7abddf57fd9177608175f6758f1f28d8d94690b/docs/references/transactions.rst>
- PostgreSQL 文件：
  - <https://www.postgresql.org/docs/current/ddl-priv.html>
  - <https://www.postgresql.org/docs/current/sql-createfunction.html>
- RFC 8252（§7.3、§8.3、§8.12）：<https://www.rfc-editor.org/rfc/rfc8252.html>
- Discord OAuth2：<https://docs.discord.com/developers/topics/oauth2>
- `gobwas/glob` v0.2.3：<https://github.com/gobwas/glob/blob/v0.2.3/glob.go>
- PySide6 QtAsyncio：<https://doc.qt.io/qtforpython-6/PySide6/QtAsyncio/index.html>
- PyPI：`supabase` 2.32.0、`supabase-auth` 2.32.0、`storage3` 2.32.0

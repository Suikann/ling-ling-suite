# Google OAuth 受限範圍的發布與驗證查證

- 來源 ticket：[#67](https://github.com/Suikann/ling-ling-suite/issues/67)（地圖 [#43](https://github.com/Suikann/ling-ling-suite/issues/43)，等這份結果做決定的是 [#68](https://github.com/Suikann/ling-ling-suite/issues/68)）
- 調查日期：2026-10-11
- 文件基準：developers.google.com、cloud.google.com 與 Workspace 說明的頁尾「Last updated」日期列在文末「來源」；support.google.com/cloud 的說明頁不顯示更新日期，以 2026-10-11 抓取的內容為準。
- 程式庫基準：`google-auth-oauthlib` 1.5.0（2026-09-29）、`google-auth` 2.61.0、`google-api-python-client` 2.201.0。
- 泠靈程式碼行號以 `origin/develop` 的 2cfb9ee 為準。
- 方法：只讀官方文件與程式庫原始碼。沒有 Cloud Console 的存取權，所以沒有實際切換發布狀態、送審或呼叫 API；需要實測才能確定的地方都標成未驗證。

## 結論摘要

| 問題 | 答案 |
| --- | --- |
| 測試中 | 只有列在名單上的測試使用者能授權，上限 100 人，「加入就佔用名額」。授權從**同意當下**起算 7 天後失效，續期權杖一起失效；不是閒置 7 天，所以每週一定要重新登入。測試使用者授權前也會看到警告畫面。只要求姓名、email、個人資料這幾個範圍才例外。 |
| 未驗證正式版：畫面 | 先出現「Google hasn't verified this app」畫面，要按「Advanced」再按「Go to 〈名稱〉 (unsafe)」才進到同意畫面。泠靈同時要求兩個非登入範圍，同意畫面會讓使用者逐項勾選。7 天失效不再適用。 |
| 未驗證正式版：100 人怎麼算 | 以 Cloud 專案為單位、**終身累計**，「cannot be reset or changed」。算的是「能授權的 Google 帳號」數。同一帳號換電腦重新授權、撤銷後再授權是否再算一次，文件沒寫。用完之後，新使用者的 Google 登入被停用。 |
| 未驗證正式版：會不會直接被擋 | 文件只描述「警告畫面＋人數上限」。直接擋下的情況有：人數上限用完；Workspace 管理員把 Drive 設為 Restricted，或不允許未設定的第三方 app；帳號加入 Advanced Protection；驗證被駁回。Workspace 預設只限制「存取 Gmail 且超過 100 人」的未驗證 app，Drive 不在內。 |
| 受限範圍的驗證 | 先品牌驗證（約 2–3 個工作天），再審受限範圍（約 6 週，Google 註明不保證）。需要公開首頁、同網域的隱私權政策、在 Search Console 驗證過的網域、YouTube 不公開的示範影片、說明為何較窄的範圍不夠，而且 app 要屬於 Drive 允許的類型。每 12 個月重新驗證與重新評估。 |
| 安全評估（CASA） | 開發者文件寫「在伺服器上儲存或傳輸」受限資料才需要；說明中心的 Security Assessment 頁卻寫成所有受限範圍 app 每年都要。泠靈是不經伺服器的桌面程式，適用哪一種說法**文件互相矛盾**。Google 不收費，由開發者付給 ADA 授權的實驗室，價格由實驗室自訂；TAC Security 的頁面列出每個 app 675 到 3600 美元，且是每年一次。 |
| 只剩敏感範圍（`spreadsheets`） | 品牌驗證加上敏感範圍驗證（約 10 個工作天）。首頁、隱私權政策、網域驗證、示範影片、逐一說明範圍用途都一樣要；不需要 CASA；文件只對受限範圍要求每年重新驗證。 |
| `drive.file` 能否用 Sheets API | 可以。`spreadsheets.create`、`values.batchUpdate`、`batchUpdate` 都接受 `drive.file`，Sheets 的範圍表把 `drive.file` 標為「Recommended」。前提是泠靈對那份試算表有權限。只用 `drive.file` 時，整個 app 只剩非敏感範圍，**不必送驗證**。 |
| 既有資料夾與檔案怎麼取得權限 | `drive.file` 只涵蓋泠靈建立的、或使用者用 Picker 交給泠靈的檔案。桌面版 Picker 是在 OAuth 授權網址加上 `trigger_onepick=true` 與 `prompt=consent`，以系統瀏覽器開啟；可以多選、可以選資料夾，回呼網址會帶 `picked_file_ids`。它只能要求 `drive.file`，不能和其他範圍一起要求。選了資料夾是否等於取得裡面所有檔案的權限：**文件沒寫**。 |
| 下一任譜務看不看得到前任建的檔 | 文件沒寫。`drive.file` 的說明一律寫成「you use with this app」，以使用者為主詞，沒說是否跨使用者。需要實測；就算看不到，下一任也能用 Picker 自己再選一次。 |
| 公開發行版內附同一個用戶端 | 共用。人數上限、測試使用者名單、Drive 與 Sheets 的 API 配額，都以 Cloud 專案為單位計算。只有「每個帳號對每個用戶端最多 100 個續期權杖」是各帳號各自計算。依 Google 的定義，給其他社團使用就不算 personal use。 |
| 專案擁有權 | 沒有機構的專案：擁有者只能是個人帳號或服務帳戶；只能在 Console 邀請，而且對方要接受；至少要有一位接受過服務條款的擁有者。擁有者刪除自己的 Google 帳號時，他單獨擁有的專案會一起刪除；所有擁有者的帳號都不在了，專案就成為孤兒。Google 建議隨時保持一位以上擁有者。 |
| 學校 Workspace 的「內部」類型 | 專案必須屬於學校網域的 organization；只有該網域的帳號能授權（其他人會遇到 `org_internal` 錯誤）。免驗證、沒有人數上限、沒有 7 天失效。但受限的 Drive 範圍可能需要管理員勾選「Trust internal apps」，Google Cloud 本身也是管理員可以關掉的「其他服務」。學校的實際設定**未查**。 |

## 1. 測試中（Testing）

### 誰能登入、上限多少

- 「Projects configured with a publishing status of Testing are limited to up to 100 test users listed in the OAuth consent screen. A test user consumes a project's test user quota once added to the project.」（[Manage App Audience](https://support.google.com/cloud/answer/15549945)）
  - 名額在「加入名單」時就被佔用。把人移出名單會不會歸還名額，文件沒寫；「consumes … once added」的字面意思偏向不歸還，這點**未驗證**。
- 不在名單上的帳號會得到「Access denied」錯誤（[Nest Device Access：Authorization errors](https://developers.google.com/nest/device-access/reference/errors/authorization)，「Access denied」一節）。
- 測試使用者授權前也會看到警告：「Google will display a warning message before allowing a specified test user to authorize scopes … should consider the risks associated with granting access to their data to an unverified app.」（[Manage App Audience](https://support.google.com/cloud/answer/15549945)）

### 7 天失效

- 「Authorizations by a test user will expire seven days from the time of consent. If your OAuth client requests an offline access type and receives a refresh token, that token will also expire.」（[Manage App Audience](https://support.google.com/cloud/answer/15549945)）
- 開發者文件的說法相同，並寫明條件是「external user type and a publishing status of "Testing"」（[Using OAuth 2.0 to Access Google APIs：Refresh token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)）。
- 起算點是**同意的時間**，不是最後一次使用。所以中間 refresh 幾次都無法延長，每 7 天一定要重跑一次瀏覽器登入。
- 唯一例外：只要求 `userinfo.email`、`userinfo.profile`、`openid` 或它們的子集合時，不需要列在名單上、不會看到警告、也不會 7 天失效。泠靈要求 Drive 與 Sheets，不適用。（同上兩份文件）
- 測試使用者也可能因為 Workspace 的第三方 app 管制或 Advanced Protection 而無法授權（[Manage App Audience](https://support.google.com/cloud/answer/15549945)）。

## 2. 未經驗證的正式版（In production、未驗證）

### 使用者看到什麼

- 發布狀態改成 In production 後，「available to any user with a Google Account」。若在完成驗證前要求敏感或受限範圍，「Google will display an Unverified apps warning message」。（[Manage App Audience](https://support.google.com/cloud/answer/15549945)）
- 警告的實際樣子：「You may see a "Google hasn't verified this app" screen … This screen can be bypassed by clicking the Advanced option and then clicking Go to Project Name (unsafe).」（[Nest Device Access：Authorization errors](https://developers.google.com/nest/device-access/reference/errors/authorization)）。Apps Script 文件的描述是「allows these users to authorize unverified apps and use them, but only after confirming they understand the risks」（[OAuth Client Verification](https://developers.google.com/apps-script/guides/client-verification)）。
- 這個畫面出現在同意畫面**之前**：「The app or script might display an "unverified app" screen before it displays the consent screen.」（[Unverified apps](https://support.google.com/cloud/answer/7454865)）
- 同意畫面本身：泠靈要求 `drive` 與 `spreadsheets` 兩個非登入範圍，屬於「more than one non-Sign-In scope」，會顯示 granular 的勾選畫面，使用者可以只勾其中一項，程式不能假設兩項都拿到（[How to handle granular permissions](https://developers.google.com/identity/protocols/oauth2/resources/granular-permissions)）。
- 沒有品牌驗證時，同意畫面不顯示 app 名稱與 logo：「Without verification, only your application domain will be visible to users.」（[Manage OAuth App Branding](https://support.google.com/cloud/answer/15549049)）。桌面程式沒有網域時實際顯示什麼，**未驗證**。
- 7 天失效只適用於 Testing。正式版的續期權杖照一般規則失效：使用者撤銷、六個月沒用、超過每帳號每用戶端 100 個等（[Using OAuth 2.0：Refresh token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)）。

### 100 人上限怎麼算

- 「The user cap limits the number of users that can grant permission to your app when requesting unapproved sensitive or restricted scopes. The user cap applies over the entire lifetime of the project, and it cannot be reset or changed.」（[Manage App Audience](https://support.google.com/cloud/answer/15549945)）FAQ 有同一句，並說明可在 Console 的「OAuth Consent Screen → OAuth user cap」查看用量（[FAQ](https://support.google.com/cloud/answer/13463817)，「How do I check my user cap status?」）。
- 配額表的寫法是「100 new users in total, after the app presents the unverified app screen」，並註明「might be adjusted for specific apps based on the app history, developer reputation, and riskiness」（[Unverified apps](https://support.google.com/cloud/answer/7454865)、[Manage App Audience](https://support.google.com/cloud/answer/15549945)）。
- 計數的單位：「A user cap restricts the number of Google Accounts able to grant access to your unverified app.」（[Restricted scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)，Personal use 一節）
- 綜合以上：
  - 是否終身累計：是，以專案為單位，不能重置。
  - 能否重置：不能。唯一的「申訴」管道是送驗證（配額表的 Appeal 欄：「Request verification for your app」）。
  - 同一人換電腦重新授權：文件以「Google 帳號」為單位計算，字面上同一個帳號不會變成兩個人。但「重新授權算不算新的一次」、「撤銷後再授權」、「Testing 期間授權過的帳號轉正式版後算不算」，文件都沒寫，**未驗證**。
- 驗證過的 app 只要不要求未核准的範圍，上限就不適用（[Manage App Audience](https://support.google.com/cloud/answer/15549945)）。
- 另有一個與驗證無關的「新使用者授權速率限制」，所有 app 都有，數值依 app 歷史與風險而定（[OAuth Application Rate Limits](https://support.google.com/cloud/answer/9028764)）。

### 會不會直接被擋

文件沒有「要求受限範圍的未驗證 app 一律封鎖」的規定；描述的都是「警告畫面＋人數上限」。會變成擋下而不只是警告的情況：

- **人數上限用完**：「Failure to get your app verified before making requests to sensitive or restricted scopes will result in your project's 100 new-user cap eventually getting exhausted and Google sign-in being disabled for your users.」（[FAQ](https://support.google.com/cloud/answer/13463817)，「Why are users seeing the Unverified App Screen or "Sign-in with Google temporarily disabled"?」）
- **驗證被駁回**：「users' access to the unapproved sensitive or restricted scopes in the app via OAuth will no longer work.」（[FAQ](https://support.google.com/cloud/answer/13463817)）
- **Workspace 帳號**（例如學校帳號）：
  - 預設：「Unverified third-party apps that access Gmail data and have more than 100 users worldwide are subject to restrictions.」以及「unverified third-party apps that access data for Google services other than Gmail aren't subject to restrictions」。也就是說，Drive 預設不擋。（[Authorize unverified third-party apps](https://knowledge.workspace.google.com/admin/apps/authorize-unverified-third-party-apps)）
  - 管理員把 Drive 服務設為 Restricted 時，沒被信任的 app「stop working, and tokens are revoked」，新安裝會「notified that the app is blocked」。管理員也可以設定成不允許任何未設定的第三方 app。（[Control which third-party & internal apps access Google Workspace data](https://knowledge.workspace.google.com/admin/apps/control-which-apps-access-google-workspace-data)）
  - 續期權杖也會因為管理員把服務設為 Restricted 而失效（錯誤是 `admin_policy_enforced`）（[Using OAuth 2.0：Refresh token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)）。
- **Advanced Protection 帳號**：「may block most non-Google apps」（[Manage App Audience](https://support.google.com/cloud/answer/15549945)）。

一般個人帳號在人數上限內是否還可能被 Google 的風險判斷直接擋下，文件沒寫，**未驗證**。

## 3. 送 Google 驗證

### 受限範圍（`drive`）

- **`drive` 是受限範圍**，`drive.readonly`、`drive.metadata` 等也是（[Restricted Scopes 清單](https://support.google.com/cloud/answer/13464325)、[Choose Google Drive API scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)）。Workspace 政策對受限 Drive 範圍的定義是「Read, modify, or manage the content or metadata of a user's Drive files, without the user individually granting file-by-file access」（[Google Workspace API user data and developer policy](https://developers.google.com/workspace/workspace-api-user-data-developer-policy)）。
- **允許的 app 類型**：Drive 的受限範圍只開放給三類（[Choose Google Drive API scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)，「Qualifications for restricted scopes」；政策原文見 [Workspace API policy](https://developers.google.com/workspace/workspace-api-user-data-developer-policy)，「Appropriate access to and use of Google Drive Scopes」）：
  - Backup and sync：提供本機同步或自動備份。
  - Productivity and education：主要介面會操作 Drive 檔案、metadata 或權限，例如任務管理、筆記、團隊溝通、課堂協作。
  - Reporting and security：分析檔案如何被分享或存取。

  泠靈若送審，最接近的是第二類，但是否成立由 Google 審查團隊判定：「if you are truly unsure of your app's application type, you can select no options … The Google API's verification team will then determine the application type.」（[Restricted scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)）
- **最小範圍原則**：「If your application's user-facing features are limited to when a user uploads, downloads and/or deletes specific files, and/or creates folders, you should not request a broad-access restricted Drive scope.」審查時被要求改用 `drive.file` 的處理方式另有說明頁（[Requesting Minimum Scopes](https://support.google.com/cloud/answer/13807380)，「I've been asked to use drive.file instead of restricted Drive API scopes」）。
- **流程**（[Restricted scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)）：
  1. 品牌驗證：在 Branding 頁填 app 名稱、logo、聯絡資訊、首頁與隱私權政策連結，按「Verify Branding」。自動審查通常幾分鐘，合格結果 7 天內要按「Publish branding」，否則要重驗。
  2. 品牌通過後，到 Verification Center 宣告所有範圍、申請資料存取。
  3. 準備示範影片（上傳 YouTube、設為不公開），要錄到英文的同意流程、網址列裡的 OAuth client ID，以及每個受限範圍實際用在哪個功能。
  4. 說明每個受限範圍的用途，以及「why more limited scopes aren't sufficient」。
  5. Trust & Safety 團隊以 email 追問，要留意開發者聯絡信箱。
- **首頁與隱私權政策的要求**：首頁要公開、不能只有登入後才看得到，Google Play 或 Facebook 頁面不算；隱私權政策要放在與首頁**同一網域**，並揭露怎麼存取、使用、儲存、分享 Google 使用者資料；網域要用專案的 Owner 或 Editor 帳號在 Google Search Console 驗證擁有權（同上頁，「Application home page requirements」「Application privacy policy link requirements」）。
- **時程**：品牌驗證 2–3 個工作天、敏感範圍 10 個工作天、受限範圍 6 週，「these estimates are not guaranteed」（[FAQ](https://support.google.com/cloud/answer/13463817)，第一題的表格）。
- **審查期間**：已要求但尚未核准的範圍，使用者照樣看到未驗證畫面、受 100 人上限限制（[FAQ](https://support.google.com/cloud/answer/13463817)，「What happens if I add new sensitive or restricted scopes…」）。
- **每年重審**：「apps must be reverified for compliance and complete a security assessment at least every 12 months after your assessor's Letter of Assessment (LOA) approval date」（[Restricted scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)）。說明中心也寫「Apps requesting restricted scopes data need to complete "re-verification" annually.」（[OAuth App Verification Help Center](https://support.google.com/cloud/answer/9110914)）
- **驗證後又改設定**：「If you add any new redirect URLs or JavaScript origins, or if you change your product name after verification, you have to go through verification again.」（[Unverified apps](https://support.google.com/cloud/answer/7454865)）

### 安全評估（CASA）

- **什麼情況需要：文件互相矛盾**
  - 開發者文件：「If you store or transmit restricted scope data on servers, then you need to complete a security assessment.」「Every app that requests access to Google users' restricted data and has the ability to access data from or through a third-party server must go through a security assessment」（[Restricted scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)）。Drive 與 Sheets 的範圍頁也寫「If you store restricted scope data on servers (or transmit), then you must go through a security assessment.」（[Choose Google Drive API scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)、[Choose Google Sheets API scopes](https://developers.google.com/workspace/sheets/api/scopes)）
  - 說明中心：「applications requesting access to restricted scopes must undergo an annual security assessment」，沒有提伺服器（[Security Assessment](https://support.google.com/cloud/answer/13465431)）。
  - Workspace 政策：「Required security measures for Restricted Scopes also include following the Cloud Application Security Assessment (CASA).」並寫「depending on the API being accessed and number of user grants or users, we may also require … a periodic security assessment」（[Workspace API policy](https://developers.google.com/workspace/workspace-api-user-data-developer-policy)，「Maintain a secure operating environment」）。
  - 泠靈直接從使用者的電腦呼叫 Google API，沒有自己的伺服器。依開發者文件的字面不需要 CASA；依說明中心的字面需要。Google 實際怎麼判定，**未驗證**。流程上，是 Trust and Safety 團隊在其他審查完成後主動通知要做評估（[Security Assessment](https://support.google.com/cloud/answer/13465431)）。
- **就算不需要 CASA，受限範圍仍有安全要求**，其中包括「Keeping user data and credentials, specifically tokens such as OAuth access and refresh tokens, encrypted at rest」（[Workspace API policy](https://developers.google.com/workspace/workspace-api-user-data-developer-policy)，「Maintain a secure operating environment」）。這與 [#46](https://github.com/Suikann/ling-ling-suite/issues/46) 用 `keyring` 存續期權杖的方向一致；現行程式把整份權杖以明文寫到 `APPDATA_DIR`（`google_auth_service.py:46, 137-139`）。
- **等級與方法**：CASA 以 OWASP ASVS 為基礎，分 AL1、AL2 兩個 assurance level，兩者要求相同、只差在評估方式；由 Google 依資料敏感度、使用者數量與內部風險指標決定等級，開發者不能自選（[CASA Assurance Levels](https://appdefensealliance.dev/casa/casa-tiering)、[Security Assessment](https://support.google.com/cloud/answer/13465431)）。說明中心 FAQ 仍沿用 Tier 2、Tier 3 的說法，並提到「free tier 2 assessment」：若在免費 Tier 2 的範圍內卻選擇找實驗室，要自付（[FAQ](https://support.google.com/cloud/answer/13463817)，Security Assessment 一節）。
- **費用**：「Google does not charge the developer any fees for security assessment.」「The cost for such a service is agreed on between the developer and the assessor without any involvement from Google.」（[FAQ](https://support.google.com/cloud/answer/13463817)）
  - 實驗室自己公布的價格：TAC Security 的 CASA FAQ 在調查時列出 Tier 2 Basic 675 美元、Tier 2 Premium 855 美元、Tier 3 Enterprise 3600 美元，都是「per application」，內容寫「annual application Security Assessment」（[TAC Security：ESOF AppSec ADA CASA FAQs](https://tacsecurity.com/?p=18721)）。TAC 另一個版本的 FAQ 頁數字不同（搜尋摘要顯示 540、720、1800、4500 美元），所以只能當量級參考。
- **時程**：使用者發起的評估由 Google 的通知決定截止日；評估完成後實驗室發給開發者與 Google 一份 Letter of Validation；「A Framework User initiated assessment is required annually」（[CASA：User Initiated Assessment](https://appdefensealliance.dev/casa/casa-start)，2022-10-13）。
- **每年重評**：「We require the annual security reassessment to be a complete test of your application whether you have made any changes or not.」嚴重漏洞未在時限內修好，API 使用可能被停權（[FAQ](https://support.google.com/cloud/answer/13463817)）。

### 只剩敏感範圍（`spreadsheets`）

- `spreadsheets` 與 `spreadsheets.readonly` 是敏感範圍（[Choose Google Sheets API scopes](https://developers.google.com/workspace/sheets/api/scopes)）。
- 流程與受限範圍相同的部分：品牌驗證、首頁與隱私權政策、網域驗證、到 Verification Center 宣告範圍、逐一說明範圍用途與為何較窄的範圍不夠、示範影片（[Sensitive scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification)）。
- 不同的部分：
  - 時程約 10 個工作天（[FAQ](https://support.google.com/cloud/answer/13463817)）。
  - 不需要 CASA（安全評估只出現在受限範圍的文件）。
  - 每年重新驗證只寫給受限範圍（[OAuth App Verification Help Center](https://support.google.com/cloud/answer/9110914)）。
- Workspace 管理員可以限制敏感範圍，只讓他們標為信任的 client 存取（[Sensitive scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification)，「Understand sensitive scopes」）。

### 只有非敏感範圍

- 「If your app utilizes only non-sensitive scopes, it is not mandatory for your app to complete the app verification process. However, if you want your app to display an app name and logo on the OAuth consent screen, you will need to complete a lighter-weight verification process known as "brand-verification".」（[OAuth App Verification Help Center](https://support.google.com/cloud/answer/9110914)）
- 人數上限與未驗證畫面都只針對「unapproved sensitive or restricted scopes」（[Manage App Audience](https://support.google.com/cloud/answer/15549945)），所以只用非敏感範圍時兩者都不適用。7 天失效只看發布狀態，所以仍要改成 In production。

### 免驗證的例外

- **Personal use**：「if you are the only user of your app or if your app is used by only a few users, all of whom are known personally to you」，仍受人數上限限制（[Restricted scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)，「Exceptions to verification requirements」）。說明中心寫成「If the app is for your personal use (fewer than 100 users)」，要超過 100 人就要驗證（[When is verification not needed](https://support.google.com/cloud/answer/13464323)）。OAuth 政策的定義：「not shared with anyone else or will be used by fewer than 100 people (all of whom are known personally to you)」（[OAuth 2.0 Policies](https://developers.google.com/identity/protocols/oauth2/policies)，「Use separate projects for testing and production」）。
- **Development、Testing、Staging**：不必驗證，但仍有警告畫面、人數上限與續期權杖期限（同上兩頁）。
- **Internal use**：見第 6 節。
- **Workspace 管理員信任的 app**：管理員把 app 加入信任清單後，該網域的使用者不論 app 是否驗證都能安裝（[When is verification not needed](https://support.google.com/cloud/answer/13464323)）。

## 4. 縮小範圍成 `drive.file`

### 範圍的定義

- `drive.file` 是非敏感範圍，說明是「Create new Drive files, or modify existing files, that you open with an app or that the user shares with an app while using the Google Picker API or the app's file picker.」（[Choose Google Drive API scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)）。同意畫面上的文字是「See, edit, create, and delete only the specific Google Drive files you use with this app」（[OAuth 2.0 Scopes for Google APIs](https://developers.google.com/identity/protocols/oauth2/scopes)）。
- 同一頁說「The drive.file scope works with all Drive API REST Resources」。`files.list` 的授權範圍清單也包含 `drive.file`（[files.list](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list)）。
- 存取權**沒有**文件明寫的部分：
  - 選取資料夾時，是否連帶取得裡面檔案的權限。
  - 泠靈建立的資料夾裡，使用者從 Drive 網頁手動放進去的檔案，泠靈看不看得到。
  - 存取被拒時 API 回 403 還是 404。

  三項都**未驗證**。

### 能否用 Sheets API 讀寫譜庫試算表

- 可以。Sheets 的範圍表列出 `drive.file`，標為「Recommended / Non-sensitive」（[Choose Google Sheets API scopes](https://developers.google.com/workspace/sheets/api/scopes)）。
- 泠靈用到的方法，參考文件的 Authorization scopes 都包含 `drive.file`：`spreadsheets.create`、`spreadsheets.values.batchUpdate`、`spreadsheets.batchUpdate`（[spreadsheets.create](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/create) 等）。`values.get`、`values.append`、`values.update`、`spreadsheets.get` 未逐一核對，Sheets 範圍頁的整體說明已涵蓋。
- 條件是泠靈對那份試算表有 `drive.file` 權限，也就是泠靈建立的，或使用者用 Picker 選過的。
- 由泠靈以 `spreadsheets` 範圍建立的既有譜庫試算表（`sheets_service.py:86-115`），改成 `drive.file` 之後算不算「泠靈建立的檔案」：**未驗證**。

### 泠靈現有的哪些動作會壞掉

`drive.file` 只看得到泠靈建立或使用者交給泠靈的檔案。以「使用者在 Drive 網頁上手動建立了『譜庫』資料夾，裡面有手動建的作曲家與曲目資料夾，再從網頁上傳 PDF」為例：

| 使用者的操作 | 現在（`drive`） | 改成 `drive.file` 後 |
| --- | --- | --- |
| 社團設定裡按「瀏覽」，從我的雲端硬碟根目錄展開資料夾樹 | 看得到所有資料夾 | 手動建立的「譜庫」資料夾不會出現，只剩泠靈建立過的資料夾 |
| 選定資料夾後，自動找裡面的譜庫試算表 | 手動建立的、別人分享的試算表都找得到 | 只找得到泠靈有權限的試算表；找不到時會提議新建一份 |
| 在選定的資料夾裡建立新的譜庫試算表並移進去 | 可以 | 試算表建得出來；移進手動建立的資料夾，需要對該資料夾也有權限（依權限模型推論，未驗證） |
| 開譜庫視窗，讀寫曲目、版本、分譜等工作表 | 可以 | 只限泠靈有權限的那份試算表 |
| 譜庫視窗列出根目錄下的作曲家、曲目資料夾與 PDF，版本頁列出分譜 | 都看得到 | 手動建立的資料夾、從網頁上傳的 PDF 都看不到，樹狀圖可能是空的 |
| 「從 Drive 其他位置收集檔案」到某個資料夾 | 可以 | 同上，看不到；移動還需要對檔案與目的資料夾都有權限 |
| 對版本資料夾套用命名格式，重新命名 Drive 上的分譜 | 可以 | 手動上傳的 PDF 列不出來，也改不了名 |
| 在手動建立的資料夾底下新增子資料夾 | 可以 | 父資料夾要先經 Picker 交給泠靈（推論，未驗證） |

對應的程式碼：

- 資料夾樹從 `"root"` 開始列：`catalog_settings_dialog.py:282, 284-298`（`_DriveFolderBrowser`）。
- 找試算表：`catalog_settings_dialog.py:147-180`（`_check_catalog_in_folder`）。
- 建立並移動試算表：`catalog_settings_dialog.py:205-243`（`_offer_create_catalog`，`files.update` 的 `addParents` 在 228-233）。
- 譜庫視窗初始化與重新整理：`catalog_window.py:151-166, 168-190`。
- 列資料夾與 PDF：`drive_service.py:27-57, 209-232`、`scan_library_folder` 在 172-207；呼叫處見 `catalog_window.py:188-189, 245-246, 793-806`。
- 收集檔案：`catalog_window.py:416-438`、`_DriveFilePicker` 在 1033-1070；移動在 `drive_service.py:96-121`。
- 重新命名：`drive_rename_service.py:38-110`（建群組）、169 起（執行），底層是 `drive_service.py:77-94`。
- 新增子資料夾：`catalog_window.py:298-320`、`drive_service.py:123-147`。
- 範圍定義：`catalog_constants.py:78-81`。

地圖上規劃中的功能（分譜存入譜庫、從譜庫下載、清除本機檔案前比對 MD5、撤銷存入）若只處理泠靈自己上傳的檔案，`drive.file` 就夠用；碰到手動上傳的檔案時，就和上表一樣看不到。

### 桌面程式用 Google Picker

- Google 有桌面與行動裝置版的 Picker，做法和網頁版不同（[Overview of the Google Picker](https://developers.google.com/workspace/drive/picker/guides/overview)，比較表）：

  | 項目 | 網頁版 | 桌面與行動裝置版 |
  | --- | --- | --- |
  | 技術 | JavaScript 程式庫 | OAuth 2.0 網址參數與重新導向 |
  | 顯示位置 | 嵌在 app 畫面裡 | 系統預設瀏覽器的新分頁；「Can no longer be displayed within an embedded webview」 |
  | 範圍 | 可以用 `drive.file`、`drive.readonly` 等 | 「only drive.file is permitted and cannot be combined with other scopes」 |

- 做法（[Integrate the Google Picker into desktop and mobile apps](https://developers.google.com/workspace/drive/picker/guides/desktop-mobile-picker)）：
  1. 在 Cloud 專案啟用 Google Picker API。
  2. 用平常的 OAuth 授權網址，`scope` 只放 `drive.file`，加上必填的 `prompt=consent` 與 `trigger_onepick=true`。可選參數有 `allow_multiple=true`、`mimetypes=…`（例如只顯示 PDF）、`file_ids=…`、`allow_folder_selection=true`。
  3. 使用者在瀏覽器裡登入、同意、挑檔案，按 Insert。
  4. 重新導向回 `redirect_uri`，網址參數帶 `picked_file_ids`（逗號分隔）、`code`、`scope`。
  5. 用 `code` 換新的權杖，再拿 `picked_file_ids` 與權杖呼叫 Drive API。
  - 同頁也說，之前授權過的檔案，之後用一般的 `drive.file` 權杖就能繼續讀寫：「Pass the OAuth 2.0 token to the Drive API to read and modify files in which the user previously granted access.」
  - Picker 本身「doesn't allow users to organize, move, or copy files from one folder to another」，搬移仍要靠 Drive API。
- 套到泠靈現行的登入程式：
  - `InstalledAppFlow.run_local_server(**kwargs)` 會把多餘的參數轉交給 `authorization_url`（`google_auth_oauthlib/flow.py:456`），所以 `prompt`、`trigger_onepick`、`allow_folder_selection` 等參數傳得進去。
  - 但重新導向的完整網址只存在區域變數 `wsgi_app.last_request_uri`（`flow.py:472`），函式只回傳 credentials（`flow.py:486`）。`picked_file_ids` 因此拿不到，要自己仿照 `run_local_server` 寫一個會回傳完整網址的版本。
  - 現行登入在 `google_auth_service.py:107-111`。
- 如果泠靈保留 `spreadsheets`，Picker 那一次授權仍然只能單獨要求 `drive.file`。前面說過 Sheets API 接受 `drive.file`，所以可以整個 app 只要求 `drive.file`。
- 「選資料夾」能否取代「把整個譜庫的檔案逐一選進來」，取決於選資料夾是否連帶取得裡面檔案的權限。文件沒寫，**未驗證**。

### 下一任譜務用同一個用戶端

- 文件沒有直接回答「A 帳號用泠靈建立的檔案，B 帳號用同一個 OAuth 用戶端時能否存取」。`drive.file` 的各處說明都以使用者為主詞：「that you open with an app」「the user shares with an app」「files you use with this app」。**未驗證**。
- 可以確定的：
  - 檔案擁有者是授權的使用者：「If the application authenticates on behalf of a user, that user becomes the file owner. The file then resides in their My Drive folder or a specified folder. It consumes their storage quota.」（[Create and manage files](https://developers.google.com/workspace/drive/api/guides/create-file)，「File ownership」）所以前任用泠靈建立的檔案放在前任的雲端硬碟、佔前任的空間；下一任要先透過 Drive 的分享取得檔案本身的權限，這一點和範圍無關。
  - 就算不能自動看到，下一任也可以用 Picker 自己選一次，之後就能持續使用（上一小節）。
- 實測方法：A 帳號用 `drive.file` 讓泠靈建一個資料夾與一份試算表，分享給 B；B 用同一個用戶端、只授權 `drive.file`，直接以檔案 ID 呼叫 `files.get`，並用 `'<資料夾ID>' in parents` 呼叫 `files.list`。

## 5. 公開發行版內附同一個用戶端

- **人數上限**：以專案為單位、終身累計（第 2 節）。所有社團的使用者都算在同一個 100 人裡。
- **測試使用者名單**：以專案為單位（第 1 節）。其他社團的人要被加進泠靈這個專案的名單才能登入，而且共用 100 個名額。
- **API 配額**：
  - Drive API：「Per minute per project」與「Per minute per user per project」，另有每專案每天的計費門檻（[Drive API Usage limits](https://developers.google.com/workspace/drive/api/guides/limits)）。2026-05-01 起改用 quota units 計算；在 2025-11 到 2026-04 間用過 Drive API 的專案維持原本的配額（同頁開頭）。
  - Sheets API：讀寫各為每專案每分鐘 300 次、每使用者每專案每分鐘 60 次（[Sheets API Usage limits](https://developers.google.com/workspace/sheets/api/limits)）。
  - 所有社團共用「每專案」那一層。
- **續期權杖上限**：「a limit of 100 refresh tokens per Google Account per OAuth 2.0 client ID」（[Using OAuth 2.0：Refresh token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)）。這是每個帳號各自計算，不會因為其他社團而互相影響。
- **算不算 personal use**：OAuth 政策把 personal use 定義成不分享給別人、或少於 100 人且「all of whom are known personally to you」（[OAuth 2.0 Policies](https://developers.google.com/identity/protocols/oauth2/policies)）。公開發行給不認識的社團使用，不符合這個定義。
- **用戶端密鑰**：
  - Google 認定安裝型 app「cannot keep secrets」，也就是預期桌面程式會把密鑰帶在身上（[OAuth 2.0 for iOS & Desktop Apps](https://developers.google.com/identity/protocols/oauth2/native-app)）。
  - OAuth 政策仍要求「You must never commit client credentials into publicly available code repositories」（[OAuth 2.0 Policies](https://developers.google.com/identity/protocols/oauth2/policies)）。[#56](https://github.com/Suikann/ling-ling-suite/issues/56) 的做法是把用戶端 JSON 放在 repo secret、打包時才放進去，符合這一條。
- **用戶端被自動刪除**：連續六個月沒有任何權杖請求、也沒改過設定的 OAuth 用戶端會被自動刪除，刪除前 30 天寄信通知。刪除後，既有的 access token 與 refresh token 都不能用（[Manage OAuth Clients](https://support.google.com/cloud/answer/15549257)，「Unused Client Deletion」）。

## 6. 專案擁有權

### 沒有機構（No organization）的專案：交接方式與限制

- **誰能當擁有者**：「The owner role can be granted to a user, serviceAccount, or a group that is part of an organization.」（[projects.setIamPolicy](https://docs.cloud.google.com/resource-manager/reference/rest/v1/projects/setIamPolicy)）
  - 泠靈的專案不在任何機構下，所以擁有者只能是個人 Google 帳號或服務帳戶。Google 群組不能當擁有者。
  - 另一份說明寫「You can add individuals or Google Groups as members, but every project must have at least one project member that is an individual.」（[Manage project members or change project ownership](https://support.google.com/googleapi/answer/6158846)）
- **怎麼加擁有者**：
  - 「to be added as an owner, a user must be invited via Cloud Platform console and must accept the invitation」「A user cannot be granted the owner role using setIamPolicy()」（[projects.setIamPolicy](https://docs.cloud.google.com/resource-manager/reference/rest/v1/projects/setIamPolicy)）。
  - 「If your project is not part of an organization, you must use the Google Cloud console to grant the Owner role.」（[Manage access to projects, folders, and organizations](https://docs.cloud.google.com/iam/docs/granting-changing-revoking-access)）
- **至少一位擁有者**：「If the project is not part of an organization, there must be at least one owner who has accepted the Terms of Service (ToS) agreement in the policy. Calling setIamPolicy() to remove the last ToS-accepted owner from the policy will fail.」（[projects.setIamPolicy](https://docs.cloud.google.com/resource-manager/reference/rest/v1/projects/setIamPolicy)）
- **擁有者刪除帳號**：「When you delete your Google account, it deletes all projects that aren't associated with an organization and that are solely owned by you. When there are multiple owners for a non-organization project, the project is not deleted until all owners are removed from the project or delete their Google accounts.」（[Data deletion on Google Cloud](https://docs.cloud.google.com/docs/security/deletion)）
- **孤兒專案**：
  - 「A Project resource becomes orphaned if it does not have an owner … if the Google accounts belonging to all owners … are deleted.」「Orphaned Project resources are inaccessible. If you want to recover an orphaned project, reach out to Google support.」
  - 有 API 活動紀錄、屬於 Workspace 網域、連結計費帳戶、或屬於機構的專案，不會立刻被標記刪除；其他的會立刻被標記，約 30 天後完全刪除。
  - Google 建議「more than one owner be associated with the project at all times」，或把專案移到機構底下。
  - （以上見 [Project suspension guidelines](https://docs.cloud.google.com/resource-manager/docs/project-suspension-guidelines)）
- **擁有者單純不再登入**：只要個人帳號還在，專案就在。畢業本身不會影響個人 Gmail 帳號。這裡的風險是沒人能操作 Console，例如無法加測試使用者、無法回 Google 的信，不是專案消失。
- **聯絡人要維持有效**：「Project owners and editors must be kept up-to-date. … If we don't have updated contact information for your project, you might miss out on important messages that require your action. This could result in the loss of your access to the Google APIs.」（[OAuth 2.0 Policies](https://developers.google.com/identity/protocols/oauth2/policies)，「Maintain a list of relevant contacts for the project」）受限範圍每年重審的通知也寄給 Owner、Editor 與同意畫面上的聯絡信箱（[Restricted scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)）。
- **使用者支援信箱**：同意畫面的「User support email」只能是登入者自己的 email，或登入者管理的 Google 群組（[Manage OAuth App Branding](https://support.google.com/cloud/answer/15549049)）。可以用群組信箱，交接時不必改。
- **權限更細的角色**：IAM 有 OAuth Config Editor（`roles/oauthconfig.editor`），可以讀寫 OAuth 設定。只給這個角色能不能管理測試使用者與發布狀態，**未驗證**。

### 學校 Google Workspace 的「內部」使用者類型

- **條件**：「Projects associated with a Google Cloud Organization can configure Internal users to limit authorization requests to members of the organization.」名單外的帳號會得到 `org_internal` 錯誤（[Manage App Audience](https://support.google.com/cloud/answer/15549945)）。
  - 免驗證的條件是「The project must be owned by the organization, and its OAuth consent screen needs to be configured for an Internal user type. In this case, your app might need approval from an organization administrator.」（[Restricted scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)，「Internal use only」）
  - 內部 app「are not subject to the unverified-app screen or the 100-user cap」（[When is verification not needed](https://support.google.com/cloud/answer/13464323)）。
  - 7 天失效只適用於 external 加 Testing（第 1 節），所以內部 app 不受影響。
- **看不到「Internal」選項**：代表專案不在機構下，要先把專案遷移進機構（[FAQ](https://support.google.com/cloud/answer/13463817)，「How can I mark my app as internal-only…」）。遷移進機構後，「Self-service rollback from an organization back to no organization isn't supported.」（[Migrating projects](https://docs.cloud.google.com/resource-manager/docs/project-migration)）
- **機構怎麼來**：Workspace 帳號關聯網域後會自動建立機構資源，網域內的使用者預設有 Project Creator 角色（[Creating and managing organization resources](https://docs.cloud.google.com/resource-manager/docs/creating-managing-organization)）。但 Google Cloud 在 Workspace 是可由管理員開關的「Additional Google services」之一（[Turn on or off additional Google services](https://knowledge.workspace.google.com/admin/users/advanced/turn-on-or-off-additional-google-services)）。
- **管理員可能擋**：
  - 受限的 Drive 範圍：「User authorization of scopes associated with restricted Google Workspace services, including high-risk Gmail and Drive scopes, might require additional configuration by your organization's administrators.」（[Manage App Audience](https://support.google.com/cloud/answer/15549945)）
  - 管理員端的對應設定是「Internal apps — This allows internal apps built by your organization to access restricted Google Workspace APIs. To allow API access for all internal apps, check the Trust internal apps box.」（[Control which third-party & internal apps access Google Workspace data](https://knowledge.workspace.google.com/admin/apps/control-which-apps-access-google-workspace-data)）
- **帳號回收**：專案屬於機構時，「it will always have at least the Organization as an owner and as such will not be orphaned. The Organization administrator has full control over each project in the Organization.」（[Project suspension guidelines](https://docs.cloud.google.com/resource-manager/docs/project-suspension-guidelines)）也就是說，學生帳號被收回後專案不會消失，但控制權在學校管理員手上。
- **使用者範圍**：只有學校網域的帳號能授權。用個人 Gmail 管理社團 Drive 的譜務，或其他學校的社團，都無法登入。
- 華夏國樂社所在學校的 Workspace 是否開放 Google Cloud、第三方與內部 app 的設定為何，**未查**，要問學校資訊單位。

## 對 #68 選項的補充

以下是查證結果對 #68 背景裡各選項的影響，不是決定：

- **「未經驗證的正式版」**：
  - 「只改 Cloud Console 設定」正確，7 天失效會消失。
  - 「人數上限算的是授權過泠靈的人」大致正確，精確地說是能授權的 Google 帳號數，以專案為單位終身累計、不能重置。
  - 一旦公開發行、其他社團也用同一個用戶端，100 人是所有社團共用；用完後所有**新**使用者都無法登入，包括下一任譜務。
- **「縮小成 `drive.file`」**：
  - 「可能免驗證、無警告」可以確認：若連 `spreadsheets` 也拿掉、只留 `drive.file`，就只剩非敏感範圍，不必驗證、沒有人數上限、沒有未驗證畫面；Sheets API 照樣能用。
  - 代價是手動建立的資料夾與手動上傳的 PDF 都看不到，要經過桌面 Picker 交給泠靈。Picker 選資料夾能否涵蓋整棵樹，是可行性的關鍵，文件沒寫、要實測。
- **「送驗證」**：
  - 保留 `drive` 要走受限範圍審查：約 6 週、要有網域與隱私權政策、每年重審，可能還要 CASA（是否適用於無伺服器的桌面程式，文件矛盾）。
  - 只降到 `spreadsheets` 加 `drive.file` 則走敏感範圍驗證：約 10 個工作天、不需要 CASA。
- **「學校 Workspace 內部 app」**：
  - 「不必驗證、無 7 天過期、無人數上限」可以確認。
  - 「專案要建在學校網域下」正確；既有專案也可以遷入，但遷入後不能自行移回。
  - 「畢業後帳號會被收回」時，專案留在學校機構下由管理員控制，不會變成孤兒。
  - 只有學校網域帳號能用，公開發行版給其他學校的社團就不能用。
- **現狀（個人帳號、No organization）的交接**：
  - 加第二位擁有者要對方接受邀請；擁有者不能是 Google 群組。
  - 至少保持兩位擁有者，就能避免「原擁有者刪帳號，專案跟著被刪」。
  - OAuth 用戶端六個月沒人用會被自動刪除。

## 未能查證

- 100 人上限的細節：同一帳號撤銷後再授權、換裝置重新授權是否再計一次；Testing 期間授權過的帳號轉正式版後是否計入。文件只說以「Google 帳號」計、終身累計、不能重置。需要的來源：Google 對 user cap 計數方式的正式說明，或用一個測試專案實測 Console 上「OAuth user cap」的數字變化。
- 移出測試使用者名單是否歸還名額：文件只寫「consumes … once added」。
- 一般個人帳號在人數上限內，是否可能因為 Google 的風險判斷而直接被擋（而不只是警告）：文件沒寫。
- 不經伺服器的桌面程式要求受限範圍時是否需要 CASA：開發者文件與說明中心的說法矛盾，要等 Google 審查團隊的實際判定。
- CASA 費用只有 TAC Security 自己公布的價格，而且它不同頁面的數字不一致；其他實驗室的價格未查。
- `drive.file` 的三個行為：選資料夾是否涵蓋裡面的檔案；泠靈建立的資料夾裡、使用者手動放入的檔案能否存取；A 帳號經泠靈建立的檔案，B 帳號用同一個用戶端能否直接存取。另外，以 `spreadsheets` 範圍建立的既有試算表，改成 `drive.file` 後是否算「泠靈建立的檔案」也未知。都需要實測。
- 沒有網域的桌面程式在未做品牌驗證時，同意畫面實際顯示什麼。
- 驗證所需的網域能否用 `github.io` 子網域：文件只說要在 Search Console 驗證、且可用任何 Top Private Domain，沒有針對 `github.io` 的說明。
- `roles/oauthconfig.editor` 單獨是否足以管理測試使用者與發布狀態。
- 華夏國樂社所在學校的 Workspace 設定：是否開放 Google Cloud、是否限制第三方或內部 app 存取 Drive。

## 來源

Google Cloud 說明中心（support.google.com/cloud，頁面不顯示更新日期，內容以 2026-10-11 抓取為準）：

- [Manage App Audience](https://support.google.com/cloud/answer/15549945)
- [Unverified apps](https://support.google.com/cloud/answer/7454865)
- [OAuth App Verification Help Center](https://support.google.com/cloud/answer/9110914)
- [Frequently Asked Questions](https://support.google.com/cloud/answer/13463817)
- [When is verification not needed](https://support.google.com/cloud/answer/13464323)
- [Restricted Scopes](https://support.google.com/cloud/answer/13464325)
- [Security Assessment](https://support.google.com/cloud/answer/13465431)
- [Requesting Minimum Scopes](https://support.google.com/cloud/answer/13807380)
- [OAuth Application Rate Limits](https://support.google.com/cloud/answer/9028764)
- [Manage OAuth App Branding](https://support.google.com/cloud/answer/15549049)
- [Manage OAuth Clients](https://support.google.com/cloud/answer/15549257)
- API Console Help：[Manage project members or change project ownership](https://support.google.com/googleapi/answer/6158846)

Google for Developers（括號內為頁尾的 Last updated）：

- [Using OAuth 2.0 to Access Google APIs](https://developers.google.com/identity/protocols/oauth2)（2026-05-26）
- [OAuth 2.0 for iOS & Desktop Apps](https://developers.google.com/identity/protocols/oauth2/native-app)（2026-09-14）
- [OAuth 2.0 Policies](https://developers.google.com/identity/protocols/oauth2/policies)（頁面無日期；changelog 最後一筆 2025-10-27）
- [Restricted scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)（2026-08-19）
- [Sensitive scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification)（2026-08-19）
- [How to handle granular permissions](https://developers.google.com/identity/protocols/oauth2/resources/granular-permissions)（2026-05-26）
- [OAuth 2.0 Scopes for Google APIs](https://developers.google.com/identity/protocols/oauth2/scopes)（2026-09-14）
- [Choose Google Drive API scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)（2026-09-03）
- [Choose Google Sheets API scopes](https://developers.google.com/workspace/sheets/api/scopes)（2026-09-03）
- Sheets API 參考：[spreadsheets.create](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/create)（2026-06-16）、[spreadsheets.values.batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchUpdate)、[spreadsheets.batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)
- Drive API 參考：[files.list](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list)
- [Create and manage files](https://developers.google.com/workspace/drive/api/guides/create-file)（2026-09-04）
- [Overview of the Google Picker](https://developers.google.com/workspace/drive/picker/guides/overview)（2026-09-14）
- [Integrate the Google Picker into desktop and mobile apps](https://developers.google.com/workspace/drive/picker/guides/desktop-mobile-picker)（2026-09-29）
- [Drive API Usage limits](https://developers.google.com/workspace/drive/api/guides/limits)（2026-09-03）
- [Sheets API Usage limits](https://developers.google.com/workspace/sheets/api/limits)（2026-09-03）
- [Google Workspace API user data and developer policy](https://developers.google.com/workspace/workspace-api-user-data-developer-policy)（2026-09-03）
- [Nest Device Access：Authorization errors](https://developers.google.com/nest/device-access/reference/errors/authorization)（2026-10-02）
- [Apps Script：OAuth Client Verification](https://developers.google.com/apps-script/guides/client-verification)（2026-09-03）

Google Cloud 文件：

- [projects.setIamPolicy（Resource Manager v1）](https://docs.cloud.google.com/resource-manager/reference/rest/v1/projects/setIamPolicy)（2025-06-11）
- [Manage access to projects, folders, and organizations](https://docs.cloud.google.com/iam/docs/granting-changing-revoking-access)（2026-10-08）
- [Project suspension guidelines](https://docs.cloud.google.com/resource-manager/docs/project-suspension-guidelines)（2026-10-09）
- [Data deletion on Google Cloud](https://docs.cloud.google.com/docs/security/deletion)
- [Creating and managing organization resources](https://docs.cloud.google.com/resource-manager/docs/creating-managing-organization)（2026-10-09）
- [Migrating projects into an organization](https://docs.cloud.google.com/resource-manager/docs/project-migration)（2026-10-09）

Google Workspace 管理員說明（knowledge.workspace.google.com）：

- [Control which third-party & internal apps access Google Workspace data](https://knowledge.workspace.google.com/admin/apps/control-which-apps-access-google-workspace-data)（2026-10-09）
- [Authorize unverified third-party apps](https://knowledge.workspace.google.com/admin/apps/authorize-unverified-third-party-apps)（2026-10-09）
- [Turn on or off additional Google services](https://knowledge.workspace.google.com/admin/users/advanced/turn-on-or-off-additional-google-services)（2026-10-09）

App Defense Alliance 與評估實驗室：

- [CASA overview](https://appdefensealliance.dev/casa)（2026-06-27）、[CASA Assurance Levels](https://appdefensealliance.dev/casa/casa-tiering)（2026-06-27）、[CASA User Initiated Assessment](https://appdefensealliance.dev/casa/casa-start)（2022-10-13）
- [TAC Security：ESOF AppSec ADA CASA FAQs](https://tacsecurity.com/?p=18721)（實驗室自行公布的價格，頁面無日期）

程式庫：

- `google-auth-oauthlib` 1.5.0：`google_auth_oauthlib/flow.py`（`run_local_server` 在 375-486 行）

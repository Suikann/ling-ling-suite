# 自建 Cloud 專案切成正式版的條件查證

- 來源 ticket：[#75](https://github.com/Suikann/ling-ling-suite/issues/75)（背景：[#68](https://github.com/Suikann/ling-ling-suite/issues/68) Q7 退回範圍 A，社團設定加「匯入自己的用戶端」；[#72](https://github.com/Suikann/ling-ling-suite/issues/72) 的實測見分支 `research/drive-file-picker` 的 `docs/notes/drive-file-picker-test.md`）
- 調查日期：2026-10-13
- 前提：專案擁有者是社團的譜庫帳號（個人 Gmail，沒有機構），只有一個要求 `drive`（受限）與 `spreadsheets`（敏感）的 Desktop app 用戶端，使用者類型 External，不送驗證。
- 已知事實不重查，見分支 `research/google-oauth-verification` 的 `docs/notes/google-oauth-verification.md`（下稱「前一份查證」）第 2、5、6 節：Testing 授權在同意後 7 天失效；未驗證正式版的 100 人上限以專案為單位、終身累計、不能重置；受限範圍審查約 6 週；沒有機構的專案擁有者只能是個人帳號；6 個月沒用的用戶端會被自動刪除。
- 文件基準：developers.google.com 與 docs.cloud.google.com 的頁尾日期列在文末「來源」；support.google.com 的說明頁不顯示更新日期，以 2026-10-13 抓取的內容為準。
- 方法：只讀 Google 官方文件與條款。沒有 Cloud Console 的存取權，沒有實際建立專案、切換發布狀態或填表；Console 實際會不會擋下某個操作，文件沒寫的都標成未能查證。

## 結論摘要

| 問題 | 答案 |
| --- | --- |
| 首頁、隱私權政策能不能留空 | 建立流程（Get Started）只要求 App name、User support email、Audience、聯絡 email 與同意 User Data Policy，不問網址。Branding 頁說這些連結「required for all external production apps」，缺了「will not be able to submit your app for verification」；但沒寫缺了會擋下「Publish app」。**按下 Publish app 時會不會因為缺網址被擋，未能查證。** |
| 能不能填泠靈維護者的網址 | 文件對「未驗證、不送審」的專案沒有限制網址歸屬的條文。網域擁有權（Search Console、DNS 層級的 Domain 資源、由專案 Owner 驗證）是**品牌驗證**的要求。不送驗證時，品牌資訊的修改只存成 Draft，沒驗證、沒 Publish branding 前不會出現在同意畫面上，所以填了也看不到。 |
| 授權網域加別人的網域 | 文件寫「If you have verified the domain with Google, you can use any Top Private Domain」，沒寫未驗證的網域不能加。要求 Search Console 的條文都限定在驗證流程。**Console 會不會拒絕加入未驗證的網域，未能查證。** 若日後要做品牌驗證，泠靈維護者必須成為每個社團專案的 Owner，並以 DNS 驗證網域；`github.io` 子網域無法做 DNS 驗證，只能用自有網域。 |
| 正式版的上限與警告 | 和泠靈的專案相同：Google hasn't verified this app 警告畫面，加上該專案自己的 100 人終身上限（各專案各算各的）。官方總表把這個狀態標為「Strongly discouraged」。 |
| 擁有者自己用有沒有例外 | 一般 Cloud 專案的文件沒有「擁有者或專案成員不顯示警告、不計人數」的條文。Apps Script 的對照表明寫 Gmail 發布者自己也會走未驗證流程（「Any Gmail account, including the account used to publish the app」）。一個帳號只佔 100 人中的 1 人。 |
| 停在 Testing | 7 天失效之外：每次授權都看到 testing 警告、只有名單上的帳號能授權（名單本身也有 100 人上限）、名單外的帳號被擋。只有一個譜庫帳號時，實際影響就是每 7 天要重跑一次瀏覽器登入。 |
| 自建的最少步驟 | 首次進 Console 同意條款 → 建專案 → 啟用 Drive API 與 Sheets API → Google Auth Platform 的 Get Started 精靈 → Audience（加測試使用者或 Publish app）→ Clients 建 Desktop app 並**當場**下載 JSON。Data Access 可以跳過。 |
| 會卡住的步驟 | 文件找得到的有：用戶端密鑰只在建立時能下載一次；API 條款要求「legal age to form a binding contract」；Workspace 指南把帳單列為 Optional，Drive 與 Sheets API 標示「no additional cost」。**要不要綁帳單、要不要電話驗證、首次進 Console 要不要選國家，文件都沒寫清楚，未能查證。** |

## 1. 首頁、隱私權政策與授權網域

### 欄位能不能留空

- 建立 app 的精靈只問四件事：「Under App Information, in App name, enter an App name.」「In User support email, choose a support email address …」「Under Audience, select the user type for your app.」「Under Contact Information, enter an Email address …」，最後「select I agree to the Google API Services: User Data Policy」再按 Create（[Configure the OAuth consent screen and choose scopes](https://developers.google.com/workspace/guides/configure-oauth-consent)）。首頁、隱私權政策、服務條款、授權網域都不在精靈裡。
- 同一頁：「If you don't know required consent screen information, you can use placeholder information prior to release.」
- Branding 頁對這三個連結的說法：「It is recommended that you provide these links so users can learn more about your application and data practices.」接著「Note : These links are required for all external production apps. You will not be able to submit your app for verification if it is missing these links.」（[Manage OAuth App Branding](https://support.google.com/cloud/answer/15549049)，「App Domain」）
  - 第一句是「required」，但後果只寫到「不能送驗證」。按 Publish app 時 Console 會不會因為缺網址而擋下，文件沒寫，**未能查證**。
- 舊版說明的寫法：「You must have a privacy policy URL. Add URLs for your homepage and Terms of Service if you have them.」這段放在「Verification for apps」底下，是送驗證的步驟（[Unverified apps](https://support.google.com/cloud/answer/7454865)）。

### 政策面：只有一個帳號在用，不算「production app」

- OAuth 政策對 production app 的定義：「It isn't for personal use. An app is considered to be for personal use if it's not shared with anyone else or will be used by fewer than 100 people (all of whom are known personally to you).」（[OAuth 2.0 Policies](https://developers.google.com/identity/protocols/oauth2/policies)，「Use separate projects for testing and production」）
- 下面兩條都只約束 production app：
  - 「Every production app that uses OAuth 2.0 must have a publicly accessible home page. … The homepage must exist on a verified domain under your ownership.」（同頁，「Host a homepage for production apps」）
  - 「For production apps, brand information must be verified.」（同頁，「Accurately represent your identity」）
- 結論：社團專案只有譜庫帳號一個人在用，屬於政策上的 personal use。發布狀態可以是 In production，但政策上不是 production app，所以「首頁必須在自己驗證過的網域」這條不適用。這是對條文的推論，Google 沒有針對這種組合的說明。
- 「Only use domains you own」那一條只講「redirect URIs and JavaScript origins」（同頁）。Desktop app 用戶端走 loopback，沒有這兩種設定，不受影響。

### 填泠靈維護者的網址會怎樣

- 品牌資訊的修改要驗證後才生效：「If you change any of the details that appear on your OAuth consent screen, such as the app's logo, name, home page, privacy policy URI, or authorized domains, the changes are saved as Draft Branding. You need to verify the new branding configuration and click the Publish branding button before the updates take effect on your Published Branding and appear on your OAuth consent screen.」（[Submit for brand verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification)）
- 沒做品牌驗證時，同意畫面只顯示網域：「Without verification, only your application domain will be visible to users.」（[Manage OAuth App Branding](https://support.google.com/cloud/answer/15549049)，「Brand Verification」）App name 也一樣：「The app name will be displayed on the OAuth consent screen only if your app has been verified.」（同頁）
- 合起來看：不送驗證時，在社團專案填泠靈的首頁與隱私權政策只會存成 Draft，使用者在同意畫面上看不到。是否填寫對使用者體驗沒有差別。
  - Testing 狀態下是否也走 Draft 機制，文件沒分開寫。[#72](https://github.com/Suikann/ling-ling-suite/issues/72) 的實測（泠靈專案，Testing）看到同意畫面提示「看不到隱私權政策與服務條款連結」，與此一致，但那次沒有確認專案是否填了網址。

### 授權網域與 Search Console

- 授權網域的說明：「All domains used in your project, whether in the branding page or client configuration pages must be pre-registered here.」「If you have verified the domain with Google, you can use any Top Private Domain as an Authorized Domain.」「If your app needs to go through verification, please go to the Google Search Console to check if your domains are verified.」（[Manage OAuth App Branding](https://support.google.com/cloud/answer/15549049)，「Authorized domains」）
  - 「需要 Search Console」的那句前提是「If your app needs to go through verification」。沒有任何一句說未驗證的網域不能加入清單。Console 實際會不會拒絕，**未能查證**。
- 要求擁有權的條文全部在驗證流程裡：
  - 「As part of the brand verification process, Google requires verification of all domains that are associated with an application's OAuth consent screen and credentials.」「A Google Account with owner permissions for a domain must be associated with the API Console project that uses that authorized domain.」（[Submit for brand verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification)）
  - 「You must verify that you own all domains listed in your Authorized domains section … An account listed as a project owner or editor on your GCP account must verify ownership of the authorized domain using Google Search Console.」（[Verification requirements](https://support.google.com/cloud/answer/13464321)，「Brand verification requirements」）
  - 驗證審查的實際要求更嚴：「The domain verification must be performed by a Google account that is currently a Project Owner of your Google Cloud Project.」並且「You must verify the Domain Property (DNS-level), rather than a "URL prefix" or "Site," property.」（[Domain Verification](https://support.google.com/cloud/answer/13804266)）
  - Apps Script 的說法相同：「You don't need to publish your app from an account in this domain, but the domain owner must be an editor or owner of the script project.」（[Apps Script：OAuth Client Verification](https://developers.google.com/apps-script/guides/client-verification)）
- 對「填泠靈網址」的意義：
  - 不送驗證：文件沒有要求。
  - 日後若某個社團要做品牌驗證：泠靈維護者的帳號必須成為該社團專案的 Owner，並用 DNS TXT 記錄驗證網域。`github.io` 在公共後綴清單上，頂層私有網域是 `<帳號>.github.io`，維護者無法對它加 DNS 記錄，只能用自有網域。首頁還要「accurately represent and identify your app or brand」、隱私權政策要與首頁同網域（[Verification requirements](https://support.google.com/cloud/answer/13464321)）。
- 授權網域上限 10 個；驗證請求也最多 10 個網域（[Domain Verification](https://support.google.com/cloud/answer/13804266)）。泠靈只會用到一個，不受影響。

## 2. 正式版的人數上限與警告畫面

### 和泠靈的專案相同

- 官方的狀態總表：Published、External、Unverified 這一列寫「Any Google user can access. Strongly discouraged. Because the app has not completed brand verification, the app's name and logo are not displayed on the consent screen. Additionally, for apps requesting sensitive or restricted scopes, unverified app warnings (Danger UI) will be displayed to users, and a hard cap of 100 total users applies.」（[OAuth app state overview](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview)）
- 上限以專案為單位：「The user cap applies over the entire lifetime of the project, and it cannot be reset or changed.」（[Manage App Audience](https://support.google.com/cloud/answer/15549945)，「OAuth user cap」）社團的專案有自己的 100 人，和泠靈的專案互不影響。
- 上限可能因專案而調整：「These might be adjusted for specific apps based on the app history, developer reputation, and riskiness.」（同頁）全新的社團專案沒有歷史，實際額度是否就是 100，**未能查證**。
- 人數上限之外，所有 app 另有新使用者授權速率限制，「Dependent on application history, developer reputation, and riskiness」（[OAuth Application Rate Limits](https://support.google.com/cloud/answer/9028764)）。一個帳號不會碰到。
- 免驗證的依據：「Personal Use apps: If the app is for your personal use (fewer than 100 users), you and your limited number of users can continue using the app without going through verification(users will be allowed to click through "unverified app" warning screens during sign-in).」（[When is verification not needed](https://support.google.com/cloud/answer/13464323)）

### 擁有者自己用，有沒有例外

- 一般 Cloud 專案的文件（Manage App Audience、Unverified apps、Submit for brand verification、OAuth app state overview）都沒有「專案擁有者或成員不顯示警告、不計入人數」的條文。
- 最接近的官方說法在 Apps Script：對照表「Publisher is a Gmail account」那一欄，Gmail 使用者是「Unverified auth flow」，註腳寫「Any Gmail account, including the account used to publish the app.」（[Apps Script：OAuth Client Verification](https://developers.google.com/apps-script/guides/client-verification)，「Applicability」）。這是 Apps Script 的文件，但 Apps Script 底下也是 Google OAuth 用戶端；據此推論，個人 Gmail 擁有的一般專案，擁有者自己授權時也會看到警告。**一般 Cloud 專案的情形未能查證。**
- 計數單位是「Google Accounts able to grant access」（[Submit for brand verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification)，「Personal use」）。只有譜庫帳號在用時佔 1 人。前一份查證已列出：同一帳號重新授權、換電腦是否再算一次，文件沒寫。

### 一個帳號在用時的其他影響

- Security Checkup：「Security Checkup might show your app as risky and unverified.」（[Unverified apps](https://support.google.com/cloud/answer/7454865)）譜庫帳號的安全性檢查可能把社團的用戶端列為有風險。
- Testing 的名單在正式版不能用：「This functionality is available only to apps which are still in the "test" phase. This is not available to apps that have been "published" to the "production" state.」（[Submitting your app for verification](https://support.google.com/cloud/answer/13461325)，「Add test users」）
- 同意畫面會讓使用者分別勾選 `drive` 與 `spreadsheets`（granular consent），這點與泠靈的專案相同，見前一份查證第 2 節。
- 正式版的續期權杖照一般規則失效：撤銷、六個月沒用、每帳號每用戶端超過 100 個等（[Using OAuth 2.0 to Access Google APIs](https://developers.google.com/identity/protocols/oauth2#expiration)）。
- 從 Testing 切到正式版時，Testing 期間已發出的續期權杖是否仍在 7 天後失效（也就是切換後要不要重新登入一次），文件沒寫，**未能查證**。

## 3. 停在測試中

7 天失效之外，文件寫到的影響：

- **只有名單上的帳號能授權**：「This setting means that your app is still in development and is only available to users you add to the list of test users. You must manage the list of Google Accounts that are involved in the development or testing of your app.」（[Submit for brand verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification)，「Projects used in Development, Testing, or Staging tiers」）[#72](https://github.com/Suikann/ling-ling-suite/issues/72) 的實測也看到名單外的帳號被擋（403 `access_denied`）。社團換譜務時，新的人若要用自己的帳號登入，得先由專案擁有者加進名單；若一直用譜庫帳號登入，名單不必改。
- **名單上限 100 人，加入就佔名額**：「Projects configured with a publishing status of Testing are limited to up to 100 test users … A test user consumes a project's test user quota once added to the project.」（[Manage App Audience](https://support.google.com/cloud/answer/15549945)）只放譜庫帳號一個，不會碰到。
- **每次授權都有 testing 警告**：「Google will display a warning message before allowing a specified test user to authorize scopes requested by your project's OAuth clients.」（同頁）總表的說法是「Users see a warning UI indicating the app is in testing, rather than the standard unverified app screen.」（[OAuth app state overview](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview)）也就是說，Testing 看到的是 testing 警告，不是正式版的 Google hasn't verified this app 畫面；每 7 天重新登入時都會再看到一次。
  - [#72](https://github.com/Suikann/ling-ling-suite/issues/72) 只要求 `drive.file`（非敏感）時沒有看到警告頁；要求 `drive` 與 `spreadsheets` 時是否顯示，該次沒測。
- **文件明列的三項限制**：「Your app is still subject to a tester warning screen, as shown in figure 2, a user cap is in effect, and the refresh token lifetime is limited.」（[Submit for brand verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification)）
- **Brand Account**：名單上的使用者可以替自己管理的 Brand Account 授權（[Manage App Audience](https://support.google.com/cloud/answer/15549945)）。譜庫帳號是一般帳號，與此無關。
- **Workspace 的例外不適用**：Workspace 管理員把 app 標為 Trusted 時，「overrides certain standard OAuth limitations for the organization's users, such as the 100-test-user cap and the 7-day refresh token expiration limit」（[OAuth app state overview](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview)）。譜庫帳號是個人 Gmail，沒有管理員。
- **政策定位**：文件把 Testing 定位成開發與測試用（同上）。社團長期用 Testing 專案做正式工作，不符合「testing」的字面用途；但如第 1 節，一個人自用本來就落在 personal use，不需要驗證。文件沒有禁止長期停在 Testing 的條文，也沒有 Testing 專案閒置多久會被處理的規定（6 個月沒用的用戶端自動刪除與發布狀態無關，見前一份查證第 5 節）。
- 文件沒有提到 Testing 與正式版在 API 配額或 Drive、Sheets API 功能上的差異。

## 4. 自建專案的最少步驟與卡關點

### 最少要經過的 Console 畫面

依官方 Workspace 指南與 Drive Python quickstart 的順序：

1. **首次進入 Console 同意條款**：文件沒有專門描述首次登入畫面；機構資源的說明提到新使用者「log in to the Google Cloud console and accept the terms and conditions」（[Quickstart: Create your Google Cloud resource hierarchy](https://docs.cloud.google.com/resource-manager/docs/manage-google-cloud-resources)）。畫面上還有哪些欄位（例如國家），**未能查證**。
2. **建專案**：「Menu > IAM & Admin > Create a Project」，填 Project Name，Location 選擇後按 Create（[Create a Google Cloud project](https://developers.google.com/workspace/guides/create-project)）。個人 Gmail 只會有「No organization」。Project ID 建立後不能改。
3. **啟用 API**：「Menu > APIs & Services > Library > Google Workspace」，點 API 再按 Enable，重複一次（[Enable Google Workspace APIs](https://developers.google.com/workspace/guides/enable-apis)）。泠靈要啟用 Google Drive API 與 Google Sheets API。
   - 通用說明另有「If prompted, enable billing.」「If prompted, read and accept the API's Terms of Service.」（[Submitting your app for verification](https://support.google.com/cloud/answer/13461325)）。Drive 與 Sheets API 會不會跳出這兩個提示，**未能查證**。
4. **Google Auth Platform 精靈**：「Menu > Google Auth platform > Branding」，看到「Google Auth platform not configured yet」時按 Get Started，依序填 App name、User support email、Audience 選 External、聯絡 email、勾同意 User Data Policy，按 Create（[Configure the OAuth consent screen and choose scopes](https://developers.google.com/workspace/guides/configure-oauth-consent)）。
   - User support email 只能選登入者自己的 email，或登入者管理的 Google 群組（[Manage OAuth App Branding](https://support.google.com/cloud/answer/15549049)）。用譜庫帳號登入，選譜庫帳號自己即可。
   - App name 不能用 Google 產品名稱（同頁的「unacceptable names」範例）。
5. **Audience**：
   - 停在 Testing：「Click Audience. Under Test users, click Add users. Enter your email address …」（[Configure the OAuth consent screen](https://developers.google.com/workspace/guides/configure-oauth-consent)）。
   - 切正式版：在 Audience 頁按「Publish app」（[Manage App Audience](https://support.google.com/cloud/answer/15549945)：「A project's publishing status is considered In production after selecting the Publish app button.」）。按下後會出現什麼對話框，文件沒有截圖或文字，**未能查證**。
6. **Data Access（可跳過）**：Drive quickstart 寫「For now, you can skip adding scopes.」（[Python quickstart](https://developers.google.com/workspace/drive/api/quickstart/python)）。不宣告範圍的後果是正式版會顯示未驗證畫面（「Your app uses sensitive or restricted scopes that you haven't selected on the OAuth consent screen configuration page.」，[Unverified apps](https://support.google.com/cloud/answer/7454865)），但不送驗證本來就會看到這個畫面，所以宣告與否對社團沒有差別。Data Access 頁對敏感與受限範圍寫「you must provide justification for the request. Additionally, you must include a link to a demo video」（[Manage App Data Access](https://support.google.com/cloud/answer/15549135)）；這是儲存時就要求還是送驗證時才要求，**未能查證**，因此建議文件直接跳過這一步。
7. **Clients**：「Menu > Google Auth platform > Clients」→ Create Client → Application type 選 Desktop app → 填 Name → Create（[Create access credentials](https://developers.google.com/workspace/guides/create-credentials)）。「The console does not require any additional information to create OAuth 2.0 credentials for desktop applications.」（[Manage OAuth Clients](https://support.google.com/cloud/answer/15549257)）

### 會卡住非技術使用者的地方

- **用戶端密鑰只能下載一次**：「you will only be able to view and download the full client secret once, at the time of its creation.」「After the initial creation, the Google Cloud Console will only display the last four characters of the client secret.」遺失時要用 secret rotation 產生新的（[Manage OAuth Clients](https://support.google.com/cloud/answer/15549257)，「Client Secret Handling and Visibility」）。「匯入自己的用戶端」的說明必須在這一步強調當場按下載 JSON。
- **帳單（billing）**：
  - Workspace 指南把它列成選用：「Optional: Enable billing for your Cloud project」「Depending on the Google Workspace APIs and features that you want to use, you might also need to enable billing」（[Create a Google Cloud project](https://developers.google.com/workspace/guides/create-project)）。
  - Drive 與 Sheets 的計價：「All standard use of the Google Drive API is available at no additional cost. Exceeding the quota request limits is planned to incur charges to your Google Cloud billing account later in 2026.」（[Drive API Usage limits](https://developers.google.com/workspace/drive/api/guides/limits)）；Sheets 同樣寫法（[Sheets API Usage limits](https://developers.google.com/workspace/sheets/api/limits)）。
  - Cloud Billing 文件另有一句：「Projects that are not linked to an active Cloud Billing account can't use Google Cloud or Google Maps Platform services. This is true even if you only use services that are free.」（[Create a new self-serve Cloud Billing account](https://docs.cloud.google.com/billing/docs/how-to/create-billing-account)）這句講的是 Google Cloud 服務；Drive 與 Sheets API 屬於 Workspace API，Workspace 自己的文件把帳單列為選用。兩邊字面上有張力，**Drive 與 Sheets API 在沒有帳單的專案能不能用，沒有實測，未能查證**。泠靈自己的 `ling-ling-suite` 專案有沒有綁帳單，可以作為佐證，但本次沒有查。
  - Starter Tier（2026 年的新方案，免帳單、由 Google 代管的專案）只支援 Google AI Studio 部署的資源，「You can't enable other Google Cloud APIs within that same project」；在 Console 用它不支援的服務時，「you are directed to sign up for the Google Cloud Free Trial and create your own standard Google Cloud project」（[Learn about the Google Cloud Starter Tier](https://docs.cloud.google.com/docs/starter-tier)）。這只影響曾用 AI Studio 部署過 app 的帳號；這類帳號開 Console 時會不會被導向要求填卡的 Free Trial，**未能查證**。
- **年齡與簽約能力**：Google APIs 條款：「You may not use the APIs and may not accept the Terms if (a) you are not of legal age to form a binding contract with Google, or (b) you are a person barred from using or receiving the APIs under the applicable laws …」；代表團體使用時「you represent and warrant that you have authority to bind that entity」（[Google APIs Terms of Service](https://developers.google.com/terms)，Section 1）。Google 帳號本身在台灣的最低年齡是 13 歲（台灣不在例外清單上，「For all countries not listed below, 13 is the minimum age」，[Age requirements on Google Accounts](https://support.google.com/accounts/answer/1350409)），但操作 Console 的人還要符合上面的簽約年齡。實際上 Console 會不會檢查年齡，**未能查證**。
- **專案配額**：「The number of projects any user or service account can create is limited.」剩不到 30 個時新專案頁會提示（[Create projects](https://docs.cloud.google.com/resource-manager/docs/creating-managing-projects)）；「Customers might have different quota based on these and other factors.」（[Project quota requests](https://support.google.com/cloud/answer/6330231)）社團只建一個專案，文件沒有新帳號配額為 0 的說法。
- **電話驗證、國家選擇、新帳號限制**：官方文件都沒有提到建立專案或 OAuth 用戶端需要電話驗證；國家只在建立帳單帳戶時出現。**是否會遇到，未能查證**，需要用一個新的 Gmail 實際走一次。
- **聯絡資訊要有人收信**：「Ensure that the provided email addresses are current, as critical information about your project will be regularly communicated to these addresses.」（[Manage OAuth App Branding](https://support.google.com/cloud/answer/15549049)，「Developer contact information」）6 個月沒用的用戶端刪除前 30 天的通知也寄到這裡（前一份查證第 5 節）。用譜庫帳號當聯絡信箱，交接時跟著帳號走。

## 未能查證

- 首頁、隱私權政策留空時，Audience 頁的「Publish app」會不會被擋；按下 Publish app 會出現什麼對話框。需要的來源：用一個測試專案實際按一次。
- 授權網域能否加入未在 Search Console 驗證的網域（文件只說驗證過的網域可以用任何頂層私有網域）。
- Testing 狀態下品牌資訊是否也走 Draft／Publish branding 機制。
- 一般 Cloud 專案（非 Apps Script）的擁有者自己授權時是否也看到 Google hasn't verified this app 畫面。只有 Apps Script 文件明寫 Gmail 發布者自己也會看到。
- 全新專案的未驗證人數上限是否就是 100（文件說可能依 app 歷史調整）。
- 從 Testing 切到正式版後，Testing 期間發出的續期權杖是否仍在 7 天後失效。
- 停在 Testing 且要求 `drive` 與 `spreadsheets` 時，testing 警告畫面的實際樣子（#72 只測了非敏感的 `drive.file`）。
- Data Access 頁加入敏感或受限範圍時，是否在儲存當下就要求填理由與示範影片。
- 沒有綁帳單的專案能否啟用並使用 Drive API 與 Sheets API；啟用時會不會跳出帳單提示。
- 新 Gmail 帳號首次進 Console 時是否要選國家、是否要電話驗證、是否被導向 Free Trial（要求信用卡）。
- Console 是否檢查年齡。

## 來源

Google Cloud 說明中心（support.google.com/cloud，頁面不顯示更新日期，內容以 2026-10-13 抓取為準）：

- [Manage App Audience](https://support.google.com/cloud/answer/15549945)
- [Manage OAuth App Branding](https://support.google.com/cloud/answer/15549049)
- [Manage OAuth Clients](https://support.google.com/cloud/answer/15549257)
- [Manage App Data Access](https://support.google.com/cloud/answer/15549135)
- [Unverified apps](https://support.google.com/cloud/answer/7454865)
- [When is verification not needed](https://support.google.com/cloud/answer/13464323)
- [Verification requirements](https://support.google.com/cloud/answer/13464321)
- [Domain Verification](https://support.google.com/cloud/answer/13804266)
- [Submitting your app for verification](https://support.google.com/cloud/answer/13461325)
- [OAuth Application Rate Limits](https://support.google.com/cloud/answer/9028764)
- [Project quota requests](https://support.google.com/cloud/answer/6330231)
- Google 帳戶說明：[Age requirements on Google Accounts](https://support.google.com/accounts/answer/1350409)

Google for Developers（括號內為頁尾的 Last updated 或頁首的 Last modified）：

- [OAuth app state overview](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview)（2026-05-22）
- [Submit for brand verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification)（2026-08-19）
- [OAuth 2.0 Policies](https://developers.google.com/identity/protocols/oauth2/policies)（Last modified 2026-08-05）
- [Using OAuth 2.0 to Access Google APIs](https://developers.google.com/identity/protocols/oauth2)（2026-05-26）
- [Apps Script：OAuth Client Verification](https://developers.google.com/apps-script/guides/client-verification)（2026-09-03）
- [Configure the OAuth consent screen and choose scopes](https://developers.google.com/workspace/guides/configure-oauth-consent)（2026-09-03）
- [Create a Google Cloud project](https://developers.google.com/workspace/guides/create-project)（2026-09-03）
- [Enable Google Workspace APIs](https://developers.google.com/workspace/guides/enable-apis)（2026-09-03）
- [Create access credentials](https://developers.google.com/workspace/guides/create-credentials)（2026-09-03）
- [Drive API：Python quickstart](https://developers.google.com/workspace/drive/api/quickstart/python)（2026-09-03）
- [Drive API Usage limits](https://developers.google.com/workspace/drive/api/guides/limits)（2026-09-11）
- [Sheets API Usage limits](https://developers.google.com/workspace/sheets/api/limits)（2026-09-03）
- [Google APIs Terms of Service](https://developers.google.com/terms)（Last modified 2021-11-09）

Google Cloud 文件：

- [Create projects](https://docs.cloud.google.com/resource-manager/docs/creating-managing-projects)（2026-10-09）
- [Quickstart: Create your Google Cloud resource hierarchy](https://docs.cloud.google.com/resource-manager/docs/manage-google-cloud-resources)（2026-10-09）
- [Create a new self-serve Cloud Billing account](https://docs.cloud.google.com/billing/docs/how-to/create-billing-account)（2026-10-09）
- [Learn about the Google Cloud Starter Tier](https://docs.cloud.google.com/docs/starter-tier)（2026-10-09）

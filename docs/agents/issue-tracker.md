# Issue tracker：GitHub

本 repo 的 issue 與 spec 都以 GitHub Issues 形式存在。所有操作用 `gh` CLI。

## 慣例

<!-- lingling:issue-tracker -->

- **內文**：issue／PR 的本文、留言與關票說明一律先用 Write 寫成 repo 外的暫存檔，再單獨一次 Bash 呼叫以 `--body-file <檔的絕對路徑>` 送出（關票要附說明時先 `gh issue comment <number> --body-file <檔>`，再 `gh issue close <number>`）。`deny-dangerous-bash` hook 會讀這個檔：含 emoji 或署名行的字樣就擋，說明規則時也一樣，改用文字描述、不寫出那兩個字串；hook 讀不到這個檔也擋。
- **讀 issue**：`gh issue view <number> --json title,body,labels,updatedAt,comments`，本文與留言一次取齊（`--comments` 在非互動模式只印留言、看不到本文）。本文與留言（例如 triage brief）說法衝突時，以時間較新者為準
- **列 issue**：`gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`，搭配 `--label`／`--state` 過濾
- **建工單（拆自案卷）**：`gh issue create --parent <spec> --title "..." --body-file <檔>`，票就掛成 spec 的原生 sub-issue
- **工單的 `## Parent` 段**：沒掛原生連結時的退路，`/lingling:spec-closeout` 靠它找案卷。第一個非空行以「無」／「None」開頭＝沒有案卷；否則該行的第一個 `#N` 就是案卷，別的票號放別段
- **把 issue 掛成 sub-issue**：建立時 `gh issue create --parent <parent> ...`，或事後 `gh issue edit <parent> --add-sub-issue <child>`（`gh` 2.94+）。舊版 `gh`：`gh api --method POST repos/<owner>/<repo>/issues/<parent>/sub_issues -F sub_issue_id=<child-db-id>`（database id，取法見〈擋住〉）。沒有 sub-issue 功能時，在子 issue 本文第一行寫 `Part of #<parent>`
- **擋住（blocking）**：用 GitHub **原生 issue dependencies**（UI 看得到的正式表示）。加一條邊：`gh issue create --blocked-by <n>,<n> ...` 或 `gh issue edit <child> --add-blocked-by <blocker>`（`gh` 2.94+）。舊版 `gh`：`gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`，`<blocker-db-id>` 是 blocker 的數字 **database id**（`gh api repos/<owner>/<repo>/issues/<n> --jq .id`，**不是** `#number` 也不是 `node_id`）。沒有 dependencies 功能時，退回在子 issue 本文第一行寫 `Blocked by: #<n>, #<n>`。所有 blocker 都關了，ticket 才算解鎖
- **數 open blocker**：`gh api repos/<owner>/<repo>/issues/<n> --jq .issue_dependencies_summary.blocked_by`（只算還 open 的；0 就是可以開工）。`gh issue view <n> --json blockedBy` 連已關的 blocker 也會列，要過濾：`--jq '[.blockedBy.nodes[] | select(.state == "OPEN")] | length'`
- **開 issue 的目標 repo**：issue 只開在使用者自己的 repo。除非使用者擁有 `mattpocock/skills`（這些 skill 的來源），否則絕不開在那裡。在 fork 裡 `gh` 可能預設指向上游的 parent，所以每個 session 第一次 `gh issue create` 之前，先用 `gh repo view --json nameWithOwner` 確認目標，不是使用者自己的 repo 就先問過使用者
- **列待 triage 的外部 PR**：`gh api --paginate 'repos/{owner}/{repo}/pulls?state=open' --jq '.[] | select(.author_association | IN("OWNER","MEMBER","COLLABORATOR") | not) | {number, title, author: .user.login, author_association, labels: [.labels[].name]}'`（`gh pr list --json` 沒有 `authorAssociation` 欄位，只能走 REST）。只在說明檔的 PR 旗標為 `yes` 時用得到

<!-- /lingling:issue-tracker -->

repo 由 `git remote -v` 推得；`gh` 在 clone 內執行會自動判定。

## PR 作為 triage 面

**PRs as a request surface: no.** _（本 repo 若把外部 PR 當 feature request 處理就改成 `yes`；`/triage` 會讀這個旗標。）_

設為 `yes` 時，PR 走與 issue 相同的 label 與狀態，用 `gh pr` 對應指令：

- **讀 PR**：`gh pr view <number> --json title,body,labels,updatedAt,comments`（理由同讀 issue），diff 用 `gh pr diff <number>`
- **列待 triage 的外部 PR**：見慣例〈列待 triage 的外部 PR〉
- **留言／label／關閉**：`gh pr comment`、`gh pr edit --add-label`／`--remove-label`、`gh pr close`

GitHub 的 issue 與 PR 共用同一組編號，光看 `#42` 分不出是哪種：先 `gh pr view 42`，不是 PR 再退回 `gh issue view 42`。

## 當 skill 說「publish to the issue tracker」

建一個 GitHub issue。

## 當 skill 說「fetch the relevant ticket」

照上方「讀 issue」取本文與留言。

## Wayfinding operations

給 `/wayfinder` 用。**地圖**（map）是一張 issue，**子 issue** 是它的 ticket。

- **地圖**：一張貼 `wayfinder:map` 的 issue，body 放 Notes／Decisions so far／Fog。`gh issue create --label wayfinder:map`
- **子 ticket**：以 GitHub sub-issue 連到地圖（見慣例〈把 issue 掛成 sub-issue〉）。sub-issue 沒開的 repo，改在地圖 body 的 task list 列出子 issue，並在子 issue body 第一行寫 `Part of #<map>`。label 用 `wayfinder:<type>`（`research`／`prototype`／`grilling`／`task`）。認領後 assignee 設為駕駛的人
- **擋住（blocking）**：見慣例〈擋住〉
- **前沿查詢（frontier）**：列出地圖底下 open 的子 issue（`gh issue list --state open`，限定在地圖的 sub-issues／task list），剔除還有 open blocker 的（見慣例〈數 open blocker〉，或 `Blocked by` 行裡有 open issue）和已有 assignee 的；依地圖順序取第一張
- **認領**：`gh issue edit <n> --add-assignee @me`，這是該 session 的第一個寫入動作
- **解決**：答案照上方「內文」寫成檔，`gh issue comment <n> --body-file <檔>`，接著 `gh issue close <n>`，再把脈絡指標（gist 加連結）補到地圖的 Decisions so far

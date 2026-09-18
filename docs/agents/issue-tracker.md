# Issue tracker：GitHub

本 repo 的 issue 與 spec 都以 GitHub Issues 形式存在。所有操作用 `gh` CLI。

## 慣例

- **建 issue**：`gh issue create --title "..." --body "..."`（多行 body 用 heredoc）
- **讀 issue**：`gh issue view <number> --comments`，必要時用 `jq` 過濾留言，並一併取 labels
- **列 issue**：`gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`，搭配 `--label`／`--state` 過濾
- **留言**：`gh issue comment <number> --body "..."`
- **加／移除 label**：`gh issue edit <number> --add-label "..."`／`--remove-label "..."`
- **關閉**：`gh issue close <number> --comment "..."`

repo 由 `git remote -v` 推得；`gh` 在 clone 內執行會自動判定。

## PR 作為 triage 面

**PRs as a request surface: no.** _（本 repo 若把外部 PR 當 feature request 處理就改成 `yes`；`/triage` 會讀這個旗標。）_

設為 `yes` 時，PR 走與 issue 相同的 label 與狀態，用 `gh pr` 對應指令：

- **讀 PR**：`gh pr view <number> --comments`，diff 用 `gh pr diff <number>`
- **列待 triage 的外部 PR**：`gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments`，只留 `authorAssociation` 為 `CONTRIBUTOR`、`FIRST_TIME_CONTRIBUTOR`、`NONE` 的（去掉 `OWNER`／`MEMBER`／`COLLABORATOR`）
- **留言／label／關閉**：`gh pr comment`、`gh pr edit --add-label`／`--remove-label`、`gh pr close`

GitHub 的 issue 與 PR 共用同一組編號，光看 `#42` 分不出是哪種：先 `gh pr view 42`，不是 PR 再退回 `gh issue view 42`。

## 當 skill 說「publish to the issue tracker」

建一個 GitHub issue。

## 當 skill 說「fetch the relevant ticket」

跑 `gh issue view <number> --comments`。

## Wayfinding operations

給 `/wayfinder` 用。**地圖**（map）是一張 issue，**子 issue** 是它的 ticket。

- **地圖**：一張貼 `wayfinder:map` 的 issue，body 放 Notes／Decisions so far／Fog。`gh issue create --label wayfinder:map`
- **子 ticket**：以 GitHub sub-issue 連到地圖（`gh api` 的 sub-issues endpoint）。sub-issue 沒開的 repo，改在地圖 body 的 task list 列出子 issue，並在子 issue body 第一行寫 `Part of #<map>`。label 用 `wayfinder:<type>`（`research`／`prototype`／`grilling`／`task`）。認領後 assignee 設為駕駛的人
- **擋住（blocking）**：用 GitHub **原生 issue dependencies**（UI 看得到的正式表示）。加一條邊：`gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`，`<blocker-db-id>` 是 blocker 的數字 **database id**（`gh api repos/<owner>/<repo>/issues/<n> --jq .id`，**不是** `#number` 也不是 `node_id`）。GitHub 會回 `issue_dependencies_summary.blocked_by`（只算還 open 的 blocker，就是即時的閘）。沒有 dependencies 功能時，退回在子 issue body 第一行寫 `Blocked by: #<n>, #<n>`。所有 blocker 都關了，ticket 才算解鎖
- **前沿查詢（frontier）**：列出地圖底下 open 的子 issue（`gh issue list --state open`，限定在地圖的 sub-issues／task list），剔除還有 open blocker 的（`issue_dependencies_summary.blocked_by > 0`，或 `Blocked by` 行裡有 open issue）和已有 assignee 的；依地圖順序取第一張
- **認領**：`gh issue edit <n> --add-assignee @me`，這是該 session 的第一個寫入動作
- **解決**：`gh issue comment <n> --body "<answer>"`，接著 `gh issue close <n>`，再把脈絡指標（gist 加連結）補到地圖的 Decisions so far

#!/usr/bin/env node
// hooks/lib/git_guard.js — Guarded branch 的兩支 git 機關（pre-push、reference-transaction）的判定
//
// 防範範圍：Guarded branch 由 git 機關判定，git 做的事它都看得到——腳本、`bash -c`、alias 裡的 git
// 照樣觸發，不必從指令字串猜 repo、分支與推送目的地。只擋 agent（`CLAUDECODE=1`）：
// hooks/pre-push、hooks/reference-transaction 兩支薄殼先在 shell 裡放行人手操作與無關的
// ref 更新、不啟動 node，本檔再檢查一次 `CLAUDECODE`（直接呼叫時也只擋 agent）。不設開關，
// 逃生路是使用者在 Claude Code 以外的終端機自己跑。
//
// 規則：
//   reference-transaction（prepared 階段）：Guarded branch 上不產生本地提交。
//     更新 refs/heads/<Guarded branch> 時，新值必須已在遠端（是 refs/remotes/origin/<該分支>
//     本身或其祖先；同一筆交易裡一起更新的遠端 ref 以新值計），否則中止。一條規則涵蓋
//     commit（含 --amend、--no-verify）、cherry-pick、revert、merge、rebase、fast-forward 到只在
//     本地的分支。放行：新值已在遠端（pull、merge --ff-only origin/<x>、reset --hard origin/<x>）、
//     刪除本地分支、全新 repo 的第一個提交（該分支本地與遠端都還不存在）。
//   pre-push：不推送、不刪除 Guarded branch，不 force push 主幹類分支。
//     逐行讀 git 給的「本地 ref、本地 sha、遠端 ref、遠端 sha」：遠端 ref 是
//     refs/heads/<Guarded branch> 的更新或刪除 → 擋；遠端 ref 在 Guarded branch ∪ 寫死清單、
//     且不是 fast-forward（遠端 sha 不是本地 sha 的祖先，或本地沒有遠端 sha）→ 擋。tag 與其他 ref
//     放行；`--all`／`--mirror` 由 git 逐條列出，照同樣規則判定。
//
// 出錯與紀錄（guard_log.js）：`CLAUDECODE=1` 時判定途中丟例外 → 擋並寫紀錄（fail-closed）；
// 擋下、Guarded branch 解析退回寫死清單也各記一行。
//
// 介面：
//   decide(hook, stdin, deps?) → { code, stderr }
//     hook：'pre-push' | 'reference-transaction'；stdin：git 給機關的原文。
//     deps：{ env, cwd, git, logPath, now, evaluate }——測試換掉 git、紀錄檔與判定本身
//     （evaluate 換成會丟例外的版本，就能單元測試內部例外路徑）。
//   兩條規則（evaluateRefTransaction、evaluatePrePush）拿到的 repo 是 repoView：
//     { guarded, trunk, source, rev(ref), isAncestor(a, b) }；guarded 等解析結果只在碰到
//     refs/heads/ 的行時才讀。
'use strict';

const fs = require('node:fs');
const { resolveGuarded, runGit, HARDCODED } = require('./guarded_branch.js');
const { defaultLogPath, writeLog } = require('./guard_log.js');

const HARD = new Set(HARDCODED);
const HARD_TEXT = HARDCODED.join('／');
const HEADS = 'refs/heads/';
const REMOTE = 'refs/remotes/origin/';
const ESCAPE_ROUTE = '真的要做，請使用者在 Claude Code 以外的終端機自己跑。';

const isZero = (sha) => /^0+$/.test(sha);

class Deny {
  constructor(rule, ref, what, why, next) {
    this.rule = rule;
    this.ref = ref;
    this.what = what;
    this.why = why;
    this.next = next;
  }
}

function render(hook, d) {
  return (
    `lingling-deny: ${d.what}（git 機關 ${hook}）\n` +
    `Ref: ${d.ref}\n` +
    `原因：${d.why}\n` +
    `下一步：${d.next}\n` +
    `${ESCAPE_ROUTE}\n`
  );
}

function sourceText(repo) {
  switch (repo.source) {
    case 'marker':
      return '來源：repo 根 CLAUDE.md 的起端標記';
    case 'origin-head':
      return `來源：\`origin/HEAD\` 指向 \`${repo.trunk}\`，CLAUDE.md 沒有帶屬性的起端標記`;
    case 'broken':
      return `來源：CLAUDE.md 的起端標記格式壞掉，退回寫死清單 ${HARD_TEXT} ∪ \`origin/HEAD\``;
    default:
      return `來源：CLAUDE.md 沒有起端標記、\`origin/HEAD\` 也未設，退回寫死清單 ${HARD_TEXT}`;
  }
}

function guardedWhy(name, repo) {
  return `\`${name}\` 是 Guarded branch（${sourceText(repo)}）：改動只能經 PR 在伺服器端合進來，agent 不直接提交或推送。`;
}

// ── reference-transaction ──────────────────────────────────────

function evaluateRefTransaction(lines, repo) {
  const updates = lines.map((line) => {
    const [oldValue, newValue, ref] = line.split(' ');
    return { line, oldValue, newValue, ref };
  });
  // 同一筆交易裡一起更新的遠端 ref（`git fetch origin x:x` 等）以新值計。
  const remoteInTxn = new Map();
  for (const u of updates) {
    if (u.ref && u.ref.startsWith(REMOTE)) remoteInTxn.set(u.ref.slice(REMOTE.length), u.newValue);
  }

  for (const u of updates) {
    if (!u.ref || !u.ref.startsWith(HEADS) || u.oldValue === u.newValue) continue;
    // 刪除本地分支；新值是符號參照（`ref:…`）的也不是提交。
    if (isZero(u.newValue) || u.newValue.startsWith('ref:')) continue;
    const name = u.ref.slice(HEADS.length);
    if (!repo.guarded.has(name)) continue;

    const remote = remoteInTxn.has(name) ? remoteInTxn.get(name) : repo.rev(`${REMOTE}${name}`);
    if (remote && !isZero(remote)) {
      if (u.newValue === remote || repo.isAncestor(u.newValue, remote)) continue;
    } else if (isZero(u.oldValue) && !repo.rev(u.ref)) {
      continue; // 全新 repo 的第一個提交：該分支本地與遠端都還不存在
    }

    const trunk = repo.trunk || name;
    let next = `先 \`git fetch origin ${trunk} && git switch -c <name> origin/${trunk}\`，在新分支上重做這一步（未提交的改動會跟著過去）。`;
    if (name !== trunk) {
      next += `若這是 \`${name}\` 的 hotfix，改從 \`origin/${name}\` 開：\`git fetch origin ${name} && git switch -c <name> origin/${name}\`。`;
    }
    return new Deny(
      'commit-on-guarded',
      u.line,
      `在 Guarded branch \`${name}\` 上產生只在本地的提交`,
      `${guardedWhy(name, repo)}新的 \`${name}\` 不在 \`origin/${name}\` 上（commit、cherry-pick、revert、merge、rebase、fast-forward 到只在本地的分支都算）。`,
      next
    );
  }
  return null;
}

// ── pre-push ───────────────────────────────────────────────────

function evaluatePrePush(lines, repo) {
  for (const line of lines) {
    const [, localSha, remoteRef, remoteSha] = line.split(' ');
    if (!remoteRef || !remoteRef.startsWith(HEADS)) continue; // tag 與其他 ref 放行
    const name = remoteRef.slice(HEADS.length);
    const guarded = repo.guarded.has(name);
    if (!guarded && !HARD.has(name)) continue;
    const deleting = isZero(localSha);

    if (deleting && guarded) {
      return new Deny(
        'push-delete-guarded',
        line,
        `刪除遠端的 Guarded branch \`${name}\``,
        guardedWhy(name, repo),
        '確認要刪的分支名；刪合併完的 feature 分支是 `git push origin --delete <feature 分支>`。'
      );
    }
    if (!isZero(remoteSha) && (deleting || !repo.isAncestor(remoteSha, localSha))) {
      return new Deny(
        'force-push',
        line,
        deleting ? `刪除遠端的 \`${name}\`` : `force push 到 \`${name}\`（不是 fast-forward）`,
        `遠端的 \`${name}\` 不是這次推送的祖先，推上去會覆寫遠端歷史；${HARD_TEXT} 與本 repo 的 Guarded branch 一律不准 force push 或刪除。`,
        `只 force push 自己的 PR 分支：\`git push --force-with-lease origin <你的分支>\`。要改 \`${name}\` 的內容就開 PR。`
      );
    }
    if (guarded) {
      return new Deny(
        'push-guarded',
        line,
        `推送到 Guarded branch \`${name}\``,
        guardedWhy(name, repo),
        `改推自己的分支再開 PR：已在 feature 分支上就 \`git push -u origin <該分支>\`；人在 \`${name}\` 上則先 \`git switch -c <name>\`（從目前的 HEAD 開、帶著已有的提交）再推。然後 \`gh pr create --base ${name}\`。`
      );
    }
  }
  return null;
}

const EVALUATE = {
  'pre-push': evaluatePrePush,
  'reference-transaction': evaluateRefTransaction,
};

// ── 接線 ────────────────────────────────────────────────────────

/** repo 的查詢介面；Guarded branch 解析延到第一次讀 guarded／trunk／source 時才做。 */
function repoView(git, onResolved) {
  let info = null;
  const resolved = () => {
    if (!info) {
      info = resolveGuarded(git);
      onResolved(info);
    }
    return info;
  };
  return {
    get guarded() {
      return resolved().guarded;
    },
    get trunk() {
      return resolved().trunk;
    },
    get source() {
      return resolved().source;
    },
    // ref 的 sha；ref 不存在回 null。git 本身失敗（逾時等）丟例外、走 fail-closed——不能把
    // 「讀不到」當成「不存在」，否則會誤入「全新 repo 的第一個提交」的放行分支。
    rev(ref) {
      const out = git(['for-each-ref', '--format=%(refname) %(objectname)', ref]);
      if (out === null) throw new Error(`讀不到 ${ref}（git for-each-ref 失敗）`);
      const hit = out.split('\n').find((l) => l.startsWith(`${ref} `));
      return hit ? hit.slice(ref.length + 1) : null;
    },
    // exit 0 才算祖先；1（不是）與 128（本地沒有該物件）都不算。
    isAncestor(a, b) {
      return git(['merge-base', '--is-ancestor', a, b]) !== null;
    },
  };
}

function decide(
  hook,
  stdin,
  {
    env = process.env,
    cwd = process.cwd(),
    git = (args) => runGit(args, cwd),
    logPath = defaultLogPath(),
    now = () => new Date(),
    evaluate = EVALUATE[hook],
  } = {}
) {
  if (env.CLAUDECODE !== '1') return { code: 0, stderr: '' };
  const log = (event, rule, extra) =>
    writeLog(logPath, { time: now().toISOString(), hook, repo: cwd, event, rule, ...extra });

  try {
    if (typeof evaluate !== 'function') throw new Error(`不認得的機關：${hook}`);
    const lines = stdin.split('\n').filter((l) => l.trim() !== '');
    const repo = repoView(git, (info) => {
      if (info.source === 'fallback' || info.source === 'broken') log('fallback', `guarded-${info.source}`, { ref: stdin.trim() });
    });
    const d = evaluate(lines, repo);
    if (!d) return { code: 0, stderr: '' };
    log('deny', d.rule, { ref: d.ref });
    return { code: 1, stderr: render(hook, d) };
  } catch (err) {
    const message = err && err.message ? err.message : String(err);
    log('error', 'internal-error', { error: message, ref: stdin.trim() });
    return {
      code: 1,
      stderr:
        `lingling-deny: git 機關 ${hook} 判定途中出錯，保守起見擋下\n` +
        `原因：${message}\n` +
        `下一步：把這則錯誤告訴使用者（紀錄在 ${logPath}）。\n` +
        `${ESCAPE_ROUTE}\n`,
    };
  }
}

module.exports = { decide };

if (require.main === module) {
  const hook = process.argv[2];
  let raw = '';
  let readError = null;
  try {
    raw = fs.readFileSync(0, 'utf8');
  } catch (err) {
    readError = err;
  }
  const deps = readError
    ? {
        evaluate: () => {
          throw readError;
        },
      }
    : {};
  const { code, stderr } = decide(hook, raw, deps);
  if (stderr) process.stderr.write(stderr);
  process.exit(code);
}

#!/usr/bin/env node
// hooks/lib/bash_guard.js — deny-dangerous-bash 的規則：一條 Bash 指令擋或不擋、擋的話說什麼
//
// 防範範圍只到 agent 的**反射動作**，不防蓄意繞過：`bash -c`、`eval`、`gh api` 都不拆。
// Guarded branch 不在這裡判定——那由 git 機關（hooks/pre-push、
// hooks/reference-transaction，判定在 git_guard.js）負責，git 做的事它都看得到。本檔
// 不呼叫 git、不解析 Guarded branch。
//
// 介面：
//   evaluate(command, { cwd }?) → null（放行）或 { rule, message }（擋下；message 給 stderr）。
//     cwd 只用來找 PR／MR 內文檔的相對路徑（預設 process.cwd()）；`$VAR`／`${VAR}`／`~` 以
//     process.env 展開。
//   hasManagedCommand(command) → 命令位置上有沒有受管的詞（MANAGED）。
//     判定途中丟例外時，hook 靠它決定擋（有）或放行（沒有）。
//   MANAGED：受管的詞。
//
// 只比對命令位置（shell_parse.js）。
'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { parse, simpleCommand } = require('./shell_parse.js');
const { HARDCODED } = require('./guarded_branch.js');
const { SIGNATURE } = require('./text_util.js');

const MANAGED = Object.freeze(['git', 'gh', 'glab', 'rm', 'curl', 'wget']);
const MANAGED_SET = new Set(MANAGED);
const SHELLS = new Set(['bash', 'sh', 'zsh', 'dash', 'ksh']);
const HARD = new Set(HARDCODED);
const ESCAPE_ROUTE = '真的要做，請使用者在提示列用 `! <指令>` 自己跑。';

// PR/MR 發佈崗哨：git hook 看不到 GitHub/GitLab 的 PR/MR 內文（那是 API metadata、不經
// commit-msg/pre-commit），故在此補洞——命令位置上是 gh/glab 的發佈命令時，擋整條指令裡的
// (1) 自動簽名行（署名字樣定義在 text_util.js，與 commit-msg 共用，不分位置一律擋）、(2) 任何
// emoji。內文檔（BODY_FILE）另外讀進來、用同一套比對，讀不到就擋（ADR-0038）。
const PUBLISH = {
  gh: { pr: ['create', 'edit', 'comment'], issue: ['create', 'edit', 'comment'] },
  glab: { mr: ['create', 'update', 'note'], issue: ['create', 'update', 'note'] },
};
// 內文檔的旗標；兩者的值是 `-` 時從 stdin 讀。不拆 `-dF file` 這類合併短旗標。
const BODY_FILE = {
  gh: { long: '--body-file', short: '-F' },
  glab: { long: '--description-file', short: null },
};
const CD_COMMANDS = new Set(['cd', 'pushd', 'popd']);
// emoji 範圍：主要 emoji 平面 + 雜項符號/dingbats + 技術符號 + 區域指示(旗) + 變體選擇子。
// 刻意**不**含 U+2190–21FF 箭頭（→ ← 等技術寫作常用、非 emoji）與 CJK 標點。
// 需 u flag：JS 無 u flag 時字元類別以 UTF-16 code unit 比對、astral 範圍會失效。
const EMOJI = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2300}-\u{23FF}\u{2B00}-\u{2BFF}\u{1F1E6}-\u{1F1FF}\u{FE0F}]/u;

class Deny {
  constructor(rule, what, why, next) {
    this.rule = rule;
    this.what = what;
    this.why = why;
    this.next = next;
  }
}

function render(d, command) {
  return (
    `lingling-deny: ${d.what}\n` +
    `Command: ${command}\n` +
    `原因：${d.why}\n` +
    `下一步：${d.next}\n` +
    `${ESCAPE_ROUTE}\n`
  );
}

function hasManagedCommand(command) {
  try {
    const r = parse(command);
    if (!r.ok) return false;
    return r.items.some((it) => MANAGED_SET.has(simpleCommand(it.words).name));
  } catch {
    // 連斷詞都出錯：退而求其次，整條指令裡出現受管的詞就算（寧可多擋）。
    return new RegExp(`\\b(${MANAGED.join('|')})\\b`).test(command);
  }
}

// ── git ─────────────────────────────────────────────────────────

const GIT_VALUE_OPTS = new Set(['--git-dir', '--work-tree', '--namespace', '--super-prefix', '--config-env']);

/** 剝掉 git 的全域選項，回 { configs, sub, args }。 */
function parseGit(argv) {
  const configs = [];
  let k = 1;
  while (k < argv.length) {
    const a = argv[k];
    if ((a === '-C' || a === '-c') && k + 1 < argv.length) {
      if (a === '-c') configs.push(argv[k + 1]);
      k += 2;
      continue;
    }
    const name = a.split('=')[0];
    if (GIT_VALUE_OPTS.has(name)) {
      k += a.includes('=') ? 1 : 2;
      continue;
    }
    if (a.startsWith('-')) {
      k += 1;
      continue;
    }
    break;
  }
  return { configs, sub: k < argv.length ? argv[k] : null, args: argv.slice(k + 1) };
}

// commit 的短旗標裡，這些會吃掉後面的字元（或下一個參數）當值：-m msg、-F file、-C/-c commit、-t file。
const COMMIT_SHORT_VALUE = new Set(['m', 'F', 'C', 'c', 't']);
// 這些只接黏著的選擇性值（-u<mode>、-S<keyid>），後面的字元不是旗標。
const COMMIT_SHORT_OPTIONAL = new Set(['u', 'S']);
const COMMIT_LONG_VALUE = new Set([
  '--message', '--file', '--reuse-message', '--reedit-message', '--template', '--author', '--date',
  '--fixup', '--squash', '--trailer', '--cleanup', '--pathspec-from-file',
]);

function commitSkipsHooks(args) {
  for (let k = 0; k < args.length; k++) {
    const a = args[k];
    if (a === '--') return false;
    if (a === '--no-verify') return true;
    if (a.startsWith('--')) {
      if (COMMIT_LONG_VALUE.has(a)) k += 1;
      continue;
    }
    if (!a.startsWith('-') || a === '-') continue;
    for (let j = 1; j < a.length; j++) {
      const ch = a[j];
      if (ch === 'n') return true;
      if (COMMIT_SHORT_VALUE.has(ch)) {
        if (j === a.length - 1) k += 1;
        break;
      }
      if (COMMIT_SHORT_OPTIONAL.has(ch)) break;
    }
  }
  return false;
}

/** `git push` 會不會跳過 pre-push（`--no-verify`，後面的 `--verify` 蓋掉它）。`-n` 是 --dry-run、不算。 */
function pushSkipsHooks(args) {
  let skip = false;
  for (const a of args) {
    if (a === '--') break;
    if (a === '--no-verify') skip = true;
    else if (a === '--verify') skip = false;
  }
  return skip;
}

function resetTarget(args) {
  if (!args.includes('--hard')) return null;
  const rev = args.find((a) => !a.startsWith('-'));
  if (!rev) return null;
  let base = rev.split(/[~^:]|@\{/)[0];
  if (base.startsWith('refs/remotes/')) base = base.slice('refs/remotes/'.length);
  return base.startsWith('origin/') ? { rev, branch: base.slice('origin/'.length) } : null;
}

// ── 合併崗哨 ────────────────────────────────────────────────────

// 合併一律經 plugin 的 `bin/lingling-merge <n>`（ADR-0036）：它先驗 PR 分支跟上 base、分支內沒有 merge commit、CI 全綠
// 才合。`gh pr merge` 兩樣都不驗，故命令位置上的 `gh pr merge` 不論帶什麼旗標與前綴一律擋；
// `lingling-merge` 不是受管的詞，照常放行。
function mergeNext(target) {
  const n = target && /^[1-9][0-9]*$/.test(target) ? target : '<PR 編號>';
  return `使用者說「合」之後，改跑 \`lingling-merge ${n}\`；它拒絕時，照它印出的下一步做。`;
}

// ── PR／MR 內文檔 ───────────────────────────────────────────────

/**
 * 發佈命令參數裡的內文檔（原樣、未展開）。`--` 之後不算旗標。旗標後面沒有值（值是被 parser
 * 拆走的 `<(…)` 等）記成 null。
 */
function bodyFileArgs(tool, args) {
  const { long, short } = BODY_FILE[tool];
  const out = [];
  for (let k = 0; k < args.length; k++) {
    const a = args[k];
    if (a === '--') break;
    if (a === long || a === short) {
      out.push(k + 1 < args.length ? args[++k] : null);
    } else if (a.startsWith(`${long}=`)) {
      out.push(a.slice(long.length + 1));
    } else if (short && a.startsWith(short)) {
      out.push(a.slice(short.length).replace(/^=/, ''));
    }
  }
  return out;
}

/**
 * 內文檔的絕對路徑 { file }，或看不準的原因 { problem }。hook 讀到的必須就是 gh／glab 會送出的
 * 那份：同一條指令裡還提到這個檔（可能在送出前才寫入）、路徑的變數在指令裡另外設定、`cd` 之後
 * 的相對路徑，都看不準。`$VAR`、`${VAR}`、開頭的 `~` 不分引號一律以 process.env 展開。
 */
function locateBodyFile(spec, { cwd, command, hasCd }) {
  if (!spec) return { problem: '旗標後面看不到檔名（例如 process substitution `<(…)`）' };
  if (spec === '-') return { problem: 'hook 看不到 stdin 的內容' };
  if (command.split(spec).length > 2) {
    return { problem: '同一條指令裡還有別處提到這個檔，可能在送出前才寫入或改動；hook 讀到的是這條指令執行前的內容' };
  }
  let p = spec;
  if (p === '~' || p.startsWith('~/')) p = (process.env.HOME || os.homedir()) + p.slice(1);
  let unset = null;
  p = p.replace(/\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))/g, (m, braced, bare) => {
    const name = braced ?? bare;
    const assigned = new RegExp(`(?:^|[^\\w$])${name}=`).test(command);
    const v = assigned ? undefined : process.env[name];
    if (v === undefined) unset ??= m;
    return v ?? m;
  });
  if (unset !== null || /[$`]/.test(p)) {
    return {
      problem: `路徑裡的 \`${unset ?? spec}\` 展不開：hook 只展開自己環境裡有、這條指令也沒有另外設定的 $VAR／\${VAR}，命令替換也看不到`,
    };
  }
  if (!path.isAbsolute(p)) {
    if (hasCd) return { problem: '指令裡有 cd／pushd／popd，相對路徑看不準是哪個檔' };
    p = path.resolve(cwd, p);
  }
  return { file: p };
}

/** 讀內文檔：{ text } 或讀不到的原因 { problem }。 */
function readBodyFile(file) {
  try {
    if (!fs.statSync(file).isFile()) return { problem: `${file} 不是一般檔案` };
    return { text: fs.readFileSync(file, 'utf8') };
  } catch (err) {
    return { problem: `${file}：${err.code || err.message}` };
  }
}

/** 第一個符合 re 的行號（從 1 起算）；沒有回 0。 */
function lineOf(text, re) {
  return text.split(/\r\n|\r|\n/).findIndex((l) => re.test(l)) + 1;
}

// ── 主判定 ──────────────────────────────────────────────────────

function evaluate(command, { cwd = process.cwd() } = {}) {
  const parsed = parse(command);
  if (!parsed.ok) return null; // 引號不成對等語法錯誤：bash 不會執行它

  for (let idx = 0; idx < parsed.items.length; idx++) {
    const item = parsed.items[idx];
    const d = check(simpleCommand(item.words), item, idx);
    if (d) return { rule: d.rule, message: render(d, command) };
  }
  return null;

  function check(sc, item, idx) {
    switch (sc.name) {
      case 'rm':
        return checkRm(sc);
      case 'curl':
      case 'wget':
        return checkPipeToShell(sc, item, idx);
      case 'git':
        return checkGit(sc);
      case 'gh':
      case 'glab':
        return checkGhGlab(sc);
      default:
        return null;
    }
  }

  function checkRm(sc) {
    const args = sc.argv.slice(1);
    let recursive = false;
    const targets = [];
    let opts = true;
    for (const a of args) {
      if (opts && a === '--') opts = false;
      else if (opts && a.startsWith('--')) recursive ||= a === '--recursive';
      else if (opts && a.startsWith('-') && a.length > 1) recursive ||= /[rR]/.test(a);
      else targets.push(a);
    }
    const system = (v) =>
      v === '~' || v.startsWith('~/') || v === '/' || v === '/*' || v.startsWith('//') ||
      /^\$(HOME|\{HOME\})(\/|$)/.test(v);
    if (!recursive || !targets.some(system)) return null;
    return new Deny(
      'rm-system-path',
      '`rm -r` 刪除系統路徑（~/、/、$HOME）',
      '遞迴刪除家目錄或根目錄，一打錯就收不回來。',
      '改用明確的子路徑，例如 `rm -rf ./build`。'
    );
  }

  function checkPipeToShell(sc, item, idx) {
    const later = parsed.items
      .slice(idx + 1)
      .filter((it) => it.pipeline === item.pipeline);
    if (!later.some((it) => SHELLS.has(simpleCommand(it.words).name))) return null;
    return new Deny(
      'pipe-to-shell',
      `\`${sc.name}\` 下載後直接 pipe 給 shell 執行`,
      '下載的內容沒看過就執行，來源被竄改或中間人攻擊都會直接跑起來。',
      '先存成檔案（例如 `curl -fsSL <url> -o install.sh`），讀過內容再 `bash install.sh`。'
    );
  }

  function checkGit(sc) {
    const g = parseGit(sc.argv);

    const hooksOff = g.configs.find((c) => {
      const eq = c.indexOf('=');
      return eq > 0 && c.slice(0, eq).toLowerCase() === 'core.hookspath' && ['', '/dev/null'].includes(c.slice(eq + 1));
    });
    if (hooksOff !== undefined) {
      return new Deny(
        'hooks-path',
        '`git -c core.hooksPath=…` 把 git hook 關掉',
        'pre-commit、commit-msg 是治理檢查，pre-push、reference-transaction 守 Guarded branch，關掉就沒有人把關。',
        '拿掉 `-c core.hooksPath=…` 重打；hook 擋下的話，修掉它回報的問題。'
      );
    }

    if (g.sub === 'reset') {
      const t = resetTarget(g.args);
      if (!t || !HARD.has(t.branch)) return null;
      return new Deny(
        'reset-hard',
        `\`git reset --hard ${t.rev}\``,
        '`--hard` 直接丟掉未提交的改動，目前分支上沒推的提交也跟著不見，收不回來。',
        `先留住現況：未提交的改動 \`git stash push -- <自己改的檔>\`、本地提交 \`git branch <備份名>\`，再 \`git reset --keep ${t.rev}\`（有未提交改動會拒絕、不會吃掉）。`
      );
    }

    if (g.sub === 'commit' && commitSkipsHooks(g.args)) {
      return new Deny(
        'commit-no-verify',
        '`git commit` 跳過 git hook（`-n`／`--no-verify`）',
        'pre-commit 與 commit-msg 是治理檢查，跳過就沒有人把關。',
        '修掉 hook 回報的問題後重新 `git commit`，不加 `-n`／`--no-verify`。'
      );
    }

    if (g.sub === 'push' && pushSkipsHooks(g.args)) {
      return new Deny(
        'push-no-verify',
        '`git push --no-verify` 跳過 pre-push',
        'pre-push 守 Guarded branch（不推送、不刪除、不 force push 主幹類分支），跳過就沒有人把關。',
        '拿掉 `--no-verify` 重打；pre-push 擋下的話，照它給的下一步改推自己的分支。'
      );
    }
    return null;
  }

  function checkGhGlab(sc) {
    const tool = sc.name;
    const args = sc.argv.slice(1);
    const positional = [];
    for (let k = 0; k < args.length && positional.length < 3; k++) {
      const a = args[k];
      if (a === '-R' || a === '--repo') k += 1;
      else if (!a.startsWith('-')) positional.push(a);
    }
    const [noun, verb, target] = positional;

    const publish = Object.hasOwn(PUBLISH[tool], noun) ? PUBLISH[tool][noun] : [];
    if (publish.includes(verb)) {
      if (SIGNATURE.test(command)) {
        return new Deny(
          'publish-signature',
          'PR／MR 內文含自動簽名行（共同作者、Generated with Claude Code、Claude-Session 或 session 連結）',
          'lingling 規範禁止署名字樣進 PR／MR 與 issue 內文，不分位置，說明規則時也一樣。',
          '移除該行後重打。'
        );
      }
      if (EMOJI.test(command)) {
        return new Deny('publish-emoji', 'PR／MR 內文含 emoji', '全域禁 emoji。', '移除 emoji 後重打。');
      }
      for (const spec of bodyFileArgs(tool, args)) {
        const d = checkBodyFile(tool, spec);
        if (d) return d;
      }
      return null;
    }

    if (tool === 'gh' && noun === 'pr' && verb === 'merge') {
      return new Deny(
        'merge-guard',
        '`gh pr merge` 直接合併',
        '合併一律經 `lingling-merge`：它先驗 PR 分支已跟上 base、分支裡沒有 merge commit、CI 全綠才合，並統一合併主旨；`gh pr merge` 一樣都不驗，free private repo 也沒有 branch protection 替你擋。',
        mergeNext(target)
      );
    }
    if (tool === 'glab' && noun === 'mr' && verb === 'merge') {
      return new Deny(
        'merge-glab',
        '`glab mr merge` 直接合併',
        'GitLab 還沒有對應 `lingling-merge` 的合併腳本（ADR-0036 的已知空缺），沒有人驗 MR 分支跟上 target branch、pipeline 全綠。',
        '請使用者確認 MR 已跟上 target branch、pipeline 綠了之後自己合。'
      );
    }
    return null;
  }

  function checkBodyFile(tool, spec) {
    const hasCd = parsed.items.some((it) => CD_COMMANDS.has(simpleCommand(it.words).name));
    const loc = locateBodyFile(spec, { cwd, command, hasCd });
    const body = loc.problem ? loc : readBodyFile(loc.file);
    if (body.problem) {
      return new Deny(
        'publish-body-file-unreadable',
        `讀不到 PR／MR 內文檔 \`${spec ?? ''}\``,
        `hook 要先讀過內文才知道有沒有 emoji 或署名行，讀不到就不讓它送出（ADR-0038）：${body.problem}。`,
        `先用 Write 把內文寫成檔，再以 \`${BODY_FILE[tool].long} <絕對路徑>\` 單獨送出（寫檔、清檔放在別次 Bash 呼叫）。`
      );
    }
    const signature = lineOf(body.text, SIGNATURE);
    if (signature) {
      return new Deny(
        'publish-signature',
        `PR／MR 內文檔 \`${spec}\` 含自動簽名行（第 ${signature} 行）`,
        'lingling 規範禁止署名字樣（共同作者、Generated with Claude Code、Claude-Session 或 session 連結）進 PR／MR 與 issue 內文，不分位置，說明規則時也一樣。',
        '從檔裡移除該行後重打。'
      );
    }
    const emoji = lineOf(body.text, EMOJI);
    if (emoji) {
      return new Deny('publish-emoji', `PR／MR 內文檔 \`${spec}\` 含 emoji（第 ${emoji} 行）`, '全域禁 emoji。', '從檔裡移除 emoji 後重打。');
    }
    return null;
  }
}

module.exports = { evaluate, hasManagedCommand, MANAGED };

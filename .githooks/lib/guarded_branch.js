#!/usr/bin/env node
// hooks/lib/guarded_branch.js — 解析 repo 的 Guarded branch（GLOSSARY.md）
// Guarded branch：agent 不得直接提交或推送、改動只能經 PR 在伺服器端合進來的分支。
//
// 在 repo 本身判定：呼叫端是 git 機關（pre-push、reference-transaction），git 執行機關時
// 的 cwd 就是 repo（工作樹的根），不必從指令字串猜目錄。
//
// 依序：
//   1. repo 根 CLAUDE.md 的合法起端標記（格式與 setup-lingling 的寫入器共用，見 #334）：
//        <!-- lingling:git-workflow trunk=<name>[ guarded=<name>[,<name>...]] -->
//      → Guarded branch＝trunk ∪ guarded。
//   2. 舊式純標記 `<!-- lingling:git-workflow -->` 或沒有標記 → `origin/HEAD` 指向的分支。
//   3. 再沒有 → 寫死清單 main／master／develop（fallback）。
//   標記存在但壞掉 → 寫死清單 ∪ `origin/HEAD`（broken）。
//
// git 一律以參數陣列呼叫、不經 shell；分支名一律字串相等比對、不拼進 regex。
//
// 介面：
//   resolveGuarded(git?) → { root, guarded: Set, trunk, source }
//     git(args) → stdout（去頭尾空白）或 null（非零退出、git 不在）；預設在 process.cwd() 跑。
//     root：工作樹的根，bare repo 等拿不到時為 null（此時視同沒有 CLAUDE.md）。
//     source：'marker' | 'origin-head' | 'fallback' | 'broken'；trunk 解析不出時為 null。
//   readMarker(text) → { state: 'legal', trunk, guarded } | { state: 'legacy' | 'none' | 'broken' }
//   runGit(args, cwd?) → 同上 git(args) 的預設實作。
//   HARDCODED：寫死清單。
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const HARDCODED = Object.freeze(['main', 'master', 'develop']);

const NAME = '[A-Za-z0-9._/-]+';
const MARKER = new RegExp(`^<!-- lingling:git-workflow trunk=(${NAME})(?: guarded=(${NAME}(?:,${NAME})*))? -->[ \\t]*$`);
const LEGACY = /^<!-- lingling:git-workflow -->[ \t]*$/;
// 行首像起端標記的一行（訖端 `<!-- /lingling:…` 不算）；合法與舊式以外的都算壞掉。
const CANDIDATE = /^<!--\s*lingling:git-workflow\b/;

function readMarker(text) {
  const lines = text.split(/\r?\n/).filter((l) => CANDIDATE.test(l));
  if (lines.length === 0) return { state: 'none' };
  if (lines.length > 1) return { state: 'broken' };
  const m = MARKER.exec(lines[0]);
  if (m) return { state: 'legal', trunk: m[1], guarded: m[2] ? m[2].split(',') : [] };
  if (LEGACY.test(lines[0])) return { state: 'legacy' };
  return { state: 'broken' };
}

/** 跑一次 git；任何失敗（非零退出、git 不在、cwd 不存在）回 null。 */
function runGit(args, cwd = process.cwd()) {
  try {
    return execFileSync('git', args, {
      cwd,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 2000,
    }).trim();
  } catch {
    return null;
  }
}

function resolveGuarded(git = (args) => runGit(args)) {
  const root = git(['rev-parse', '--show-toplevel']);

  let text = '';
  if (root) {
    try {
      text = fs.readFileSync(path.join(root, 'CLAUDE.md'), 'utf8');
    } catch {
      // 沒有 CLAUDE.md＝沒有標記
    }
  }
  const marker = readMarker(text);

  const originHead = () => {
    const ref = git(['symbolic-ref', '--short', 'refs/remotes/origin/HEAD']);
    return ref && ref.startsWith('origin/') ? ref.slice('origin/'.length) : null;
  };

  if (marker.state === 'legal') {
    return { root, trunk: marker.trunk, guarded: new Set([marker.trunk, ...marker.guarded]), source: 'marker' };
  }
  const trunk = originHead();
  if (marker.state === 'broken') {
    const guarded = new Set(HARDCODED);
    if (trunk) guarded.add(trunk);
    return { root, trunk, guarded, source: 'broken' };
  }
  return trunk
    ? { root, trunk, guarded: new Set([trunk]), source: 'origin-head' }
    : { root, trunk: null, guarded: new Set(HARDCODED), source: 'fallback' };
}

module.exports = { resolveGuarded, readMarker, runGit, HARDCODED };

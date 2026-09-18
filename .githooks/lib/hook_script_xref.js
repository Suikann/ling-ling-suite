#!/usr/bin/env node
// hooks/lib/hook_script_xref.js — F.2 hook-script-xref lint
// 來源：由 hook_script_xref.py 移植（ADR-0029：hook 不相依 python3）。行為契約不變、
// 惟參照樣式加入 .js（hook lint 工具本身已改 node 實作、否則新引用會失去 xref 覆蓋）。
// 規則：hooks/ 下的 hook 引用 hooks/lib/* / hooks/* 必須真實存在
// 介面：hook_script_xref.js
// 輸出：<file>:<line>:missing-ref:<path>
// exit code：1 if any missing ref, 0 if clean
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const HOOKS_DIR = path.join(REPO_ROOT, 'hooks');

// 抓 hooks/... 形式的路徑引用。py 保留：vendored 消費端可能仍有舊引用、應被抓出為 missing-ref。
const REF_PATTERNS = [
  /hooks\/lib\/[\w\-/]+\.(py|sh|txt|js)/g,
  /hooks\/[\w\-/]+\.(py|sh|js)/g,
];

function findRefs(text) {
  const refs = new Set();
  for (const pat of REF_PATTERNS) {
    for (const m of text.matchAll(pat)) refs.add(m[0]);
  }
  return refs;
}

function main() {
  let entries;
  try {
    if (!fs.statSync(HOOKS_DIR).isDirectory()) return 0;
    entries = fs.readdirSync(HOOKS_DIR).sort();
  } catch {
    return 0;
  }

  const violations = [];
  for (const name of entries) {
    const hookPath = path.join(HOOKS_DIR, name);
    let text;
    try {
      if (!fs.statSync(hookPath).isFile()) continue;
      text = fs.readFileSync(hookPath, 'utf8');
    } catch {
      continue;
    }
    const lines = text.split(/\r\n|\r|\n/);
    lines.forEach((line, i) => {
      for (const ref of findRefs(line)) {
        if (!fs.existsSync(path.join(REPO_ROOT, ref))) {
          violations.push([`hooks/${name}`, i + 1, ref]);
        }
      }
    });
  }

  for (const [file, lineNo, ref] of violations) {
    process.stdout.write(`${file}:${lineNo}:missing-ref:${ref}\n`);
  }
  return violations.length > 0 ? 1 : 0;
}

if (require.main === module) process.exit(main());

module.exports = { findRefs };

#!/usr/bin/env node
// hooks/lib/chinese_quality.js — Layer A 三檢（簡繁 / 編碼 / 異體字）
// 來源：由 chinese_quality.py 移植（ADR-0029：hook 不相依 python3）。行為契約不變。
// 拍板：ADR-0039（全 repo scan + 異體字硬卡 + 放行名單）
// 介面：
//   chinese_quality.js --stdin           從 stdin 讀（commit-msg hook 用）
//   chinese_quality.js <file> [<file>..] 逐檔檢查（pre-commit hook 用）
// 輸出：<file>:<line>:<col>:<type>:<char>[:<suggested>]
// exit code：1 if any issues, 0 if clean, 2 if it cannot judge（用法錯、字表或檔案讀不到）
// 讀不到一律 exit 2（ADR-0038），唯一的例外：工作樹裡已不存在的檔跳過——pre-commit 的清單取自
// `git ls-files`（索引），檔案在工作樹裡刪了就沒有內容可讀。不是一般檔案的（submodule 等）不查。
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const DATA = path.join(__dirname, 'data');
// 路徑上沒有這個檔：ENOENT，或路徑中段已不是目錄（ENOTDIR）。
const MISSING = new Set(['ENOENT', 'ENOTDIR']);

// 逐「字元」處理一律走 code point（Array.from），不可用 UTF-16 index——
// 否則 CJK Ext-B 等增補平面字會被拆成代理對、col 與判定都會錯。
function loadSet(filename) {
  const chars = new Set();
  for (const raw of fs.readFileSync(path.join(DATA, filename), 'utf8').split(/\r\n|\r|\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    if (Array.from(line).length === 1) chars.add(line);
  }
  return chars;
}

function loadVariants() {
  const variants = new Map();
  for (const line of fs.readFileSync(path.join(DATA, 'variant_to_standard.txt'), 'utf8').split(/\r\n|\r|\n/)) {
    if (!line.trim() || line.startsWith('#')) continue;
    if (!line.includes('\t')) continue;
    const idx = line.indexOf('\t');
    const v = line.slice(0, idx).trim();
    const s = line.slice(idx + 1).trim();
    if (Array.from(v).length === 1 && s) variants.set(v, s);
  }
  return variants;
}

// 字表第一次用到才讀：讀不到時丟出的例外由 main 轉成 exit 2，不在 require 時就炸成 exit 1
//（1 是「有問題」，呼叫端會誤讀）。
let tables = null;
function loadTables() {
  tables ??= {
    simplified: loadSet('simplified_only_chars.txt'),
    variants: loadVariants(),
    allowlist: loadSet('variant_allowlist.txt'),
  };
  return tables;
}

function isObscure(cp) {
  return (
    (cp >= 0x3400 && cp <= 0x4dbf) ||
    (cp >= 0xf900 && cp <= 0xfaff) ||
    (cp >= 0xe000 && cp <= 0xf8ff) ||
    cp >= 0x20000
  );
}

/** 回傳 [[col, char, checkType, suggested]]、col 為 0-based（以 code point 計）。 */
function checkLine(text) {
  const { simplified, variants, allowlist } = loadTables();
  const issues = [];
  const chars = Array.from(text);
  for (let i = 0; i < chars.length; i++) {
    const ch = chars[i];
    if (simplified.has(ch)) {
      issues.push([i, ch, 'simplified', '']);
      continue;
    }
    if (isObscure(ch.codePointAt(0))) {
      issues.push([i, ch, 'encoding', '']);
      continue;
    }
    if (variants.has(ch) && !allowlist.has(ch)) {
      issues.push([i, ch, 'variant', variants.get(ch)]);
    }
  }
  return issues;
}

/** 掃整段、印 issue、回 issue 數。 */
function checkText(text, pathLabel) {
  let n = 0;
  const lines = text.split(/\r\n|\r|\n/);
  // Python splitlines() 對結尾換行不產生額外空行；split 會，故去掉尾端空元素。
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  lines.forEach((line, i) => {
    for (const [col, ch, kind, suggested] of checkLine(line)) {
      const tail = suggested ? `:${suggested}` : '';
      process.stdout.write(`${pathLabel}:${i + 1}:${col + 1}:${kind}:${ch}${tail}\n`);
      n += 1;
    }
  });
  return n;
}

function main(argv) {
  if (argv.length === 0) {
    process.stderr.write('usage: chinese_quality.js --stdin | <file> [<file>..]\n');
    return 2;
  }
  let total = 0;
  try {
    loadTables();
    if (argv[0] === '--stdin') {
      total += checkText(fs.readFileSync(0, 'utf8'), '<stdin>');
    } else {
      for (const p of argv) {
        let text;
        try {
          if (!fs.statSync(p).isFile()) continue;
          text = fs.readFileSync(p, 'utf8');
        } catch (err) {
          if (MISSING.has(err.code)) continue;
          throw err;
        }
        total += checkText(text, p);
      }
    }
  } catch (err) {
    process.stderr.write(`chinese_quality: 無法判定：${err.message}\n`);
    return 2;
  }
  return total > 0 ? 1 : 0;
}

if (require.main === module) process.exit(main(process.argv.slice(2)));

module.exports = { checkLine, checkText };

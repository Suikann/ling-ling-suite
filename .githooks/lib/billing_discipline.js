#!/usr/bin/env node
// hooks/lib/billing_discipline.js — F.4 billing-discipline lint（含 H 條訂閱邊界）
// 來源：由 billing_discipline.py 移植（ADR-0029：hook 不相依 python3）。行為契約不變。
// 對齊 stance：訂閱 only / 不設 API key env / 不 import Agent SDK / 不走 headless / Bedrock / Vertex / Foundry
// 介面：billing_discipline.js <file> [<file>..]
// 輸出：<file>:<line>:<rule>:<excerpt>
// exit code：1 if any violation, 0 if clean
'use strict';

const fs = require('node:fs');

// 規則：[ruleName, regex]
const RULES = [
  // claude headless 模式（subprocess arg 或 build args 後 append）
  ['headless-claude', /claude['"\s\],]{0,40}?(--print|\s-p)\b/],
  ['headless-claude', /(?<![\w-])--print(?![\w-])/],
  // API key 引用（任何出現都疑似違反、敘述文本靠 path allowlist 護）
  ['api-key', /\bANTHROPIC_API_KEY\b/],
  // Anthropic SDK import（Python / TS / JS）
  ['anthropic-sdk-import', /^\s*(from\s+anthropic\b|import\s+anthropic\b)/],
  ['anthropic-sdk-import', /@anthropic-ai\/sdk/],
  ['anthropic-sdk-import', /require\(['"]anthropic['"]\)/],
  // Bedrock / Vertex / Foundry env 切換
  ['non-subscription-backend', /\bCLAUDE_CODE_USE_(BEDROCK|VERTEX|FOUNDRY)\b\s*=/],
];

// 檔案允許（敘述性文件 / hook 自身 error message 可命中 forbidden flag、不視為違反）
const PATH_ALLOWLIST_PREFIXES = [
  'docs/adr/',
  'docs/notes/',
  // hook lint 工具自身與其測試：偵測規則／error message 必然含禁字面值（原為 billing_discipline.py 單檔）
  'hooks/lib/',
  'hooks/pre-commit', // hook error message 會敘述禁令
  'hooks/commit-msg',
  '.githooks/', // vendored 進消費端的 lingling hook 整包（見 ADR-0005 vendor 模式）
  'docs/agents/', // 消費端 repo 的 agent ground-truth doc
  'CHANGELOG.md',
  'RETROSPECTIVE',
];

function isAllowedPath(rel) {
  // git ls-files 給的是正斜線相對路徑；Windows 上若混入反斜線先正規化。
  const norm = rel.replace(/\\/g, '/');
  return PATH_ALLOWLIST_PREFIXES.some((p) => norm.startsWith(p));
}

/** 回傳 [[lineNo, rule, excerpt]]。 */
function checkFile(label, text) {
  if (isAllowedPath(label)) return [];
  const violations = [];
  const lines = text.split(/\r\n|\r|\n/);
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  lines.forEach((line, i) => {
    for (const [rule, pattern] of RULES) {
      if (pattern.test(line)) {
        violations.push([i + 1, rule, line.trim().slice(0, 120)]);
        break; // 每行只報第一條命中的規則（同 python 版）
      }
    }
  });
  return violations;
}

function main(argv) {
  if (argv.length === 0) {
    process.stderr.write('usage: billing_discipline.js <file> [<file>..]\n');
    return 2;
  }
  let total = 0;
  for (const arg of argv) {
    let text;
    try {
      if (!fs.statSync(arg).isFile()) continue;
      text = fs.readFileSync(arg, 'utf8');
    } catch {
      continue;
    }
    for (const [lineNo, rule, excerpt] of checkFile(arg, text)) {
      process.stdout.write(`${arg}:${lineNo}:${rule}:${excerpt}\n`);
      total += 1;
    }
  }
  return total > 0 ? 1 : 0;
}

if (require.main === module) process.exit(main(process.argv.slice(2)));

module.exports = { checkFile, isAllowedPath };

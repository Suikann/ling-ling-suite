#!/usr/bin/env node
// hooks/lib/text_util.js — commit-msg 用的兩個小工具
// 取代 commit-msg 裡原本兩段 inline `python3 -c`（ADR-0029：hook 不相依 python3）。
// 抽成檔案而非 inline `node -e`：可被測試覆蓋，且 emoji 範圍只此一處定義。
//
// 介面：
//   text_util.js charlen     stdin → 印出字元數（code point 計、去尾端換行）
//   text_util.js has-emoji   stdin → exit 0 = 有 emoji、exit 1 = 沒有
'use strict';

const fs = require('node:fs');

// 與原 commit-msg 的 python 範圍逐段對齊（注意：與 deny-dangerous-bash 的範圍不同、刻意不合併）。
//   1F300-1F9FF  Misc symbols & pictographs、emoticons、transport 等
//   1FA00-1FAFF  Symbols & Pictographs Extended-A
//   1F1E6-1F1FF  區域指示符／國旗
//   2600-26FF    Misc symbols
//   2700-27BF    Dingbats
//   20E3         keycap 結合字
//   FE00-FE0F    variation selectors
const EMOJI =
  /[\u{1F300}-\u{1F9FF}\u{1FA00}-\u{1FAFF}\u{1F1E6}-\u{1F1FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}\u{20E3}\u{FE00}-\u{FE0F}]/u;

function readStdin() {
  try {
    return fs.readFileSync(0, 'utf8');
  } catch {
    return '';
  }
}

/** 字元數以 code point 計——UTF-16 length 會把增補平面字算成 2。 */
function charLen(text) {
  return Array.from(text.replace(/\n+$/, '')).length;
}

function hasEmoji(text) {
  return EMOJI.test(text);
}

function main(argv) {
  const cmd = argv[0];
  if (cmd === 'charlen') {
    process.stdout.write(String(charLen(readStdin())));
    return 0;
  }
  if (cmd === 'has-emoji') {
    return hasEmoji(readStdin()) ? 0 : 1;
  }
  process.stderr.write('usage: text_util.js charlen | has-emoji\n');
  return 2;
}

if (require.main === module) process.exit(main(process.argv.slice(2)));

module.exports = { charLen, hasEmoji };

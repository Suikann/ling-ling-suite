#!/usr/bin/env node
// hooks/lib/text_util.js — commit-msg 用的小工具
// 取代 commit-msg 裡原本兩段 inline `python3 -c`（ADR-0029：hook 不相依 python3）。
// 抽成檔案而非 inline `node -e`：可被測試覆蓋，且 emoji 範圍與署名字樣只此一處定義
// （署名字樣 deny-dangerous-bash 也引用；emoji 範圍刻意不共用，見 EMOJI）。
//
// 介面：
//   text_util.js charlen         stdin → 印出字元數（code point 計、去尾端換行）
//   text_util.js has-emoji       stdin → exit 0 = 有 emoji、exit 1 = 沒有
//   text_util.js find-signature  stdin → 印出第一個署名字樣（沒有就不印），exit 0
//   stdin 讀不到 exit 2（ADR-0038）。node 當掉也是 exit 1，所以呼叫端先拿已知的 emoji 驗 has-emoji
//   會退 0，才信它的 1（commit-msg、setup-lingling 的 lingling-governance.yml）；find-signature 只有
//   exit 0 才是答案。
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

// 署名字樣：commit 訊息、PR／MR 與 issue 內文一律不能出現，不分位置、不分大小寫，說明規則時也一樣
// （只擋行首會漏掉 `> `、`- **…**`、`<sub>` 等前綴）。共同作者 trailer、Claude Code 的生成署名
// （有沒有中括號都算）、session 署名行與 session 連結。
const SIGNATURE = /Co-Authored-By|Generated with \[?Claude Code|Claude-Session|claude\.ai\/code\/session/i;

/** 字元數以 code point 計——UTF-16 length 會把增補平面字算成 2。 */
function charLen(text) {
  return Array.from(text.replace(/\n+$/, '')).length;
}

function hasEmoji(text) {
  return EMOJI.test(text);
}

/** 第一個署名字樣；沒有回 null。 */
function findSignature(text) {
  const m = SIGNATURE.exec(text);
  return m ? m[0] : null;
}

function main(argv) {
  const cmd = argv[0];
  if (!['charlen', 'has-emoji', 'find-signature'].includes(cmd)) {
    process.stderr.write('usage: text_util.js charlen | has-emoji | find-signature\n');
    return 2;
  }
  let text;
  try {
    text = fs.readFileSync(0, 'utf8');
  } catch (err) {
    process.stderr.write(`text_util: 讀不到 stdin：${err.message}\n`);
    return 2;
  }
  if (cmd === 'charlen') {
    process.stdout.write(String(charLen(text)));
    return 0;
  }
  if (cmd === 'find-signature') {
    process.stdout.write(findSignature(text) ?? '');
    return 0;
  }
  return hasEmoji(text) ? 0 : 1;
}

if (require.main === module) process.exit(main(process.argv.slice(2)));

module.exports = { charLen, hasEmoji, findSignature, SIGNATURE };

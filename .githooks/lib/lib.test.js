#!/usr/bin/env node
/*
 * hooks/lib/ 四支 lint 工具的規格測試（node:test、零相依）。
 *
 *   node --test hooks/lib/lib.test.js
 *
 * 這些工具原為 python，移植至 node 見 ADR-0029。移植前無任何測試覆蓋。
 *
 * 注意：本檔會被 repo 自己的 chinese_quality lint 掃到，故測試用的簡體／異體／
 * 冷僻字一律以 String.fromCodePoint 構造，不寫字面值——否則測試檔自身會被判違規。
 */
'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const { spawnSync } = require('node:child_process');
const path = require('node:path');

const LIB = __dirname;
const cq = require('./chinese_quality.js');
const bd = require('./billing_discipline.js');
const xr = require('./hook_script_xref.js');
const tu = require('./text_util.js');

function runCli(script, args, input) {
  return spawnSync(process.execPath, [path.join(LIB, script), ...args], {
    input: input ?? '',
    encoding: 'utf8',
  });
}

// ── chinese_quality ────────────────────────────────────────────
// 註解本身也不可出現這些字元——本檔會被 chinese_quality 掃到（CI 已證實會擋）。
const SIMPLIFIED_ZHE = String.fromCodePoint(0x8fd9); // 簡體字 U+8FD9
const VARIANT_WEI = String.fromCodePoint(0x50de); // 異體字 U+50DE、標準字為「偽」
const OBSCURE_EXT_B = String.fromCodePoint(0x20000); // CJK Ext-B（增補平面）

test('chinese_quality: 抓簡體字', () => {
  const r = cq.checkLine(SIMPLIFIED_ZHE);
  assert.strictEqual(r.length, 1);
  assert.strictEqual(r[0][2], 'simplified');
});

test('chinese_quality: 抓異體字並給出標準字建議', () => {
  const r = cq.checkLine(VARIANT_WEI);
  assert.strictEqual(r.length, 1);
  assert.strictEqual(r[0][2], 'variant');
  assert.strictEqual(r[0][3], '偽');
});

test('chinese_quality: 抓冷僻編碼區', () => {
  const r = cq.checkLine(OBSCURE_EXT_B);
  assert.strictEqual(r.length, 1);
  assert.strictEqual(r[0][2], 'encoding');
});

test('chinese_quality: col 以 code point 計、增補平面字不被拆成代理對', () => {
  // 「AB<Ext-B>」→ 該字在第 3 個 code point（0-based col = 2）。
  // 若誤用 UTF-16 index，代理對會讓後續 col 全數偏移。
  const r = cq.checkLine(`AB${OBSCURE_EXT_B}`);
  assert.strictEqual(r.length, 1);
  assert.strictEqual(r[0][0], 2);
});

test('chinese_quality: 正常繁體中文不報錯', () => {
  assert.deepStrictEqual(cq.checkLine('這是正常的繁體中文'), []);
});

test('chinese_quality: --stdin 乾淨時 exit 0、無輸出', () => {
  const r = runCli('chinese_quality.js', ['--stdin'], '這是乾淨的繁體中文\n');
  assert.strictEqual(r.status, 0);
  assert.strictEqual(r.stdout, '');
});

test('chinese_quality: --stdin 有問題時 exit 1、輸出格式為 file:line:col:type:char', () => {
  const r = runCli('chinese_quality.js', ['--stdin'], `abc\nx${SIMPLIFIED_ZHE}y\n`);
  assert.strictEqual(r.status, 1);
  // 第 2 行、第 2 個字元（1-based col）
  assert.match(r.stdout, /^<stdin>:2:2:simplified:/m);
});

test('chinese_quality: 無參數時 exit 2（usage）', () => {
  assert.strictEqual(runCli('chinese_quality.js', [], '').status, 2);
});

// ── billing_discipline ─────────────────────────────────────────
test('billing_discipline: 抓 API key env 引用', () => {
  const v = bd.checkFile('src/a.ts', 'const k = process.env.ANTHROPIC_API_KEY;');
  assert.strictEqual(v.length, 1);
  assert.strictEqual(v[0][1], 'api-key');
});

test('billing_discipline: 抓 headless --print', () => {
  const v = bd.checkFile('scripts/run.sh', 'claude --print "hi"');
  assert.strictEqual(v.length, 1);
  assert.strictEqual(v[0][1], 'headless-claude');
});

test('billing_discipline: 抓 Agent SDK import', () => {
  const v = bd.checkFile('src/a.ts', "import x from '@anthropic-ai/sdk';");
  assert.strictEqual(v.length, 1);
  assert.strictEqual(v[0][1], 'anthropic-sdk-import');
});

test('billing_discipline: 抓非訂閱 backend env', () => {
  const v = bd.checkFile('src/a.sh', 'CLAUDE_CODE_USE_BEDROCK=1');
  assert.strictEqual(v.length, 1);
  assert.strictEqual(v[0][1], 'non-subscription-backend');
});

test('billing_discipline: allowlist 路徑豁免（敘述性文件）', () => {
  const text = 'const k = process.env.ANTHROPIC_API_KEY;';
  assert.deepStrictEqual(bd.checkFile('docs/adr/0001-x.md', text), []);
  assert.deepStrictEqual(bd.checkFile('hooks/lib/billing_discipline.js', text), []);
  // 但一般原始碼不豁免
  assert.strictEqual(bd.checkFile('src/a.ts', text).length, 1);
});

test('billing_discipline: 乾淨檔案無違規、行號正確', () => {
  assert.deepStrictEqual(bd.checkFile('src/a.ts', 'const a = 1;\nconst b = 2;\n'), []);
  const v = bd.checkFile('src/a.ts', 'ok\nok\nCLAUDE_CODE_USE_VERTEX=1\n');
  assert.strictEqual(v[0][0], 3);
});

// ── hook_script_xref ───────────────────────────────────────────
test('hook_script_xref: 抓得到 hooks/ 路徑引用（含 .js）', () => {
  const refs = xr.findRefs('run node "hooks/lib/chinese_quality.js" and hooks/pre-commit.sh');
  assert.ok(refs.has('hooks/lib/chinese_quality.js'));
});

test('hook_script_xref: 對本 repo 現況執行為乾淨（exit 0）', () => {
  const r = spawnSync(process.execPath, [path.join(LIB, 'hook_script_xref.js')], {
    encoding: 'utf8',
  });
  assert.strictEqual(r.status, 0, `xref 回報缺檔引用：\n${r.stdout}`);
});

// ── glossary（逐詞查詢、不整表載入）───────────────────────────
const gl = require('./glossary.js');

test('glossary: 查得到譯名（精確、大小寫不敏感）', () => {
  const r = gl.lookup('FAIL-OPEN');
  assert.strictEqual(r.length, 1);
  assert.strictEqual(r[0].zh, '失守');
});

test('glossary: 領域專名有中譯，並標明檔案內識別名不動', () => {
  const r = gl.lookup('Reference skill');
  assert.strictEqual(r[0].zh, '參照 skill', '領域專名在對話中要有中譯');
  assert.match(r[0].note, /DOMAIN/);
  assert.match(r[0].note, /識別名維持英文/, '必須提醒：CONTEXT.md／程式碼裡不得改動');
});

test('glossary: 查無時 CLI 回 NOT-FOUND 且 exit 1（提報使用者、不得自創譯名）', () => {
  const r = runCli('glossary.js', ['fail-open', 'zzz-not-a-real-term'], '');
  assert.strictEqual(r.status, 1);
  assert.match(r.stdout, /zzz-not-a-real-term\tNOT-FOUND/);
  assert.match(r.stdout, /fail-open\t失守/, '其他查得到的詞仍要照常回傳');
});

test('glossary: 全部查到時 exit 0', () => {
  assert.strictEqual(runCli('glossary.js', ['stub', 'lint'], '').status, 0);
});

test('glossary: 只回傳查詢的詞——不得把整張表吐出來', () => {
  const r = runCli('glossary.js', ['stub'], '');
  const lines = r.stdout.trim().split('\n');
  assert.strictEqual(lines.length, 1, '查一個詞就該只回一行；回整表等於白做了');
  assert.match(lines[0], /^stub\t空殼程式/);
});

test('glossary: 兩張表不得收錄同一個詞（否則查詢結果自相矛盾）', () => {
  const domain = gl.loadDomain().map((t) => t.term.toLowerCase());
  const dup = gl.loadTerms().filter((t) => domain.includes(t.term.toLowerCase()));
  assert.deepStrictEqual(
    dup.map((t) => t.term),
    [],
    '同一詞同時出現在 glossary.tsv 與 glossary_domain.tsv'
  );
});

test('glossary: 已被否決的譯名不得復活（汰換名冊的回歸測試）', () => {
  // 被否決過的東西如果沒被釘住，下一輪就會重新提一次——補詞的人看不到否決史，
  // 只會照著英文字面再生一批同樣的直譯。glossary_rejected.tsv 因此不是文件，是護欄。
  const rejected = gl.loadRejected();
  assert.ok(rejected.length > 0, '汰換名冊不該是空的——否則這條護欄形同虛設');

  const live = [...gl.loadTerms(), ...gl.loadDomain()];
  const revived = [];
  for (const r of rejected) {
    const hit = live.find(
      (t) => t.term.toLowerCase() === r.term.toLowerCase() && t.zh === r.zh
    );
    if (hit) revived.push(`${r.term} → 「${r.zh}」（否決理由：${r.note}）`);
  }
  assert.deepStrictEqual(
    revived,
    [],
    `已否決的譯名復活了：\n  ${revived.join('\n  ')}\n譯名須合信達雅、見 glossary.tsv 表頭原則`
  );
});

test('glossary: 使用者裁定的譯名確實生效', () => {
  const pick = (q) => gl.lookup(q)[0].zh;
  assert.strictEqual(pick('exit code'), '回傳碼');
  assert.strictEqual(pick('surrogate pair'), '雙碼合字');
  assert.strictEqual(pick('fail-open'), '失守');
  assert.strictEqual(pick('fail-closed'), '死守');
  assert.strictEqual(pick('registry'), '名冊');
  assert.strictEqual(pick('Reference skill'), '參照 skill');
  assert.strictEqual(pick('Entry skill'), '入口 skill');
  assert.strictEqual(pick('followup'), '掛帳');
  assert.strictEqual(pick('dependency'), '相依套件');
  assert.strictEqual(pick('CI'), '自動驗收');
  assert.strictEqual(pick('hook'), '機關');
  assert.strictEqual(pick('rebase'), '嫁接');
  assert.strictEqual(pick('repo'), '典藏');
  assert.strictEqual(pick('session'), '會期');
  assert.strictEqual(pick('agent'), '執事');
  assert.strictEqual(pick('regex'), '正規表示式', '「正則表達式」是大陸譯法');
});

// ── text_util ──────────────────────────────────────────────────
test('text_util: charLen 以 code point 計（中文不算 byte、增補平面不算 2）', () => {
  assert.strictEqual(tu.charLen('繁體中文'), 4);
  assert.strictEqual(tu.charLen(OBSCURE_EXT_B), 1);
  assert.strictEqual(tu.charLen('abc'), 3);
});

test('text_util: charLen 去掉尾端換行', () => {
  assert.strictEqual(tu.charLen('abc\n\n'), 3);
});

test('text_util: hasEmoji 判定', () => {
  assert.strictEqual(tu.hasEmoji('ship it 🚀'), true);
  assert.strictEqual(tu.hasEmoji('fix：修正一個問題'), false);
  assert.strictEqual(tu.hasEmoji('A → B'), false, '箭頭不算 emoji');
});

test('text_util: CLI has-emoji 的 exit code（0 = 有、1 = 無）', () => {
  assert.strictEqual(runCli('text_util.js', ['has-emoji'], 'done 🎉').status, 0);
  assert.strictEqual(runCli('text_util.js', ['has-emoji'], 'done').status, 1);
});

test('text_util: CLI charlen 輸出字元數', () => {
  const r = runCli('text_util.js', ['charlen'], '繁體中文\n');
  assert.strictEqual(r.status, 0);
  assert.strictEqual(r.stdout.trim(), '4');
});

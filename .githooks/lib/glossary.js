#!/usr/bin/env node
// hooks/lib/glossary.js — 術語譯名查詢（逐詞、不整表載入）
//
// 為何是查詢而非「載入整張表」：整表載入的 context 成本會隨表變大而線性膨脹，
// 且絕大多數條目與當下回覆無關——那本質上還是全表常駐、只是晚一點發生。
// 逐詞查詢的成本只跟「這次要用幾個詞」有關，與表的大小無關。見 ADR-0030。
//
// 兩張表：
//   glossary.tsv         一般技術術語 → 直接用中譯
//   glossary_domain.tsv  領域專名     → 對話用中譯，但檔案內識別名維持英文（回 DOMAIN 標記）
//
// 介面：
//   glossary.js <term> [<term>..]   查譯名（大小寫不敏感、支援子字串）
//   glossary.js --list              印出全部原文（供人審閱、非給 agent 常態使用）
//
// 輸出：<原文>\t<譯名>\t<備註>              一般術語
//       <原文>\t<譯名>\tDOMAIN：…           領域專名（檔案內識別名不譯）
//       <query>\tNOT-FOUND                  查無——保留原文，提報使用者決定是否收錄
// exit code：0 = 全部查到（或 --list）、1 = 有任一詞查無
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const DATA = path.join(__dirname, 'data');

const DOMAIN_NOTE = 'DOMAIN：對話用此中譯（首次標原文）；CONTEXT.md／程式碼／ADR 內識別名維持英文';

function loadTsv(file) {
  const p = path.join(DATA, file);
  if (!fs.existsSync(p)) return [];
  return fs
    .readFileSync(p, 'utf8')
    .split(/\r\n|\r|\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#') && l.includes('\t'))
    .map((l) => {
      const [term, zh, note = ''] = l.split('\t').map((s) => s.trim());
      return { term, zh, note };
    });
}

function loadTerms() {
  return loadTsv('glossary.tsv');
}

function loadDomain() {
  return loadTsv('glossary_domain.tsv').map((e) => ({
    ...e,
    note: e.note ? `${DOMAIN_NOTE}。${e.note}` : DOMAIN_NOTE,
    domain: true,
  }));
}

/** 汰換名冊：已被使用者否決的譯名。由測試釘住、不得復活於上面兩張表。 */
function loadRejected() {
  return loadTsv('glossary_rejected.tsv');
}

/** 先精確比對（大小寫不敏感），再退回子字串比對。領域表優先。 */
function lookup(query, terms = loadTerms(), domain = loadDomain()) {
  const q = query.toLowerCase();
  const all = [...domain, ...terms];

  const exact = all.filter((t) => t.term.toLowerCase() === q);
  if (exact.length) return exact;

  return all.filter(
    (t) => t.term.toLowerCase().includes(q) || q.includes(t.term.toLowerCase())
  );
}

function main(argv) {
  const terms = loadTerms();
  const domain = loadDomain();

  if (argv[0] === '--list') {
    for (const t of [...terms, ...domain]) process.stdout.write(`${t.term}\n`);
    return 0;
  }
  if (argv.length === 0) {
    process.stderr.write('usage: glossary.js <term> [<term>..] | --list\n');
    return 2;
  }

  let missing = 0;
  for (const q of argv) {
    const hits = lookup(q, terms, domain);
    if (hits.length === 0) {
      process.stdout.write(`${q}\tNOT-FOUND\n`);
      missing += 1;
      continue;
    }
    for (const h of hits) {
      process.stdout.write(`${h.term}\t${h.zh}${h.note ? `\t${h.note}` : ''}\n`);
    }
  }
  return missing > 0 ? 1 : 0;
}

if (require.main === module) process.exit(main(process.argv.slice(2)));

module.exports = { lookup, loadTerms, loadDomain, loadRejected };

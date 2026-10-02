#!/usr/bin/env node
// hooks/lib/shell_parse.js — 把一條 Bash 指令切成「命令位置上的簡單命令」
// deny-dangerous-bash 的所有規則只比對命令位置：行首，或 `|` `;` `&&` `||` `&` `(` `$(`
// 反引號 換行之後的第一個詞。引號裡、heredoc 內文裡、註解裡的字串不是命令，grep 的
// 樣式、寫進檔案的文件、commit 訊息都不會被當成要執行的指令。
//
// 介面：
//   parse(src) → { ok: true, items } | { ok: false, reason }
//     items 依執行順序排列，每個是一個簡單命令 { words, pipeline }：子 shell（`( )`、`$( )`、
//     反引號、`<( )`）裡的命令也各自一個 item，排在包住它的命令之前；pipeline 相同的是同一條
//     管線的各段。
//     word = { value, raw }：value 是去掉引號後的字面（`$X`、`$(…)` 原樣保留、不展開），
//     raw 是原文。
//   simpleCommand(words) → { argv, name }：跳過開頭的 `VAR=值`、
//     sudo／env／command／time／nohup（再加 exec／timeout／nice）與 `!` `{` `if` 等保留字
//     後的真正命令。
//
// 不是完整的 bash parser：只求把命令位置找對。引號不成對、`$(`／反引號沒收尾是 shell 語法
// 錯誤（bash 不會執行它），回 ok: false，由呼叫端放行。反過來，看不懂但 bash 照跑的寫法
// （`case` 樣式的 `a)`、`[[ =~ (a) ]]`）一律從寬切開、當成更多命令來看——誤判成語法錯誤
// 就等於放行。
'use strict';

class ShellSyntaxError extends Error {}

const BLANK = new Set([' ', '\t']);
// 在引號外會結束一個詞的字元。
const META = new Set([' ', '\t', '\n', ';', '&', '|', '(', ')', '<', '>']);

class Parser {
  constructor(src) {
    this.src = src;
    this.i = 0;
    this.items = [];
    this.heredocs = [];
    this.nextPipeline = 0;
  }

  peek(n = 0) {
    return this.src[this.i + n];
  }

  startsWith(s) {
    return this.src.startsWith(s, this.i);
  }

  // 一串命令，直到 term（null＝輸入結尾、')'、'`'）。
  parseList(term) {
    let words = [];
    // pipeline 編號在每一層各自配：命令替換裡的命令不會跟外層的 pipeline 混在一起。
    let pipeline = this.nextPipeline++;
    const finish = (sep) => {
      if (words.length > 0) this.items.push({ words, pipeline });
      if (sep !== '|') pipeline = this.nextPipeline++;
      words = [];
    };

    for (;;) {
      while (BLANK.has(this.peek())) this.i += 1;
      const c = this.peek();

      if (c === undefined) {
        if (term !== null) throw new ShellSyntaxError(`unterminated ${term === ')' ? '(' : '`'}`);
        finish('end');
        return;
      }
      if (c === '\\' && this.peek(1) === '\n') {
        this.i += 2;
        continue;
      }
      if (c === '\n') {
        this.i += 1;
        finish('\n');
        this.readHeredocBodies();
        continue;
      }
      if (c === '#') {
        while (this.peek() !== undefined && this.peek() !== '\n') this.i += 1;
        continue;
      }
      if (term === '`' && c === '`') {
        this.i += 1;
        finish('`');
        return;
      }
      if (c === ')') {
        this.i += 1;
        if (term === ')') {
          finish(')');
          return;
        }
        finish(';'); // 多出來的 `)`（case 樣式等）當分隔
        continue;
      }
      if (c === ';') {
        this.i += this.peek(1) === ';' ? 2 : 1;
        finish(';');
        continue;
      }
      if (c === '&') {
        if (this.peek(1) === '&') {
          this.i += 2;
          finish('&&');
        } else if (this.peek(1) === '>') {
          this.i += this.peek(2) === '>' ? 3 : 2;
          this.readRedirectTarget(term);
        } else {
          this.i += 1;
          finish('&');
        }
        continue;
      }
      if (c === '|') {
        if (this.peek(1) === '|') {
          this.i += 2;
          finish('||');
        } else {
          this.i += this.peek(1) === '&' ? 2 : 1;
          finish('|');
        }
        continue;
      }
      if (c === '(') {
        this.i += 1;
        if (words.length > 0) {
          // `name()`：函式定義，本體接在後面、當成下一個命令來看。其他接在詞後面的 `(`
          //（`[[ x =~ (a) ]]` 等）從寬當成子 shell。
          finish(';');
          while (BLANK.has(this.peek())) this.i += 1;
          if (this.peek() === ')') {
            this.i += 1;
            continue;
          }
        }
        this.parseList(')');
        continue;
      }
      if (c === '<' || c === '>') {
        this.readRedirect(term);
        continue;
      }

      const before = this.i;
      const word = this.readWord(term);
      // 防呆：一個字元都沒吃就是 parser 自己的錯，丟例外（不是語法錯誤）交給 hook 保守處理，
      // 不原地打轉到 hook timeout。
      if (this.i === before) throw new Error(`shell_parse stalled at offset ${before}`);
      // `2>&1` 的 2：緊貼在重導向前的數字是 fd，不是參數。
      if (/^\d+$/.test(word.raw) && (this.peek() === '<' || this.peek() === '>')) continue;
      if (word.raw !== '') words.push(word);
    }
  }

  readRedirect(term) {
    if (this.startsWith('<(') || this.startsWith('>(')) {
      // process substitution 裡也是命令。
      this.i += 2;
      this.parseList(')');
      return;
    }
    if (this.startsWith('<<<')) {
      this.i += 3;
      this.readRedirectTarget(term);
      return;
    }
    if (this.startsWith('<<')) {
      this.i += 2;
      const stripTabs = this.peek() === '-';
      if (stripTabs) this.i += 1;
      while (BLANK.has(this.peek())) this.i += 1;
      const delim = this.readWord(term);
      if (delim.raw === '') throw new ShellSyntaxError('heredoc without delimiter');
      this.heredocs.push({ delim: delim.value, stripTabs });
      return;
    }
    for (const op of ['>>', '>|', '>&', '<>', '<&', '>', '<']) {
      if (this.startsWith(op)) {
        this.i += op.length;
        break;
      }
    }
    this.readRedirectTarget(term);
  }

  readRedirectTarget(term) {
    while (BLANK.has(this.peek())) this.i += 1;
    this.readWord(term);
  }

  // 換行之後：依序吃掉登記過的 heredoc 內文，直到各自的結束行。內文不是命令。
  readHeredocBodies() {
    const pending = this.heredocs;
    this.heredocs = [];
    for (const { delim, stripTabs } of pending) {
      for (;;) {
        if (this.i >= this.src.length) return; // bash 以輸入結尾收掉 heredoc（只警告）
        let nl = this.src.indexOf('\n', this.i);
        if (nl < 0) nl = this.src.length;
        let line = this.src.slice(this.i, nl);
        if (stripTabs) line = line.replace(/^\t+/, '');
        if (line === delim) {
          this.i = Math.min(nl + 1, this.src.length);
          break;
        }
        // `$(cat <<EOF … EOF)`：結束行後面直接接 `)`，把 `)` 留給外層。
        if (line.startsWith(delim) && line.slice(delim.length).trimStart().startsWith(')')) {
          this.i = nl - (line.length - delim.length);
          break;
        }
        this.i = nl + 1;
      }
    }
  }

  readWord(term) {
    const start = this.i;
    const w = { value: '', raw: '' };

    for (;;) {
      const c = this.peek();
      if (c === undefined || META.has(c)) break;
      if (c === '`') {
        if (term === '`') break;
        this.i += 1;
        this.parseList('`');
        w.value += '`…`';
        continue;
      }
      if (c === '\\') {
        if (this.peek(1) === '\n') {
          this.i += 2;
          continue;
        }
        if (this.peek(1) === undefined) {
          this.i += 1;
          continue;
        }
        w.value += this.peek(1);
        this.i += 2;
        continue;
      }
      if (c === "'") {
        const end = this.src.indexOf("'", this.i + 1);
        if (end < 0) throw new ShellSyntaxError("unterminated '");
        w.value += this.src.slice(this.i + 1, end);
        this.i = end + 1;
        continue;
      }
      if (c === '"') {
        this.i += 1;
        this.readDouble(w);
        continue;
      }
      if (c === '$') {
        this.readDollar(w, false);
        continue;
      }
      w.value += c;
      this.i += 1;
    }
    w.raw = this.src.slice(start, this.i);
    return w;
  }

  // 雙引號內：`\` 只跳脫 $ ` " \ 換行；`$(` 與反引號照樣是命令替換（裡面是新的引號層次）。
  readDouble(w) {
    for (;;) {
      const c = this.peek();
      if (c === undefined) throw new ShellSyntaxError('unterminated "');
      if (c === '"') {
        this.i += 1;
        return;
      }
      if (c === '\\') {
        const n = this.peek(1);
        if (n === '\n') {
          this.i += 2;
          continue;
        }
        if (n === '$' || n === '`' || n === '"' || n === '\\') {
          w.value += n;
          this.i += 2;
          continue;
        }
        w.value += c;
        this.i += 1;
        continue;
      }
      if (c === '`') {
        this.i += 1;
        this.parseList('`');
        w.value += '`…`';
        continue;
      }
      if (c === '$') {
        this.readDollar(w, true);
        continue;
      }
      w.value += c;
      this.i += 1;
    }
  }

  readDollar(w, inDouble) {
    const n = this.peek(1);
    if (n === '(' && this.peek(2) === '(') {
      const start = this.i;
      this.i += 3;
      let depth = 2;
      while (depth > 0) {
        const c = this.peek();
        if (c === undefined) throw new ShellSyntaxError('unterminated $((');
        if (c === '(') depth += 1;
        if (c === ')') depth -= 1;
        this.i += 1;
      }
      w.value += this.src.slice(start, this.i);
      return;
    }
    if (n === '(') {
      this.i += 2;
      this.parseList(')');
      w.value += '$(…)';
      return;
    }
    if (n === '{') {
      const start = this.i;
      this.i += 2;
      let depth = 1;
      while (depth > 0) {
        const c = this.peek();
        if (c === undefined) throw new ShellSyntaxError('unterminated ${');
        if (c === '\\') this.i += 1;
        else if (c === '{') depth += 1;
        else if (c === '}') depth -= 1;
        this.i += 1;
      }
      w.value += this.src.slice(start, this.i);
      return;
    }
    if (!inDouble && n === "'") {
      // $'…'：ANSI-C 引號，反斜線跳脫下一個字元。
      this.i += 2;
      for (;;) {
        const c = this.peek();
        if (c === undefined) throw new ShellSyntaxError("unterminated $'");
        if (c === "'") {
          this.i += 1;
          break;
        }
        if (c === '\\' && this.peek(1) !== undefined) {
          w.value += this.src.slice(this.i, this.i + 2);
          this.i += 2;
          continue;
        }
        w.value += c;
        this.i += 1;
      }
      return;
    }
    if (!inDouble && n === '"') {
      this.i += 2;
      this.readDouble(w);
      return;
    }
    w.value += '$';
    this.i += 1;
  }
}

function parse(src) {
  const p = new Parser(String(src));
  try {
    p.parseList(null);
  } catch (e) {
    if (e instanceof ShellSyntaxError) return { ok: false, reason: e.message };
    throw e;
  }
  return { ok: true, items: p.items };
}

const ASSIGN = /^[A-Za-z_][A-Za-z0-9_]*=/;
const RESERVED = new Set(['!', '{', 'if', 'then', 'else', 'elif', 'do', 'while', 'until']);
// 前綴命令：values 是要吃掉下一個參數當值的選項（分開寫的形式，如 `sudo -u bob`）；
// operands 是選項之後、真正命令之前還要跳過的參數個數（`timeout 60 git …` 的 60）。
const PREFIX = {
  sudo: { values: new Set(['-u', '-g', '-h', '-p', '-C', '-D', '-r', '-t', '-U', '-T']), operands: 0 },
  env: { values: new Set(['-u', '-C', '-S']), operands: 0 },
  command: { values: new Set(), operands: 0 },
  time: { values: new Set(), operands: 0 },
  nohup: { values: new Set(), operands: 0 },
  exec: { values: new Set(['-a']), operands: 0 },
  timeout: { values: new Set(['-s', '-k']), operands: 1 },
  nice: { values: new Set(['-n']), operands: 0 },
};

function basename(name) {
  const k = name.lastIndexOf('/');
  return k < 0 ? name : name.slice(k + 1);
}

function simpleCommand(words) {
  let k = 0;
  for (;;) {
    while (k < words.length && ASSIGN.test(words[k].raw)) k += 1; // 跳過開頭的 `VAR=值`
    if (k >= words.length) break;
    const head = words[k].value;
    if (RESERVED.has(head)) {
      k += 1;
      continue;
    }
    const prefix = Object.hasOwn(PREFIX, basename(head)) ? PREFIX[basename(head)] : null;
    if (!prefix) break;
    k += 1;
    while (k < words.length && words[k].value.startsWith('-') && words[k].value !== '-') {
      const opt = words[k].value;
      k += 1;
      if (opt === '--') break;
      if (prefix.values.has(opt)) k += 1;
    }
    k += prefix.operands;
  }
  const argv = words.slice(k).map((w) => w.value);
  return { argv, name: argv.length > 0 ? basename(argv[0]) : '' };
}

module.exports = { parse, simpleCommand };

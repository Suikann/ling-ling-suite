#!/usr/bin/env node
// hooks/lib/guard_log.js — 崗哨的紀錄檔：deny-dangerous-bash 與兩支 git 機關共用
//
// 紀錄檔 `~/.claude/lingling-guard.log`，一事件一行 JSON，記三類：擋下（deny）、內部錯誤
// （error）、Guarded branch 解析退回寫死清單（fallback）。不輪替。
//
// 介面：
//   defaultLogPath() → 紀錄檔路徑（跟著 HOME 走，測試把 HOME 指向臨時目錄就不會寫進真的紀錄檔）。
//   writeLog(logPath, entry)：寫一行；寫不進去（唯讀家目錄等）靜默略過，紀錄不影響判定。
'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function defaultLogPath() {
  return path.join(os.homedir(), '.claude', 'lingling-guard.log');
}

function writeLog(logPath, entry) {
  try {
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    fs.appendFileSync(logPath, `${JSON.stringify(entry)}\n`);
  } catch {
    // 紀錄是旁證，不是判定的一部分
  }
}

module.exports = { defaultLogPath, writeLog };

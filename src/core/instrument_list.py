# -*- coding: utf-8 -*-
"""
匯出樂器表的文字內容

一個群組的樂器表依聲部組分段列出，聲部後面寫人數（未填的只寫聲部），上方寫時長、最後寫聲部數與人數合計。
聲部組依專案的規則（`Project.section_of`），介面為英文時換成英文名稱（`core/sections.py` 的 `section_label`）。
只組出文字，以 \\n 分行；寫檔由呼叫端經 FileService 處理。

使用範例：
    from core.instrument_list import instrument_list_text
    text = instrument_list_text(project, group)
"""
from typing import Dict, List
from core.locale import t
from core.models import Group, Headcount, Project, format_duration, sum_headcounts
from core.sections import section_label

# 標題與合計上下的分隔線
_RULE = "=" * 40


def instrument_list_text(project: Project, group: Group) -> str:
    """群組樂器表（連同人數與時長）匯出成文字檔的內容

    Args:
        project: 群組所屬的專案（聲部組）
        group: 要匯出的群組

    Returns:
        以 \\n 分行的文字
    """
    grouped: Dict[str, List[tuple]] = {}
    for voice, headcount in zip(group.instruments, group.headcounts):
        grouped.setdefault(section_label(project.section_of(voice)), []).append((voice, headcount))
    lines = [t("instrument.title"), _RULE, ""]
    duration = format_duration(group.duration_seconds)
    if duration:
        lines.extend([f"{t('instrumentation.duration')}：{duration}", ""])
    for section, items in grouped.items():
        lines.append(f"{section}：")
        lines.extend(_voice_line(voice, headcount) for voice, headcount in items)
        lines.append("")
    total = sum_headcounts(group.headcounts).text() or "0"
    lines.extend([_RULE, t("instrument.export_total", parts=len(group.instruments), people=total)])
    return "\n".join(lines)


def _voice_line(voice: str, headcount: Headcount) -> str:
    """一個聲部的一行：填了人數時對齊後寫在後面"""
    return f"  {voice:<30s} x {headcount.text()}" if headcount.filled else f"  {voice}"

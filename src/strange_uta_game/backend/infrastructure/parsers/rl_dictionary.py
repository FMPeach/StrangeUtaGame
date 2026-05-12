"""RhythmicaLyrics 字典文件解析 — 纯文本 → annotated 格式条目列表。

格式（与 RhythmicaLyrics 兼容）：每行 ``[原文]\\t[注音1],[注音2],...``。

- 注音项中的全角加号 ``＋`` 为连词占位符，解析时剥离；
- 仅含 ``＋`` 的项表示该字符无独立读音（与前字符连词），与其他空读音一同在尾部被去除；
- 空行 / 无 ``\\t`` / 原文或注音全空的行直接跳过；
- 含 ASCII 字母的 ``word`` 整条丢弃（项目不再支持英文词条走用户词典）；
- 注音直接转换为项目规范的 annotated 行内格式（参见 ``annotated_text``）：

  - 段数 == 字符数且每段非空 → 直转为 ``{word||r1,r2,...,rN}``；
  - 否则跑 Sudachi 重分析 → ``{block||reading,空,...}`` 块拼接；
  - Sudachi 失败 → 整词单块兜底 ``{word||full_reading,空,...}``。

Public API
----------
- :func:`parse_rl_dictionary` — 文本 → ``List[Dict[str, object]]``，形如
  ``[{"enabled": True, "word": "赤い", "reading": "{赤||あか}い"}, ...]``。
- :func:`convert_legacy_reading` — 单条 ``(word, 老逗号 reading)`` → 新 annotated reading。
  迁移脚本与 RL 导入共用。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# 全角加号 U+FF0B
_LINK_MARKER = "\uff0b"


# ──────────────────────────────────────────────
# 字符判定 / 转换
# ──────────────────────────────────────────────


def _has_ascii_letter(s: str) -> bool:
    """``word`` 是否含 ASCII 英文字母（用于识别需丢弃的英文词条）。"""
    return any(("a" <= c <= "z") or ("A" <= c <= "Z") for c in s)


def _is_kanji(c: str) -> bool:
    """是否汉字（CJK 统合汉字基本区 + 扩展 A）。"""
    o = ord(c)
    return 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF


def _kata_to_hira(s: str) -> str:
    """片假名转平假名。"""
    out: List[str] = []
    for c in s:
        o = ord(c)
        if 0x30A1 <= o <= 0x30F6:
            out.append(chr(o - 0x60))
        else:
            out.append(c)
    return "".join(out)


# ──────────────────────────────────────────────
# 直转分支：段数严格 == 字符数 且每段非空
# ──────────────────────────────────────────────


def _try_direct_convert(word: str, old_reading: str) -> Optional[str]:
    """尝试把老逗号分段读音直转为新 annotated 格式。

    Returns:
        新 reading（annotated 行内格式）；不满足直转条件返回 ``None``。
    """
    if "," not in old_reading:
        return None
    segs = old_reading.split(",")
    if len(segs) != len(word):
        return None
    if any(s == "" for s in segs):
        return None
    readings_part = ",".join(segs)
    return f"{{{word}||{readings_part}}}"


# ──────────────────────────────────────────────
# 重分析分支：Sudachi
# ──────────────────────────────────────────────


def _reanalyze(word: str, old_reading: str) -> Optional[str]:
    """用 Sudachi 切分 + 老 reading 拼接生成新 annotated reading。

    策略：
      1. 把老 reading 合并为完整读音；
      2. 跑 Sudachi 拿 morpheme 切分锚点；
      3. 用 ``analyzer._distribute_morpheme_reading(word, full_reading)`` 按锚点重切；
      4. 失败/单块 → 整词作为单 block 兜底 ``{word||full_reading,空,...}``；
      5. 每个 block：
         - 含汉字 → ``{block||reading,空,空,...}``；
         - 无汉字 → 逐字符字面输出（片假名转平假名注音）；
         - reading == 字面 → 字面直接输出（无 ruby 段）。

    Returns:
        新 reading；老 reading 完全为空时返回 ``None``。
    """
    # 延迟 import，避免模块级循环 import
    from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
        SudachiAnalyzer,
    )

    full_reading = (old_reading or "").replace(",", "").strip()
    full_reading = _kata_to_hira(full_reading)
    if not full_reading:
        return None

    analyzer = SudachiAnalyzer()
    try:
        sudachi_blocks = analyzer.analyze(word)
    except Exception:
        sudachi_blocks = []

    distributed: Optional[List[Tuple[str, str]]] = None
    if sudachi_blocks and len(sudachi_blocks) > 1:
        try:
            distributed = analyzer._distribute_morpheme_reading(word, full_reading)
        except Exception:
            distributed = None

    if not distributed or len(distributed) <= 1:
        distributed = [(word, full_reading)]

    out: List[str] = []
    for block_text, block_reading in distributed:
        block_reading = _kata_to_hira(block_reading)
        block_has_kanji = any(_is_kanji(c) for c in block_text)

        if block_has_kanji:
            # 含汉字块：reading 整体塞首字符 RubyPart，其余字符空段
            empty_tail = "," * (len(block_text) - 1)
            out.append(f"{{{block_text}||{block_reading}{empty_tail}}}")
        else:
            # 无汉字块（纯假名/数字/符号）：逐字符处理
            # 没有逐字符 reading 分布信息时，按字符均分行不通；
            # 折中：若 block_reading == _kata_to_hira(block_text) → 字面输出（无 ruby）；
            # 否则当整体单字符块 → 包成 {block||reading,空,...}。
            if _kata_to_hira(block_text) == block_reading or not block_reading:
                # 字面无 ruby
                for ch in block_text:
                    out.append(ch)
            else:
                empty_tail = "," * (len(block_text) - 1)
                out.append(f"{{{block_text}||{block_reading}{empty_tail}}}")

    return "".join(out)


# ──────────────────────────────────────────────
# 共用入口：老 reading → 新 annotated reading
# ──────────────────────────────────────────────


def convert_legacy_reading(word: str, old_reading: str) -> Optional[str]:
    """把单条 ``(word, 老逗号 reading)`` 转换为新 annotated reading。

    迁移脚本与 RL 导入共用此函数。

    Returns:
        新 reading；``word`` 含 ASCII 字母 / 读音为空时返回 ``None``。
    """
    if not word or not old_reading:
        return None
    if _has_ascii_letter(word):
        return None
    new_reading = _try_direct_convert(word, old_reading)
    if new_reading is not None:
        return new_reading
    return _reanalyze(word, old_reading)


# ──────────────────────────────────────────────
# 主入口：RL 文本 → 条目列表
# ──────────────────────────────────────────────


def parse_rl_dictionary(text: str) -> List[Dict[str, object]]:
    """解析 RL 字典文本为新 annotated 格式条目列表。

    Args:
        text: 原始文本内容。

    Returns:
        条目列表；每项包含 ``enabled`` (bool, 总为 True)、``word`` (str) 与
        ``reading`` (str，annotated 行内格式)。
        被丢弃的条目（含 ASCII 字母 / 读音全空 / Sudachi 解析无注音）不出现。
    """
    entries: List[Dict[str, object]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "\t" not in line:
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        word = parts[0].strip()
        raw_readings = parts[1].strip()
        if not word or not raw_readings:
            continue

        cleaned: List[str] = []
        for piece in raw_readings.split(","):
            piece = piece.strip().replace(_LINK_MARKER, "")
            cleaned.append(piece)

        # 去除尾部多余空读音（含纯 ＋ 被剥离后的空项）
        while cleaned and not cleaned[-1]:
            cleaned.pop()

        old_reading = ",".join(cleaned)
        if not old_reading:
            continue

        new_reading = convert_legacy_reading(word, old_reading)
        if not new_reading:
            continue
        entries.append({"enabled": True, "word": word, "reading": new_reading})
    return entries

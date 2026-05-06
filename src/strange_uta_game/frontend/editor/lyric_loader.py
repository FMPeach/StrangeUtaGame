"""歌词文件解析工具。

提供歌词格式检测和解析功能，支持 LRC/SRT/ASS/Nicokara/内联格式等。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from strange_uta_game.backend.domain import Sentence, Singer
from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
    LRCParser,
    NicokaraParser,
    parse_to_sentences,
)
from strange_uta_game.backend.infrastructure.parsers.inline_format import (
    sentences_from_inline_text,
)


# 内联格式检测
_INLINE_PATTERN = re.compile(r"\[.*?\|.*?\]|{[^}]+\|[^}]+}")
# LRC 时间标签检测
_LRC_PATTERN = re.compile(r"\[\d{2}:\d{2}\.\d{2,3}\]")
# ASS 格式检测
_ASS_PATTERN = re.compile(r"^\[Script Info\]|^Dialogue:\s*\d+", re.MULTILINE)
# SRT 格式检测
_SRT_PATTERN = re.compile(
    r"\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}"
)


def detect_lyric_format(content: str) -> str:
    """检测歌词内容的格式。

    Returns:
        格式名称: "inline", "nicokara", "ass", "srt", "lrc", "text"
    """
    if _INLINE_PATTERN.search(content):
        return "inline"
    if NicokaraParser.is_nicokara_format(content):
        return "nicokara"
    if _ASS_PATTERN.search(content):
        return "ass"
    if _SRT_PATTERN.search(content):
        return "srt"
    if _LRC_PATTERN.search(content):
        return "lrc"
    return "text"


def parse_lyric_content(
    content: str,
    default_singer_id: str,
    project_singers: Optional[List[Singer]] = None,
) -> Tuple[List[Sentence], bool, List[Singer]]:
    """解析歌词内容，返回解析后的句子列表。

    Args:
        content: 歌词文本内容
        default_singer_id: 默认演唱者 ID
        project_singers: 当前项目的演唱者列表（用于 Nicokara 格式的演唱者匹配）

    Returns:
        (sentences, is_nicokara, new_singers):
        - sentences: 解析后的句子列表
        - is_nicokara: 是否为 Nicokara 格式
        - new_singers: Nicokara 格式中需要新增的演唱者列表
    """
    fmt = detect_lyric_format(content)
    is_nicokara = False
    new_singers: List[Singer] = []

    # 内联格式
    if fmt == "inline":
        sentences = sentences_from_inline_text(content, default_singer_id)
        return sentences, False, []

    # Nicokara 格式
    if fmt == "nicokara":
        is_nicokara = True
        parser = NicokaraParser()
        result = parser.parse(content)

        # NicokaraParseResult 没有 sentences/singers 属性
        # 需要通过 parse_to_sentences 处理
        sentences = parse_to_sentences(result.lines, default_singer_id)

        # 收集需要新增的演唱者（从 singer_definitions 中）
        if project_singers and result.singer_definitions:
            for singer_key, display_name in result.singer_definitions.items():
                existing = None
                for s in project_singers:
                    if s.name == display_name:
                        existing = s
                        break
                if not existing:
                    new_singers.append(Singer(name=display_name, color="#FF6B6B", is_default=False))

        return sentences, is_nicokara, new_singers

    # ASS 格式
    if fmt == "ass":
        from strange_uta_game.backend.infrastructure.parsers.ass_parser import (
            ASSParser,
        )

        parser = ASSParser()
        parsed_lines = parser.parse(content)
        sentences = parse_to_sentences(parsed_lines, default_singer_id)
        return sentences, False, []

    # SRT 格式
    if fmt == "srt":
        from strange_uta_game.backend.infrastructure.parsers.srt_parser import (
            SRTParser,
        )

        parser = SRTParser()
        parsed_lines = parser.parse(content)
        sentences = parse_to_sentences(parsed_lines, default_singer_id)
        return sentences, False, []

    # LRC 格式
    if fmt == "lrc":
        lrc_parser = LRCParser()
        parsed_lines = lrc_parser.parse(content)
        sentences = parse_to_sentences(parsed_lines, default_singer_id)
        return sentences, False, []

    # 纯文本：按行分割
    sentences = []
    lines = [line.strip() for line in content.split("\n") if line.strip()]
    for line_text in lines:
        from strange_uta_game.backend.domain import Character

        sentence = Sentence(
            singer_id=default_singer_id,
            characters=[Character(char=c) for c in line_text],
        )
        sentences.append(sentence)

    return sentences, False, []


def read_lyric_file(path: str) -> Optional[str]:
    """读取歌词文件内容。

    Returns:
        文件内容，读取失败返回 None
    """
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception:
        return None

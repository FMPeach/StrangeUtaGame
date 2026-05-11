"""txt2ass 与 ASS 字幕格式导出器。

包含两类导出器：
1. Txt2AssExporter: 给 txt2ass 工具用的简单 [mm:ss.xx]text 文本格式。
2. ASSDirectExporter: 直接生成 Aegisub 兼容的 .ass 卡拉OK字幕，
   支持 \\k 时长标签和 Aegisub 风格的注音 ({字|<かな})。

设计原则（参考 entities.py 重构后契约）：
1. 时间永远从 char.global_timestamps / char.global_sentence_end_ts 取，
   领域层已经把偏移量算好，导出器不再二次叠加。
2. 每个字符在 Dialogue 文本里只出现一次。多 checkpoint 字符的额外
   timestamps 不再生成重复字符（修复字符重影 bug）。
3. 行 End Time 不再依赖「下一行 Start」（会让字幕跨过整段间奏），
   而是用本行最后字符的 global_sentence_end_ts，没有则退化为
   global_timing_end_ms + post-roll。
4. ASS 卡拉OK标签 \\k 的单位是厘秒(10ms)。每字时长 =
   下一个时间戳(或行末 sentence_end_ts) - 当前字时间戳，转厘秒。
"""

from typing import List
from .base import BaseExporter, ExportError
from strange_uta_game.backend.domain import Project, Sentence


class Txt2AssExporter(BaseExporter):
    """txt2ass 格式导出器

    导出 txt2ass 格式，用于配合外部 txt2ass 工具生成 ASS。
    格式简单：每行 [mm:ss.xx]Lyrics
    """

    @property
    def name(self) -> str:
        return "txt2ass"

    @property
    def description(self) -> str:
        return "用于生成 ASS 字幕的格式"

    @property
    def file_extension(self) -> str:
        return ".txt"

    @property
    def file_filter(self) -> str:
        return "txt2ass 文件 (*.txt)"

    def export(self, project: Project, file_path: str) -> None:
        """导出为 txt2ass 格式"""
        self._validate_project(project)

        lines: List[str] = []

        # 标题信息（注释）
        if project.metadata:
            if project.metadata.title:
                lines.append(f"# Title: {project.metadata.title}")
            if project.metadata.artist:
                lines.append(f"# Artist: {project.metadata.artist}")

        lines.append("# Format: [mm:ss.xx]Lyrics")
        lines.append("")

        for sentence in project.sentences:
            line_text = self._export_sentence(sentence)
            if line_text:
                lines.append(line_text)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as e:
            raise ExportError(f"写入文件失败: {e}")

    def _export_sentence(self, sentence: Sentence) -> str:
        """导出一行歌词"""
        if not sentence.has_timetags:
            return f"[00:00.00]{sentence.text}"

        start_ms = sentence.global_timing_start_ms
        if start_ms is None:
            return f"[00:00.00]{sentence.text}"

        time_str = self._format_timestamp(start_ms, "lrc")
        return f"{time_str}{sentence.text}"


# ──────────────────────────────────────────────
# ASSDirectExporter
# ──────────────────────────────────────────────

# 行前后留白（毫秒），让字幕进入/退出更自然
_PRE_ROLL_MS = 200
_POST_ROLL_MS = 200
# 行末若无 sentence_end_ts 时的兜底拖音时长（毫秒）
_FALLBACK_TAIL_MS = 500


class ASSDirectExporter(BaseExporter):
    """ASS 字幕直接导出器

    直接生成 Aegisub 兼容的 ASS 卡拉OK字幕。
    支持 \\k 时长标签和 Aegisub 注音 ({汉字|<かな})。
    """

    @property
    def name(self) -> str:
        return "ASS"

    @property
    def description(self) -> str:
        return "ASS 字幕格式（Advanced SubStation Alpha）"

    @property
    def file_extension(self) -> str:
        return ".ass"

    @property
    def file_filter(self) -> str:
        return "ASS 字幕文件 (*.ass)"

    def export(self, project: Project, file_path: str) -> None:
        """导出为 ASS 格式"""
        self._validate_project(project)
        file_path = self._ensure_extension(file_path)

        lines: List[str] = []

        # ASS 文件头
        lines.extend(self._generate_header(project))
        lines.append("")

        # Styles
        lines.extend(self._generate_styles())
        lines.append("")

        # Events
        lines.extend(self._generate_events(project))

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as e:
            raise ExportError(f"写入文件失败: {e}")

    def _generate_header(self, project: Project) -> List[str]:
        title = project.metadata.title if project.metadata else "Untitled"
        return [
            "[Script Info]",
            f"Title: {title}",
            "ScriptType: v4.00+",
            "Collisions: Normal",
            "PlayDepth: 0",
            "Timer: 100.0000",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: TV.601",
        ]

    def _generate_styles(self) -> List[str]:
        return [
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,5,10,10,10,1",
            "Style: Karaoke,Arial,24,&H00FF6B6B,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,2,0,5,10,10,30,1",
        ]

    def _generate_events(self, project: Project) -> List[str]:
        lines = [
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]

        for sentence in project.sentences:
            if not sentence.has_timetags:
                continue

            line_start_ms = sentence.global_timing_start_ms
            if line_start_ms is None:
                continue

            # 行结束时间：优先用本行 sentence_end_ts，否则用最大全局时间戳 + 兜底
            line_end_ms = self._compute_line_end_ms(sentence)

            # 行首尾留白
            start_str = self._format_timestamp(
                max(0, line_start_ms - _PRE_ROLL_MS), "ass"
            )
            end_str = self._format_timestamp(line_end_ms + _POST_ROLL_MS, "ass")

            # 卡拉OK文本
            karaoke_text = self._generate_karaoke_text(
                sentence, line_start_ms, line_end_ms
            )

            event_line = (
                f"Dialogue: 0,{start_str},{end_str},Karaoke,,0,0,0,,{karaoke_text}"
            )
            lines.append(event_line)

        return lines

    def _compute_line_end_ms(self, sentence: Sentence) -> int:
        """计算本行的结束时间（毫秒）。

        优先级：
        1. 最后一个 is_sentence_end 字符的 global_sentence_end_ts
        2. 行内最晚全局时间戳 + 兜底拖音
        """
        for ch in reversed(sentence.characters):
            if ch.is_sentence_end and ch.global_sentence_end_ts is not None:
                return ch.global_sentence_end_ts

        end = sentence.global_timing_end_ms
        if end is None:
            # 不应发生：has_timetags 已保证至少一个时间戳
            return 0
        return end + _FALLBACK_TAIL_MS

    def _generate_karaoke_text(
        self, sentence: Sentence, line_start_ms: int, line_end_ms: int
    ) -> str:
        """生成带卡拉OK效果的 Dialogue 文本。

        基于「节拍块 (Block)」机制：一个有时间戳的字符及其后所有无时间戳
        字符（如标点、被注音吞掉的连词后续字）打包为同一个 \\k 节拍块。
        这样可避免两类 bug：
        1. 标点漂移：`あ。い` 不会变成 `{\\k}あ{\\k}。い`，而是 `{\\k}あ。{\\k}い`。
        2. 多字注音断裂：`漢字|<かんじ` 不会被拆成两块，会整体输出
           `{\\k}漢字|<かんじ`，符合 Aegisub 注音语法。

        - 行首未打轴的前导字符（如前导标点）落入 pre-roll \\k 区。
        - 行尾 post-roll \\k 让退出平滑。
        """
        chars = sentence.characters
        if not chars:
            return f"{{\\k{_PRE_ROLL_MS // 10}}}{{\\k{_POST_ROLL_MS // 10}}}"

        parts: List[str] = [f"{{\\k{_PRE_ROLL_MS // 10}}}"]

        # 1. 行首未打轴字符并入 pre-roll \k 区
        idx = 0
        while idx < len(chars) and not chars[idx].global_timestamps:
            parts.append(self._escape_ass_text(chars[idx].char))
            idx += 1

        # 2. 按「带时间戳的字符」为锚点切分成 Block
        blocks: List[List] = []
        current_block: List = []
        for ch in chars[idx:]:
            if ch.global_timestamps:
                if current_block:
                    blocks.append(current_block)
                current_block = [ch]
            else:
                current_block.append(ch)
        if current_block:
            blocks.append(current_block)

        # 3. 渲染每个 Block
        for i, block in enumerate(blocks):
            first_ch = block[0]
            current_ts = first_ch.global_timestamps[0]

            # 时长 = 下一个 Block 锚点时间 - 当前锚点时间（最后一块用 line_end_ms）
            if i + 1 < len(blocks):
                nxt_ts = blocks[i + 1][0].global_timestamps[0]
            else:
                nxt_ts = line_end_ms
            duration_ms = max(0, nxt_ts - current_ts)
            k_cs = duration_ms // 10

            # 合并块内所有字符的文字和注音
            kanji_text = "".join(self._escape_ass_text(c.char) for c in block)
            kana_text = "".join(
                self._escape_ass_text(c.ruby.text)
                for c in block
                if c.ruby and c.ruby.text
            )

            if kana_text:
                parts.append(f"{{\\k{k_cs}}}{kanji_text}|<{kana_text}")
            else:
                parts.append(f"{{\\k{k_cs}}}{kanji_text}")

        # 4. 行尾 post-roll
        parts.append(f"{{\\k{_POST_ROLL_MS // 10}}}")

        return "".join(parts)

    @staticmethod
    def _escape_ass_text(text: str) -> str:
        """转义 ASS 文本中的特殊字符。

        ASS 里 `{` `}` `\\` 是标签语法的一部分，需转义。
        """
        if not text:
            return text
        # 反斜杠先处理，避免连锁替换
        text = text.replace("\\", "\\\\")
        text = text.replace("{", "\\{").replace("}", "\\}")
        return text

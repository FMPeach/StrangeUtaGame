"""歌词解析器测试。"""

import pytest
from pathlib import Path
from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
    TXTParser,
    LRCParser,
    KRAParser,
    LyricParserFactory,
    ParseError,
    parse_to_sentences,
    ParsedLine,
)


class TestTXTParser:
    """测试 TXT 解析器"""

    def test_parse_simple_text(self):
        parser = TXTParser()
        content = "第一行\n第二行\n第三行"
        result = parser.parse(content)

        assert len(result) == 3
        assert result[0].text == "第一行"
        assert result[0].timetags == []

    def test_parse_skip_empty_lines(self):
        parser = TXTParser()
        content = "第一行\n\n第三行"
        result = parser.parse(content)

        assert len(result) == 2

    def test_parse_strip_whitespace(self):
        parser = TXTParser()
        content = "  第一行  \n  第二行  "
        result = parser.parse(content)

        assert result[0].text == "第一行"
        assert result[1].text == "第二行"


class TestLRCParser:
    """测试 LRC 解析器"""

    def test_parse_simple_lrc(self):
        parser = LRCParser()
        content = "[00:10.50]第一行\n[00:15.20]第二行"
        result = parser.parse(content)

        assert len(result) == 2
        assert result[0].text == "第一行"
        assert result[0].timetags == [(0, 10500)]

        assert result[1].text == "第二行"
        assert result[1].timetags == [(0, 15200)]

    def test_parse_skip_metadata(self):
        parser = LRCParser()
        content = "[ti:Title]\n[ar:Artist]\n[00:10.00]歌词"
        result = parser.parse(content)

        assert len(result) == 1
        assert result[0].text == "歌词"

    def test_parse_milliseconds_precision(self):
        parser = LRCParser()
        content = "[00:10.123]歌词"
        result = parser.parse(content)

        assert result[0].timetags == [(0, 10123)]

    def test_parse_start_end_timestamps(self):
        """测试 [start]歌词[end] 格式 — 增强LRC常见格式"""
        parser = LRCParser()
        content = "[00:06.540]一闪一闪亮晶晶[00:09.300]"
        result = parser.parse(content)

        assert len(result) == 1
        assert result[0].text == "一闪一闪亮晶晶"
        assert result[0].timetags == [(0, 6540)]

    def test_parse_start_end_multi_lines(self):
        """测试多行 [start]歌词[end] 格式"""
        parser = LRCParser()
        content = (
            "[00:06.540]一闪一闪亮晶晶[00:09.300]\n"
            "[00:09.300]满天都是小星星[00:12.120]\n"
            "[00:12.120]挂在天上放光明[00:15.060]"
        )
        result = parser.parse(content)

        assert len(result) == 3
        assert result[0].text == "一闪一闪亮晶晶"
        assert result[0].timetags == [(0, 6540)]
        assert result[1].text == "满天都是小星星"
        assert result[1].timetags == [(0, 9300)]
        assert result[2].text == "挂在天上放光明"
        assert result[2].timetags == [(0, 12120)]

    def test_parse_colon_separator(self):
        """测试冒号分隔的时间标签 [mm:ss:cc]"""
        parser = LRCParser()
        content = "[00:06:54]一闪一闪[00:09:30]"
        result = parser.parse(content)

        assert len(result) == 1
        assert result[0].text == "一闪一闪"
        assert result[0].timetags == [(0, 6540)]


class TestLyricParserFactory:
    """测试解析器工厂"""

    def test_get_txt_parser(self):
        parser = LyricParserFactory.get_parser("test.txt")
        assert isinstance(parser, TXTParser)

    def test_get_lrc_parser(self):
        parser = LyricParserFactory.get_parser("test.lrc")
        assert isinstance(parser, LRCParser)

    def test_get_kra_parser(self):
        parser = LyricParserFactory.get_parser("test.kra")
        assert isinstance(parser, KRAParser)

    def test_unsupported_format_raises_error(self):
        with pytest.raises(ParseError):
            LyricParserFactory.get_parser("test.mp3")


class TestParseToSentences:
    """测试转换为 Sentence"""

    def test_convert_with_timetags(self):
        parsed_lines = [
            ParsedLine(text="测试", timetags=[(0, 1000)]),
        ]

        sentences = parse_to_sentences(parsed_lines, "singer_1")

        assert len(sentences) == 1
        assert sentences[0].text == "测试"


class TestApplyRubyEntries:
    """测试 @Ruby 注音应用（含位置范围消歧）"""

    def _make_sentence(self, text: str, timestamps: list) -> "Sentence":
        """创建带时间戳的句子"""
        from strange_uta_game.backend.domain import Sentence

        sentence = Sentence.from_text(text, "singer_1")
        for i, ts in enumerate(timestamps):
            if ts is not None and i < len(sentence.characters):
                sentence.characters[i].add_timestamp(ts)
        return sentence

    def test_same_kanji_different_readings_across_sentences(self):
        """同一词组在不同句子有不同读音时，应按位置范围正确匹配"""
        from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
            _apply_ruby_entries,
            NicokaraRubyEntry,
        )

        # 句子1: 言葉は (言→こと, ts=1000)
        s1 = self._make_sentence("言葉は", [1000, 1300, 1500])
        s1.characters[0].check_count = 2
        s1.characters[0].add_timestamp(1000, checkpoint_idx=0)
        s1.characters[0].add_timestamp(1163, checkpoint_idx=1)

        # 句子2: 言う (言→い, ts=5000)
        s2 = self._make_sentence("言う", [5000, 5200])

        # @Ruby 条目: 言有两个不同读音，需要位置范围
        entries = [
            NicokaraRubyEntry(
                kanji="言", reading="こ[00:00:16]と", positions=["", "[00:05:00]"]
            ),
            NicokaraRubyEntry(
                kanji="言", reading="い", positions=["[00:05:00]"]
            ),
        ]

        _apply_ruby_entries(s1, entries)
        _apply_ruby_entries(s2, entries)

        # 句子1 的 言 应该是 こと
        assert s1.characters[0].ruby is not None
        ruby_text_1 = "".join(p.text for p in s1.characters[0].ruby.parts)
        assert ruby_text_1 == "こと"

        # 句子2 的 言 应该是 い
        assert s2.characters[0].ruby is not None
        ruby_text_2 = "".join(p.text for p in s2.characters[0].ruby.parts)
        assert ruby_text_2 == "い"

    def test_no_position_falls_back_to_sequential(self):
        """无位置范围时按顺序匹配第一个未标注出现"""
        from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
            _apply_ruby_entries,
            NicokaraRubyEntry,
        )

        s = self._make_sentence("嫌い嫌い", [1000, 2000, 3000, 4000])

        entries = [
            NicokaraRubyEntry(kanji="嫌", reading="きら"),
            NicokaraRubyEntry(kanji="嫌", reading="いや"),
        ]

        _apply_ruby_entries(s, entries)

        # 第一个嫌 → きら
        ruby1 = "".join(p.text for p in s.characters[0].ruby.parts)
        assert ruby1 == "きら"
        # 第二个嫌 → いや
        ruby2 = "".join(p.text for p in s.characters[2].ruby.parts)
        assert ruby2 == "いや"

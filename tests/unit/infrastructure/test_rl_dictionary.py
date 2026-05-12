"""RL 字典解析器单元测试。

新格式：``parse_rl_dictionary`` 输出的 ``reading`` 是 annotated 行内格式
（参见 ``annotated_text``），而不是逗号分隔的字符级读音。
"""

from __future__ import annotations

from strange_uta_game.backend.infrastructure.parsers.rl_dictionary import (
    parse_rl_dictionary,
)


class TestParseRlDictionary:
    def test_basic_entry(self):
        # 段数 == 字符数 且每段非空 → 直转为单 annotated block
        text = "赤い\tあ,かい\n"
        entries = parse_rl_dictionary(text)
        assert entries == [
            {"enabled": True, "word": "赤い", "reading": "{赤い||あ,かい}"}
        ]

    def test_skip_empty_and_malformed_lines(self):
        text = "\n   \n漢字 no-tab\n本当\tほん,とう\n"
        entries = parse_rl_dictionary(text)
        assert len(entries) == 1
        assert entries[0]["word"] == "本当"
        assert entries[0]["reading"] == "{本当||ほん,とう}"

    def test_link_marker_stripped(self):
        # ＋ (U+FF0B) 剥离，仅含 ＋ 的读音项在尾部被去除 → 段数对齐字符数后直转
        text = "本当に\tほん,とう,に,＋\n"
        entries = parse_rl_dictionary(text)
        assert entries[0]["reading"] == "{本当に||ほん,とう,に}"

    def test_link_marker_inside_reading_stripped_but_kept(self):
        text = "特別\tとく＋,べつ\n"
        entries = parse_rl_dictionary(text)
        # 剥离 ＋ 后还有 "とく"，与 "べつ" 一起直转
        assert entries[0]["reading"] == "{特別||とく,べつ}"

    def test_trailing_empty_readings_removed(self):
        # 尾部空段移除后 segs=["ここ","ろ"]，len=2 != len("心")=1 → 走 reanalyze 兜底
        text = "心\tここ,ろ,,,\n"
        entries = parse_rl_dictionary(text)
        # 单字 + 整词单块兜底
        assert entries[0]["reading"] == "{心||こころ}"

    def test_entry_dropped_when_all_readings_empty(self):
        # 读音全为空或仅 ＋ → 丢弃整条
        text = "空\t＋,＋,＋\n"
        entries = parse_rl_dictionary(text)
        assert entries == []

    def test_multiple_entries_preserve_order(self):
        text = "一\tいち\n二\tに\n三\tさん\n"
        entries = parse_rl_dictionary(text)
        assert [e["word"] for e in entries] == ["一", "二", "三"]

    def test_enabled_flag_always_true(self):
        text = "赤\tあか\n"
        entries = parse_rl_dictionary(text)
        assert entries[0]["enabled"] is True

    def test_line_with_empty_word_skipped(self):
        text = "\tあ,か\n赤\tあか\n"
        entries = parse_rl_dictionary(text)
        assert len(entries) == 1
        assert entries[0]["word"] == "赤"

    def test_english_word_dropped(self):
        """word 含 ASCII 字母 → 整条丢弃。"""
        text = "hello\tハロー\n赤\tあか\n"
        entries = parse_rl_dictionary(text)
        assert [e["word"] for e in entries] == ["赤"]


class TestFrontendShimCompatibility:
    """确认前端旧导入路径 ``_parse_rl_dictionary`` 与后端实现等价。"""

    def test_frontend_shim_delegates_to_backend(self):
        from strange_uta_game.frontend.settings.app_settings import (
            _parse_rl_dictionary,
        )

        text = "赤い\tあ,かい\n本当\tほん,とう\n"
        assert _parse_rl_dictionary(text) == parse_rl_dictionary(text)

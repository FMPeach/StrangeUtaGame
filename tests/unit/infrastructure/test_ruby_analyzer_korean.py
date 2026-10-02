# -*- coding: utf-8 -*-
"""韩文汉字（韩音）分析器：Unihan kHangul 查表 + 词首두음법칙。"""

from strange_uta_game.backend.infrastructure.parsers import ruby_analyzer
from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
    KoreanHanjaAnalyzer,
    _apply_initial_sound_law,
)


def _readings(text: str) -> dict:
    return {
        r.text: r.reading
        for r in KoreanHanjaAnalyzer().analyze(text)
        if r.text.strip()
    }


class TestHanjaTableLookup:
    def test_common_hanja_get_korean_readings(self):
        assert _readings("漢字") == {"漢": "한", "字": "자"}
        assert _readings("人生") == {"人": "인", "生": "생"}

    def test_hangul_and_latin_pass_through(self):
        assert _readings("사A3") == {"사": "사", "A": "A", "3": "3"}

    def test_unknown_hanja_returns_self(self):
        # 表未收录的生僻汉字返回自身（resolver 侧对 KANJI 自读音保持
        # 缺口 → 执行前明确阻断，可手工标注）
        rare = "𠀀"  # U+20000 CJK Ext B，kHangul 未覆盖
        assert _readings(rare) == {rare: rare}


class TestInitialSoundLaw:
    """두음법칙：仅词首应用（行首 / 前邻非 CJK 文字）。"""

    def test_l_before_y_becomes_silent(self):
        assert _apply_initial_sound_law("륙") == "육"
        assert _apply_initial_sound_law("류") == "유"
        assert _apply_initial_sound_law("리") == "이"

    def test_l_before_other_vowel_becomes_n(self):
        assert _apply_initial_sound_law("라") == "나"
        assert _apply_initial_sound_law("래") == "내"

    def test_n_before_y_becomes_silent(self):
        assert _apply_initial_sound_law("녀") == "여"

    def test_non_initial_positions_unchanged(self):
        assert _apply_initial_sound_law("가") == "가"

    def test_word_initial_applied_in_context(self):
        # 词首：龍(룡→용)、六(륙→육)；词内：金(금) 不变
        assert _readings("龍門 六十") == {
            "龍": "용", "門": "문", "六": "육", "十": "십",
        }

    def test_mid_word_not_applied(self):
        # 漢字 内部的 字 不在词首；前邻汉字仍属词内
        assert _readings("한龍") == {"한": "한", "龍": "룡"}


class TestTableLoading:
    def test_bundled_table_loads(self):
        table = ruby_analyzer._load_hanja_table()
        # 表随仓库分发（config/hanja_korean.json，Unihan kHangul 派生）
        assert len(table) > 8000
        assert table["漢"] == "한"
        # 0E 词典词目形优先：北=북（非 배）、金=금（非 김）
        assert table["北"] == "북"
        assert table["金"] == "금"

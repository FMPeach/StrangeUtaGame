"""中文拼音 → 片假名近似音注音测试。

覆盖两层：
- 音节级 ``pinyin_syllable_to_katakana``（声母行 + 韵母段拼接、
  整音节特例、ü 写法消歧、声调剥离、长音ー、未命中回退）；
- 分析器 ``PinyinKatakanaAnalyzer``（继承 PinyinAnalyzer 的字符
  对齐口径不得回归——非汉字回退原字符、片假名不错位）。

长音口径（本轮采纳的日中音译惯例）：带声调音节在「主元音」后补
一个「ー」（中文单音节约当日语二拍，如 你好→ニー ハーオ）；轻声
（pypinyin 给无调形）不补。
"""

import pytest

from strange_uta_game.backend.infrastructure.parsers.pinyin_katakana import (
    PinyinKatakanaAnalyzer,
    create_pinyin_katakana_analyzer,
    pinyin_syllable_to_katakana,
    strip_tone,
)


class TestStripTone:
    def test_removes_tone_marks(self):
        assert strip_tone("zhōng") == "zhong"
        assert strip_tone("hǎo") == "hao"
        assert strip_tone("rén") == "ren"

    def test_preserves_u_umlaut(self):
        """ü（ǚǜ 等）剥离声调后须重组回单码位 ü，否则查表失配。"""
        assert strip_tone("nǚ") == "nü"
        assert strip_tone("lǜ") == "lü"

    def test_empty(self):
        assert strip_tone("") == ""


class TestSyllableToKatakanaToned:
    """带声调音节：主元音后补长音ー。"""

    @pytest.mark.parametrize(
        "pinyin, expected",
        [
            ("zhōng", "ジョーン"),
            ("hǎo", "ハーオ"),
            ("rén", "レーン"),
            ("nǐ", "ニー"),
            ("xǐ", "シー"),
            ("mā", "マー"),
            ("guó", "グーオ"),
            ("xuě", "シューエ"),
            ("jiā", "ジャー"),
            ("wǒ", "ウォー"),
            ("duì", "ドゥーイ"),
            ("shuǐ", "シューイ"),
            # 参考：中文歌词片假名标注惯例 你好→ニー ハーオ
            ("nǐ", "ニー"), ("hǎo", "ハーオ"),
        ],
    )
    def test_core_toned_syllables(self, pinyin, expected):
        assert pinyin_syllable_to_katakana(pinyin) == expected

    @pytest.mark.parametrize(
        "pinyin, expected",
        [
            # 舌尖元音 + 长音
            ("zhī", "ジー"), ("chī", "チー"), ("shī", "シー"), ("rī", "リー"),
            ("zì", "ズー"), ("cì", "ツー"), ("sì", "スー"),
            # 卷舌
            ("ér", "アール"),
            # 零声母整读
            ("yī", "イー"), ("wǔ", "ウー"), ("yù", "ユー"), ("wǒ", "ウォー"),
            ("ān", "アーン"),
        ],
    )
    def test_toned_whole_syllable_cases(self, pinyin, expected):
        assert pinyin_syllable_to_katakana(pinyin) == expected

    @pytest.mark.parametrize(
        "pinyin, expected",
        [
            # l/n + ü 取拗音（日语无 [ly]/[ny]），长音插在拗音拍后
            ("lǜ", "リュー"), ("nǚ", "ニュー"),
            ("lüè", "リューエ"), ("nüè", "ニューエ"),
            # j/q/x 拼写里 ü 写作 u，需与 üe 同解
            ("xué", "シューエ"), ("jué", "ジューエ"), ("què", "チューエ"),
        ],
    )
    def test_toned_u_umlaut_variants(self, pinyin, expected):
        assert pinyin_syllable_to_katakana(pinyin) == expected

    @pytest.mark.parametrize(
        "pinyin, expected",
        [
            # 鼻韵尾：长音插在元音拍后、拨音 ン 之前
            ("hàn", "ハーン"), ("shén", "シェーン"), ("qīng", "チーン"),
            ("fēng", "フェーン"), ("hóng", "ホーン"),
        ],
    )
    def test_long_vowel_before_nasal_coda(self, pinyin, expected):
        assert pinyin_syllable_to_katakana(pinyin) == expected


class TestSyllableToKatakanaToneless:
    """轻声/无调音节：不补长音。"""

    @pytest.mark.parametrize(
        "pinyin, expected",
        [
            ("de", "デ"), ("le", "レ"), ("me", "メ"), ("ne", "ネ"),
            ("zhi", "ジ"), ("chi", "チ"), ("shi", "シ"), ("ri", "リ"),
            ("zi", "ズ"), ("ci", "ツ"), ("si", "ス"),
            ("er", "アル"),
            ("yi", "イ"), ("wu", "ウ"), ("yu", "ユ"), ("wo", "ウォ"),
            ("an", "アン"), ("guo", "グオ"), ("xue", "シュエ"),
            ("jia", "ジャ"), ("shui", "シュイ"), ("lü", "リュ"),
        ],
    )
    def test_toneless_syllables(self, pinyin, expected):
        assert pinyin_syllable_to_katakana(pinyin) == expected


class TestSyllableFallback:
    def test_unmatched_falls_back_to_original(self):
        """未命中声母/韵母时回退原拼音串，不丢信息。"""
        assert pinyin_syllable_to_katakana("qwerty") == "qwerty"

    def test_empty_string(self):
        assert pinyin_syllable_to_katakana("") == ""


class TestPinyinKatakanaAnalyzer:
    @pytest.fixture(scope="class")
    def analyzer(self):
        return create_pinyin_katakana_analyzer()

    def test_is_pinyin_analyzer_subclass(self, analyzer):
        assert isinstance(analyzer, PinyinKatakanaAnalyzer)

    def test_word_context_reading(self, analyzer):
        results = analyzer.analyze("中文")
        readings = {r.text: r.reading for r in results}
        assert readings["中"] == "ジョーン"
        assert readings["文"] == "ウェーン"

    def test_english_prefix_does_not_shift_han_readings(self, analyzer):
        """沿用 PinyinAnalyzer 的 G3 对齐口径：非汉字回退原字符，汉字不错位。"""
        results = analyzer.analyze("ab中文字")
        assert [r.text for r in results] == list("ab中文字")
        readings = {r.text: r.reading for r in results}
        assert readings["a"] == "a"
        assert readings["b"] == "b"
        assert readings["中"] == "ジョーン"
        assert readings["文"] == "ウェーン"
        assert readings["字"] == "ズー"

    def test_digits_and_symbols_pass_through(self, analyzer):
        results = analyzer.analyze("1中2")
        readings = [r.reading for r in results]
        assert readings[0] == "1"
        assert readings[2] == "2"

    def test_indices_cover_full_text(self, analyzer):
        text = "A4纸上ab"
        results = analyzer.analyze(text)
        assert [(r.start_idx, r.end_idx) for r in results] == [
            (i, i + 1) for i in range(len(text))
        ]

    def test_get_reading_joins_with_spaces(self, analyzer):
        assert analyzer.get_reading("你好") == "ニー ハーオ"

    def test_common_phrases(self, analyzer):
        """常用短语整体读数（多音字消歧 + 长音）。"""
        assert analyzer.get_reading("中国") == "ジョーン グーオ"
        assert analyzer.get_reading("我爱你") == "ウォー アーイ ニー"

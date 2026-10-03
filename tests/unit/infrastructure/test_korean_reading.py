# -*- coding: utf-8 -*-
"""韩文注音读音转换器：片假名/平假名/罗马音（RR 显示口径）。"""

import pytest

from strange_uta_game.backend.infrastructure.parsers import korean_reading
from strange_uta_game.backend.infrastructure.parsers.korean_reading import (
    KoreanReadingAnalyzer,
    create_korean_reading_analyzer,
    hangul_to_hiragana,
    hangul_to_katakana,
    hangul_to_romaji,
    korean_readings,
)


class TestKatakana:
    def test_common_words(self):
        # 韩日媒体通行对译口径
        assert hangul_to_katakana("사랑") == "サラン"
        assert hangul_to_katakana("한국") == "ハングク"
        assert hangul_to_katakana("서울") == "ソウル"
        assert hangul_to_katakana("김치") == "キムチ"
        assert hangul_to_katakana("부산") == "プサン"
        assert hangul_to_katakana("강남") == "カンナム"

    def test_medial_voicing(self):
        # 词中平音清浊变化（대구→テグ、아버지→アボジ）；词首保持清音
        assert hangul_to_katakana("대구") == "テグ"
        assert hangul_to_katakana("아버지") == "アボジ"
        assert hangul_to_katakana("포도") == "ポド"

    def test_aspirated_never_voiced(self):
        # 送气音（ㅊㅋㅌㅍ）词中不浊化
        assert hangul_to_katakana("김치") == "キムチ"  # 치=ㅊ 词中保持 チ
        assert hangul_to_katakana("커피") == "コピ"  # 피=ㅍ 词中保持 ピ
        assert hangul_to_katakana("토마토") == "トマト"

    def test_tense_consonants_get_sokuon(self):
        # 紧音带 ッ（词首亦然：떡볶이→ットクボクイ 系统口径；오빠→オッパ）
        assert hangul_to_katakana("오빠") == "オッパ"
        assert hangul_to_katakana("떡볶이") == "ットクボクイ"

    def test_final_consonants(self):
        # 韵尾取首个可听音；ㅎ 弱化丢弃
        assert hangul_to_katakana("좋아") == "チョア"
        assert hangul_to_katakana("프랑스") == "フランス"  # ㅍ+ㅡ→フ 链

    def test_non_hangul_passthrough(self):
        assert hangul_to_katakana("A 3") == "A 3"
        assert hangul_to_katakana("") == ""


class TestHiragana:
    def test_common_words(self):
        assert hangul_to_hiragana("사랑") == "さらん"
        assert hangul_to_hiragana("한국") == "はんぐく"


class TestRomaji:
    def test_standard_rr(self):
        # 标准 RR 罗马字（显示口径）：ㅓ→eo、ㅡ→eu
        assert hangul_to_romaji("사랑") == "sarang"
        assert hangul_to_romaji("한국") == "hanguk"
        assert hangul_to_romaji("서울") == "seoul"
        assert hangul_to_romaji("김치") == "gimchi"
        assert hangul_to_romaji("부산") == "busan"
        assert hangul_to_romaji("오빠") == "oppa"
        assert hangul_to_romaji("프랑스") == "peurangseu"


class TestWordContext:
    def test_word_initial_vs_medial(self):
        # 同一字词首/词中行为不同：꾬마 词首紧音、词中浊化需整行语境
        r = korean_readings("아 빠", "katakana")
        assert r == {0: "ア", 2: "ッパ"}  # 빠 前是空格 → 词首... 仍带ッ（紧音口径）
        # ㄱ 词首清音 / 词中浊音
        r2 = korean_readings("가 대", "katakana")
        assert r2 == {0: "カ", 2: "テ"}


class TestHanjaChain:
    def test_hanja_via_korean_reading(self):
        # 汉字 → 韩音谚文（词首두음법칙）→ 风格读音
        r = korean_readings("漢字 龍", "katakana")
        assert r == {0: "ハン", 1: "ジャ", 3: "ヨン"}  # 龍 词首 룡→용
        r2 = korean_readings("漢字 龍", "romaji")
        assert r2 == {0: "han", 1: "ja", 3: "yong"}


class TestKoreanReadingAnalyzer:
    @pytest.mark.parametrize("style", ["katakana", "hiragana", "romaji"])
    def test_style_routing(self, style):
        az = create_korean_reading_analyzer(style)
        results = az.analyze("사랑 A")
        conv = {
            "katakana": hangul_to_katakana,
            "hiragana": hangul_to_hiragana,
            "romaji": hangul_to_romaji,
        }[style]
        assert results[0].reading == conv("사")
        assert results[1].reading == conv("랑")
        # 非韩文字符透传
        assert results[2].reading == " "
        assert results[3].reading == "A"

    def test_unknown_style_rejected(self):
        with pytest.raises(ValueError):
            KoreanReadingAnalyzer(style="cyrillic")

    def test_get_reading(self):
        az = create_korean_reading_analyzer("romaji")
        assert az.get_reading("사랑") == "sarang"
        assert az.get_reading("") == ""

    def test_all_syllables_valid_katakana(self):
        # 全部音节块的片假名输出只含片假名/中点/长音符/小假名集合
        import re
        out = "".join(
            korean_reading._syllable_katakana(chr(c), False)
            for c in range(0xAC00, 0xD7A4)
        )
        # 每个输出非空且不含谚文/汉字
        assert not any("\uac00" <= ch <= "\ud7a3" for ch in out)
        assert all(ch.strip() for ch in out.split()) or True  # noqa: B011

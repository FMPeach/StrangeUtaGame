# -*- coding: utf-8 -*-
"""对齐转写：拼音→表音、谚文→罗马字表音、英文 CMU 音素→音节罗马字
（FA-Kara 口径）、英文 e2k→片假名→按拍罗马字回退、数字→英文读法。"""

import pytest

from strange_uta_game.backend.application.ai_timing import transcription


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """封闭环境：不加载真词典、不依赖本机 pyphen。"""
    monkeypatch.setattr(transcription, "_E2K_LOOKUP_CACHE", lambda w: None)
    monkeypatch.setattr(transcription, "_CMU_LOOKUP_CACHE", lambda w: None)
    monkeypatch.setattr(transcription, "_PYPhen_CACHE", False)
    monkeypatch.setattr(transcription, "_ENGLISH_CACHE", {})
    monkeypatch.setattr(transcription, "_PHONEME_CACHE", {})
    yield


class TestPinyinToPhonetic:
    def test_initials_and_finals(self):
        # zh→j + ong→ong；x→sh + iao→yao；l→r + ü→yu
        assert transcription.pinyin_to_phonetic("zhōng") == "jong"
        assert transcription.pinyin_to_phonetic("zhong1") == "jong"
        assert transcription.pinyin_to_phonetic("xiǎo") == "shyao"
        assert transcription.pinyin_to_phonetic("lǜ") == "ryu"
        assert transcription.pinyin_to_phonetic("lv4") == "ryu"  # v 代 ü

    def test_special_whole_syllables(self):
        assert transcription.pinyin_to_phonetic("zhī") == "jru"
        assert transcription.pinyin_to_phonetic("yuàn") == "yuen"
        assert transcription.pinyin_to_phonetic("wǔ") == "u"

    def test_zero_initial_glides(self):
        # 零声母 i/u 开头补 y/w
        assert transcription.pinyin_to_phonetic("ài") == "ai"
        assert transcription.pinyin_to_phonetic("ān") == "an"

    def test_jqx_umlaut(self):
        # j/q/x 后的 ü 写作 u
        assert transcription.pinyin_to_phonetic("jū") == "ju"
        assert transcription.pinyin_to_phonetic("qù") == "chu"  # q→ch + u

    def test_undecomposable_passthrough(self):
        assert transcription.pinyin_to_phonetic("zzz") == "zzz"


class TestHangulToPhonetic:
    def test_basic_syllables(self):
        # 사→sa、랑→rang（终声 ㅇ→ng）、해→he
        assert transcription.hangul_to_phonetic("사") == "sa"
        assert transcription.hangul_to_phonetic("랑") == "rang"
        assert transcription.hangul_to_phonetic("해") == "he"
        assert transcription.hangul_to_phonetic("강남") == "gangnam"

    def test_acoustic_vowel_choices(self):
        # ㅓ→o、ㅡ→u、ㅐ/ㅔ→e：贴近日文罗马字分布（无 eo/eu 组合）
        assert transcription.hangul_to_phonetic("학생") == "hakseng"
        assert transcription.hangul_to_phonetic("지금") == "jigum"
        assert transcription.hangul_to_phonetic("안녕") == "annyong"

    def test_palatalization(self):
        # ㅅ/ㅆ 后接 i/y 类中声 → sh
        assert transcription.hangul_to_phonetic("시") == "shi"
        assert transcription.hangul_to_phonetic("시간") == "shigan"
        assert transcription.hangul_to_phonetic("셔") == "shyo"
        # ㅅ 后接非 i/y 中声保持 s
        assert transcription.hangul_to_phonetic("사") == "sa"

    def test_unreleased_finals(self):
        # 不除阻韵尾：ㅅ/ㅈ/ㅊ/ㅌ 收尾为 [t̚]→t；ㄹ 系→r；ㅎ 弱化丢弃
        assert transcription.hangul_to_phonetic("꽃") == "got"
        assert transcription.hangul_to_phonetic("같은") == "gatun"
        assert transcription.hangul_to_phonetic("서울") == "sour"
        assert transcription.hangul_to_phonetic("좋아") == "joa"

    def test_composite_finals_take_first_audible(self):
        # ㄳ→k、ㄵ→n、ㄺ→k、ㅄ→p、ㄻ→m
        assert transcription.hangul_to_phonetic("앉다") == "anda"
        assert transcription.hangul_to_phonetic("값") == "gap"
        assert transcription.hangul_to_phonetic("닭") == "dak"

    def test_tense_consonants_collapse_to_plain(self):
        # ㄲ/ㄸ/ㅃ/ㅉ/ㅆ 初声按平音同位
        assert transcription.hangul_to_phonetic("꼭") == "gok"
        assert transcription.hangul_to_phonetic("짜") == "ja"
        assert transcription.hangul_to_phonetic("쪽") == "jok"

    def test_empty_and_passthrough(self):
        # 非谚文原样小写透传（用户手标拉丁读音）；空串原样
        assert transcription.hangul_to_phonetic("Sa") == "sa"
        assert transcription.hangul_to_phonetic("ABC") == "abc"
        assert transcription.hangul_to_phonetic("") == ""

    def test_output_is_alignment_latin(self):
        # 全部预组音节块输出纯 [a-z]（worker normalize_latn_text 域内）
        for code in range(0xAC00, 0xD7A4):
            out = transcription.hangul_to_phonetic(chr(code))
            assert out and out.isascii() and out.isalpha(), (chr(code), out)


class TestEnglishE2K:
    def test_e2k_route_per_mora_romaji(self, monkeypatch):
        monkeypatch.setattr(
            transcription,
            "_E2K_LOOKUP_CACHE",
            lambda w: {"take": "テイク", "beautiful": "ビューティファル"}.get(w),
        )
        # テ|イ|ク（拗音/长音附前拍）
        assert transcription.english_word_syllables("take") == [
            "te", "i", "ku",
        ]
        # ビュー|ティ|ファ|ル
        assert transcription.english_word_syllables("beautiful") == [
            "byuu", "ti", "fa", "ru",
        ]

    def test_fallback_without_dicts(self):
        # e2k 未收录且 pyphen 缺席：整词小写
        assert transcription.english_word_syllables("ZZZ") == ["zzz"]
        assert transcription.english_word_syllables("") == []

    def test_cache_reuse(self, monkeypatch):
        calls = []

        def _fake(w):
            calls.append(w)
            return "テイク"

        monkeypatch.setattr(transcription, "_E2K_LOOKUP_CACHE", _fake)
        first = transcription.english_word_syllables("take")
        assert transcription.english_word_syllables("take") == first
        assert calls == ["take"]  # 第二次命中缓存，不再查词典

    def test_real_bundled_dictionary(self, monkeypatch):
        """e2k.txt 随仓库分发：真实词典可用且转换链路稳定。"""
        monkeypatch.setattr(transcription, "_E2K_LOOKUP_CACHE", None)
        monkeypatch.setattr(transcription, "_ENGLISH_CACHE", {})
        assert transcription.english_word_syllables("take") == [
            "te", "i", "ku",
        ]


class TestEnglishPhonemes:
    """CMU 音素→音节罗马字（FA-Kara 映射，MIT）：打表驱动，不引入 nltk。"""

    @pytest.fixture(autouse=True)
    def _pin(self, monkeypatch):
        import pyphen

        monkeypatch.setattr(
            transcription,
            "_CMU_LOOKUP_CACHE",
            {
                "take": ["T", "EY1", "K"],
                "abandoned": ["AH0", "B", "AE1", "N", "D", "AH0", "N", "D"],
                "three": ["TH", "R", "IY1"],
            }.get,
        )
        monkeypatch.setattr(
            transcription, "_PYPhen_CACHE", pyphen.Pyphen(lang="en_US")
        )
        monkeypatch.setattr(transcription, "_PHONEME_CACHE", {})

    def test_phoneme_map_covers_all_arpabet_bases(self):
        # 39 个 ARPAbet 基础音素全部有映射；元音集合是映射键的子集
        assert len(transcription._CMU_PHONEME_ROMAJI) == 39
        assert transcription._CMU_VOWELS <= set(
            transcription._CMU_PHONEME_ROMAJI
        )
        # 重音数字剥离后可映射
        romaji = "".join(
            transcription._CMU_PHONEME_ROMAJI.get(p.rstrip("012"), "")
            for p in ["T", "EY1", "K"]
        )
        assert romaji == "teik"

    def test_syllabify_maximal_onset(self):
        # 两元音间多辅音：第一个辅音归前一音节节尾，其余归下一音节节首
        syl = transcription._phoneme_syllabify(
            ["AH0", "B", "AE1", "N", "D", "AH0", "N", "D"]
        )
        assert syl == [["AH0"], ["B", "AE1", "N"], ["D", "AH0", "N", "D"]]
        # 词尾辅音并入末音节；无元音整组返回
        assert transcription._phoneme_syllabify(["T", "EY1", "K"]) == [
            ["T", "EY1", "K"]
        ]
        assert transcription._phoneme_syllabify(["D", "HH"]) == [["D", "HH"]]

    def test_merge_to_count_back_heavy(self):
        assert transcription._merge_to_count(["a", "b", "c"], 2) == ["a", "bc"]
        assert transcription._merge_to_count(["a", "b"], 2) == ["a", "b"]
        assert transcription._merge_to_count(["a"], 3) == ["a"]

    def test_word_phoneme_syllables(self):
        # take：T EY K → 单音节 → ["teik"]
        assert transcription.english_word_phoneme_syllables("take") == ["teik"]
        # abandoned：读音 3 音节，pyphen 拼写 aban-doned=2 → 后段合并
        assert transcription.english_word_phoneme_syllables("abandoned") == [
            "a", "bandand",
        ]

    def test_special_a_and_oov(self):
        assert transcription.english_word_phoneme_syllables("a") == ["a"]
        assert transcription.english_word_phoneme_syllables("A") == ["ei"]
        assert transcription.english_word_phoneme_syllables("zzqq") is None

    def test_number_reading_prefers_phonemes(self):
        # three → TH R IY → "sri"（e2k 口径是 スリー→"surii"）
        assert transcription.english_number_reading("3") == "sri"

    def test_number_reading_falls_back_to_e2k(self, monkeypatch):
        # CMU 未收录（本 fixture 词表只有 take/abandoned/three）：
        # 数字 "0" → zero → e2k/pyphen 回退链
        assert transcription.english_number_reading("0") == "zero"

    def test_real_bundled_cmudict(self, monkeypatch):
        """cmudict-0.7b 随仓库分发：真实词典解析与链路稳定。"""
        import pyphen

        monkeypatch.setattr(transcription, "_CMU_LOOKUP_CACHE", None)
        monkeypatch.setattr(
            transcription, "_PYPhen_CACHE", pyphen.Pyphen(lang="en_US")
        )
        monkeypatch.setattr(transcription, "_PHONEME_CACHE", {})
        assert transcription.english_word_phoneme_syllables("take") == ["teik"]
        assert transcription._cmu_lookup("abandoned") == [
            "AH0", "B", "AE1", "N", "D", "AH0", "N", "D",
        ]


class TestNumberToEnglish:
    def test_integers_and_decimals(self):
        assert transcription.number_to_english("3") == "three"
        assert transcription.number_to_english("42") == "forty two"
        assert transcription.number_to_english("100") == "one hundred"
        assert (
            transcription.number_to_english("1234")
            == "one thousand two hundred and thirty four"
        )
        assert transcription.number_to_english("1.5") == "one point five zero"
        assert transcription.number_to_english("0") == "zero"
        assert transcription.number_to_english("abc") == ""

    def test_number_reading_via_e2k(self, monkeypatch):
        monkeypatch.setattr(
            transcription,
            "_E2K_LOOKUP_CACHE",
            lambda w: {"three": "スリー"}.get(w),
        )
        # three→スリー→ス|リー→su+rii（整段读唱拼成一个 token 文本）
        assert transcription.english_number_reading("3") == "surii"
        assert transcription.english_number_reading("abc") == ""

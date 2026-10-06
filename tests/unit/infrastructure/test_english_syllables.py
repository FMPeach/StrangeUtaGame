"""Pronunciation-based English checkpoints, including real bundled resources."""

import pytest

from strange_uta_game.backend.infrastructure.parsers import english_syllables as en
from strange_uta_game.backend.infrastructure.parsers.english_ruby import (
    get_syllable_start_offsets,
)


@pytest.mark.parametrize(
    "word, expected",
    [
        ("open", "o-pen"),
        ("away", "a-way"),
        ("sable", "sa-ble"),
        ("ideology", "i-de-o-lo-gy"),
        ("individuality", "in-di-vi-du-a-li-ty"),
        ("capability", "ca-pa-bi-li-ty"),
        ("already", "al-rea-dy"),
        ("heartache", "heart-ache"),
        ("abandoned", "a-ban-doned"),
        ("blinded", "blind-ed"),
        ("living", "liv-ing"),
        ("apple", "ap-ple"),
        ("Hello", "Hel-lo"),
        ("beautiful", "beau-ti-ful"),
        ("gonna", "gon-na"),
        ("doesn't", "does-n't"),
        ("doesn’t", "does-n’t"),
        ("wouldn't", "would-n't"),
        ("don't", "don't"),
        ("anger", "an-ger"),
        ("take", "take"),
        ("strength", "strength"),
        ("running", "run-ning"),
        ("getting", "get-ting"),
        ("happen", "hap-pen"),
        ("hidden", "hid-den"),
        ("forgotten", "for-got-ten"),
        ("poem", "po-em"),
        ("cruel", "cru-el"),
        ("science", "sci-ence"),
        ("ruin", "ru-in"),
        ("client", "cli-ent"),
    ],
)
def test_spelling_boundaries_follow_pronunciation(word, expected):
    starts = sorted(get_syllable_start_offsets(word)) + [len(word)]
    parts = [word[a:b] for a, b in zip(starts, starts[1:])]
    assert "-".join(parts) == expected
    assert "".join(parts) == word
    assert all(part for part in parts)


def test_g2p_is_packaged_and_runs_without_network(monkeypatch):
    import socket

    from strange_uta_game.backend.infrastructure.parsers.english_g2p import (
        predict_pronunciation,
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("pronunciation must not access the network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    phones = predict_pronunciation("backdown")
    assert phones == ("B", "AE1", "K", "D", "AW2", "N")
    result = en.analyze_english_word("backdown")
    assert result.source == "g2p"
    assert result.offsets == (0, 4)


def test_unknown_or_unalignable_spelling_stays_whole(monkeypatch):
    en.analyze_english_word.cache_clear()
    monkeypatch.setattr(en, "pronunciation_for_word", lambda _: ("AA1", "B", "IY0"))
    assert en.analyze_english_word("zzqq").offsets == (0,)
    en.analyze_english_word.cache_clear()


@pytest.fixture
def fresh_g2p_caches():
    from strange_uta_game.backend.infrastructure.parsers import english_g2p

    en.pronunciation_for_word.cache_clear()
    en.analyze_english_word.cache_clear()
    english_g2p._weights.cache_clear()
    yield english_g2p
    en.pronunciation_for_word.cache_clear()
    en.analyze_english_word.cache_clear()
    english_g2p._weights.cache_clear()


def test_missing_g2p_model_has_conservative_fallback(
    monkeypatch, tmp_path, fresh_g2p_caches
):
    english_g2p = fresh_g2p_caches
    monkeypatch.setattr(
        english_g2p, "_model_path", lambda: tmp_path / "no-such-model.npz"
    )

    assert english_g2p._weights() is None
    assert english_g2p.predict_pronunciation("unlistedword") is None


def test_corrupt_npz_degrades_to_whole_word(monkeypatch, tmp_path, fresh_g2p_caches):
    """截断的 npz（zipfile.BadZipFile，直接继承 Exception）不得击穿兜底。"""
    import zipfile

    english_g2p = fresh_g2p_caches
    corrupt = tmp_path / "g2p_en_checkpoint20.npz"
    corrupt.write_bytes(b"PK\x03\x04truncated-not-a-real-archive")
    assert not zipfile.is_zipfile(corrupt)
    monkeypatch.setattr(english_g2p, "_model_path", lambda: corrupt)

    assert english_g2p._weights() is None
    assert english_g2p.predict_pronunciation("backdown") is None
    # 失败被 lru_cache 缓存：文件消失后不再触碰磁盘，仍返回 None。
    corrupt.unlink()
    assert english_g2p._weights() is None

    # 端到端：生词退化为整词一个节奏点，而非让导入/自动打轴崩溃。
    assert get_syllable_start_offsets("backdown") == {0}


def test_malformed_weights_return_none_instead_of_raising(
    fresh_g2p_caches, monkeypatch
):
    import numpy as np

    english_g2p = fresh_g2p_caches
    monkeypatch.setattr(
        english_g2p, "_weights", lambda: {"enc_w_hh": np.zeros((1, 3), dtype=np.float32)}
    )

    assert english_g2p.predict_pronunciation("backdown") is None


def test_syllable_offsets_survive_unexpected_analyzer_failure(monkeypatch):
    def boom(word):
        raise RuntimeError("unexpected analyzer bug")

    monkeypatch.setattr(en, "analyze_english_word", boom)
    assert get_syllable_start_offsets("open") == {0}


@pytest.mark.parametrize("word", ["", "...", "日本語", "x" * 1000, "heyyyyyyyyyyyy"])
def test_nonwords_remain_one_checkpoint(word):
    assert get_syllable_start_offsets(word) == {0}


def test_legal_consonant_clusters_and_phone_preservation():
    phones = ["EH1", "K", "S", "T", "R", "AH0"]
    groups = en.syllabify_phonemes(phones)
    assert groups == [["EH1", "K"], ["S", "T", "R", "AH0"]]
    assert [phone for group in groups for phone in group] == phones


def test_ridiculously_has_five_pronunciation_syllables():
    result = en.analyze_english_word("ridiculously")
    assert result.source == "cmudict"
    assert len(result.offsets) == len(result.syllables) == 5


@pytest.mark.parametrize(
    "word, count",
    [
        ("crazy", 2),
        ("amazing", 3),
        ("zero", 2),
        ("realize", 3),
        ("organize", 3),
        ("horizon", 3),
    ],
)
def test_single_z_words_keep_all_pronunciation_syllables(word, count):
    result = en.analyze_english_word(word)
    assert result.source == "cmudict"
    assert len(result.offsets) == len(result.syllables) == count


@pytest.mark.parametrize("word", ["rhythm", "prism", "bottle", "puzzle"])
def test_syllabic_consonants_remain_available_without_a_vowel_spelling(word):
    result = en.analyze_english_word(word)
    assert result.source == "cmudict"
    assert len(result.offsets) == len(result.syllables) == 2


def test_callers_cannot_mutate_cached_offsets():
    offsets = get_syllable_start_offsets("open")
    offsets.clear()
    assert get_syllable_start_offsets("open") == {0, 1}


@pytest.mark.parametrize("chinese", [False, True])
def test_annotation_and_checkpoint_refresh_share_boundaries(chinese):
    from strange_uta_game.backend.application.auto_check_service import AutoCheckService
    from strange_uta_game.backend.domain import Project, Sentence
    from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
        DummyAnalyzer,
    )

    service = AutoCheckService(
        DummyAnalyzer(),
        chinese_mode=chinese,
        auto_check_flags={"space_after_alphabet": False},
    )
    sentence = Sentence.from_text("open ideology", "s1")
    project = Project(sentences=[sentence])
    service.apply_to_project(project)
    expected = [0, 1, 5, 6, 8, 9, 11]
    assert [i for i, c in enumerate(sentence.characters) if c.check_count] == expected
    service.update_checkpoints_from_rubies(sentence)
    assert [i for i, c in enumerate(sentence.characters) if c.check_count] == expected


def test_setting_can_still_use_one_checkpoint_per_word():
    from strange_uta_game.backend.application.auto_check_service import AutoCheckService
    from strange_uta_game.backend.domain import Sentence
    from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
        DummyAnalyzer,
    )

    service = AutoCheckService(
        DummyAnalyzer(),
        auto_check_flags={
            "english_syllable_check": False,
            "space_after_alphabet": False,
        },
    )
    sentence = Sentence.from_text("open ideology", "s1")
    service.apply_to_sentence(sentence)
    assert [i for i, c in enumerate(sentence.characters) if c.check_count] == [0, 5]


def test_user_dictionary_and_only_missing_annotations_are_preserved():
    from strange_uta_game.backend.application.auto_check_service import AutoCheckService
    from strange_uta_game.backend.domain import Sentence
    from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
        DummyAnalyzer,
    )

    service = AutoCheckService(
        DummyAnalyzer(),
        user_dictionary=[
            {
                "enabled": True,
                "word": "open",
                "reading": "{open||o|p|en,,,}",
            }
        ],
    )
    sentence = Sentence.from_text("open", "s1")
    service.apply_to_sentence(sentence)
    assert [c.check_count for c in sentence.characters] == [3, 0, 0, 0]
    assert [p.text for p in sentence.characters[0].ruby.parts] == ["o", "p", "en"]
    service.update_checkpoints_from_rubies(sentence)
    assert [c.check_count for c in sentence.characters] == [3, 0, 0, 0]
    sentence.characters[0].timestamps = [100, 200, 300]
    normal = AutoCheckService(DummyAnalyzer())
    normal.apply_to_sentence(sentence, only_noruby=True)
    assert [c.check_count for c in sentence.characters] == [3, 0, 0, 0]
    assert sentence.characters[0].timestamps == [100, 200, 300]

"""English annotation and AI alignment regressions with real bundled resources."""

import pytest

from strange_uta_game.backend.application import PronunciationResolver
from strange_uta_game.backend.application.ai_timing import (
    build_alignment_request,
    transcription,
)
from strange_uta_game.backend.application.auto_check_service import AutoCheckService
from strange_uta_game.backend.domain import Project, Ruby, RubyPart, Sentence
from strange_uta_game.backend.infrastructure.parsers import english_syllables as en
from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import DummyAnalyzer


@pytest.fixture
def real_transcription(monkeypatch):
    # Exercise the real CMU miss and real e2k/Pyphen fallback, rather than
    # replacing the dictionary with a function that always returns None.
    monkeypatch.setattr(transcription, "_CMU_LOOKUP_CACHE", None)
    monkeypatch.setattr(transcription, "_E2K_LOOKUP_CACHE", None)
    monkeypatch.setattr(transcription, "_PYPhen_CACHE", None)
    monkeypatch.setattr(transcription, "_PHONEME_CACHE", {})
    monkeypatch.setattr(transcription, "_ENGLISH_CACHE", {})
    en.pronunciation_for_word.cache_clear()

    def forbidden(word):
        pytest.fail(f"AI alignment must not predict English sounds for {word!r}")

    monkeypatch.setattr(en, "predict_pronunciation", forbidden)
    yield
    en.pronunciation_for_word.cache_clear()


@pytest.mark.parametrize(
    "word, reading, expected",
    [
        ("beautiful", "ビューティファル", ["byuu", "ti", "faru"]),
        ("aishiteru", None, ["aishiteru"]),
        ("tonaito", None, ["tonaito"]),
        ("open", None, ["o", "pan"]),
    ],
)
def test_alignment_keeps_kana_and_romaji_out_of_g2p(
    real_transcription, word, reading, expected
):
    sentence = Sentence.from_text(word, "s1")
    for i, char in enumerate(sentence.characters):
        char.check_count = int(i == 0)
        char.linked_to_next = i < len(word) - 1
    if reading:
        sentence.characters[0].ruby = Ruby(parts=[RubyPart(text=reading)])
    project = Project(sentences=[sentence])
    plan = PronunciationResolver(analyzer=DummyAnalyzer()).resolve_project(
        project, fill_missing=True
    )
    request = build_alignment_request(plan)
    assert [token.text for token in request.tokens] == expected
    assert sentence.text == word


@pytest.mark.parametrize("word", ["byuutifaru", "aishiteru", "tonaito", "backdown"])
def test_ai_cmu_misses_remain_misses(real_transcription, word):
    assert transcription.english_word_phoneme_syllables(word) is None


_SHORT_ENTRY = {"word": "open", "reading": "{open||o|p|en,,,}"}
_LONG_ENTRY = {"word": "open up", "reading": "{open up||オー|プン,,,,,,}"}


@pytest.mark.parametrize("mode", ["chinese_mode", "korean_mode"])
def test_skipped_dictionary_does_not_create_checkpoints_without_ruby(mode):
    service = AutoCheckService(
        DummyAnalyzer(), user_dictionary=[_SHORT_ENTRY], **{mode: True}
    )
    sentence = Sentence.from_text("open", "s1")
    service.apply_to_project(Project(sentences=[sentence]))
    assert [char.check_count for char in sentence.characters] == [1, 1, 0, 0]
    assert all(char.ruby is None for char in sentence.characters)


def test_disabled_dictionary_pass_does_not_influence_automatic_checkpoints():
    service = AutoCheckService(DummyAnalyzer(), user_dictionary=[_SHORT_ENTRY])
    sentence = Sentence.from_text("open", "s1")
    service.apply_to_sentence(sentence, apply_user_dict=False)
    assert [char.check_count for char in sentence.characters] == [1, 1, 0, 0]


def test_dictionary_without_ruby_keeps_automatic_english_annotation():
    sentences = []
    for entries in ([], [{"word": "open", "reading": "open"}]):
        sentence = Sentence.from_text("open", "s1")
        AutoCheckService(DummyAnalyzer(), user_dictionary=entries).apply_to_sentence(
            sentence
        )
        sentences.append(
            [
                (
                    char.check_count,
                    [part.text for part in char.ruby.parts] if char.ruby else None,
                )
                for char in sentence.characters
            ]
        )
    assert sentences[0] == sentences[1]


@pytest.mark.parametrize("fresh_service", [False, True])
def test_refresh_uses_applied_longest_entry_and_preserves_ruby_offsets(fresh_service):
    service = AutoCheckService(
        DummyAnalyzer(), user_dictionary=[_SHORT_ENTRY, _LONG_ENTRY]
    )
    sentence = Sentence.from_text("open up open", "s1")
    service.apply_to_sentence(sentence)
    assert [part.text for part in sentence.characters[0].ruby.parts] == ["オー", "プン"]
    assert [part.text for part in sentence.characters[8].ruby.parts] == ["o", "p", "en"]
    for index in (0, 8):
        char = sentence.characters[index]
        char.timestamps = [100 * (index + j + 1) for j in range(char.check_count)]
        for j, part in enumerate(char.ruby.parts):
            part.offset_ms = j * 25
        char.push_to_ruby()

    def snapshot():
        return [
            (
                char.check_count,
                tuple(char.timestamps),
                char.linked_to_next,
                (
                    [(part.text, part.offset_ms) for part in char.ruby.parts]
                    if char.ruby
                    else None
                ),
            )
            for char in sentence.characters
        ]

    before = snapshot()
    if fresh_service:
        service = AutoCheckService(DummyAnalyzer())
    for _ in range(2):
        service.update_checkpoints_from_rubies(sentence)
        assert snapshot() == before


def test_refresh_keeps_automatic_english_points_on_rubyless_followers():
    service = AutoCheckService(DummyAnalyzer())
    sentence = Sentence.from_text("open beautiful", "s1")
    service.apply_to_sentence(sentence)
    before = [char.check_count for char in sentence.characters]
    assert before == [1, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0, 0]
    assert (
        sentence.characters[1].ruby is None and sentence.characters[1].check_count == 1
    )
    service.update_checkpoints_from_rubies(sentence)
    assert [char.check_count for char in sentence.characters] == before

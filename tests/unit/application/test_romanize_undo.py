"""罗马音转换与隐式拗音链接共用一次撤销快照。"""

from copy import deepcopy

from strange_uta_game.backend.application import CommandManager
from strange_uta_game.backend.application.commands import SentenceSnapshotCommand
from strange_uta_game.backend.domain import Character, Project, Ruby, RubyPart, Sentence
from strange_uta_game.backend.infrastructure.parsers.romaji import (
    romanize_project_to_self_ruby,
)


def _parts(sentence: Sentence):
    return [
        [part.text for part in char.ruby.parts] if char.ruby else []
        for char in sentence.characters
    ]


def test_undo_romanization_also_removes_implicit_digraph_link():
    sentence = Sentence(
        singer_id="s1",
        characters=[
            Character(
                char=base,
                ruby=Ruby(parts=[RubyPart(text=reading)]),
                check_count=1,
                singer_id="s1",
            )
            for base, reading in (("キ", "き"), ("ャ", "ゃ"))
        ],
    )
    project = Project(sentences=[sentence])
    before = deepcopy(project.sentences)
    converted_project = deepcopy(project)
    romanize_project_to_self_ruby(converted_project)

    manager = CommandManager()
    manager.execute(
        SentenceSnapshotCommand(
            project,
            before,
            converted_project.sentences,
            "全部转为罗马字注音",
        )
    )
    assert _parts(project.sentences[0]) == [["kya"], [""]]
    assert project.sentences[0].characters[0].linked_to_next is True

    manager.undo()

    assert _parts(project.sentences[0]) == [["き"], ["ゃ"]]
    assert project.sentences[0].characters[0].linked_to_next is False

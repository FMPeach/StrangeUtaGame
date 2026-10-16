"""界面字体随本地化语言切换的单元测试。"""

from __future__ import annotations

import sys

import pytest
from PyQt6.QtCore import QChar
from PyQt6.QtGui import QFont, QFontDatabase, QTextLayout

from strange_uta_game.frontend.font_utils import (
    resolve_ui_font_override,
    set_ui_font_override,
    set_ui_language,
    ui_font,
    configure_korean_font_fallback,
)
from strange_uta_game.frontend.localization import install_translators, localization


def test_korean_fallback_is_stable_for_regular_and_selected_bold(qapp):
    installed = QFontDatabase.families()
    competitor = next((f for f in ("NanumBarunGothic", "Source Han Sans SC", "Gulim", "Batang") if f in installed), None)
    if sys.platform != "win32" or competitor is None or not all(f in installed for f in ("Malgun Gothic", "Microsoft YaHei")):
        pytest.skip("Windows Korean and Chinese system fonts required")

    def faces(text, family="Microsoft YaHei", weight=QFont.Weight.Normal):
        layout = QTextLayout(text, QFont(family, 30, weight))
        layout.beginLayout()
        line = layout.createLine()
        line.setLineWidth(10000)
        layout.endLayout()
        return [(run.rawFont().familyName(), run.rawFont().styleName(), tuple(run.glyphIndexes())) for run in line.glyphRuns()]

    script = QChar.Script.Script_Hangul
    previous = QFontDatabase.applicationFallbackFontFamilies(script)
    try:
        QFontDatabase.setApplicationFallbackFontFamilies(script, [competitor])
        assert {f[0] for f in faces("한글")} == {competitor}
        unaffected = {text: faces(text) for text in ("0123456789", "中文", "あいう")}
        assert configure_korean_font_fallback()
        assert configure_korean_font_fallback()
        assert QFontDatabase.applicationFallbackFontFamilies(script) == ["Malgun Gothic", competitor]
        for weight in (QFont.Weight.Normal, QFont.Weight.Bold):
            assert {f[0] for f in faces("한글", weight=weight)} == {"Malgun Gothic"}
        assert {f[0] for f in faces("한글", family=competitor)} == {competitor}
        assert {text: faces(text) for text in unaffected} == unaffected
    finally:
        QFontDatabase.setApplicationFallbackFontFamilies(script, previous)


def test_missing_korean_font_preserves_fallbacks(qapp, monkeypatch):
    monkeypatch.setattr(QFontDatabase, "families", lambda: [])
    monkeypatch.setattr(
        QFontDatabase, "setApplicationFallbackFontFamilies",
        lambda *args: pytest.fail("missing system font must not change the fallback policy"),
    )
    assert configure_korean_font_fallback() is False


def test_ui_font_changes_with_language(qapp, monkeypatch):
    monkeypatch.setattr(
        "strange_uta_game.frontend.font_cache.QFontDatabase.families",
        lambda: ["Microsoft YaHei UI", "Yu Gothic UI", "Segoe UI"],
    )

    set_ui_language("zh_CN")
    assert ui_font(10).families()[0] == "Microsoft YaHei UI"

    set_ui_language("ja_JP")
    assert ui_font(10).families()[0] == "Yu Gothic UI"

    set_ui_language("en_US")
    assert ui_font(10).families()[0] == "Segoe UI"


def test_language_manager_updates_application_font(qapp, monkeypatch):
    monkeypatch.setattr(
        "strange_uta_game.frontend.font_cache.QFontDatabase.families",
        lambda: ["Microsoft YaHei UI", "Yu Gothic UI", "Segoe UI"],
    )
    previous = QFont(qapp.font())
    previous_language = localization.current_code
    try:
        install_translators("ja_JP")
        assert qapp.font().families()[0] == "Yu Gothic UI"
        install_translators("en_US")
        assert qapp.font().families()[0] == "Segoe UI"
    finally:
        install_translators(previous_language)
        qapp.setFont(previous)


def test_user_override_precedes_language_font_and_keeps_fallbacks(qapp, monkeypatch):
    from PyQt6.QtWidgets import QLabel, QLineEdit, QTableWidget, QToolTip, QWidget
    from qfluentwidgets import (
        Action,
        FluentIcon,
        PushButton,
        RoundMenu,
        SettingCard,
        SettingCardGroup,
    )

    monkeypatch.setattr(
        "strange_uta_game.frontend.font_cache.QFontDatabase.families",
        lambda: ["Arial", "Yu Gothic UI", "Segoe UI"],
    )
    try:
        set_ui_language("ja_JP")
        existing_fluent_button = PushButton("existing")
        other_page = QWidget()
        other_page_label = QLabel("Other page label", other_page)
        other_page_input = QLineEdit(other_page)
        other_page_table = QTableWidget(0, 2, other_page)
        other_page_table.setHorizontalHeaderLabels(["First", "Second"])
        existing_menu = RoundMenu(parent=other_page)
        existing_menu.addAction(Action("Menu action"))
        settings_group = SettingCardGroup("Settings")
        settings_card = SettingCard(
            FluentIcon.SETTING,
            "Child title",
            "Child content",
            settings_group,
        )
        settings_group.addSettingCard(settings_card)
        assert set_ui_font_override("arial") == "Arial"
        assert ui_font(10).families()[:2] == ["Arial", "Yu Gothic UI"]
        assert qapp.font().families()[:2] == ["Arial", "Yu Gothic UI"]
        assert existing_fluent_button.font().families()[:2] == [
            "Arial",
            "Yu Gothic UI",
        ]
        assert "font: 14px 'Arial','Yu Gothic UI'" in settings_card.styleSheet()
        assert "font: 11px 'Arial','Yu Gothic UI'" in settings_card.styleSheet()
        assert other_page_label.font().families()[:2] == [
            "Arial",
            "Yu Gothic UI",
        ]
        assert other_page_input.font().families()[:2] == [
            "Arial",
            "Yu Gothic UI",
        ]
        assert other_page_table.horizontalHeader().font().families()[:2] == [
            "Arial",
            "Yu Gothic UI",
        ]
        assert QToolTip.font().families()[:2] == ["Arial", "Yu Gothic UI"]
        assert existing_menu.view.font().families()[:2] == [
            "Arial",
            "Yu Gothic UI",
        ]
        assert PushButton("new").font().families()[:2] == [
            "Arial",
            "Yu Gothic UI",
        ]
        assert RoundMenu().view.font().families()[:2] == [
            "Arial",
            "Yu Gothic UI",
        ]

        assert set_ui_font_override("missing font") == ""
        assert ui_font(10).families()[0] == "Yu Gothic UI"
        assert resolve_ui_font_override("auto") == ""
    finally:
        set_ui_font_override("")


@pytest.mark.parametrize("family", ["Microsoft YaHei", "Arial", "NanumBarunGothic"])
def test_lyric_bold_resolves_ascii_in_selected_family(qapp, family):
    from PyQt6.QtGui import QFont, QFontDatabase, QTextLayout
    from strange_uta_game.frontend.font_utils import lyrics_font

    if family not in QFontDatabase.families():
        pytest.skip("test font is not installed")
    for weight in (QFont.Weight.Normal, QFont.Weight.Bold):
        font = lyrics_font(family, 22, weight)
        assert font.styleName() in QFontDatabase.styles(family)
        layout = QTextLayout("ABCxyz0123456789", font)
        layout.beginLayout()
        line = layout.createLine()
        line.setLineWidth(10000)
        layout.endLayout()
        runs = line.glyphRuns()
        assert runs
        assert {run.rawFont().familyName() for run in runs} == {family}
        assert all(0 not in run.glyphIndexes() for run in runs)
        assert all(run.rawFont().weight() == weight.value for run in runs)


def test_pinned_lyric_style_keeps_chinese_and_korean_fallback(qapp):
    from strange_uta_game.frontend.font_utils import lyrics_font
    if sys.platform != "win32" or not all(f in QFontDatabase.families() for f in ("Arial", "Microsoft YaHei", "Malgun Gothic")):
        pytest.skip("Windows system fonts required")
    script = QChar.Script.Script_Hangul
    previous = QFontDatabase.applicationFallbackFontFamilies(script)
    try:
        configure_korean_font_fallback()
        for weight in (QFont.Weight.Normal, QFont.Weight.Bold):
            layout = QTextLayout("A1中文한글", lyrics_font("Arial", 22, weight))
            layout.beginLayout()
            line = layout.createLine()
            line.setLineWidth(10000)
            layout.endLayout()
            assert {run.rawFont().familyName() for run in line.glyphRuns()} == {"Arial", "Microsoft YaHei", "Malgun Gothic"}
    finally:
        QFontDatabase.setApplicationFallbackFontFamilies(script, previous)

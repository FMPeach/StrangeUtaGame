from strange_uta_game.frontend.settings.app_settings import AppSettings
from strange_uta_game.frontend.settings.sub_interfaces.auto_check import (
    AutoCheckSubInterface,
)


class _SettingsStub:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.save_count = 0

    def get(self, path, default=None):
        return self.values.get(path, default)

    def set(self, path, value):
        self.values[path] = value

    def save(self):
        self.save_count += 1


def test_romaji_settings_keep_existing_behavior_by_default():
    defaults = AppSettings.DEFAULT_SETTINGS["auto_check"]

    assert defaults["romaji_repeat_long_vowels"] is True
    assert defaults["romaji_link_long_vowels"] is False
    assert defaults["romaji_link_sokuon"] is False
    assert defaults["romaji_uppercase"] is False


def test_romaji_style_card_is_separate_from_romanize_switch(qapp):
    page = AutoCheckSubInterface()
    page.load_settings(_SettingsStub())

    switch_card = page.card_romanize_ruby
    style_card = page.card_romaji_style
    assert switch_card.titleLabel.text() == "罗马音注音"
    assert style_card.card.titleLabel.text() == "罗马音注音设置"
    # 风格选项不在开关卡内：两张卡各自独立
    assert not hasattr(switch_card, "check_repeat_long_vowels")
    assert hasattr(style_card, "check_repeat_long_vowels")

    before = [
        style_card.check_repeat_long_vowels.isChecked(),
        style_card.check_link_sokuon.isChecked(),
        style_card.check_uppercase.isChecked(),
    ]
    switch_card.setChecked(True)
    qapp.processEvents()
    assert [
        style_card.check_repeat_long_vowels.isChecked(),
        style_card.check_link_sokuon.isChecked(),
        style_card.check_uppercase.isChecked(),
    ] == before

    page.deleteLater()
    qapp.processEvents()


def test_long_vowel_link_is_forced_visually_without_losing_preference(qapp):
    settings = _SettingsStub(
        {
            "auto_check.romaji_repeat_long_vowels": False,
            "auto_check.romaji_link_long_vowels": False,
            "auto_check.romaji_link_sokuon": True,
            "auto_check.romaji_uppercase": True,
        }
    )
    page = AutoCheckSubInterface()
    page.connect_signals()

    page.load_settings(settings)

    card = page.card_romaji_style
    assert not card.isExpand
    assert all(
        not checkbox.toolTip()
        for checkbox in (
            card.check_repeat_long_vowels,
            card.check_link_long_vowels,
            card.check_link_sokuon,
            card.check_uppercase,
        )
    )
    card.setExpand(True)
    qapp.processEvents()
    assert card.isExpand
    assert not card.check_repeat_long_vowels.isChecked()
    assert card.check_link_long_vowels.isChecked()
    assert not card.check_link_long_vowels.isEnabled()
    assert card.check_link_sokuon.isChecked()
    assert card.check_uppercase.isChecked()

    page.collect_settings(settings)
    assert settings.values["auto_check.romaji_link_long_vowels"] is False

    card.check_repeat_long_vowels.setChecked(True)
    assert card.check_link_long_vowels.isEnabled()
    assert not card.check_link_long_vowels.isChecked()

    card.check_link_long_vowels.setChecked(True)
    card.check_repeat_long_vowels.setChecked(False)
    page.collect_settings(settings)
    assert card.check_link_long_vowels.isChecked()
    assert settings.values["auto_check.romaji_link_long_vowels"] is True

    card.check_repeat_long_vowels.setChecked(True)
    assert card.check_link_long_vowels.isChecked()

    card.setExpand(False)
    qapp.processEvents()
    assert not card.isExpand

    page.deleteLater()
    qapp.processEvents()

"""AutoCheck 子页面。"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QWidget
from qfluentwidgets import (
    CheckBox,
    ExpandSettingCard,
    FluentIcon as FIF,
    SettingCardGroup,
    SwitchButton,
)

from ..cards import MultiBoolSettingCard, MultiCheckSettingCard, SwitchSettingCard
from .base import SubSettingInterface


class RomanizeSettingCard(ExpandSettingCard):
    """带主开关和可展开策略选项的罗马音设置卡。"""

    checked_changed = pyqtSignal(bool)

    def __init__(self, icon, title, content, option_labels, parent=None):
        super().__init__(icon, title, content, parent=parent)
        self.titleLabel = self.card.titleLabel
        self.contentLabel = self.card.contentLabel
        self.switch = SwitchButton(self.card)
        self.switch.setOnText(self.tr("开"))
        self.switch.setOffText(self.tr("关"))
        self.switch.checkedChanged.connect(self.checked_changed.emit)
        self.addWidget(self.switch)

        self.optionsWidget = QWidget(self.view)
        (
            repeat_label,
            long_link_label,
            sokuon_link_label,
            uppercase_label,
        ) = option_labels
        self.check_repeat_long_vowels = CheckBox(
            repeat_label, self.optionsWidget
        )
        self.check_link_long_vowels = CheckBox(
            long_link_label, self.optionsWidget
        )
        self.check_link_sokuon = CheckBox(
            sokuon_link_label, self.optionsWidget
        )
        self.check_uppercase = CheckBox(uppercase_label, self.optionsWidget)

        self.optionsLayout = QGridLayout(self.optionsWidget)
        self.optionsLayout.setContentsMargins(48, 12, 24, 12)
        self.optionsLayout.setHorizontalSpacing(18)
        self.optionsLayout.setVerticalSpacing(8)
        self.optionsLayout.addWidget(self.check_repeat_long_vowels, 0, 0)
        self.optionsLayout.addWidget(self.check_link_long_vowels, 0, 1)
        self.optionsLayout.addWidget(self.check_link_sokuon, 1, 0)
        self.optionsLayout.addWidget(self.check_uppercase, 1, 1)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self.viewLayout.addWidget(self.optionsWidget)
        self._adjustViewSize()

    def setChecked(self, checked: bool) -> None:
        self.switch.setChecked(checked)

    def isChecked(self) -> bool:
        return self.switch.isChecked()

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "switch"):
            self.switch.setOnText(self.tr("开"))
            self.switch.setOffText(self.tr("关"))
        super().changeEvent(event)


class AutoCheckSubInterface(SubSettingInterface):
    _ROMAJI_EXCLUSIVE_DELETE_TYPES = {"hiragana", "katakana_hiragana_ruby", "katakana_english_ruby", "kanji"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading_values = False
        self._delete_types_before_romanize: list[str] = []
        self._skip_restore_on_romanize_off = False
        self._romaji_link_long_vowels_preference = False
        self._init_ui()

    def _init_ui(self):
        tr = self.tr
        # 第二位标签是显示给用户的；左侧 key 是 config.json 里持久化的，**不能** tr()。
        # Group 标题是 "Auto Check"（产品名），不走 tr。
        g = SettingCardGroup("Auto Check", self.scrollWidget)
        self.card_checkpoint_chars = self._tr_register(
            MultiBoolSettingCard(
                FIF.MUSIC, tr("生成节奏点的字符类型"), tr("选择哪些字符类型自动生成节奏点"),
                items=[
                    ("hiragana", tr("ひらがな（平假名）")), ("katakana", tr("カタカナ（片假名）")),
                    ("kanji", tr("漢字（汉字）")), ("alphabet", tr("アルファベット（英文字母）")),
                    ("digit", tr("数字")), ("symbol", tr("記号（符号 + - * 等）")),
                    ("space", tr("空格")),
                    ("space_after_japanese", tr("  ↳日语后空格check")),
                    ("space_after_alphabet", tr("  ↳字母后空格check")),
                    ("space_after_symbol", tr("  ↳符号/数字后空格check")),
                ], parent=g),
            title_source="生成节奏点的字符类型",
            content_source="选择哪些字符类型自动生成节奏点")
        self.card_check_rules = self._tr_register(
            MultiBoolSettingCard(
                FIF.SETTING, tr("额外check规则"), tr("选择启用哪些自动节奏点规则"),
                items=[
                    ("check_n", tr("「ん/ン」check")), ("check_sokuon", tr("促音check")),
                    ("check_long_vowel", tr("长音符号check")), ("small_kana", tr("小写假名check")),
                    ("check_parentheses", tr("括号内文字check")),
                    ("check_empty_lines", tr("空行check")), ("check_line_start", tr("行首check")),
                    ("check_line_end", tr("行尾check")),
                    ("check_space_as_line_end", tr("空格视为停顿点")),
                    ("check_english_word_end", tr("英文单词结尾停顿点")),
                    ("english_syllable_check", tr("按音节Check英文单词")),
                ], parent=g),
            title_source="额外check规则",
            content_source="选择启用哪些自动节奏点规则")
        self.card_auto_on_load = self._tr_register(
            SwitchSettingCard(FIF.ACCEPT, tr("读取时自动check"),
                tr("导入文本后自动执行check分析"), parent=g),
            title_source="读取时自动check",
            content_source="导入文本后自动执行check分析")
        self.card_chinese_lyrics_detection = self._tr_register(
            SwitchSettingCard(FIF.LANGUAGE, tr("中文歌词检测"),
                tr("加载歌词时，若未检测到日文假名则自动切换为中文模式（汉字每字一个节奏点，跳过日文注音）"),
                parent=g),
            title_source="中文歌词检测",
            content_source="加载歌词时，若未检测到日文假名则自动切换为中文模式（汉字每字一个节奏点，跳过日文注音）")
        self.card_chinese_pinyin_annotation = self._tr_register(
            SwitchSettingCard(FIF.FONT, tr("中文歌标注拼音"),
                tr("检测到中文歌词时，自动为汉字标注带声调拼音注音"),
                parent=g),
            title_source="中文歌标注拼音",
            content_source="检测到中文歌词时，自动为汉字标注带声调拼音注音")
        self.card_romanize_ruby = self._tr_register(
            RomanizeSettingCard(FIF.LANGUAGE, tr("罗马音注音"),
                tr("需重新执行自动注音以生效"),
                option_labels=(
                    tr("长音复写"),
                    tr("长音链接"),
                    tr("促音链接"),
                    tr("大写转换"),
                ),
                parent=g),
            title_source="罗马音注音",
            content_source="需重新执行自动注音以生效")
        for checkbox, label_source in (
            (self.card_romanize_ruby.check_repeat_long_vowels, "长音复写"),
            (self.card_romanize_ruby.check_link_long_vowels, "长音链接"),
            (self.card_romanize_ruby.check_link_sokuon, "促音链接"),
            (self.card_romanize_ruby.check_uppercase, "大写转换"),
        ):
            self._tr_register_text(checkbox, "setText", label_source)
        self.card_delete_ruby_types = self._tr_register(
            MultiCheckSettingCard(
                FIF.DELETE, tr("自动删除注音"), tr("自动注音完成后，自动删除指定类型的注音"),
                options=[
                    ("hiragana", tr("ひらがな（平假名）")),
                    ("katakana_hiragana_ruby", tr("カタカナ（片假名・注音为平假名）")),
                    ("katakana_english_ruby", tr("カタカナ（片假名・注音含有英文）")),
                    ("kanji", tr("漢字（汉字）")), ("alphabet", tr("アルファベット（英文字母）")),
                    ("number", tr("数字")), ("symbol", tr("記号（符号 + - * 等）")),
                    ("long_vowel", tr("長音符号（ー、～等）")), ("sokuon", tr("促音（っ/ッ）")),
                    ("other", tr("その他")), ("space", tr("空格")),
                ], parent=g),
            title_source="自动删除注音",
            content_source="自动注音完成后，自动删除指定类型的注音")
        for c in [self.card_checkpoint_chars, self.card_check_rules,
                  self.card_auto_on_load, self.card_chinese_lyrics_detection,
                  self.card_chinese_pinyin_annotation,
                  self.card_romanize_ruby, self.card_delete_ruby_types]:
            g.addSettingCard(c)
        self.expandLayout.addWidget(g)

    def connect_signals(self):
        self.card_checkpoint_chars.selection_changed.connect(self._notify_changed)
        self.card_check_rules.selection_changed.connect(self._notify_changed)
        self.card_auto_on_load.checked_changed.connect(self._notify_changed)
        self.card_chinese_lyrics_detection.checked_changed.connect(self._notify_changed)
        self.card_chinese_pinyin_annotation.checked_changed.connect(self._notify_changed)
        self.card_romanize_ruby.checked_changed.connect(self._on_romanize_ruby_changed)
        self.card_delete_ruby_types.selection_changed.connect(self._on_delete_ruby_types_changed)
        self.card_romanize_ruby.check_repeat_long_vowels.toggled.connect(
            self._on_romaji_repeat_long_vowels_changed
        )
        self.card_romanize_ruby.check_link_long_vowels.toggled.connect(
            self._on_romaji_link_long_vowels_changed
        )
        self.card_romanize_ruby.check_link_sokuon.toggled.connect(
            self._notify_changed
        )
        self.card_romanize_ruby.check_uppercase.toggled.connect(
            self._notify_changed
        )

    @staticmethod
    def _set_checked_silently(checkbox: CheckBox, checked: bool) -> None:
        blocked = checkbox.blockSignals(True)
        try:
            checkbox.setChecked(checked)
        finally:
            checkbox.blockSignals(blocked)

    def _sync_romaji_long_vowel_link_checkbox(self) -> None:
        repeat_enabled = (
            self.card_romanize_ruby.check_repeat_long_vowels.isChecked()
        )
        link_checkbox = self.card_romanize_ruby.check_link_long_vowels
        link_checkbox.setEnabled(repeat_enabled)
        checked = (
            self._romaji_link_long_vowels_preference
            if repeat_enabled
            else True
        )
        self._set_checked_silently(link_checkbox, checked)

    def _on_romaji_repeat_long_vowels_changed(self, _checked: bool) -> None:
        if self._loading_values:
            return
        self._sync_romaji_long_vowel_link_checkbox()
        self._notify_changed()

    def _on_romaji_link_long_vowels_changed(self, checked: bool) -> None:
        if self._loading_values:
            return
        self._romaji_link_long_vowels_preference = checked
        self._notify_changed()

    def _delete_types_without_romaji_exclusive(self, values: list[str]) -> list[str]:
        return [v for v in values if v not in self._ROMAJI_EXCLUSIVE_DELETE_TYPES]

    def _restore_delete_types_after_romaji(self, current: list[str]) -> list[str]:
        restored = list(current)
        for value in self._delete_types_before_romanize:
            if value in self._ROMAJI_EXCLUSIVE_DELETE_TYPES and value not in restored:
                restored.append(value)
        return restored

    def _on_romanize_ruby_changed(self, checked: bool):
        if self._loading_values:
            return
        if checked:
            selected = self.card_delete_ruby_types.selectedValues()
            self._delete_types_before_romanize = list(selected)
            filtered = self._delete_types_without_romaji_exclusive(selected)
            if filtered != selected:
                self.card_delete_ruby_types.setSelectedValues(filtered)
        elif self._skip_restore_on_romanize_off:
            self._skip_restore_on_romanize_off = False
            self._delete_types_before_romanize = list(self.card_delete_ruby_types.selectedValues())
        else:
            selected = self.card_delete_ruby_types.selectedValues()
            restored = self._restore_delete_types_after_romaji(selected)
            if restored != selected:
                self.card_delete_ruby_types.setSelectedValues(restored)
        self._notify_changed()

    def _on_delete_ruby_types_changed(self, selected: list[str]):
        if self._loading_values:
            return
        if (self.card_romanize_ruby.isChecked()
                and any(v in self._ROMAJI_EXCLUSIVE_DELETE_TYPES for v in selected)):
            self._skip_restore_on_romanize_off = True
            self.card_romanize_ruby.setChecked(False)
        if not self.card_romanize_ruby.isChecked():
            self._delete_types_before_romanize = list(self.card_delete_ruby_types.selectedValues())
        self._notify_changed()

    def load_settings(self, s):
        self._loading_values = True
        try:
            self.card_checkpoint_chars.setValues({
                "hiragana": s.get("auto_check.hiragana", True),
                "katakana": s.get("auto_check.katakana", True),
                "kanji": s.get("auto_check.kanji", True),
                "alphabet": s.get("auto_check.alphabet", False),
                "digit": s.get("auto_check.digit", False),
                "symbol": s.get("auto_check.symbol", False),
                "space": s.get("auto_check.space", False),
                "space_after_japanese": s.get("auto_check.space_after_japanese", True),
                "space_after_alphabet": s.get("auto_check.space_after_alphabet", True),
                "space_after_symbol": s.get("auto_check.space_after_symbol", True),
            })
            self.card_check_rules.setValues({
                "check_n": s.get("auto_check.check_n", True),
                "check_sokuon": s.get("auto_check.check_sokuon", True),
                "check_long_vowel": s.get("auto_check.check_long_vowel", False),
                "small_kana": s.get("auto_check.small_kana", False),
                "check_parentheses": s.get("auto_check.check_parentheses", True),
                "check_empty_lines": s.get("auto_check.check_empty_lines", False),
                "check_line_start": s.get("auto_check.check_line_start", False),
                "check_line_end": s.get("auto_check.check_line_end", True),
                "check_space_as_line_end": s.get("auto_check.check_space_as_line_end", True),
                "check_english_word_end": s.get("auto_check.check_english_word_end", True),
                "english_syllable_check": s.get("auto_check.english_syllable_check", True),
            })
            self.card_auto_on_load.setChecked(s.get("auto_check.auto_on_load", True))
            self.card_chinese_lyrics_detection.setChecked(s.get("auto_check.chinese_lyrics_detection", True))
            self.card_chinese_pinyin_annotation.setChecked(s.get("auto_check.chinese_pinyin_annotation", False))
            romanize_ruby = s.get("auto_check.romanize_ruby", False)
            saved_delete_types = s.get("auto_check.delete_ruby_types", [])
            if "katakana" in saved_delete_types:
                saved_delete_types.remove("katakana")
                if "katakana_hiragana_ruby" not in saved_delete_types:
                    saved_delete_types.append("katakana_hiragana_ruby")
                if "katakana_english_ruby" not in saved_delete_types:
                    saved_delete_types.append("katakana_english_ruby")
                s.set("auto_check.delete_ruby_types", saved_delete_types)
                s.save()
            if romanize_ruby:
                self._delete_types_before_romanize = list(saved_delete_types)
                filtered = self._delete_types_without_romaji_exclusive(saved_delete_types)
                if filtered != saved_delete_types:
                    saved_delete_types = filtered
                    s.set("auto_check.delete_ruby_types", saved_delete_types)
                    s.save()
            else:
                self._delete_types_before_romanize = list(saved_delete_types)
            self.card_romanize_ruby.setChecked(romanize_ruby)
            self.card_delete_ruby_types.setSelectedValues(saved_delete_types)
            self.card_romanize_ruby.check_repeat_long_vowels.setChecked(
                s.get("auto_check.romaji_repeat_long_vowels", True)
            )
            self._romaji_link_long_vowels_preference = s.get(
                "auto_check.romaji_link_long_vowels", False
            )
            self.card_romanize_ruby.check_link_sokuon.setChecked(
                s.get("auto_check.romaji_link_sokuon", False)
            )
            self.card_romanize_ruby.check_uppercase.setChecked(
                s.get("auto_check.romaji_uppercase", False)
            )
            self._sync_romaji_long_vowel_link_checkbox()
        finally:
            self._loading_values = False

    def collect_settings(self, s):
        for key, val in self.card_checkpoint_chars.values().items():
            s.set(f"auto_check.{key}", val)
        for key, val in self.card_check_rules.values().items():
            s.set(f"auto_check.{key}", val)
        s.set("auto_check.auto_on_load", self.card_auto_on_load.isChecked())
        s.set("auto_check.chinese_lyrics_detection", self.card_chinese_lyrics_detection.isChecked())
        s.set("auto_check.chinese_pinyin_annotation", self.card_chinese_pinyin_annotation.isChecked())
        delete_types = self.card_delete_ruby_types.selectedValues()
        romanize_ruby = self.card_romanize_ruby.isChecked()
        if romanize_ruby:
            filtered = self._delete_types_without_romaji_exclusive(delete_types)
            if filtered != delete_types:
                delete_types = filtered
                self.card_delete_ruby_types.setSelectedValues(delete_types)
        else:
            self._delete_types_before_romanize = list(delete_types)
        s.set("auto_check.romanize_ruby", romanize_ruby)
        s.set("auto_check.delete_ruby_types", delete_types)
        s.set(
            "auto_check.romaji_repeat_long_vowels",
            self.card_romanize_ruby.check_repeat_long_vowels.isChecked(),
        )
        s.set(
            "auto_check.romaji_link_long_vowels",
            self._romaji_link_long_vowels_preference,
        )
        s.set(
            "auto_check.romaji_link_sokuon",
            self.card_romanize_ruby.check_link_sokuon.isChecked(),
        )
        s.set(
            "auto_check.romaji_uppercase",
            self.card_romanize_ruby.check_uppercase.isChecked(),
        )

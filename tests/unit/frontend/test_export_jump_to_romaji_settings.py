"""导出页「跳转到设置」按钮：显隐随 Kirakara 格式 + 直达罗马音设置卡。"""

from PyQt6.QtCore import Qt

from strange_uta_game.frontend.export.export_interface import ExportInterface
from strange_uta_game.frontend.settings.sub_interfaces.auto_check import (
    AutoCheckSubInterface,
)


def _select_format(page: ExportInterface, name: str) -> None:
    for i in range(page.format_list.count()):
        if page.format_list.item(i).data(Qt.ItemDataRole.UserRole) == name:
            page.format_list.setCurrentRow(i)
            return
    raise AssertionError(f"找不到格式 {name}")


def test_jump_button_follows_kirakara_format(qapp):
    page = ExportInterface()
    assert page._romaji_option_row.isHidden()

    _select_format(page, "Kirakara")
    assert not page._romaji_option_row.isHidden()
    assert not page.btn_romaji_settings.isHidden()

    _select_format(page, "Nicokara (带注音)")
    assert page._romaji_option_row.isHidden()

    page.deleteLater()
    qapp.processEvents()


class _StubAutoCheck:
    def __init__(self):
        self.focus_count = 0

    def focus_romaji_style_card(self):
        self.focus_count += 1


class _StubWindow:
    def __init__(self):
        self.switched_to = None

    def switchTo(self, iface):
        self.switched_to = iface


class _StubSettings:
    def __init__(self):
        self.autoCheckInterface = _StubAutoCheck()
        self.switched_tab = None
        self.window_stub = _StubWindow()

    def window(self):
        return self.window_stub

    def switch_to_tab(self, key):
        self.switched_tab = key


def test_jump_button_switches_to_settings_and_expands_card(qapp):
    page = ExportInterface()
    _select_format(page, "Kirakara")

    setting_iface = _StubSettings()
    page.settingInterface = setting_iface
    page.btn_romaji_settings.click()

    assert setting_iface.window_stub.switched_to is setting_iface
    assert setting_iface.switched_tab == "auto_check"
    assert setting_iface.autoCheckInterface.focus_count == 1

    page.deleteLater()
    qapp.processEvents()


def test_focus_romaji_style_card_expands_card(qapp):
    page = AutoCheckSubInterface()
    assert not page.card_romaji_style.isExpand

    page.focus_romaji_style_card()
    qapp.processEvents()

    assert page.card_romaji_style.isExpand

    page.deleteLater()
    qapp.processEvents()

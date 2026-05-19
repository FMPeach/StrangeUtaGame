"""关于子页面。"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QWidget
from qfluentwidgets import (
    FluentIcon as FIF,
    InfoBar, InfoBarPosition,
    PrimaryPushButton, PushButton,
    SettingCard, SettingCardGroup,
)

from strange_uta_game.__version__ import __version__ as _app_version
from .base import SubSettingInterface


class AboutSubInterface(SubSettingInterface):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings_ref = None
        self._init_ui()

    def _init_ui(self):
        self.about_group = SettingCardGroup("关于", self.scrollWidget)

        about_card = SettingCard(FIF.INFO, "StrangeUtaGame - 歌词打轴软件",
            f"版本 v{_app_version}  |  由 RhythmicaLyrics 启发", self.about_group)
        self.about_group.addSettingCard(about_card)

        link_card = SettingCard(FIF.GITHUB, "GitHub",
            "https://github.com/Xuan-cc/StrangeUtaGame", self.about_group)
        self.about_group.addSettingCard(link_card)

        self._path_card = SettingCard(FIF.FOLDER, "配置文件位置", "（未加载）", self.about_group)
        btn_open = PushButton("打开目录", self._path_card)
        btn_open.setFont(QFont("Microsoft YaHei", 10))
        btn_open.clicked.connect(self._open_config_dir)
        btn_change = PushButton("更改位置", self._path_card)
        btn_change.setFont(QFont("Microsoft YaHei", 10))
        btn_change.clicked.connect(self._change_config_dir)
        self._path_card.hBoxLayout.addWidget(btn_open, 0, Qt.AlignmentFlag.AlignRight)
        self._path_card.hBoxLayout.addWidget(btn_change, 0, Qt.AlignmentFlag.AlignRight)
        self._path_card.hBoxLayout.addSpacing(16)
        self.about_group.addSettingCard(self._path_card)
        self.expandLayout.addWidget(self.about_group)

        # 保存/重置按钮
        btn_widget = QWidget(self.scrollWidget)
        btn_widget.setMinimumHeight(60)
        btn_layout = QHBoxLayout(btn_widget)
        btn_layout.setContentsMargins(0, 10, 0, 24)
        self.btn_save = PrimaryPushButton("保存设置", btn_widget)
        self.btn_save.setIcon(FIF.SAVE)
        self.btn_save.setMinimumHeight(36)
        self.btn_reset = PushButton("重置为默认设置", btn_widget)
        self.btn_reset.setIcon(FIF.DELETE)
        self.btn_reset.setMinimumHeight(36)
        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(self.btn_reset)
        btn_layout.addStretch()
        self.expandLayout.addWidget(btn_widget)

    def connect_signals(self):
        pass  # 按钮回调由外层连接

    def load_settings(self, s):
        self._settings_ref = s
        self._path_card.setContent(str(s._config_path))

    def collect_settings(self, s):
        pass  # 关于页没有可编辑的设置项

    def _open_config_dir(self):
        if self._settings_ref is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._settings_ref._config_path.parent)))

    def _change_config_dir(self):
        if self._settings_ref is None:
            return
        s = self._settings_ref
        new_dir = QFileDialog.getExistingDirectory(self, "选择配置文件存储目录", str(s._config_path.parent))
        if not new_dir:
            return

        new_dir_path = Path(new_dir)
        program_dir = Path(sys.argv[0]).resolve().parent
        redirect_file = program_dir / ".config_redirect"

        if new_dir_path.resolve() == program_dir.resolve():
            try:
                if redirect_file.exists():
                    redirect_file.unlink()
            except Exception:
                pass
        else:
            try:
                redirect_file.write_text(str(new_dir_path), encoding="utf-8")
            except Exception as e:
                InfoBar.error(title="更改失败", content=f"无法写入重定向文件: {e}",
                    orient=Qt.Orientation.Horizontal, isClosable=True,
                    position=InfoBarPosition.TOP, duration=5000, parent=self)
                return

        old_path = s._config_path
        new_path = new_dir_path / "config.json"
        if old_path.exists() and old_path != new_path:
            try:
                import shutil
                new_dir_path.mkdir(exist_ok=True)
                shutil.copy2(str(old_path), str(new_path))
                for fname in ("dictionary.json", "network_dictionary.json", "singers.json"):
                    op = old_path.parent / fname
                    np = new_dir_path / fname
                    if op.exists() and op != np:
                        shutil.copy2(str(op), str(np))
            except Exception as e:
                InfoBar.warning(title="配置复制失败", content=f"请手动复制配置文件: {e}",
                    orient=Qt.Orientation.Horizontal, isClosable=True,
                    position=InfoBarPosition.TOP, duration=5000, parent=self)

        s._config_path = new_path
        s._dict_path = new_dir_path / "dictionary.json"
        s._network_dict_path = new_dir_path / "network_dictionary.json"
        s._singers_path = new_dir_path / "singers.json"
        self._path_card.setContent(str(new_path))
        InfoBar.success(title="配置位置已更改", content=f"配置文件将保存到: {new_path}",
            orient=Qt.Orientation.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP, duration=5000, parent=self)

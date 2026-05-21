"""演奏控制子页面。"""

from __future__ import annotations

from qfluentwidgets import FluentIcon as FIF, SettingCardGroup

from ..cards import DoubleSpinSettingCard, SpinSettingCard, SwitchSettingCard
from .base import SubSettingInterface


class PlaybackSubInterface(SubSettingInterface):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        g = SettingCardGroup("演奏控制", self.scrollWidget)
        self.card_volume = SpinSettingCard(FIF.VOLUME, "默认音量", "音频加载后的初始音量",
            min_val=0, max_val=100, suffix=" %", parent=g)
        self.card_speed = DoubleSpinSettingCard(FIF.SPEED_HIGH, "默认速度", "音频加载后的初始播放速度",
            min_val=0.2, max_val=2.0, step=0.1, suffix=" x", parent=g)
        self.card_fast_forward = SpinSettingCard(FIF.CHEVRON_RIGHT, "快进量", "按下快进键跳过的时间",
            min_val=1000, max_val=30000, step=1000, suffix=" ms", parent=g)
        self.card_rewind = SpinSettingCard(FIF.LEFT_ARROW, "快退量", "按下快退键后退的时间",
            min_val=1000, max_val=30000, step=1000, suffix=" ms", parent=g)
        self.card_auto_play = SwitchSettingCard(FIF.PLAY, "自动播放", "加载音频文件后自动开始播放", parent=g)
        self.card_hq_speed = SwitchSettingCard(
            FIF.SPEED_HIGH, "启用高质量音频变速",
            "开启后变速不变调且无爆音，但需占用 .cache 进行离线预渲染；"
            "关闭则仅使用原版 BASS 引擎实时变速（零缓存，但可能爆音）。默认开启。",
            parent=g,
        )
        self.card_jump_before = SpinSettingCard(FIF.HISTORY, "删除节奏点跳转提前量",
            "删除节奏点时跳转到该时间戳前的毫秒数",
            min_val=0, max_val=30000, step=500, suffix=" ms", parent=g)
        for c in [self.card_volume, self.card_speed, self.card_fast_forward,
                  self.card_rewind, self.card_auto_play, self.card_hq_speed,
                  self.card_jump_before]:
            g.addSettingCard(c)
        self.expandLayout.addWidget(g)

    def connect_signals(self):
        self.card_volume.value_changed.connect(self._notify_changed)
        self.card_speed.value_changed.connect(self._notify_changed)
        self.card_fast_forward.value_changed.connect(self._notify_changed)
        self.card_rewind.value_changed.connect(self._notify_changed)
        self.card_auto_play.checked_changed.connect(self._notify_changed)
        self.card_hq_speed.checked_changed.connect(self._notify_changed)
        self.card_jump_before.value_changed.connect(self._notify_changed)

    def load_settings(self, s):
        self.card_volume.setValue(s.get("audio.default_volume", 80))
        self.card_speed.setValue(s.get("audio.default_speed", 1.0))
        self.card_fast_forward.setValue(s.get("timing.fast_forward_ms", 5000))
        self.card_rewind.setValue(s.get("timing.rewind_ms", 5000))
        self.card_auto_play.setChecked(s.get("audio.auto_play_on_load", False))
        self.card_hq_speed.setChecked(s.get("audio.hq_speed_change", True))
        self.card_jump_before.setValue(s.get("timing.jump_before_ms", 3000))

    def collect_settings(self, s):
        s.set("audio.default_volume", self.card_volume.value())
        s.set("audio.default_speed", self.card_speed.value())
        s.set("audio.auto_play_on_load", self.card_auto_play.isChecked())
        s.set("audio.hq_speed_change", self.card_hq_speed.isChecked())
        s.set("timing.fast_forward_ms", self.card_fast_forward.value())
        s.set("timing.rewind_ms", self.card_rewind.value())
        s.set("timing.jump_before_ms", self.card_jump_before.value())

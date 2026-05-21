"""打轴设定 + Offset校准子页面。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from qfluentwidgets import FluentIcon as FIF, PushButton, SettingCard, SettingCardGroup

from ..calibration_dialog import CalibrationDialog
from ..cards import SpinSettingCard, SwitchSettingCard
from .base import SubSettingInterface


class TimingSubInterface(SubSettingInterface):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._calibration_dialog = None
        self._init_ui()

    def _init_ui(self):
        # 打轴设定
        g = SettingCardGroup("打轴设定", self.scrollWidget)
        self.card_offset = SpinSettingCard(FIF.DATE_TIME, "按键补偿",
            "建议用下方的offset校正来矫正，用于设备引起的反应延迟（负值=提前，正值=延后）",
            min_val=-5000, max_val=5000, step=10, suffix=" ms", parent=g)
        self.card_speed_correction = SpinSettingCard(FIF.SPEED_MEDIUM, "速度补正",
            "打轴时间戳的速度修正系数", min_val=50, max_val=200, step=5, suffix=" %", parent=g)
        self.card_export_offset = SpinSettingCard(FIF.HISTORY, "全局偏移",
            "全局偏移（原RL内默认为-230补偿）,用于控制本软件内整体轴时间偏移（毫秒），（负值=提前，正值=延后）",
            min_val=-5000, max_val=5000, step=10, suffix=" ms", parent=g)
        self.card_timing_step = SpinSettingCard(FIF.UP, "微调时间戳步长",
            "Alt+↑/Alt+↓ 微调选中节奏点时间戳的步长",
            min_val=1, max_val=500, step=1, suffix=" ms", parent=g)
        self.card_disable_click_jump = SwitchSettingCard(FIF.CLOSE, "禁用单击跳转",
            "关闭单击字符/节奏点延迟后跳转到目标行的功能（双击跳转不受影响）", parent=g)
        for c in [self.card_offset, self.card_speed_correction, self.card_export_offset,
                  self.card_timing_step, self.card_disable_click_jump]:
            g.addSettingCard(c)
        self.expandLayout.addWidget(g)

        # Offset 校准
        cg = SettingCardGroup("Offset 校准", self.scrollWidget)
        cal_card = SettingCard(FIF.SPEED_HIGH, "节拍器校准",
            "打开校准弹窗，跟随节拍器按空格键测量 Offset", cg)
        self.btn_cal_open = PushButton("开始校准", cal_card)
        self.btn_cal_open.setFont(QFont("Microsoft YaHei", 10))
        self.btn_cal_open.clicked.connect(self._open_calibration_dialog)
        cal_card.hBoxLayout.addWidget(self.btn_cal_open, 0, Qt.AlignmentFlag.AlignRight)
        cal_card.hBoxLayout.addSpacing(16)
        cg.addSettingCard(cal_card)
        self.expandLayout.addWidget(cg)

    def _open_calibration_dialog(self):
        self._calibration_dialog = CalibrationDialog(self)
        self._calibration_dialog.exec()
        if self._calibration_dialog is not None:
            self._calibration_dialog._stop_metronome()
        self._calibration_dialog = None

    def close_calibration(self):
        if self._calibration_dialog is not None:
            self._calibration_dialog.close()
            self._calibration_dialog = None

    def connect_signals(self):
        self.card_offset.value_changed.connect(self._notify_changed)
        self.card_speed_correction.value_changed.connect(self._notify_changed)
        self.card_export_offset.value_changed.connect(self._notify_changed)
        self.card_timing_step.value_changed.connect(self._notify_changed)
        self.card_disable_click_jump.checked_changed.connect(self._notify_changed)

    def load_settings(self, s):
        self.card_offset.setValue(s.get("timing.tag_offset_ms", -230))
        self.card_speed_correction.setValue(s.get("timing.speed_correction", 80))
        self.card_export_offset.setValue(s.get("export.offset_ms", 0))
        self.card_timing_step.setValue(s.get("timing.timing_adjust_step_ms", 10))
        self.card_disable_click_jump.setChecked(s.get("timing.disable_click_jump", False))

    def collect_settings(self, s):
        s.set("timing.tag_offset_ms", self.card_offset.value())
        s.set("timing.speed_correction", self.card_speed_correction.value())
        s.set("export.offset_ms", self.card_export_offset.value())
        s.set("timing.timing_adjust_step_ms", self.card_timing_step.value())
        s.set("timing.disable_click_jump", self.card_disable_click_jump.isChecked())

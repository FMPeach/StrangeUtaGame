"""时间轴控件。

显示音频波形、当前播放位置、打轴节奏点分布。
支持缩放和横向滚动，类似视频剪辑软件的时间线。
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np
from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QMouseEvent,
    QWheelEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPolygon,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollBar,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import CaptionLabel

from strange_uta_game.frontend.theme import theme


# ──────────────────────────────────────────────
# 波形显示区域
# ──────────────────────────────────────────────

class WaveformDisplay(QWidget):
    """波形显示区域 - 绘制音频波形 + 时间网格 + 标签 + 播放头"""

    seek_requested = pyqtSignal(int)
    scroll_position_changed = pyqtSignal(float)
    zoom_changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms = 0
        self._current_ms = 0
        self._time_tags: List[int] = []

        # 音频数据
        self._samples: Optional[np.ndarray] = None
        self._sample_rate: int = 44100
        self._channels: int = 2

        # 缩放和滚动
        self._zoom_factor: float = 50.0  # 默认50x缩放，减少初始渲染压力
        self._scroll_position: float = 0.0

        # 波形峰值缓存
        self._peaks_cache: Optional[List[tuple]] = None
        self._peaks_cache_key: Optional[tuple] = None  # (width, zoom, scroll, samples_id)

        self.setMinimumHeight(80)
        self.setMouseTracking(True)

        # 监听主题变化，触发重绘
        theme.changed.connect(self.update)

    def set_duration(self, ms: int):
        self._duration_ms = ms
        self.update()

    def set_position(self, ms: int):
        self._current_ms = ms
        # 自动滚动保持播放头可见
        if self._duration_ms > 0 and self._zoom_factor > 1.0:
            visible_start = self._scroll_position * self._duration_ms
            visible_end = visible_start + self._duration_ms / self._zoom_factor
            if ms < visible_start or ms > visible_end:
                self._scroll_position = max(0.0, min(1.0,
                    (ms - self._duration_ms / (2 * self._zoom_factor)) / self._duration_ms))
                self.scroll_position_changed.emit(self._scroll_position)
        self.update()

    def set_time_tags(self, tags_ms: List[int]):
        self._time_tags = sorted(tags_ms)
        self.update()

    def set_audio_data(self, samples: np.ndarray, sample_rate: int, channels: int):
        self._samples = samples
        self._sample_rate = sample_rate
        self._channels = channels
        # 清除波形缓存
        self._peaks_cache = None
        self._peaks_cache_key = None
        self.update()

    def set_zoom(self, zoom: float):
        self._zoom_factor = max(1.0, min(100.0, zoom))
        self.update()

    def set_scroll_position(self, position: float):
        self._scroll_position = max(0.0, min(1.0, position))
        self.update()

    def _compute_waveform_peaks(self, width: int) -> Optional[List[tuple]]:
        """计算波形峰值数据（按像素降采样），带缓存"""
        if self._samples is None or self._duration_ms <= 0 or width <= 0:
            return None

        # 缓存键：(width, zoom, scroll_position, samples_id)
        samples_id = id(self._samples)
        cache_key = (width, self._zoom_factor, self._scroll_position, samples_id)

        if self._peaks_cache_key == cache_key and self._peaks_cache is not None:
            return self._peaks_cache

        visible_start_ms = self._scroll_position * self._duration_ms
        visible_duration_ms = self._duration_ms / self._zoom_factor
        visible_end_ms = visible_start_ms + visible_duration_ms

        start_sample = int((visible_start_ms / 1000.0) * self._sample_rate)
        end_sample = int((visible_end_ms / 1000.0) * self._sample_rate)
        start_sample = max(0, min(start_sample, len(self._samples) - 1))
        end_sample = max(start_sample + 1, min(end_sample, len(self._samples)))

        visible_samples = self._samples[start_sample:end_sample]

        # 立体声混合为单声道
        if self._channels > 1:
            visible_samples = np.mean(visible_samples, axis=1)

        # 按像素宽度降采样
        samples_per_pixel = max(1, len(visible_samples) // width)
        peaks = []

        for i in range(width):
            start_idx = i * samples_per_pixel
            end_idx = min(start_idx + samples_per_pixel, len(visible_samples))
            if start_idx >= len(visible_samples):
                break
            chunk = visible_samples[start_idx:end_idx]
            if len(chunk) > 0:
                peaks.append((float(np.min(chunk)), float(np.max(chunk))))

        # 更新缓存
        self._peaks_cache = peaks
        self._peaks_cache_key = cache_key

        return peaks

    def paintEvent(self, a0: Optional[QPaintEvent]):
        _ = a0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        painter.fillRect(self.rect(), theme.waveform_bg)

        if self._duration_ms <= 0:
            painter.setPen(theme.text_hint)
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "请加载音频文件"
            )
            return

        visible_start_ms = self._scroll_position * self._duration_ms
        visible_duration_ms = self._duration_ms / self._zoom_factor
        visible_end_ms = visible_start_ms + visible_duration_ms

        self._draw_time_grid(painter, w, h, visible_start_ms, visible_end_ms)
        self._draw_waveform(painter, w, h)
        self._draw_time_tags(painter, w, h, visible_start_ms, visible_end_ms)
        self._draw_playhead(painter, w, h, visible_start_ms, visible_duration_ms)

    def _draw_time_grid(self, painter: QPainter, w: int, h: int,
                        visible_start_ms: float, visible_end_ms: float):
        visible_duration = visible_end_ms - visible_start_ms
        if visible_duration <= 10000:
            grid_interval = 1000
        elif visible_duration <= 60000:
            grid_interval = 5000
        elif visible_duration <= 300000:
            grid_interval = 10000
        else:
            grid_interval = 60000

        painter.setPen(QPen(theme.border_primary, 1))
        first_grid = int(visible_start_ms / grid_interval) * grid_interval
        for t in range(first_grid, int(visible_end_ms) + 1, grid_interval):
            if visible_duration > 0:
                ratio = (t - visible_start_ms) / visible_duration
                x = int(ratio * w)
                painter.drawLine(x, 0, x, h)

                painter.setPen(theme.text_secondary)
                s = t // 1000
                time_text = f"{s // 60}:{s % 60:02d}"
                painter.drawText(x + 2, 12, time_text)
                painter.setPen(QPen(theme.border_primary, 1))

    def _draw_waveform(self, painter: QPainter, w: int, h: int):
        peaks = self._compute_waveform_peaks(w)
        if not peaks:
            return

        mid_y = h // 2
        amplitude_scale = h / 2.0 * 0.8

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.waveform_fill)

        # 上半部分
        for i, (_, max_val) in enumerate(peaks):
            y = int(mid_y - max_val * amplitude_scale)
            painter.drawRect(i, y, 1, mid_y - y)

        # 下半部分
        for i, (min_val, _) in enumerate(peaks):
            y = int(mid_y - min_val * amplitude_scale)
            painter.drawRect(i, mid_y, 1, y - mid_y)

        # 中心线
        painter.setPen(QPen(theme.waveform_line, 1))
        painter.drawLine(0, mid_y, w, mid_y)

    def _draw_time_tags(self, painter: QPainter, w: int, h: int,
                        visible_start_ms: float, visible_end_ms: float):
        visible_duration = visible_end_ms - visible_start_ms
        if visible_duration <= 0:
            return

        painter.setPen(QPen(theme.accent_warning, 2))
        for tag in self._time_tags:
            if visible_start_ms <= tag <= visible_end_ms:
                ratio = (tag - visible_start_ms) / visible_duration
                x = int(ratio * w)
                painter.drawLine(x, int(h * 0.2), x, int(h * 0.8))

    def _draw_playhead(self, painter: QPainter, w: int, h: int,
                       visible_start_ms: float, visible_duration_ms: float):
        if visible_duration_ms <= 0:
            return

        if visible_start_ms <= self._current_ms <= visible_start_ms + visible_duration_ms:
            ratio = (self._current_ms - visible_start_ms) / visible_duration_ms
            x = int(ratio * w)

            painter.setPen(QPen(theme.accent_primary, 2))
            painter.drawLine(x, 0, x, h)

            # 播放头三角形标记
            painter.setBrush(QBrush(theme.accent_primary))
            triangle = QPolygon([
                QPoint(x - 6, 0),
                QPoint(x + 6, 0),
                QPoint(x, 10),
            ])
            painter.drawPolygon(triangle)

    def mousePressEvent(self, a0: Optional[QMouseEvent]):
        if a0 is None or self._duration_ms <= 0:
            return

        visible_duration_ms = self._duration_ms / self._zoom_factor
        visible_start_ms = self._scroll_position * self._duration_ms
        ratio = max(0.0, min(1.0, a0.position().x() / self.width()))
        target_ms = int(visible_start_ms + ratio * visible_duration_ms)
        self.seek_requested.emit(target_ms)

    def wheelEvent(self, a0: Optional[QWheelEvent]):
        if a0 is None:
            return

        delta = a0.angleDelta().y()
        new_zoom = self._zoom_factor * (1.2 if delta > 0 else 1 / 1.2)
        new_zoom = max(1.0, min(100.0, new_zoom))

        if new_zoom != self._zoom_factor:
            mouse_ratio = a0.position().x() / self.width()
            visible_start = self._scroll_position
            visible_duration = 1.0 / self._zoom_factor
            audio_position = visible_start + mouse_ratio * visible_duration

            self._zoom_factor = new_zoom
            new_visible_duration = 1.0 / self._zoom_factor
            self._scroll_position = max(0.0, min(1.0,
                audio_position - mouse_ratio * new_visible_duration))

            self.zoom_changed.emit(self._zoom_factor)
            self.scroll_position_changed.emit(self._scroll_position)
            self.update()


# ──────────────────────────────────────────────
# 时间轴控件（包含波形显示 + 缩放控制 + 滚动条）
# ──────────────────────────────────────────────

class TimelineWidget(QWidget):
    """时间轴 - 显示音频波形 + 时间网格 + 时间标签 + 播放位置"""

    seek_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms = 0
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # 波形显示区域
        self.waveform_display = WaveformDisplay(self)
        self.waveform_display.seek_requested.connect(self.seek_requested.emit)
        self.waveform_display.zoom_changed.connect(self._on_zoom_changed)
        self.waveform_display.scroll_position_changed.connect(self._on_scroll_changed)
        layout.addWidget(self.waveform_display, stretch=1)

        # 底部控制栏
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(4, 0, 4, 2)
        bottom_layout.setSpacing(8)

        # 缩放控制
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.zoom_slider.setRange(100, 10000)
        self.zoom_slider.setValue(5000)  # 默认50x
        self.zoom_slider.setFixedWidth(120)
        self.zoom_slider.valueChanged.connect(self._on_zoom_slider_changed)
        bottom_layout.addWidget(self.zoom_slider)

        self.zoom_label = CaptionLabel("50.0x", self)
        self.zoom_label.setFixedWidth(40)
        bottom_layout.addWidget(self.zoom_label)

        bottom_layout.addStretch()

        # 横向滚动条
        self.scroll_bar = QScrollBar(Qt.Orientation.Horizontal, self)
        self.scroll_bar.setRange(0, 1000)
        self.scroll_bar.setValue(0)
        self.scroll_bar.valueChanged.connect(self._on_scroll_bar_changed)
        bottom_layout.addWidget(self.scroll_bar, stretch=1)

        layout.addLayout(bottom_layout)

    def set_duration(self, ms: int):
        self._duration_ms = ms
        self.waveform_display.set_duration(ms)

    def set_position(self, ms: int):
        self.waveform_display.set_position(ms)

    def set_time_tags(self, tags_ms: List[int]):
        self.waveform_display.set_time_tags(tags_ms)

    def set_audio_data(self, samples: np.ndarray, sample_rate: int, channels: int):
        self.waveform_display.set_audio_data(samples, sample_rate, channels)

    def _on_zoom_changed(self, zoom: float):
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(int(zoom * 100))
        self.zoom_slider.blockSignals(False)
        self.zoom_label.setText(f"{zoom:.1f}x")

    def _on_scroll_changed(self, position: float):
        self.scroll_bar.blockSignals(True)
        self.scroll_bar.setValue(int(position * 1000))
        self.scroll_bar.blockSignals(False)

    def _on_zoom_slider_changed(self, value: int):
        zoom = value / 100.0
        self.waveform_display.set_zoom(zoom)
        self.zoom_label.setText(f"{zoom:.1f}x")

    def _on_scroll_bar_changed(self, value: int):
        position = value / 1000.0
        self.waveform_display.set_scroll_position(position)

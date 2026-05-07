"""编辑器界面（打轴主界面）。

本模块仅包含 ``EditorInterface`` 主类。控件与对话框已拆分到 ``timing/`` 子包：

- ``timing.commands``        : ``_SentenceSnapshotCommand``
- ``timing.transport_bar``   : ``TransportBar``
- ``timing.toolbar``         : ``EditorToolBar``
- ``timing.karaoke_preview`` : ``KaraokePreview``
- ``timing.timeline_widget`` : ``TimelineWidget``
- ``timing.dialogs``         : ``ModifyCharacterDialog`` / ``InsertGuideSymbolDialog`` / ``CharEditDialog``

为保留历史 import 路径（``from ...editor.timing_interface import _SentenceSnapshotCommand`` 等），
本模块对子包内符号进行 re-export。
"""

from __future__ import annotations

import time
from copy import deepcopy
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QKeyEvent
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    StateToolTip,
)

from strange_uta_game.backend.application import (
    CheckpointPosition,
    TimingService,
)
from strange_uta_game.backend.domain import Character, Project
from strange_uta_game.backend.infrastructure.audio import AudioLoadError
from strange_uta_game.backend.infrastructure.parsers.text_splitter import (
    CharType,
    get_char_type,
)
from strange_uta_game.frontend.theme import theme

from .timing import (
    _SentenceSnapshotCommand,
    SentenceSnapshotCommand,
    CharEditDialog,
    EditorToolBar,
    FileLoader,
    InsertGuideSymbolDialog,
    KaraokePreview,
    ModifyCharacterDialog,
    TimelineWidget,
    TransportBar,
)

__all__ = [
    "EditorInterface",
    # re-exports for backward compatibility
    "_SentenceSnapshotCommand",
    "SentenceSnapshotCommand",
    "TransportBar",
    "EditorToolBar",
    "KaraokePreview",
    "TimelineWidget",
    "ModifyCharacterDialog",
    "InsertGuideSymbolDialog",
    "CharEditDialog",
]


# ──────────────────────────────────────────────
# 编辑器主界面
# ──────────────────────────────────────────────

class EditorInterface(QWidget):
    """编辑器界面主容器"""

    project_saved = pyqtSignal()
    _position_changed_signal = pyqtSignal(int, int, object)
    _checkpoint_moved_signal = pyqtSignal(object)
    _timetag_added_signal = pyqtSignal()
    _timing_error_signal = pyqtSignal(str, str)
    # 渲染进度：(speed, progress)。内部从音频 worker 线程触发，经此信号
    # 自动 marshal 到 UI 线程（Qt 跨线程默认 queued connection）。
    _render_progress_signal = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._timing_service: Optional[TimingService] = None
        self._audio_file_path: Optional[str] = None
        self._current_line_idx = 0
        self._pressed_keys: set[str] = set()  # 当前按下的打轴按键集合（支持多键独立）
        self._last_position_update_time = 0.0  # 60fps UI 节流
        self._fast_forward_ms = 5000
        self._rewind_ms = 5000
        self._key_map = {}  # key_string -> action_name, populated by _apply_settings
        # 当 cp 标记被点击时，沿 _on_checkpoint_clicked → move_to_checkpoint →
        # on_checkpoint_moved (signal) → _handle_checkpoint_moved →
        # _apply_checkpoint_position 链路同步执行；此标志使后者跳过
        # set_current_position，从而不污染"选中字符"光标 (_current_char_idx)。
        # 区分：selected_cp（cp 标记选中态）vs selected_char（光标/选中字符态）。
        self._suppress_cp_cursor_move = False
        self._file_loader = FileLoader(self)
        self._init_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self._bind_callback_signals()

    def _bind_callback_signals(self):
        self._position_changed_signal.connect(self._handle_position_changed)
        self._checkpoint_moved_signal.connect(self._handle_checkpoint_moved)
        self._timetag_added_signal.connect(self._handle_timetag_added)
        self._timing_error_signal.connect(self._handle_timing_error)
        self._render_progress_signal.connect(self._handle_render_progress)

    def _handle_render_progress(self, speed: float, progress: float) -> None:
        """UI 线程：把进度转交给 TransportBar 显示。"""
        self.transport.set_render_progress(speed, progress)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 5)
        layout.setSpacing(8)

        # 1) 工具栏
        self.toolbar = EditorToolBar(self)
        self.toolbar.save_clicked.connect(self._on_save)
        self.toolbar.save_as_clicked.connect(self._on_save_as)
        self.toolbar.new_project_clicked.connect(self._on_new_project)
        self.toolbar.load_project_clicked.connect(self._on_load_project)
        self.toolbar.load_audio_clicked.connect(self._on_load_audio)
        self.toolbar.load_lyrics_clicked.connect(self._on_load_lyrics)
        self.toolbar.modify_char_clicked.connect(self._on_modify_char)
        self.toolbar.insert_guide_clicked.connect(self._on_insert_guide)
        self.toolbar.bulk_change_clicked.connect(self._on_bulk_change)
        self.toolbar.analyze_rubies_clicked.connect(self._on_analyze_rubies)
        self.toolbar.delete_rubies_by_type_clicked.connect(self._on_delete_rubies_by_type)
        self.toolbar.offset_changed.connect(self._on_offset_changed)
        layout.addWidget(self.toolbar)

        # 2) 播放控制栏
        self.transport = TransportBar(self)
        self.transport.play_clicked.connect(self._on_play)
        self.transport.pause_clicked.connect(self._on_pause)
        self.transport.stop_clicked.connect(self._on_stop)
        self.transport.seek_requested.connect(self._on_seek)
        self.transport.speed_changed.connect(self._on_speed_changed)
        self.transport.volume_changed.connect(self._on_volume_changed)
        layout.addWidget(self.transport)

        # 3) 时间轴
        self.timeline = TimelineWidget(self)
        self.timeline.seek_requested.connect(self._on_seek)
        layout.addWidget(self.timeline)

        # 4) 歌词预览（占主要空间）
        self.preview = KaraokePreview(self)
        self.preview.line_clicked.connect(self._on_line_clicked)
        self.preview.checkpoint_clicked.connect(self._on_checkpoint_clicked)
        self.preview.char_selected.connect(self._on_char_selected)
        self.preview.char_edit_requested.connect(self._on_char_edit_requested)
        self.preview.seek_to_char_requested.connect(self._on_seek_to_char)
        self.preview.seek_to_checkpoint_requested.connect(self._on_seek_to_checkpoint)
        self.preview.singer_change_requested.connect(self._on_singer_change_selection)
        self.preview.delete_chars_requested.connect(self._on_delete_chars_requested)
        self.preview.delete_timestamp_requested.connect(self._on_delete_timestamp_requested)
        self.preview.insert_space_after_requested.connect(
            self._on_insert_space_after_requested
        )
        self.preview.merge_line_up_requested.connect(self._on_merge_line_up_requested)
        self.preview.delete_line_requested.connect(self._on_delete_line_requested)
        self.preview.insert_blank_line_requested.connect(
            self._on_insert_blank_line_requested
        )
        self.preview.add_checkpoint_requested.connect(
            self._on_add_checkpoint_requested
        )
        self.preview.remove_checkpoint_requested.connect(
            self._on_remove_checkpoint_requested
        )
        self.preview.toggle_sentence_end_requested.connect(
            self._on_toggle_sentence_end_requested
        )
        layout.addWidget(self.preview, stretch=1)

        # 5) 底部打轴操作栏
        # 布局：[模式指示器] [打轴按钮] [清除按钮] <stretch> [快捷键提示]
        bottom = QHBoxLayout()
        bottom.setSpacing(10)

        # 左下角模式指示器（#8：区分音乐播放/暂停模式）
        self.lbl_mode = QLabel("模式：编辑")
        self.lbl_mode.setStyleSheet(
            "font-size: 12px; padding: 2px 8px; border-radius: 4px;"
            "background-color: #e0e0e0; color: #444;"
        )
        bottom.addWidget(self.lbl_mode)

        self.btn_tag = PrimaryPushButton("打轴 (Space)", self)
        self.btn_tag.setIcon(FIF.PIN)
        self.btn_tag.setMinimumHeight(36)
        self.btn_tag.setMinimumWidth(160)
        self.btn_tag.clicked.connect(self._on_tag_now)
        bottom.addWidget(self.btn_tag)

        self.btn_clear_tags = PushButton("清除当前行时间戳", self)
        self.btn_clear_tags.setIcon(FIF.DELETE)
        self.btn_clear_tags.clicked.connect(self._on_clear_current_line_tags)
        bottom.addWidget(self.btn_clear_tags)

        bottom.addStretch()

        # 快捷键提示（动态跟随设置）
        self.lbl_shortcut_hint = QLabel("")
        self.lbl_shortcut_hint.setStyleSheet(f"font-size: 11px; color: {theme.text_hint.name()};")
        bottom.addWidget(self.lbl_shortcut_hint)

        layout.addLayout(bottom)

        # 6) 状态栏
        # 布局：[播放状态] <stretch> [当前行/字符/时间戳] <stretch> [总体进度]
        status = QHBoxLayout()
        status.setContentsMargins(5, 2, 5, 2)
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setStyleSheet(f"font-size: 11px; color: {theme.text_hint.name()};")
        status.addWidget(self.lbl_status)
        status.addStretch()
        # 行号/字符/时间戳信息（#5：从打轴栏移到此处，与播放状态一同显示）
        self.lbl_line_info = QLabel("当前行: -")
        self.lbl_line_info.setStyleSheet(f"font-size: 11px; color: {theme.text_hint.name()};")
        status.addWidget(self.lbl_line_info)
        status.addStretch()
        self.lbl_progress = QLabel("行: 0/0 | 进度: 0%")
        self.lbl_progress.setStyleSheet(f"font-size: 11px; color: {theme.text_hint.name()};")
        status.addWidget(self.lbl_progress)
        layout.addLayout(status)

    def set_timing_service(self, timing_service: TimingService):
        self._timing_service = timing_service
        self._timing_service.set_callbacks(self)
        # 注册渲染进度回调：经 pyqtSignal 自动 marshal 到 UI 线程。
        self._timing_service.set_render_progress_callback(
            lambda spd, prog: self._render_progress_signal.emit(float(spd), float(prog))
        )
        # 注册timing_servive焦点时间戳改变回调
        self._timing_service._global_qt._focus_moved_signal.connect(self._handle_foucus_moved)

    def set_store(self, store):
        """接入 ProjectStore 统一数据中心。"""
        self._store = store
        store.data_changed.connect(self._on_data_changed)

    def _on_data_changed(self, change_type: str):
        """响应 ProjectStore 的数据变更。"""
        if change_type == "project":
            self.set_project(self._store.project)
        elif change_type in ("rubies", "lyrics", "checkpoints"):
            self.refresh_lyric_display()
        elif change_type == "timetags":
            self._update_time_tags_display()
            self._update_status()
        elif change_type == "settings":
            self._apply_settings()

    def _apply_settings(self):
        """从 AppSettings 读取设定并应用到编辑器。"""
        if not self._store:
            return
        # 通过 MainWindow 的 settingInterface 获取 AppSettings
        main_window = self.window()
        setting_iface = getattr(main_window, "settingInterface", None)
        if setting_iface is None:
            return
        settings = setting_iface.get_settings()
        self._fast_forward_ms = settings.get("timing.fast_forward_ms", 5000)
        self._rewind_ms = settings.get("timing.rewind_ms", 5000)
        self._jump_before_ms = settings.get("timing.jump_before_ms", 3000)
        # #4：读取时间戳微调步长（默认 10ms）
        self._timing_adjust_step_ms = int(
            settings.get("timing.timing_adjust_step_ms", 10)
        )
        # #8/#11/#13：读取双模式快捷键映射（打轴模式=播放中、编辑模式=未播放）
        # 动作集合（所有动作在两种模式下都存在，读设置时各自取值，互不干扰）
        action_names = [
            "tag_now",
            "play_pause",
            "stop",
            "seek_back",
            "seek_forward",
            "speed_down",
            "speed_up",
            "edit_ruby",
            "add_checkpoint",
            "remove_checkpoint",
            "toggle_line_end",
            "toggle_word_join",
            "volume_up",
            "volume_down",
            "nav_prev_line",
            "nav_next_line",
            "nav_prev_char",
            "nav_next_char",
            "timestamp_up",
            "timestamp_down",
            "cycle_checkpoint",
            "cycle_checkpoint_prev",
            "delete_timestamp",
        ]
        # 默认值兜底（当设置未写入新 schema 时使用）
        defaults = {
            "tag_now": "Space",
            "play_pause": "D",
            "stop": "S",
            "seek_back": "Z",
            "seek_forward": "X",
            "speed_down": "Q",
            "speed_up": "W",
            "edit_ruby": "F2",
            "add_checkpoint": "F4",
            "remove_checkpoint": "F5",
            "toggle_line_end": "F6",
            "toggle_word_join": "F3",
            "volume_up": "",
            "volume_down": "",
            "nav_prev_line": "UP",
            "nav_next_line": "DOWN",
            "nav_prev_char": "LEFT",
            "nav_next_char": "RIGHT",
            "timestamp_up": "ALT+UP",
            "timestamp_down": "ALT+DOWN",
            "cycle_checkpoint": "ALT+RIGHT",
            "cycle_checkpoint_prev": "ALT+LEFT",
            "delete_timestamp": "Backspace",
        }

        def _collect_map(mode_key: str) -> tuple[dict, dict]:
            """返回 (key_map, action->key_str) 两套数据，后者用于提示显示。"""
            key_map: dict[str, str] = {}
            action_to_keys: dict[str, str] = {}
            for action in action_names:
                raw = settings.get(
                    f"shortcuts.{mode_key}.{action}",
                    # 兼容旧 schema（无 mode_key 的扁平 shortcuts.xxx）
                    settings.get(f"shortcuts.{action}", defaults[action]),
                )
                action_to_keys[action] = raw
                for k in (raw or "").split(","):
                    k = k.strip()
                    if k:
                        key_map[k.upper()] = action
            return key_map, action_to_keys

        self._key_map_timing, timing_actions = _collect_map("timing_mode")
        self._key_map_edit, edit_actions = _collect_map("edit_mode")
        for key_name in ("SPACE", "Z", "X"):
            self._key_map_edit.pop(key_name, None)
        # 兼容旧字段名：当前活动 map（按播放状态切换；初始为编辑模式）
        self._key_map = self._key_map_edit
        # 应用默认音量
        default_volume = int(settings.get("audio.default_volume", 80))
        if self._timing_service:
            self._timing_service.set_volume(default_volume)
        self.transport.slider_volume.blockSignals(True)
        self.transport.slider_volume.setValue(default_volume)
        self.transport.slider_volume.blockSignals(False)
        # 应用默认速度
        default_speed = settings.get("audio.default_speed", 1.0)
        # 同步到音频引擎，避免 UI 与引擎速度分道扬镳
        if self._timing_service:
            self._timing_service.set_speed(default_speed)
        speed_pct = int(default_speed * 100)
        self.transport.edit_speed.blockSignals(True)
        self.transport.edit_speed.setText(f"{max(50, min(200, speed_pct))}%")
        self.transport.edit_speed.blockSignals(False)
        # 应用渲染偏移（与导出偏移联动）
        render_offset = settings.get("export.offset_ms", -100)
        self.preview.set_global_offset(render_offset)
        # 同步工具栏偏移控件
        self.toolbar.edit_offset.blockSignals(True)
        self.toolbar.edit_offset.setText(str(render_offset))
        self.toolbar.edit_offset.blockSignals(False)
        # 将偏移量写入所有字符的渲染/导出时间戳
        if self._project:
            for sentence in self._project.sentences:
                for ch in sentence.characters:
                    ch.set_offset(render_offset)
        # 应用歌词对齐方式
        lyrics_alignment = settings.get("ui.lyrics_alignment", "center")
        self.preview.set_alignment(lyrics_alignment)
        # 应用字体大小设置
        base_font_size = settings.get("ui.font_size", 18)
        current_line_size = settings.get("ui.current_line_font_size", 22)
        ruby_size = settings.get("ui.ruby_size", 10)
        cp_size = settings.get("ui.cp_size", 8)
        line_height_factor = settings.get("ui.line_height_factor", 1.20)
        self.preview.set_font_sizes(base_font_size, current_line_size, ruby_size, cp_size, line_height_factor)
        # 更新快捷键提示（#6：只保留 9 项核心）
        self._update_shortcut_hint(timing_actions, edit_actions)
        # #7：打轴按钮文字联动 shortcuts.timing_mode.tag_now
        tag_key_raw = timing_actions.get("tag_now", "Space")
        tag_first = tag_key_raw.split(",")[0].strip() if tag_key_raw else "Space"
        if hasattr(self, "btn_tag"):
            self.btn_tag.setText(f"打轴 ({tag_first})")
        # #8：同步模式指示器（首次应用设置时刷新）
        self._update_mode_indicator()

    def _update_shortcut_hint(
        self, timing_actions: dict, edit_actions: Optional[dict] = None
    ):
        """根据当前设置的快捷键映射，动态更新底部提示。

        #6：只显示 9 项核心动作（播放/停止/前进/后退/加速/减速/加节奏点/减节奏点/句尾），
        按当前模式（播放中=打轴模式，否则=编辑模式）取快捷键文本。
        """
        action_labels = [
            ("play_pause", "播放"),
            ("stop", "停止"),
            ("seek_back", "后退"),
            ("seek_forward", "前进"),
            ("speed_down", "减速"),
            ("speed_up", "加速"),
            ("add_checkpoint", "加节奏点"),
            ("remove_checkpoint", "减节奏点"),
            ("toggle_line_end", "句尾"),
        ]
        playing = bool(self._timing_service and self._timing_service.is_playing())
        active = timing_actions if playing else (edit_actions or timing_actions)
        parts = []
        for action, label in action_labels:
            key = active.get(action, "")
            if key:
                first_key = key.split(",")[0].strip()
                parts.append(f"{first_key}{label}")
        parts.append("Alt+→ 切换字内节奏点")
        if hasattr(self, "lbl_shortcut_hint"):
            self.lbl_shortcut_hint.setText(" ".join(parts))
        # 缓存以便模式切换时再次调用（无需重读设置）
        self._shortcut_actions_timing = timing_actions
        self._shortcut_actions_edit = edit_actions or timing_actions

    # ==================== 项目 ====================

    def _on_offset_changed(self, offset_ms: int):
        """工具栏偏移控件变更 — 更新设置、字符偏移时间戳和渲染缓存"""
        # 写入设置（与设置页面联动）
        try:
            from strange_uta_game.frontend.settings.settings_interface import (
                AppSettings,
            )

            app_settings = AppSettings()
            app_settings.set("export.offset_ms", offset_ms)
            app_settings.save()
        except Exception:
            pass
        # 更新所有字符的偏移时间戳
        if self._project:
            for sentence in self._project.sentences:
                for ch in sentence.characters:
                    ch.set_offset(offset_ms)
        # 更新渲染
        self.preview.set_global_offset(offset_ms)

    def set_project(self, project: Project):
        self._project = project
        self.preview.set_project(project)
        # 应用当前渲染/导出偏移到新加载项目的所有字符
        offset = self.preview._global_offset_ms
        for sentence in project.sentences:
            for ch in sentence.characters:
                ch.set_offset(offset)
        self._apply_checkpoint_position(
            self._timing_service.get_current_position()
            if self._timing_service
            else CheckpointPosition()
        )
        self._update_time_tags_display()
        self._update_status()

    def release_resources(self):
        """释放音频资源"""
        if self._timing_service:
            self._timing_service.release()

    # ==================== 拖拽加载 ====================

    def dragEnterEvent(self, a0: Optional[QDragEnterEvent]):
        if a0 is None:
            return
        mime = a0.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                if self._file_loader.can_accept_drop(url.toLocalFile()):
                    a0.acceptProposedAction()
                    return
        a0.ignore()

    def dropEvent(self, a0: Optional[QDropEvent]):
        if a0 is None:
            return
        mime = a0.mimeData()
        if mime is None or not mime.hasUrls():
            a0.ignore()
            return
        for url in mime.urls():
            self._file_loader.handle_drop(url.toLocalFile())
        a0.acceptProposedAction()

    # ==================== 工具栏操作 ====================

    def _on_paste_lyrics(self):
        """从剪贴板粘贴歌词（Ctrl+V）"""
        from PyQt6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if not clipboard:
            return

        text = clipboard.text()
        if not text or not text.strip():
            return

        # 检查是否可以加载
        if not self._file_loader.can_load_from_clipboard():
            InfoBar.warning(
                title="无法粘贴",
                content="仅在未创建项目或项目无歌词时可粘贴歌词",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        self._file_loader.load_lyrics_from_text(text)

    def _on_save(self):
        if not self._project:
            InfoBar.warning(
                title="无项目",
                content="请先创建或打开项目",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        store = getattr(self, "_store", None)

        # 已有保存路径 → 直接保存
        if store and store.save_path:
            if store.save():
                InfoBar.success(
                    title="保存成功",
                    content=store.save_path,
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=2000,
                    parent=self,
                )
                self.project_saved.emit()
            else:
                InfoBar.error(
                    title="保存失败",
                    content="无法保存到 " + (store.save_path or ""),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
            return

        # 无保存路径 → 弹出另存为对话框
        path, _ = QFileDialog.getSaveFileName(
            self, "保存项目", "", "StrangeUtaGame 项目 (*.sug);;所有文件 (*.*)"
        )
        if not path:
            return
        if not path.endswith(".sug"):
            path += ".sug"

        try:
            if store:
                success = store.save(path)
            else:
                from strange_uta_game.backend.infrastructure.persistence.sug_io import (
                    SugProjectParser,
                )

                SugProjectParser.save(self._project, path)
                success = True

            if success:
                InfoBar.success(
                    title="保存成功",
                    content=path,
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
                self.project_saved.emit()
            else:
                InfoBar.error(
                    title="保存失败",
                    content="无法保存到 " + path,
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
        except Exception as e:
            InfoBar.error(
                title="保存失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )

    def _on_new_project(self):
        """新建项目（检查当前项目是否需要保存）"""
        if self._project:
            store = getattr(self, "_store", None)
            # 检查是否有未保存的更改
            if store and store.dirty:
                msg = QMessageBox(self)
                msg.setWindowTitle("保存当前项目")
                msg.setText("当前项目有未保存的更改，是否保存？")
                btn_save = msg.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
                msg.addButton("放弃", QMessageBox.ButtonRole.DestructiveRole)
                btn_cancel = msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
                msg.setDefaultButton(btn_save)
                msg.exec()
                clicked = msg.clickedButton()
                if clicked is btn_save:
                    self._on_save()
                elif clicked is btn_cancel:
                    return

        # 创建新项目
        from strange_uta_game.backend.application import ProjectService

        project_service = ProjectService()
        project = project_service.create_project()
        if self._store:
            self._store._project = project
            self._store._save_path = None
            self._store.notify("project")
        else:
            self.set_project(project)

    def _on_save_as(self):
        """项目另存为"""
        if not self._project:
            InfoBar.warning(
                title="无项目",
                content="请先创建或打开项目",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "另存为", "", "StrangeUtaGame 项目 (*.sug);;所有文件 (*.*)"
        )
        if not path:
            return
        if not path.endswith(".sug"):
            path += ".sug"

        try:
            store = getattr(self, "_store", None)
            if store:
                success = store.save(path)
            else:
                from strange_uta_game.backend.infrastructure.persistence.sug_io import (
                    SugProjectParser,
                )
                SugProjectParser.save(self._project, path)
                success = True

            if success:
                InfoBar.success(
                    title="保存成功",
                    content=path,
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
                self.project_saved.emit()
            else:
                InfoBar.error(
                    title="保存失败",
                    content="无法保存到 " + path,
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
        except Exception as e:
            InfoBar.error(
                title="保存失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )

    def _on_load_project(self):
        """加载项目文件"""
        self._file_loader.prompt_load_project()

    def _on_load_audio(self):
        self._file_loader.prompt_load_audio()

    def _on_load_lyrics(self):
        """加载歌词文件到当前项目（替换现有歌词）。"""
        self._file_loader.prompt_load_lyrics()

    def _on_undo(self):
        if self._timing_service and self._timing_service.can_undo():
            self._timing_service.undo()
            self._update_time_tags_display()
            self._apply_checkpoint_position(self._timing_service.get_current_position())
            self._update_status()

    def _on_redo(self):
        if self._timing_service and self._timing_service.can_redo():
            self._timing_service.redo()
            self._update_time_tags_display()
            self._apply_checkpoint_position(self._timing_service.get_current_position())
            self._update_status()

    def _on_bulk_change(self):
        """Ctrl+H — 打开批量変更对话框，自动填充当前焦点字符的连词或划选区域"""
        from strange_uta_game.frontend.editor.timing import BulkChangeDialog

        initial_word = ""
        initial_reading = ""
        if self._project:
            line_idx = self.preview._current_line_idx
            char_idx = self.preview._current_char_idx
            if 0 <= line_idx < len(self._project.sentences):
                sentence = self._project.sentences[line_idx]
                text = sentence.text
                chars = sentence.characters

                # 优先使用划选区域（多字符选择）
                sel_line = self.preview._focus_line_idx
                sel_start = self.preview._focus_char_idx
                sel_end = self.preview._focus_char_range_end
                if sel_line >= 0 and sel_start >= 0 and sel_line == line_idx:
                    lo = min(sel_start, sel_end)
                    hi = max(sel_start, sel_end)
                    if lo < len(chars) and hi < len(chars) and hi >= lo:
                        initial_word = text[lo : hi + 1]
                        readings: list[str] = []
                        for ci in range(lo, hi + 1):
                            r = chars[ci].ruby
                            readings.append(r.text if r else "")
                        if any(readings):
                            initial_reading = ",".join(readings)
                elif 0 <= char_idx < len(chars):
                    # 回退到连词逻辑（由领域方法 Sentence.get_word_char_range 计算）
                    start, end = sentence.get_word_char_range(char_idx)
                    initial_word = text[start:end]
                    readings = []
                    for ci in range(start, end):
                        r = chars[ci].ruby
                        readings.append(r.text if r else "")
                    if any(readings):
                        initial_reading = ",".join(readings)

        dialog = BulkChangeDialog(
            self._project,
            self,
            initial_word=initial_word,
            initial_reading=initial_reading,
        )
        dialog.exec()

    def _on_modify_char(self):
        """打开修改所选字符对话框"""
        if not self._project:
            return

        # Determine selection range
        line_idx = self.preview._current_line_idx
        sel_line = self.preview._focus_line_idx
        sel_start = self.preview._focus_char_idx
        sel_end = self.preview._focus_char_range_end

        if sel_line >= 0 and sel_start >= 0:
            # Use drag selection
            use_line = sel_line
            start_idx = min(sel_start, sel_end)
            end_idx = max(sel_start, sel_end)
        else:
            # Use single char selection
            use_line = line_idx
            char_idx = self.preview._current_char_idx
            start_idx = char_idx
            end_idx = char_idx

        if use_line < 0 or use_line >= len(self._project.sentences):
            return
        sentence = self._project.sentences[use_line]
        if start_idx < 0 or end_idx >= len(sentence.characters):
            return

        # 快照 before：ModifyCharacterDialog 会原地修改 project.sentences
        before_sentences = deepcopy(self._project.sentences)

        dialog = ModifyCharacterDialog(sentence, start_idx, end_idx, self)
        dialog.exec()

        if dialog.was_modified():
            # 将本次修改登记为一次 SentenceSnapshotCommand（支持撤销/重做）
            command_manager = None
            if self._timing_service:
                command_manager = self._timing_service.command_manager
            if command_manager is not None:
                after_sentences = deepcopy(self._project.sentences)
                cmd = SentenceSnapshotCommand(
                    self._project,
                    before_sentences,
                    after_sentences,
                    f"修改字符（第 {use_line + 1} 句 第 {start_idx + 1}-{end_idx + 1} 字）",
                )
                # 我们已经原地修改完成，不希望 execute() 再跑一次：
                # 用直接入栈方式——调用 execute 会重置为 after_sentences（幂等，安全）
                command_manager.execute(cmd)

            # Rebuild global checkpoints
            if self._timing_service:
                self._timing_service.rebuild_global_checkpoints()
            self.refresh_lyric_display()
            self._update_time_tags_display()
            self._update_status()
            if hasattr(self, "_store") and self._store:
                self._store.notify("rubies")
                self._store.notify("checkpoints")
                self._store.notify("lyrics")

            # 弹窗汇总连词失败项
            failures = dialog.get_linked_failures()
            if failures:
                lines = []
                for abs_idx, ch, reason in failures[:20]:
                    lines.append(
                        f"  第 {use_line + 1} 句 第 {abs_idx + 1} 字「{ch}」：{reason}"
                    )
                more = ""
                if len(failures) > 20:
                    more = f"\n...（还有 {len(failures) - 20} 项未显示）"
                QMessageBox.information(
                    self,
                    "部分连词设置未应用",
                    "以下位置为末字/句尾/行尾，不能设置连词，已自动跳过：\n\n"
                    + "\n".join(lines)
                    + more,
                )

    def _on_delete_rubies_by_type(self):
        """工具栏「按类型删除注音」入口。

        与全文本编辑界面的同名功能逻辑保持一致（复用 DeleteRubyByTypeDialog 与
        扩展类型集合规则），但通过 :py:meth:`_execute_structural_edit` 包装为
        SentenceSnapshotCommand，支持撤销/重做并自动同步 timing_service。

        勾选 HIRAGANA → 同时移除小假名(ぁぃ等)与促音 っ；
        勾选 KATAKANA → 同时移除小假名(ァィ等)与促音 ッ。
        """
        if not self._project:
            return
        # 复用 fulltext_interface 的对话框（CharType 复选 + 默认勾选平假名/片假名）
        from .fulltext_interface import DeleteRubyByTypeDialog

        dlg = DeleteRubyByTypeDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected = dlg.selected_types()
        if not selected:
            return

        # 扩展匹配集：与 fulltext_interface._on_delete_rubies_by_type 完全一致
        _SMALL_HIRAGANA = set("ぁぃぅぇぉゃゅょゎ")
        _SMALL_KATAKANA = set("ァィゥェォャュョヮゕゖ")
        extended = set(selected)
        if CharType.HIRAGANA in selected:
            extended.add(CharType.SOKUON)  # っ
        if CharType.KATAKANA in selected:
            extended.add(CharType.SOKUON)  # ッ

        removed_box = [0]

        def _mutate() -> Optional[tuple[int, int, Optional[int], str]]:
            assert self._project is not None
            removed = 0
            for sentence in self._project.sentences:
                for ch in sentence.characters:
                    if not ch.ruby:
                        continue
                    ct = get_char_type(ch.char)
                    if ct in extended:
                        if ct == CharType.SOKUON:
                            if ch.char == "っ" and CharType.HIRAGANA not in selected:
                                continue
                            if ch.char == "ッ" and CharType.KATAKANA not in selected:
                                continue
                        ch.set_ruby(None)
                        removed += 1
                    elif CharType.HIRAGANA in selected and ch.char in _SMALL_HIRAGANA:
                        ch.set_ruby(None)
                        removed += 1
                    elif CharType.KATAKANA in selected and ch.char in _SMALL_KATAKANA:
                        ch.set_ruby(None)
                        removed += 1
            if removed == 0:
                return None
            removed_box[0] = removed
            # 焦点保持在当前位置；ruby 变更使用 "rubies" 通道刷新（与 fulltext 一致）
            return (self._current_line_idx, self.preview._current_char_idx, None, "rubies")

        ok = self._execute_structural_edit("按类型删除注音", _mutate)
        if not ok:
            InfoBar.info(
                title="无变化",
                content="所选类型范围内没有需要删除的注音",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
            return

        labels = ", ".join(
            label for ct, label in DeleteRubyByTypeDialog._TYPE_LABELS if ct in selected
        )
        InfoBar.success(
            title="删除完成",
            content=f"已删除 {removed_box[0]} 个注音（类型: {labels}）",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4000,
            parent=self,
        )

    def _on_insert_guide(self):
        """打开插入导唱符对话框"""
        if not self._project:
            return

        line_idx = self.preview._current_line_idx
        char_idx = self.preview._current_char_idx

        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx < 0 or char_idx >= len(sentence.characters):
            return

        dialog = InsertGuideSymbolDialog(sentence, char_idx, self)
        dialog.exec()

        if dialog.was_modified():
            # Rebuild global checkpoints
            if self._timing_service:
                self._timing_service.rebuild_global_checkpoints()
            self.refresh_lyric_display()
            self._update_time_tags_display()
            self._update_status()
            if hasattr(self, "_store") and self._store:
                self._store.notify("lyrics")

    # ==================== 音频 ====================

    def _on_singer_change_selection(
        self, line_idx: int, start_char: int, end_char: int, singer_id: str
    ):
        """划词选中后，修改选中范围内所有字符的 per-char singer_id"""
        if (
            not self._project
            or line_idx < 0
            or line_idx >= len(self._project.sentences)
        ):
            return

        sentence = self._project.sentences[line_idx]

        # 更新选中范围内每个字符的 singer_id
        for ci in range(start_char, end_char + 1):
            if ci < len(sentence.characters):
                sentence.characters[ci].singer_id = singer_id
                sentence.characters[ci].push_to_ruby()

        # 如果选中了整行，也更新 sentence.singer_id
        if start_char == 0 and end_char >= len(sentence.chars) - 1:
            sentence.singer_id = singer_id

        if hasattr(self, "_store") and self._store:
            self._store.notify("lyrics")
        self.preview.update()

        InfoBar.success(
            title="演唱者已更新",
            content=f"已将第 {line_idx + 1} 行第 {start_char + 1}~{end_char + 1} 字的演唱者更改",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def load_audio(self, file_path: str) -> bool:
        if not self._timing_service:
            return False

        try:
            # 创建状态提示
            state_tooltip = StateToolTip("正在加载音频", "正在读取音频文件...", self)
            green = theme.status_complete.name()
            state_tooltip.setStyleSheet(f"""
                StateToolTip {{
                    background-color: {green};
                    border: 1px solid {green};
                    border-radius: 8px;
                }}
                StateToolTip QLabel {{
                    color: white;
                }}
            """)
            state_tooltip.move(state_tooltip.getSuitablePos())
            state_tooltip.show()

            def on_progress(stage: str, value: float):
                state_tooltip.setContent(stage)
                from PyQt6.QtWidgets import QApplication
                QApplication.processEvents()

            self._timing_service.load_audio(file_path, progress_cb=on_progress)
            state_tooltip.setState(True)  # 设置为完成状态
            state_tooltip.setContent("加载完成")
            state_tooltip.close()

            info = self._timing_service.get_audio_info()
            if info:
                self.transport.set_duration(info.duration_ms)
                self.timeline.set_duration(info.duration_ms)
                self.preview.set_duration(info.duration_ms)
                self.transport.set_position(0)
                self.timeline.set_position(0)

                # 获取音频采样数据用于波形显示
                samples = self._timing_service.get_original_samples()
                if samples is not None:
                    self.timeline.set_audio_data(
                        samples, info.sample_rate, info.channels
                    )

            self._audio_file_path = file_path
            self.toolbar.lbl_audio.setText(Path(file_path).name)

            # 应用设置中的默认音量和速度
            if self._timing_service:
                main_window = self.window()
                setting_iface = getattr(main_window, "settingInterface", None)
                if setting_iface is not None:
                    settings = setting_iface.get_settings()
                    # 默认音量
                    default_volume = int(settings.get("audio.default_volume", 80))
                    self._timing_service.set_volume(default_volume)
                    self.transport.slider_volume.blockSignals(True)
                    self.transport.slider_volume.setValue(default_volume)
                    self.transport.slider_volume.blockSignals(False)
                    # 默认速度
                    default_speed = settings.get("audio.default_speed", 1.0)
                    self._timing_service.set_speed(default_speed)
                    speed_pct = int(default_speed * 100)
                    self.transport.edit_speed.blockSignals(True)
                    self.transport.edit_speed.setText(f"{max(50, min(200, speed_pct))}%")
                    self.transport.edit_speed.blockSignals(False)

            # 与 Home 页加载音频的动作对称：广播 audio 变更，使导出页等订阅者同步
            if hasattr(self, "_store") and self._store:
                self._store.set_audio_path(file_path)

            InfoBar.success(
                title="音频已加载",
                content=Path(file_path).name,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return True
        except AudioLoadError as e:
            InfoBar.error(
                title="加载失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
            return False
        except Exception as e:
            InfoBar.error(
                title="加载失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
            return False

    def _update_mode_indicator(self):
        """#8：根据播放状态更新左下角模式指示器与激活的 key_map。

        - 播放中 → "模式：打轴"，使用 _key_map_timing
        - 未播放 → "模式：编辑"，使用 _key_map_edit
        同步刷新底部快捷键提示（因为两模式文本可能不同）。
        """
        if not hasattr(self, "lbl_mode"):
            return
        playing = bool(self._timing_service and self._timing_service.is_playing())
        if playing:
            self.lbl_mode.setText("模式：打轴")
            self.lbl_mode.setStyleSheet(
                "font-size: 12px; padding: 2px 8px; border-radius: 4px;"
                "background-color: #ffd54f; color: #333; font-weight: bold;"
            )
            if hasattr(self, "_key_map_timing"):
                self._key_map = self._key_map_timing
        else:
            self.lbl_mode.setText("模式：编辑")
            self.lbl_mode.setStyleSheet(
                "font-size: 12px; padding: 2px 8px; border-radius: 4px;"
                "background-color: #e0e0e0; color: #444;"
            )
            if hasattr(self, "_key_map_edit"):
                self._key_map = self._key_map_edit
        # 刷新快捷键提示（按新模式取文本）
        if hasattr(self, "_shortcut_actions_timing"):
            self._update_shortcut_hint(
                self._shortcut_actions_timing,
                getattr(self, "_shortcut_actions_edit", None),
            )

    # ==================== 播放控制 ====================

    def _on_play(self):
        if self._timing_service:
            try:
                self._timing_service.play()
                self.transport.set_playing(self._timing_service.is_playing())
                self.preview.set_playing(self._timing_service.is_playing())
                self.lbl_status.setText("播放中")
                self._update_mode_indicator()
            except Exception as e:
                self._show_runtime_error(str(e))

    def _on_pause(self):
        if self._timing_service:
            self._timing_service.pause()
            self.transport.set_playing(False)
            self.preview.set_playing(False)
            self.lbl_status.setText("已暂停")
            self._update_mode_indicator()
            # 切换到编辑模式时校验所有行时间戳
            self._validate_all_timestamps()

    def _on_stop(self):
        if self._timing_service:
            self._timing_service.stop()
            self.transport.set_playing(False)
            self.preview.set_playing(False)
            self.transport.set_position(0)
            self.timeline.set_position(0)
            self.lbl_status.setText("已停止")
            self._update_mode_indicator()
            # 切换到编辑模式时校验所有行时间戳
            self._validate_all_timestamps()

    def _on_seek(self, ms: int):
        if self._timing_service:
            self._timing_service.seek(ms)
            self.transport.set_position(ms)
            self.timeline.set_position(ms)

    def _on_speed_changed(self, speed: float):
        if self._timing_service:
            self._timing_service.set_speed(speed)

    def _on_volume_changed(self, vol: int):
        if self._timing_service:
            self._timing_service.set_volume(vol)

    # ==================== 打轴 ====================

    def _on_tag_now(self):
        if not self._timing_service:
            return

        try:
            self._timing_service.on_timing_key_pressed("SPACE")
            self._timing_service.on_timing_key_released("SPACE")
        except Exception as e:
            self._show_runtime_error(str(e))

    def _on_clear_current_line_tags(self):
        if not self._timing_service:
            return

        self._timing_service.clear_timetags_for_current_line()
        self._update_time_tags_display()
        self._update_status()

    def _on_line_clicked(self, idx: int):
        # 切换行前，校验上一行的时间戳
        if self._project and 0 <= self._current_line_idx < len(self._project.sentences):
            self._validate_line_timestamps(self._current_line_idx)
        self._current_line_idx = idx
        self._update_line_info()

    def _validate_line_timestamps(self, line_idx: int) -> None:
        """校验指定行的所有字符时间戳，确保不超过允许的数量。

        规则：
        - 每个字符允许的时间戳数量 = check_count + (1 if is_sentence_end else 0)
        - timestamps 列表长度不允许超过 check_count
        - 如果有冗余时间戳，截断并推送至 ruby
        """
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        for ch in sentence.characters:
            max_timestamps = ch.check_count
            if len(ch.timestamps) > max_timestamps:
                ch.timestamps = ch.timestamps[:max_timestamps]
                ch._update_offset_timestamps()
                ch.push_to_ruby()

    def _validate_all_timestamps(self) -> None:
        """校验项目中所有行的时间戳（切换到编辑模式时调用）"""
        if not self._project:
            return
        for line_idx in range(len(self._project.sentences)):
            self._validate_line_timestamps(line_idx)

    def _resolve_target_char(self) -> tuple[int, int]:
        """解析字符级操作的目标 (line_idx, char_idx)。

        双域设计：
        - focus 域 (`preview._focus_*`)：用户视觉/操作真理，由点击/拖选/纯←→/打轴驱动，
          不被 cp 自动跳跃污染。字符级操作的优先来源。
        - current 域 (`self._current_line_idx` + `preview._current_char_idx`)：
          后台 TimingService 反馈的合法 cp 位置，会被 cp 跳跃污染。打轴模式
          (TimingService.is_playing()) 下字符级操作目标 — 因为打轴时 TimingService
          自动推进，focus 是用户上次点的位置，可能不是当前正在打的字符。

        Returns:
            (line_idx, char_idx)：目标字符。无 focus 时回退 current；
            两域都无效时返回 (-1, -1)。
        """
        # focus 域优先（line + char 一起取，避免 cp 跳跃后
        # _current_line_idx 与 _focus_line_idx 不一致导致目标错位）
        if (
            self.preview._focus_line_idx >= 0
            and self.preview._focus_char_idx >= 0
            and self.preview._focus_char_range_end >= 0
        ):
            line_idx = self.preview._focus_line_idx
            char_idx = min(
                self.preview._focus_char_idx,
                self.preview._focus_char_range_end,
            )
            return line_idx, char_idx
        # focus 无效：回退 current
        return self._current_line_idx, self.preview._current_char_idx

    def _on_checkpoint_clicked(self, line_idx: int, char_idx: int, cp_idx: int):
        """点击 checkpoint 标记：仅切换 selected_cp 与音频跳转，不移动光标。

        selected_cp（Character.selected_checkpoint_idx + preview._current_checkpoint_idx）
        与 selected_char（preview._current_char_idx + _focus_*）是两个独立状态：
        - 点击 cp 标记 → 仅 selected_cp 改变；selected_char（光标）保持
        - 点击字符文本 / 方向键 → selected_char（光标）改变
        - F4/F5/F6/Alt+←→ 等编辑/循环操作 → 作用于 selected_char

        通过临时设置 _suppress_cp_cursor_move 阻止
        _apply_checkpoint_position 调用 set_current_position。
        """
        if not self._timing_service:
            return
        self._suppress_cp_cursor_move = True
        try:
            self._timing_service.move_to_checkpoint(line_idx, char_idx, cp_idx)
        finally:
            self._suppress_cp_cursor_move = False
        self._update_time_tags_display()
        self._update_status()

    def _on_char_selected(self, line_idx: int, char_idx: int):
        """点击字符选中 — 移动到该字符的第一个 checkpoint。

        若字符无 checkpoint（check_count=0 且非句尾），保持视觉焦点在
        该字符上，方便用户通过 F4 添加节奏点；内部打轴位置仍移到最近的
        下一个有效 checkpoint，确保按空格时能正确赋时间戳。
        """
        # #9: 单一 set_current_position 入口，避免 timing_service 回调在
        # 同帧内反复覆盖 _scroll_center_line 造成空白行抖动。仅当字符无
        # checkpoint 时由本地直接居中；否则交给 _apply_checkpoint_position
        # 统一处理。
        self._current_line_idx = line_idx

        # 判断当前字符是否有 checkpoint
        no_checkpoint = True
        if self._project and line_idx < len(self._project.sentences):
            sentence = self._project.sentences[line_idx]
            if char_idx < len(sentence.characters):
                ch = sentence.characters[char_idx]
                no_checkpoint = ch.check_count == 0 and not ch.is_sentence_end

        if no_checkpoint:
            # 无 checkpoint：直接把视觉焦点定到被点击字符
            self.preview.set_current_position(line_idx, char_idx)
        else:
            # 有 checkpoint：由 timing_service 回调经 _apply_checkpoint_position
            # 统一调用 set_current_position，避免双写 _scroll_center_line
            if self._timing_service:
                self._timing_service.move_to_checkpoint(line_idx, char_idx, 0)
            else:
                self.preview.set_current_position(line_idx, char_idx)
            self._update_line_info()
            self._update_time_tags_display()
            self._update_status()
            return

        # 无 checkpoint 分支也触发 timing_service 移动（便于随后空格赋时间戳）
        if self._timing_service:
            self._timing_service.move_to_checkpoint(line_idx, char_idx, 0)

        self._update_line_info()
        self._update_time_tags_display()
        self._update_status()

    def _on_char_edit_requested(self, line_idx: int, char_idx: int):
        """F2 键弹出注音编辑对话框"""
        if not self._project or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx >= len(sentence.chars):
            return
        dialog = CharEditDialog(sentence, char_idx, self)
        dialog.exec()
        if dialog.was_modified():
            self.preview._update_display()
            self._update_time_tags_display()
            self._update_status()
            if hasattr(self, "_store"):
                self._store.notify("rubies")
                self._store.notify("checkpoints")
                self._store.notify("lyrics")

    def _add_checkpoint(self):
        """F4 增加当前字符节奏点 (+1)。"""
        self._change_checkpoint(delta=1)

    def _remove_checkpoint(self):
        """F5 删除当前字符节奏点 (-1)，最小为 0。"""
        self._change_checkpoint(delta=-1)

    def _adjust_current_timestamp(self, delta_ms: int):
        """Alt+↑/↓ 微调当前选中 checkpoint 的时间戳。

        批 18 #8：委托给 TimingService.adjust_current_timestamp 统一处理，
        由服务层保证 _update_offset_timestamps + push_to_ruby 双同步。
        """
        if not self._project or not self._timing_service:
            return
        if not self._timing_service.adjust_current_timestamp(delta_ms):
            return
        self._update_time_tags_display()
        self.refresh_lyric_display()
        self._update_line_info()
        if hasattr(self, "_store") and self._store:
            self._store.notify("timetags")

    def _cycle_current_checkpoint(self, direction: int = 1):
        """#2：Alt+→/Alt+← 循环切换"当前选中字符"的 checkpoint 索引。

        目标字符优先级：
        1. 若 KaraokePreview 存在有效选中范围，使用选中字符的起点
           (line = _focus_line_idx, char = min(sel_start, sel_end))。
        2. 否则回退到 TimingService.get_current_position()（播放/打轴上下文）。

        句尾字符若带 is_sentence_end，则句尾 checkpoint 也在循环序列内
        （位置为 check_count）。

        Args:
            direction: +1 表示下一个 checkpoint（Alt+→），-1 表示上一个（Alt+←）。
        """
        if not self._project or not self._timing_service:
            return
        # 优先用选中字符
        if (
            self.preview._focus_line_idx >= 0
            and self.preview._focus_char_idx >= 0
            and self.preview._focus_char_range_end >= 0
        ):
            line_idx = self.preview._focus_line_idx
            char_idx = min(self.preview._focus_char_idx, self.preview._focus_char_range_end)
            # 以 TimingService 当前 checkpoint_idx 为起点（若行/字匹配），
            # 否则从 0 起。
            pos = self._timing_service.get_current_position()
            base_idx = (
                pos.checkpoint_idx
                if (pos.line_idx == line_idx and pos.char_idx == char_idx)
                else 0
            )
        else:
            pos = self._timing_service.get_current_position()
            line_idx = pos.line_idx
            char_idx = pos.char_idx
            base_idx = pos.checkpoint_idx
        if line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx >= len(sentence.characters):
            return
        ch = sentence.characters[char_idx]
        total = ch.check_count + (1 if ch.is_sentence_end else 0)
        if total <= 0:
            return
        step = 1 if direction >= 0 else -1
        next_idx = (base_idx + step) % total
        self._timing_service.move_to_checkpoint(line_idx, char_idx, next_idx)
        self._update_line_info()
        self.refresh_lyric_display()

    def _rebuild_checkpoints(self):
        if self._timing_service:
            if hasattr(self._timing_service, "rebuild_global_checkpoints"):
                self._timing_service.rebuild_global_checkpoints()
            else:
                self._timing_service.rebuild_global_checkpoints()

    def _sync_after_structure_change(
        self,
        change_type: str = "lyrics",
        focus_line_idx: Optional[int] = None,
        focus_char_idx: Optional[int] = None,
        checkpoint_idx: Optional[int] = None,
    ):
        if not self._project:
            return

        self._rebuild_checkpoints()

        total_lines = len(self._project.sentences)
        if total_lines == 0:
            self._current_line_idx = 0
            self.preview._current_line_idx = 0
            self.preview._current_char_idx = 0
            self.preview._current_checkpoint_idx = None
            self.refresh_lyric_display()
            self._update_time_tags_display()
            self._update_status()
            return

        line_idx = focus_line_idx if focus_line_idx is not None else self._current_line_idx
        line_idx = max(0, min(line_idx, total_lines - 1))
        sentence = self._project.sentences[line_idx]

        if sentence.characters:
            char_idx = focus_char_idx if focus_char_idx is not None else self.preview._current_char_idx
            char_idx = max(0, min(char_idx, len(sentence.characters) - 1))
        else:
            char_idx = 0

        self._update_selected_checkpoint(line_idx, char_idx, checkpoint_idx)
        self.preview.set_current_position(line_idx, char_idx)
        self._current_line_idx = line_idx

        if self._timing_service and sentence.characters:
            target_cp = checkpoint_idx if checkpoint_idx is not None else 0
            self._timing_service.move_to_checkpoint(line_idx, char_idx, target_cp)

        self.refresh_lyric_display()
        self._update_time_tags_display()
        self._update_status()
        if hasattr(self, "_store") and self._store:
            self._store.notify(change_type)

    def _execute_structural_edit(
        self,
        description: str,
        mutator: Callable[[], Optional[tuple[int, int, Optional[int], str]]],
    ) -> bool:
        if not self._project:
            return False

        before_sentences = deepcopy(self._project.sentences)
        result = mutator()
        if result is None:
            return False

        after_sentences = deepcopy(self._project.sentences)
        command_manager = None
        if self._timing_service:
            command_manager = self._timing_service.command_manager
        if command_manager is not None:
            command = SentenceSnapshotCommand(
                self._project,
                before_sentences,
                after_sentences,
                description,
            )
            command_manager.execute(command)

        focus_line_idx, focus_char_idx, checkpoint_idx, change_type = result
        self._sync_after_structure_change(
            change_type=change_type,
            focus_line_idx=focus_line_idx,
            focus_char_idx=focus_char_idx,
            checkpoint_idx=checkpoint_idx,
        )
        return True

    def _delete_char_range(
        self, line_idx: int, start_idx: int, end_idx: int
    ) -> Optional[tuple[int, int, Optional[int], str]]:
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return None

        sentence = self._project.sentences[line_idx]
        if not sentence.characters:
            return None

        start = max(0, min(start_idx, len(sentence.characters) - 1))
        end = max(start + 1, min(end_idx, len(sentence.characters)))
        delete_count = end - start
        for _ in range(delete_count):
            became_empty = sentence.delete_character(start)
            if became_empty:
                break

        if not sentence.characters:
            self._project.delete_line(line_idx)
            if not self._project.sentences:
                return 0, 0, None, "lyrics"
            new_line_idx = max(0, min(line_idx, len(self._project.sentences) - 1))
            new_sentence = self._project.sentences[new_line_idx]
            new_char_idx = 0 if not new_sentence.characters else min(start, len(new_sentence.characters) - 1)
            return new_line_idx, new_char_idx, 0, "lyrics"

        new_char_idx = min(start, len(sentence.characters) - 1)
        return line_idx, new_char_idx, 0, "lyrics"
    
    def _delete_timestamp(self, line_idx: int, char_idx: int) :
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return None

        sentence = self._project.sentences[line_idx]
        if not sentence.characters:
            return None
        
        sentence.clear_one_timestamps(char_idx)

    def _insert_line_break_at_current(self):
        if not self._project:
            return
        line_idx = self._current_line_idx
        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        char_idx = self.preview._current_char_idx
        if char_idx < 0 or char_idx >= len(sentence.characters):
            return

        project = self._project

        self._execute_structural_edit(
            "插入换行",
            lambda: (
                project.insert_line_break(line_idx, char_idx)
                or (line_idx + 1, 0, 0, "lyrics")
            ),
        )

    def _delete_current_selection_or_char(self):
        if not self._project:
            return

        # Del 仅在编辑模式触发（keyPressEvent 路由）。focus 域为真理：
        # 用户拖选范围 → 删整段；单点 focus → 删该字符；focus 无效 → 删 current。
        if (
            self.preview._focus_line_idx >= 0
            and self.preview._focus_char_idx >= 0
            and self.preview._focus_char_range_end >= 0
        ):
            line_idx = self.preview._focus_line_idx
            start = min(self.preview._focus_char_idx, self.preview._focus_char_range_end)
            end = max(self.preview._focus_char_idx, self.preview._focus_char_range_end) + 1
        else:
            line_idx = self._current_line_idx
            start = self.preview._current_char_idx
            end = start + 1

        self._execute_structural_edit(
            "删除字符",
            lambda: self._delete_char_range(line_idx, start, end),
        )

    def _toggle_sentence_end_at_current(self):
        if not self._project:
            return
        # `.` (编辑模式) / F4 (打轴模式) 共用入口；目标字符由 `_resolve_target_char()`
        # 按模式分流：编辑模式 focus 优先，打轴模式 current。
        line_idx, char_idx = self._resolve_target_char()
        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx < 0 or char_idx >= len(sentence.characters):
            return

        self._execute_structural_edit(
            "切换句尾",
            lambda: (
                sentence.toggle_sentence_end(char_idx)
                or (line_idx, char_idx, 0, "checkpoints")
            ),
        )

    def _change_checkpoint(self, delta: int):
        """增加或减少"当前选中字符"的节奏点数量。

        通过 `_resolve_target_char()` 解析目标：编辑/编辑模式下都 focus 域优先
        （用户点击/拖选/纯←→设置的字符，不被 cp 自动跳跃污染）；打轴模式
        """
        if not self._project:
            return
        line_idx, char_idx = self._resolve_target_char()
        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx < 0 or char_idx >= len(sentence.characters):
            return

        def _mutate():
            if delta > 0:
                sentence.add_checkpoint(char_idx)
            else:
                # 减到 0 时自动退化为 Nicokara 无 mora 格式（注音文本保留）
                sentence.remove_checkpoint(char_idx, force=True)
            cp_idx = self.preview._current_checkpoint_idx
            if cp_idx is not None and delta < 0:
                cp_idx = min(cp_idx, sentence.characters[char_idx].check_count)
            return line_idx, char_idx, cp_idx if cp_idx is not None else 0, "checkpoints"

        self._execute_structural_edit("调整节奏点", _mutate)

    def _toggle_line_end(self):
        """F6 切换当前字符的句尾标记 (is_line_end)。

        句尾标记独立于普通 checkpoint 数量。
        """
        if not self._project:
            return
        line_idx = self._current_line_idx
        if line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        char_idx = self.preview._current_char_idx
        if char_idx >= len(sentence.characters):
            return

        self._execute_structural_edit(
            "切换句尾",
            lambda: (
                sentence.toggle_sentence_end(char_idx)
                or (line_idx, char_idx, 0, "checkpoints")
            ),
        )

    def _toggle_word_join(self):
        """F3 连词/取消连词 — toggle 当前字符的 linked_to_next 标记"""
        if not self._project:
            return
        line_idx = self._current_line_idx
        if line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        char_idx = self.preview._current_char_idx
        if char_idx >= len(sentence.characters):
            return

        # 不能在最后一个字符上连词
        if char_idx >= len(sentence.characters) - 1:
            InfoBar.warning(
                title="无法连词",
                content="已是最后一个字符",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2000,
                parent=self,
            )
            return

        ch = sentence.characters[char_idx]
        new_linked = not ch.linked_to_next
        ch.linked_to_next = new_linked

        if self._timing_service:
            self._timing_service.rebuild_global_checkpoints()
        self.refresh_lyric_display()
        self.preview.repaint()  # 强制同步重绘，确保连词视觉立即更新
        self._update_status()
        if hasattr(self, "_store") and self._store:
            self._store.notify("checkpoints")

        InfoBar.success(
            title="连词" if new_linked else "取消连词",
            content=f"已{'连接' if new_linked else '断开'}「{sentence.chars[char_idx]}」与「{sentence.chars[char_idx + 1]}」",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def _on_nav_line(self, delta: int):
        """方向键导航：上一行 (delta=-1) 或下一行 (delta=+1)。

        编辑模式：focus 域为真理来源（与 ←→/Space/Backspace/`.` 一致）。
        起点取 focus 行（无效则 current），目标行落在第一个字符 (char_idx=0)。
        使用 :py:meth:`Project.find_prev_line_with_characters` /
        :py:meth:`Project.find_next_line_with_characters` 跳过空行（无字符的行）。
        到达项目首尾时停止。

        打轴模式：保持原 cp 跳跃语义（focus 不跟随，current 由 TimingService 推进）。
        """
        if not self._project or not self._timing_service:
            return
        sentences = self._project.sentences

        # playing = bool(self._timing_service.is_playing())
        # if playing:
        #     # 打轴模式：原行为不变（基于 current 行 + cp 跳跃）
        #     if delta < 0:
        #         cand = self._project.find_prev_line_with_checkpoints(self._current_line_idx)
        #         if cand < 0:
        #             return
        #         new_idx = cand
        #     else:
        #         new_idx = self._current_line_idx + delta
        #         if new_idx < 0 or new_idx >= len(sentences):
        #             return
        #     self._timing_service.move_to_checkpoint(new_idx, 0, 0)
        #     self._update_time_tags_display()
        #     self._update_status()
        #     return

        # 编辑模式：focus 起点 + 跳空行 + 写 focus + 驱动 current
        if self.preview._focus_line_idx >= 0:
            line_idx = self.preview._focus_line_idx
        else:
            line_idx = self._current_line_idx
        if delta < 0:
            cand = self._project.find_prev_line_with_characters(line_idx)
        else:
            cand = self._project.find_next_line_with_characters(line_idx)
        if cand < 0:
            return
        new_line, new_char = cand, 0
        # 行切换前校验当前行的时间戳
        if new_line != line_idx:
            self._validate_line_timestamps(line_idx)
        # 直接写 focus 域（与 _on_nav_char 同款，不依赖 cp 回调链污染）
        self.preview._focus_line_idx = new_line
        self.preview._focus_char_idx = new_char
        self.preview._focus_char_range_end = new_char
        # 驱动 current 跟随：找最近 cp 反馈到 current
        self._timing_service.move_to_checkpoint(new_line, new_char, 0)
        self._update_time_tags_display()
        self._update_status()
        self.preview.update()

    def _on_nav_char(self, delta: int):
        """方向键左右导航：上一字符 (delta=-1) 或下一字符 (delta=+1)。

        字符级操作 → 读 focus 域（用户视觉真理），不读被 cp 跳跃污染的
        current 域。同时直接更新 focus 域字段，并驱动 current 跟随
        (move_to_checkpoint 让 TimingService 找最近 cp 反馈到 current)。

        行内移动：在当前 focus 行的字符序列内 ±1。
        跨行边界：
        - delta=-1 且 focus 已在首字符 (char_idx == 0)：跳到上一行的末字符。
        - delta=+1 且 focus 已在末字符：跳到下一行的首字符 (char_idx = 0)。
        跨行使用 :py:meth:`Project.find_prev_line_with_characters` /
        :py:meth:`Project.find_next_line_with_characters` 跳过空行。
        到达项目首尾时停止（不循环）。

        Args:
            delta: -1 表示左移 (LEFT)，+1 表示右移 (RIGHT)。
        """
        if not self._project or not self._timing_service:
            return
        sentences = self._project.sentences
        # focus 域作为真理来源；focus 无效则回退 current 一次
        if self.preview._focus_line_idx >= 0 and self.preview._focus_char_idx >= 0:
            line_idx = self.preview._focus_line_idx
            char_idx = min(
                self.preview._focus_char_idx,
                self.preview._focus_char_range_end
                if self.preview._focus_char_range_end >= 0
                else self.preview._focus_char_idx,
            )
        else:
            line_idx = self._current_line_idx
            char_idx = self.preview._current_char_idx
        if line_idx < 0 or line_idx >= len(sentences):
            return
        chars = sentences[line_idx].characters
        if delta < 0:
            if char_idx > 0:
                new_line, new_char = line_idx, char_idx - 1
            else:
                cand = self._project.find_prev_line_with_characters(line_idx)
                if cand < 0:
                    return
                prev_chars = sentences[cand].characters
                new_line, new_char = cand, max(0, len(prev_chars) - 1)
        else:
            if char_idx < len(chars) - 1:
                new_line, new_char = line_idx, char_idx + 1
            else:
                cand = self._project.find_next_line_with_characters(line_idx)
                if cand < 0:
                    return
                new_line, new_char = cand, 0
        # 直接更新 focus 域（不依赖 cp 回调链）
        self.preview._focus_line_idx = new_line
        self.preview._focus_char_idx = new_char
        self.preview._focus_char_range_end = new_char
        # 驱动 current 跟随：让 TimingService 找最近 cp，
        # 反馈经 _apply_checkpoint_position 更新 current 域。
        self._timing_service.move_to_checkpoint(new_line, new_char, 0)
        self._update_time_tags_display()
        self._update_status()

    def _on_seek_to_char(self, line_idx: int, char_idx: int):
        """双击字符 → 跳转到该字符的时间戳"""
        if not self._project or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx >= len(sentence.chars):
            return

        char = sentence.get_character(char_idx)
        if not char:
            return

        tags = char.all_global_timestamps
        if tags:
            self._on_seek(tags[0])

        # 同时移动打轴位置到该字符
        if self._timing_service:
            self._timing_service.move_to_checkpoint(line_idx, char_idx, 0)
            self._update_time_tags_display()
            self._update_status()
    
    def _on_seek_to_checkpoint(self, line_idx: int, char_idx: int, cp_idx: int):
        """单击字符 → 跳转到 checkpoint 前指定毫秒数"""
        if not self._project or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx >= len(sentence.chars):
            return
        if cp_idx:
            # 未开发
            pass
        jump_before = getattr(self, "_jump_before_ms", 3000)
        char = sentence.get_character(char_idx)
        if char:
            tags = char.all_global_timestamps
            if tags:
                target_ms = max(0, tags[0] - jump_before)
                self._on_seek(target_ms)

        # 同时移动打轴位置到该字符
        if self._timing_service:
            self._timing_service.move_to_checkpoint(line_idx, char_idx, 0)
            self._update_time_tags_display()
            self._update_status()

    def _on_delete_chars_requested(self, line_idx: int, start: int, end: int):
        self._execute_structural_edit(
            "删除字符",
            lambda: self._delete_char_range(line_idx, start, end),
        )
    
    def _on_delete_timestamp_requested(self, line_idx: int, char_idx: int):
        if not self._project or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx >= len(sentence.chars):
            return

        jump_before = getattr(self, "_jump_before_ms", 3000)
        char = sentence.get_character(char_idx)
        if char:
            tags = char.all_global_timestamps
            if tags:
                target_ms = max(0, tags[0] - jump_before)
                self._on_seek(target_ms)

        # 同时移动打轴位置到该字符
        if self._timing_service:
            self._timing_service.move_to_checkpoint(line_idx, char_idx, 0)
            self._update_time_tags_display()
            self._update_status()
        # 清除当前字符的时间戳
        self._delete_timestamp(line_idx, char_idx)

    def _on_insert_space_after_requested(self, line_idx: int, char_idx: int):
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        project = self._project

        def _mutate():
            sentence = project.sentences[line_idx]
            if char_idx < 0 or char_idx >= len(sentence.characters):
                return None
            ref_char = sentence.characters[char_idx]
            new_char = Character(
                char=" ",
                check_count=1,
                singer_id=ref_char.singer_id or sentence.singer_id,
            )
            sentence.insert_character(char_idx + 1, new_char)
            return line_idx, char_idx + 1, 0, "lyrics"

        self._execute_structural_edit("插入空格", _mutate)

    def _on_merge_line_up_requested(self, line_idx: int):
        if not self._project:
            return
        project = self._project
        self._execute_structural_edit(
            "合并上一行",
            lambda: (
                (
                    line_idx - 1,
                    max(0, len(project.sentences[line_idx - 1].characters) - 1),
                    0,
                    "lyrics",
                )
                if project.merge_line_into_previous(line_idx)
                else None
            ),
        )

    def _on_delete_line_requested(self, line_idx: int):
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        project = self._project

        def _mutate():
            project.delete_line(line_idx)
            if not project.sentences:
                return 0, 0, None, "lyrics"
            new_line_idx = max(0, min(line_idx, len(project.sentences) - 1))
            return new_line_idx, 0, 0, "lyrics"

        self._execute_structural_edit("删除本行", _mutate)

    def _on_insert_blank_line_requested(self, line_idx: int):
        if not self._project:
            return
        project = self._project

        singer_id = ""
        if 0 <= line_idx < len(project.sentences):
            sentence = project.sentences[line_idx]
            if sentence.characters:
                singer_id = sentence.characters[-1].singer_id

        self._execute_structural_edit(
            "插入空行",
            lambda: ((project.insert_blank_line(line_idx, singer_id=singer_id), 0, None, "lyrics")),
        )

    def _on_add_checkpoint_requested(self, line_idx: int, char_idx: int):
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        project = self._project

        self._execute_structural_edit(
            "增加节奏点",
            lambda: (
                project.sentences[line_idx].add_checkpoint(char_idx)
                or (line_idx, char_idx, 0, "checkpoints")
            ),
        )

    def _on_remove_checkpoint_requested(self, line_idx: int, char_idx: int):
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        project = self._project
        sentence = project.sentences[line_idx]

        def _mutate():
            # 减到 0 时自动退化为 Nicokara 无 mora 格式（注音文本保留）
            sentence.remove_checkpoint(char_idx, force=True)
            return line_idx, char_idx, 0, "checkpoints"

        self._execute_structural_edit("减少节奏点", _mutate)

    def _on_toggle_sentence_end_requested(self, line_idx: int, char_idx: int):
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        project = self._project

        self._execute_structural_edit(
            "切换句尾",
            lambda: (
                project.sentences[line_idx].toggle_sentence_end(char_idx)
                or (line_idx, char_idx, 0, "checkpoints")
            ),
        )

    # ==================== 键盘 ====================

    def keyPressEvent(self, a0: Optional[QKeyEvent]):
        if a0 is None:
            return
        key = a0.key()
        modifiers = a0.modifiers()
        playing = bool(self._timing_service and self._timing_service.is_playing())

        if playing and key == Qt.Key.Key_F4:
            self._toggle_sentence_end_at_current()
            a0.accept()
            return
        if playing and key == Qt.Key.Key_F5:
            self._add_checkpoint()
            a0.accept()
            return
        if playing and key == Qt.Key.Key_F6:
            self._remove_checkpoint()
            a0.accept()
            return

        # Ctrl 快捷键（系统级，优先处理）
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_Z:
                self._on_undo()
                a0.accept()
                return
            elif key == Qt.Key.Key_Y:
                self._on_redo()
                a0.accept()
                return
            elif key == Qt.Key.Key_S:
                self._on_save()
                a0.accept()
                return
            elif key == Qt.Key.Key_H:
                self._on_bulk_change()
                a0.accept()
                return
            elif key == Qt.Key.Key_V:
                self._on_paste_lyrics()
                a0.accept()
                return
            # 其他 Ctrl 组合键：不直接 return，继续走 key_map 查找

        # Convert Qt key to string name for mapping lookup
        key_name = self._qt_key_to_name(key, modifiers)
        if not key_name:
            super().keyPressEvent(a0)
            return

        action = self._key_map.get(key_name.upper())
        # Fallback to default key map if settings not loaded yet
        if action is None:
            action = self._default_key_action(key, modifiers)

        if action == "tag_now":
            if not playing:
                self._add_checkpoint()
                a0.accept()
                return
            if a0.isAutoRepeat():
                a0.ignore()
                return
            if self._timing_service and key_name not in self._pressed_keys:
                try:
                    self._pressed_keys.add(key_name)
                    self._timing_service.on_timing_key_pressed(key_name)
                except Exception as e:
                    self._pressed_keys.discard(key_name)
                    self._show_runtime_error(str(e))
            a0.accept()
            return
        elif action == "play_pause":
            if self._timing_service and self._timing_service.is_playing():
                self._on_pause()
            else:
                self._on_play()
        elif action == "stop":
            self._on_stop()
        elif action == "seek_back":
            if not playing:
                a0.accept()
                return
            if self._timing_service:
                cur = self._timing_service.get_position_ms()
                self._on_seek(max(0, cur - self._rewind_ms))
        elif action == "seek_forward":
            if not playing:
                a0.accept()
                return
            if self._timing_service:
                cur = self._timing_service.get_position_ms()
                dur = self._timing_service.get_duration_ms()
                self._on_seek(min(dur, cur + self._fast_forward_ms))
        elif action == "speed_down":
            v = self.transport.get_speed_value()
            self.transport.set_speed_value(max(50, v - 10))
        elif action == "speed_up":
            v = self.transport.get_speed_value()
            self.transport.set_speed_value(min(200, v + 10))
        elif action == "volume_up":
            v = self.transport.slider_volume.value()
            self.transport.slider_volume.setValue(min(100, v + 5))
        elif action == "volume_down":
            v = self.transport.slider_volume.value()
            self.transport.slider_volume.setValue(max(0, v - 5))
        elif action == "nav_prev_line":
            self._on_nav_line(-1)
            a0.accept()
            return
        elif action == "nav_next_line":
            self._on_nav_line(1)
            a0.accept()
            return
        elif action == "nav_prev_char":
            self._on_nav_char(-1)
            a0.accept()
            return
        elif action == "nav_next_char":
            self._on_nav_char(1)
            a0.accept()
            return
        elif action == "timestamp_up":
            # #3/#4：以 checkpoint 为单位 + 步长可配置
            self._adjust_current_timestamp(self._timing_adjust_step_ms)
            a0.accept()
            return
        elif action == "timestamp_down":
            self._adjust_current_timestamp(-self._timing_adjust_step_ms)
            a0.accept()
            return
        elif action == "cycle_checkpoint":
            # #2：Alt+→ 循环切换当前字符的 checkpoint（正向）
            self._cycle_current_checkpoint(1)
            a0.accept()
            return
        elif action == "cycle_checkpoint_prev":
            # #2：Alt+← 循环切换当前字符的 checkpoint（反向）
            self._cycle_current_checkpoint(-1)
            a0.accept()
            return
        elif action == "edit_ruby":
            if self._project:
                line_idx = self._current_line_idx
                char_idx = self.preview._current_char_idx
                self._on_char_edit_requested(line_idx, char_idx)
        elif action == "add_checkpoint":
            if self._project:
                self._add_checkpoint()
        elif action == "remove_checkpoint":
            if self._project:
                self._remove_checkpoint()
        elif action == "toggle_word_join":
            if self._project:
                self._toggle_word_join()
        elif action == "toggle_line_end":
            if self._project:
                line_idx, char_idx = self._resolve_target_char()
                if line_idx >= 0 and char_idx >= 0:
                    self.preview.toggle_sentence_end_requested.emit(line_idx, char_idx)
                else:
                    self._toggle_sentence_end_at_current()
                a0.accept()
        elif action == "delete_timestamp":
            if self._project:
                line_idx = self._current_line_idx
                char_idx = self.preview._current_char_idx
                self._on_delete_timestamp_requested(line_idx, char_idx)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._insert_line_break_at_current()
            a0.accept()
            return
        elif key == Qt.Key.Key_Delete:
            self._delete_current_selection_or_char()
            a0.accept()
            return
        else:
            super().keyPressEvent(a0)

    def keyReleaseEvent(self, a0: Optional[QKeyEvent]):
        if a0 is None:
            return
        key = a0.key()
        modifiers = a0.modifiers()
        key_name = self._qt_key_to_name(key, modifiers)
        action = self._key_map.get(key_name.upper()) if key_name else None
        if action is None:
            action = self._default_key_action(key, modifiers)
        if action == "tag_now":
            if not (self._timing_service and self._timing_service.is_playing()):
                a0.accept()
                return
            if a0.isAutoRepeat():
                a0.ignore()
                return
            if self._timing_service and key_name in self._pressed_keys:
                try:
                    self._timing_service.on_timing_key_released(key_name)
                except Exception as e:
                    self._show_runtime_error(str(e))
                finally:
                    self._pressed_keys.discard(key_name)
            a0.accept()
            return
        super().keyReleaseEvent(a0)

    def _qt_key_to_name(
        self, key, modifiers=Qt.KeyboardModifier.NoModifier
    ) -> Optional[str]:
        """Convert Qt key enum to string name for shortcut mapping.

        支持组合键，如 CTRL+F4、ALT+A、SHIFT+Z 等。
        """
        parts = []
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("CTRL")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("ALT")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("SHIFT")

        _key_names = {
            Qt.Key.Key_Space: "SPACE",
            Qt.Key.Key_Escape: "ESCAPE",
            Qt.Key.Key_F1: "F1",
            Qt.Key.Key_F2: "F2",
            Qt.Key.Key_F3: "F3",
            Qt.Key.Key_F4: "F4",
            Qt.Key.Key_F5: "F5",
            Qt.Key.Key_F6: "F6",
            Qt.Key.Key_F7: "F7",
            Qt.Key.Key_F8: "F8",
            Qt.Key.Key_F9: "F9",
            Qt.Key.Key_F10: "F10",
            Qt.Key.Key_F11: "F11",
            Qt.Key.Key_F12: "F12",
            Qt.Key.Key_Up: "UP",
            Qt.Key.Key_Down: "DOWN",
            Qt.Key.Key_Left: "LEFT",
            Qt.Key.Key_Right: "RIGHT",
            Qt.Key.Key_Return: "ENTER",
            Qt.Key.Key_Enter: "ENTER",
            Qt.Key.Key_Tab: "TAB",
            Qt.Key.Key_Backspace: "BACKSPACE",
            Qt.Key.Key_Delete: "DELETE",
            Qt.Key.Key_Home: "HOME",
            Qt.Key.Key_End: "END",
            Qt.Key.Key_PageUp: "PAGEUP",
            Qt.Key.Key_PageDown: "PAGEDOWN",
            Qt.Key.Key_Insert: "INSERT",
            # 标点键（#11 修复：支持字面量键名，与 _KeyCaptureButton 保持一致）
            Qt.Key.Key_Comma: ",",
            Qt.Key.Key_Period: ".",
            Qt.Key.Key_Slash: "/",
            Qt.Key.Key_Semicolon: ";",
            Qt.Key.Key_Apostrophe: "'",
            Qt.Key.Key_BracketLeft: "[",
            Qt.Key.Key_BracketRight: "]",
            Qt.Key.Key_Backslash: "\\",
            Qt.Key.Key_Minus: "-",
            Qt.Key.Key_Equal: "=",
            Qt.Key.Key_QuoteLeft: "`",
        }
        if key in _key_names:
            parts.append(_key_names[key])
        elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            parts.append(chr(key))
        elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            parts.append(chr(key))
        else:
            return None
        return "+".join(parts) if parts else None

    def _default_key_action(
        self, key, modifiers=Qt.KeyboardModifier.NoModifier
    ) -> Optional[str]:
        """Fallback key mapping when settings not loaded."""
        key_name = self._qt_key_to_name(key, modifiers)
        if not key_name:
            return None
        defaults = {
            "SPACE": "tag_now",
            "A": "play_pause",
            "S": "stop",
            "Z": "seek_back",
            "X": "seek_forward",
            "Q": "speed_down",
            "W": "speed_up",
            "F2": "edit_ruby",
            "F3": "toggle_word_join",
            "F4": "add_checkpoint",
            "F5": "remove_checkpoint",
            "F6": "toggle_line_end",
            "UP": "nav_prev_line",
            "DOWN": "nav_next_line",
            "LEFT": "nav_prev_char",
            "RIGHT": "nav_next_char",
            "ALT+UP": "timestamp_up",
            "ALT+DOWN": "timestamp_down",
            "ALT+LEFT": "cycle_checkpoint_prev",
            "ALT+RIGHT": "cycle_checkpoint",
        }
        return defaults.get(key_name.upper())

    # ==================== TimingService 回调 ====================

    def on_timetag_added(
        self,
        singer_id: str,
        line_idx: int,
        char_idx: int,
        checkpoint_idx: int,
        timestamp_ms: int,
    ) -> None:
        _ = singer_id, line_idx, char_idx, checkpoint_idx, timestamp_ms
        self._timetag_added_signal.emit()

    def on_position_changed(
        self, position_ms: int, duration_ms: int, singer_positions
    ) -> None:
        self._position_changed_signal.emit(position_ms, duration_ms, singer_positions)

    def on_singer_changed(self, new_singer_id: str, prev_singer_id: str) -> None:
        _ = new_singer_id, prev_singer_id

    def on_checkpoint_moved(self, position: CheckpointPosition) -> None:
        self._checkpoint_moved_signal.emit(position)

    def on_timing_error(self, error_type: str, message: str) -> None:
        self._timing_error_signal.emit(error_type, message)

    def _handle_position_changed(
        self, position_ms: int, duration_ms: int, singer_positions
    ):
        # 60fps UI 节流：跳过间隔 < 16ms 的更新
        now = time.monotonic()
        if now - self._last_position_update_time < 0.016:
            return
        self._last_position_update_time = now

        _ = singer_positions
        self.transport.set_duration(duration_ms)
        self.timeline.set_duration(duration_ms)
        self.transport.set_position(position_ms)
        self.timeline.set_position(position_ms)
        self.preview.set_current_time_ms(position_ms)
        if self._timing_service:
            playing = self._timing_service.is_playing()
            self.transport.set_playing(playing)
            self.preview.set_playing(playing)

    def _handle_checkpoint_moved(self, position: CheckpointPosition):
        self._apply_checkpoint_position(position)
        self._update_status()
    
    def _handle_foucus_moved(self, line_idx: int, char_idx: int):
        self.preview.set_focus_position(line_idx, char_idx)

    def _handle_timetag_added(self):
        self._update_time_tags_display()
        self._update_status()

    def _handle_timing_error(self, error_type: str, message: str):
        InfoBar.warning(
            title=error_type,
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    # ==================== 辅助 ====================

    def _update_selected_checkpoint(
        self,
        line_idx: int,
        char_idx: int,
        cp_idx: Optional[int],
    ) -> None:
        """统一入口：更新 cp 选中态（UI 状态 + domain 选中状态）。

        Issue #9 第十六批架构性修复：
        - UI 侧 preview._current_checkpoint_idx 仍维持（用于渲染判断兼容旧路径）
        - Domain 侧 Project.set_selected_checkpoint 维持全局单选不变量 I1
        - 渲染时 paintEvent 直接读 char.selected_checkpoint_idx → singer.complement_color
          单管道上色，不再需要"选中分支 + HSV 运行时补色 + 额外 drawText"

        调用点覆盖所有 cp 切换事件（除 F5/F6 增减 cp 外，按用户约定不触发）：
        - _apply_checkpoint_position（TimingService 主通路）
        - _sync_after_structure_change（结构编辑后）
        - _on_char_selected 无 cp 分支的直接 set_current_position
        """
        self.preview._current_checkpoint_idx = cp_idx
        if self._project is None or cp_idx is None:
            # cp_idx=None 时不清 project 选中态：保持旧选中直到下次有效切换。
            # 这是因为某些路径（空项目、无 cp 字符）传 None 只代表"当前字符没 cp"，
            # 不代表"用户想取消选中"。
            return
        self._project.set_selected_checkpoint(line_idx, char_idx, cp_idx)

    def _apply_checkpoint_position(self, position: CheckpointPosition):
        if not self._project or not self._project.sentences:
            self._current_line_idx = 0
            self.preview._current_checkpoint_idx = None
            self._update_line_info()
            return

        new_line_idx = max(0, min(position.line_idx, len(self._project.sentences) - 1))
        # 行切换时校验上一行的时间戳
        if new_line_idx != self._current_line_idx:
            if 0 <= self._current_line_idx < len(self._project.sentences):
                self._validate_line_timestamps(self._current_line_idx)
        self._current_line_idx = new_line_idx
        self._update_selected_checkpoint(new_line_idx, position.char_idx, position.checkpoint_idx)
        # cp 标记点击路径：跳过光标移动，保持 selected_char 不被污染。
        # 仍需要刷新 preview 显示以反映新的 selected_cp 高亮。
        if self._suppress_cp_cursor_move:
            self.preview._update_display()
        else:
            self.preview.set_current_position(new_line_idx, position.char_idx)
        self._update_line_info()

    def _show_runtime_error(self, message: str):
        InfoBar.error(
            title="操作失败",
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _update_line_info(self):
        if self._project and self._project.sentences:
            total = len(self._project.sentences)
            idx = min(self._current_line_idx, total - 1)
            text = self._project.sentences[idx].text
            preview = text[:30] + "..." if len(text) > 30 else text
            # 显示选中字符的时间戳信息
            char_info = ""
            char_idx = self.preview._current_char_idx
            sentence = self._project.sentences[idx]
            if 0 <= char_idx < len(sentence.characters):
                ch = sentence.characters[char_idx]
                ts_parts = []
                for ts in ch.timestamps:
                    m, s = divmod(ts // 1000, 60)
                    ms = ts % 1000
                    ts_parts.append(f"{m:02d}:{s:02d}.{ms:03d}")
                if ch.is_sentence_end and ch.sentence_end_ts is not None:
                    ets = ch.sentence_end_ts
                    m, s = divmod(ets // 1000, 60)
                    ms = ets % 1000
                    ts_parts.append(f"句尾{m:02d}:{s:02d}.{ms:03d}")
                if ts_parts:
                    char_info = f" | 「{ch.char}」 {', '.join(ts_parts)}"
                else:
                    char_info = f" | 「{ch.char}」 未打轴"
            self.lbl_line_info.setText(f"行 {idx + 1}/{total}: {preview}{char_info}")
        else:
            self.lbl_line_info.setText("当前行: -")

    def _update_time_tags_display(self):
        if not self._project:
            return
        # 使用渲染时间戳（带偏移），与波形显示对齐
        self.timeline.set_time_tags(self._project.collect_all_global_timestamp_ms())

    def _update_status(self):
        if not self._project:
            self.lbl_progress.setText("行: 0/0 | 进度: 0%")
            return
        total = len(self._project.sentences)
        timed = sum(1 for s in self._project.sentences if s.has_timetags)
        pct = int(timed / total * 100) if total > 0 else 0
        self.lbl_progress.setText(f"行: {total} | 已打轴: {timed}/{total} ({pct}%)")

    def refresh_lyric_display(self):
        self.preview._update_display()

    def _auto_analyze_rubies(self, only_noruby: bool = False):
        """执行注音分析（核心逻辑，供多处复用）

        Args:
            only_noruby: True=仅分析未注音字符，False=全部重新分析
        """
        if not self._project:
            return
        try:
            from strange_uta_game.backend.application import AutoCheckService
            from strange_uta_game.frontend.settings.settings_interface import AppSettings

            app_settings = AppSettings()
            auto_check_flags = app_settings.get_all().get("auto_check", {})
            user_dict = app_settings.load_dictionary()
            auto_check = AutoCheckService(
                auto_check_flags=auto_check_flags, user_dictionary=user_dict
            )
            auto_check.apply_to_project(self._project, only_noruby=only_noruby)
            auto_check.update_checkpoints_for_project(self._project)
            self.refresh_lyric_display()
            if hasattr(self, "_store") and self._store:
                self._store.notify("rubies")
                self._store.notify("checkpoints")

            InfoBar.success(
                title="注音分析完成",
                content="已重新分析注音",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
        except Exception as e:
            InfoBar.warning(
                title="注音分析失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )

    def _on_analyze_rubies(self):
        """工具栏「注音分析」— 弹三选项对话框"""
        if not self._project:
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("自动分析全部注音")
        msg.setText("请选择分析范围：")
        msg.setInformativeText(
            "「全部重新分析」会覆盖现有注音。\n"
            "「仅分析未注音字符」会保留已有的人工/字典注音。"
        )
        btn_all = msg.addButton("全部重新分析", QMessageBox.ButtonRole.DestructiveRole)
        btn_only_noruby = msg.addButton(
            "仅分析未注音字符", QMessageBox.ButtonRole.AcceptRole
        )
        btn_cancel = msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_only_noruby)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked is btn_cancel or clicked is None:
            return
        only_noruby = clicked is btn_only_noruby
        self._auto_analyze_rubies(only_noruby=only_noruby)

    def _auto_analyze_all_rubies(self):
        """自动分析全部注音（用于歌词导入后重新注音，覆盖已有）"""
        self._auto_analyze_rubies(only_noruby=False)

"""读音词典子页面。

设置卡片布局
------------
1. "自定义读音"（本地词典编辑入口）
2. "启用网络词典"（SwitchSettingCard，即时落盘 ``config.json``）
3. "网络词典管理"（按钮 → :class:`NetworkDictionaryDialog`）
4. "字典源优先级"（按钮 → :class:`PriorityOrderDialog`，每次打开重新加载源列表）
5. "根据用户词典给片假名标注英文"（SwitchSettingCard）

数据流（防失联）
----------------
* 优先级对话框每次 ``_on_open_priority_order`` 时调用 ``load_network_dictionary()``
  → 反映管理对话框中刚添加的源 / `ensure_builtin_sources` 新追加的内置源。
* 管理对话框 accept 后，会调用 ``save_network_dictionary``，并把可能的新增 id
  自动补到 ``source_order`` 末尾再回写。
"""

from __future__ import annotations

from typing import Any, Dict, List

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtGui import QIntValidator
from qfluentwidgets import (
    ComboBox,
    FluentIcon as FIF,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SettingCard,
    SettingCardGroup,
)

from ..cards import SwitchSettingCard
from ..dictionary_dialog import DictionaryEditDialog
from ..network_dictionary_dialog import NetworkDictionaryDialog
from .base import SubSettingInterface


_LOCAL_LABEL = "📒 本地词典"
_LOCAL_ID = "local"


class PriorityOrderDialog(QDialog):
    """字典源优先级编辑对话框。

    输入 ``sources`` 与 ``source_order``，UI 表现为可上下移的列表（含 sentinel
    "本地词典"）；``确定`` 后通过 :meth:`get_order` 取新顺序。

    显式接收源列表而非自己 load —— 这样调用方负责"每次打开都取最新数据"，
    避免对话框生命周期内 stale 数据问题。
    """

    def __init__(
        self,
        sources: List[Dict[str, Any]],
        source_order: List[str],
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("字典源优先级")
        self.setMinimumSize(420, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("字典源优先级")
        title.setFont(QFont("Microsoft YaHei", 14))
        layout.addWidget(title)

        desc = QLabel(
            "lookup 时按下方顺序自顶向下遍历各字典源，每源内按 entries 自顶向下首个命中即停。\n"
            "📒 本地词典 sentinel 代表 dictionary.json；其他项对应网络源。"
        )
        desc.setFont(QFont("Microsoft YaHei", 10))
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self._list = QListWidget(self)
        layout.addWidget(self._list, 1)

        # 数据准备：sources -> id->name；source_order 补全缺失 id；保证 local 存在
        id_to_name = {s.get("id"): s.get("name", s.get("id", "")) for s in (sources or [])}
        id_to_name[_LOCAL_ID] = _LOCAL_LABEL
        order = list(source_order or [_LOCAL_ID])
        for s in sources or []:
            sid = s.get("id")
            if sid and sid not in order:
                order.append(sid)
        if _LOCAL_ID not in order:
            order.insert(0, _LOCAL_ID)
        # 去除孤儿 id（既不是 local 也不在 sources 表中）
        valid_ids = {_LOCAL_ID} | {s.get("id") for s in (sources or []) if s.get("id")}
        order = [sid for sid in order if sid in valid_ids]

        for sid in order:
            item = QListWidgetItem(id_to_name.get(sid, sid))
            item.setData(Qt.ItemDataRole.UserRole, sid)
            self._list.addItem(item)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

        btn_row = QHBoxLayout()
        btn_up = PushButton("上移", self)
        btn_up.clicked.connect(lambda: self._move(-1))
        btn_dn = PushButton("下移", self)
        btn_dn.clicked.connect(lambda: self._move(+1))
        btn_row.addWidget(btn_up)
        btn_row.addWidget(btn_dn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        ok_row = QHBoxLayout()
        btn_ok = PrimaryPushButton("确定", self)
        btn_ok.clicked.connect(self.accept)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        ok_row.addStretch()
        ok_row.addWidget(btn_ok)
        ok_row.addWidget(btn_cancel)
        layout.addLayout(ok_row)

    def _move(self, delta: int) -> None:
        row = self._list.currentRow()
        if row < 0:
            return
        new_row = row + delta
        if not (0 <= new_row < self._list.count()):
            return
        item = self._list.takeItem(row)
        self._list.insertItem(new_row, item)
        self._list.setCurrentRow(new_row)

    def get_order(self) -> List[str]:
        return [
            self._list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._list.count())
        ]


class DictionarySubInterface(SubSettingInterface):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings_ref = None
        self._init_ui()

    def _init_ui(self):
        g = SettingCardGroup("读音词典", self.scrollWidget)

        # 1. 本地词典
        dict_card = SettingCard(FIF.DICTIONARY, "自定义读音",
            "固定特定词汇的注音读法（最长匹配优先）", g)
        self.btn_open_dict = PushButton("编辑词典", dict_card)
        self.btn_open_dict.setFont(QFont("Microsoft YaHei", 10))
        self.btn_open_dict.clicked.connect(self._on_open_dictionary)
        dict_card.hBoxLayout.addWidget(self.btn_open_dict, 0, Qt.AlignmentFlag.AlignRight)
        dict_card.hBoxLayout.addSpacing(16)
        self.dict_card = dict_card

        # 2. 网络词典总开关
        self.card_network_enabled = SwitchSettingCard(
            FIF.CLOUD, "启用网络词典",
            "开启后注音时叠加网络词典源的条目（按优先级链）；关闭则仅使用本地词典。",
            parent=g)

        # 3. 网络词典管理
        net_card = SettingCard(FIF.CLOUD_DOWNLOAD, "网络词典管理",
            "管理网络词典源（含 RL 官方 / 键盘office 预设），可添加自定义 URL、查看条目", g)
        self.btn_open_net = PushButton("管理网络词典", net_card)
        self.btn_open_net.setFont(QFont("Microsoft YaHei", 10))
        self.btn_open_net.clicked.connect(self._on_open_network_dictionary)
        net_card.hBoxLayout.addWidget(self.btn_open_net, 0, Qt.AlignmentFlag.AlignRight)
        net_card.hBoxLayout.addSpacing(16)
        self.net_card = net_card

        # 3b. 网络源自动更新开关
        self.card_auto_update_enabled = SwitchSettingCard(
            FIF.SYNC, "启用网络源自动更新",
            "应用启动时检查所有启用的网络源是否到期，到期则后台自动拉取",
            parent=g)

        # 3c. 自动更新间隔（LineEdit 数字输入 + ComboBox 内嵌到 SettingCard）
        interval_card = SettingCard(FIF.DATE_TIME, "网络源自动更新间隔",
            "距上次自动同步超过此间隔后，下次启动触发后台拉取", g)
        self._interval_edit = LineEdit(interval_card)
        self._interval_edit.setFixedWidth(120)
        self._interval_edit.setPlaceholderText("数值")
        self._interval_edit.setValidator(QIntValidator(1, 9999, self._interval_edit))
        self._interval_edit.setClearButtonEnabled(False)
        self._interval_combo = ComboBox(interval_card)
        # 显示文本 ↔ 内部 unit key
        self._UNIT_LABELS = [("周", "week"), ("天", "day"), ("小时", "hour")]
        for label, _key in self._UNIT_LABELS:
            self._interval_combo.addItem(label)
        self._interval_combo.setFixedWidth(90)
        interval_card.hBoxLayout.addWidget(self._interval_edit, 0, Qt.AlignmentFlag.AlignRight)
        interval_card.hBoxLayout.addSpacing(8)
        interval_card.hBoxLayout.addWidget(self._interval_combo, 0, Qt.AlignmentFlag.AlignRight)
        interval_card.hBoxLayout.addSpacing(16)
        self.interval_card = interval_card

        # 4. 字典源优先级（按钮卡片 → 打开 PriorityOrderDialog）
        prio_card = SettingCard(FIF.ALIGNMENT, "字典源优先级",
            "调整本地词典与各网络源的全局优先级（自顶向下递减）", g)
        self.btn_open_prio = PushButton("编辑优先级", prio_card)
        self.btn_open_prio.setFont(QFont("Microsoft YaHei", 10))
        self.btn_open_prio.clicked.connect(self._on_open_priority_order)
        prio_card.hBoxLayout.addWidget(self.btn_open_prio, 0, Qt.AlignmentFlag.AlignRight)
        prio_card.hBoxLayout.addSpacing(16)
        self.prio_card = prio_card

        # 5. 片假名标注英文开关
        self.card_annotate_katakana_with_english = SwitchSettingCard(
            FIF.LANGUAGE, "根据用户词典给片假名标注英文",
            "开启后，用户词典中纯片假名词条或读音为英文的词条将被应用；关闭时拦截这类词条",
            parent=g)

        g.addSettingCard(self.dict_card)
        g.addSettingCard(self.card_network_enabled)
        g.addSettingCard(self.net_card)
        g.addSettingCard(self.card_auto_update_enabled)
        g.addSettingCard(self.interval_card)
        g.addSettingCard(self.prio_card)
        g.addSettingCard(self.card_annotate_katakana_with_english)
        self.expandLayout.addWidget(g)

    # ──────────────────────────────────────────────
    # 对话框入口（按钮槽）
    # ──────────────────────────────────────────────

    def _on_open_dictionary(self):
        if self._settings_ref is None:
            return
        entries = self._settings_ref.load_dictionary()
        dialog = DictionaryEditDialog(entries, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._settings_ref.save_dictionary(dialog.get_entries())

    def _on_open_network_dictionary(self):
        if self._settings_ref is None:
            return
        doc = self._settings_ref.load_network_dictionary()
        cache_path = str(self._settings_ref._network_dict_path)
        dialog = NetworkDictionaryDialog(doc, cache_path=cache_path, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_doc = dialog.get_doc()
            # 总开关由外卡片掌管
            new_doc["enabled"] = self.card_network_enabled.isChecked()
            # 新增源补到 source_order 末尾，删除的源从 order 中剔除
            existing_ids = {s.get("id") for s in (new_doc.get("sources") or []) if s.get("id")}
            existing_ids.add(_LOCAL_ID)
            order: List[str] = list(new_doc.get("source_order") or [_LOCAL_ID])
            order = [sid for sid in order if sid in existing_ids]
            for sid in (s.get("id") for s in (new_doc.get("sources") or []) if s.get("id")):
                if sid not in order:
                    order.append(sid)
            if _LOCAL_ID not in order:
                order.insert(0, _LOCAL_ID)
            new_doc["source_order"] = order
            self._settings_ref.save_network_dictionary(new_doc)

    def _on_open_priority_order(self):
        """打开优先级编辑对话框 —— 每次都重新 load_network_dictionary 取最新源列表。"""
        if self._settings_ref is None:
            return
        doc = self._settings_ref.load_network_dictionary()
        dialog = PriorityOrderDialog(
            sources=doc.get("sources") or [],
            source_order=doc.get("source_order") or [_LOCAL_ID],
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # 落盘：仅改 source_order，保留 sources + cache
            doc["source_order"] = dialog.get_order()
            doc["enabled"] = self.card_network_enabled.isChecked()
            self._settings_ref.save_network_dictionary(doc)
            self._notify_changed()

    # ──────────────────────────────────────────────
    # 开关槽
    # ──────────────────────────────────────────────

    def connect_signals(self):
        self.card_annotate_katakana_with_english.checked_changed.connect(self._notify_changed)
        self.card_network_enabled.checked_changed.connect(self._on_network_enabled_changed)
        self.card_auto_update_enabled.checked_changed.connect(self._on_auto_update_enabled_changed)
        self._interval_edit.editingFinished.connect(self._on_interval_changed)
        self._interval_combo.currentIndexChanged.connect(lambda _: self._on_interval_changed())

    def _on_auto_update_enabled_changed(self, _checked: bool):
        if self._settings_ref is None:
            return
        self._settings_ref.set(
            "network_dictionary.auto_update.enabled",
            self.card_auto_update_enabled.isChecked(),
        )
        self._settings_ref.save()
        self._notify_changed()

    def _on_interval_changed(self):
        if self._settings_ref is None:
            return
        unit_idx = self._interval_combo.currentIndex()
        if unit_idx < 0 or unit_idx >= len(self._UNIT_LABELS):
            return
        unit_key = self._UNIT_LABELS[unit_idx][1]
        # LineEdit 文本 → 整数（validator 已保证字符合法，但仍兜底）
        raw = (self._interval_edit.text() or "").strip()
        try:
            value = max(1, int(raw)) if raw else 1
        except ValueError:
            value = 1
        # 把规范化值写回控件，避免空 / 0 / 负数显示残留
        self._interval_edit.blockSignals(True)
        self._interval_edit.setText(str(value))
        self._interval_edit.blockSignals(False)
        self._settings_ref.set("network_dictionary.auto_update.interval_value", value)
        self._settings_ref.set("network_dictionary.auto_update.interval_unit", unit_key)
        self._settings_ref.save()
        self._notify_changed()

    def _on_network_enabled_changed(self, _checked: bool):
        if self._settings_ref is None:
            return
        self._settings_ref.set(
            "network_dictionary.enabled",
            self.card_network_enabled.isChecked(),
        )
        self._settings_ref.save()
        self._notify_changed()

    def load_settings(self, s):
        self._settings_ref = s
        self.card_annotate_katakana_with_english.setChecked(
            s.get("ruby_dictionary.annotate_katakana_with_english", False))
        self.card_network_enabled.setChecked(
            bool(s.get("network_dictionary.enabled", True)))
        self.card_auto_update_enabled.setChecked(
            bool(s.get("network_dictionary.auto_update.enabled", False)))
        self._interval_edit.setText(
            str(int(s.get("network_dictionary.auto_update.interval_value", 1) or 1)))
        unit_key = str(s.get("network_dictionary.auto_update.interval_unit", "week") or "week")
        for i, (_label, key) in enumerate(self._UNIT_LABELS):
            if key == unit_key:
                self._interval_combo.setCurrentIndex(i)
                break

    def collect_settings(self, s):
        s.set("ruby_dictionary.annotate_katakana_with_english",
              self.card_annotate_katakana_with_english.isChecked())
        s.set("network_dictionary.enabled",
              self.card_network_enabled.isChecked())
        s.set("network_dictionary.auto_update.enabled",
              self.card_auto_update_enabled.isChecked())
        try:
            iv = max(1, int((self._interval_edit.text() or "1").strip()))
        except ValueError:
            iv = 1
        s.set("network_dictionary.auto_update.interval_value", iv)
        unit_idx = self._interval_combo.currentIndex()
        if 0 <= unit_idx < len(self._UNIT_LABELS):
            s.set("network_dictionary.auto_update.interval_unit",
                  self._UNIT_LABELS[unit_idx][1])

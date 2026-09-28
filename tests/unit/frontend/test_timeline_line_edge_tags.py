"""波形时间标签"行界强调"（每行首/尾时间戳）的行界句柄判定单元测试。

用 SimpleNamespace 充当 self（与 test_timeline_tag_drag.py 同范式），不依赖
Qt 控件实例化。行首/行尾按**该行 ts 最小/最大**判定（跨 normal/warning 合并），
行尾在打了停顿点时是 is_sentence_end 标签——两类都可能成为行界。
"""
from __future__ import annotations

from types import SimpleNamespace

from strange_uta_game.frontend.editor.timing.timeline_widget import WaveformDisplay


def _fake_wd():
    """WaveformDisplay 的轻量替身：只持有 set_time_tags / _append_time_tag 所需
    状态，避免实例化 QWidget。"""
    f = SimpleNamespace(
        _time_tags=[], _warning_time_tags=[], _handle_index={},
        _selected_handles=set(), _last_tags_input=None,
        _running_max_ts=-1, _seen_char_keys=set(),
        _max_file_order_key=None, update=lambda: None,
        _line_edge_handles=set(), _line_bounds={},
    )
    f._append_time_tag = lambda entry: WaveformDisplay._append_time_tag(f, entry)
    f.set_time_tags = lambda tags: WaveformDisplay.set_time_tags(f, tags)
    f._recompute_line_edge_handles = lambda: WaveformDisplay._recompute_line_edge_handles(f)
    f._update_line_edges_on_append = (
        lambda li, ts, h: WaveformDisplay._update_line_edges_on_append(f, li, ts, h)
    )
    return f


def test_full_rebuild_marks_per_line_edges():
    """全量重建：每行 ts 最小/最大者为行界；句尾停顿点可作行尾。"""
    tags = [
        (100, "a", 0, 0, 0, False, "a"),
        (200, "a", 0, 0, 1, False, None),
        (300, "b", 0, 1, 0, False, None),
        (350, "b", 0, 1, 1, True, None),   # 行 0 句尾停顿点
        (500, "c", 1, 0, 0, False, "c"),
        (600, "c", 1, 0, 1, False, None),
    ]
    f = _fake_wd()
    f.set_time_tags(tags)
    assert f._line_edge_handles == {
        (0, 0, 0, False),   # 行 0 首
        (0, 1, 1, True),    # 行 0 尾 = 句尾停顿点
        (1, 0, 0, False),   # 行 1 首
        (1, 0, 1, False),   # 行 1 尾 = 普通 checkpoint
    }


def test_nonmonotonic_warning_tag_can_be_line_edge():
    """ts 回退进警告表的标签同样参与行界判定（此处为行首）。"""
    tags = [
        (300, "a", 0, 0, 0, False, None),
        (250, "b", 0, 1, 0, False, None),   # 回退 → warning，但是行 0 最小 ts
    ]
    f = _fake_wd()
    f.set_time_tags(tags)
    assert any(t.handle == (0, 1, 0, False) for t in f._warning_time_tags)
    assert f._line_edge_handles == {(0, 1, 0, False), (0, 0, 0, False)}


def test_single_tag_line_marks_both_edges():
    """单标签行：首尾同为该句柄，集合中仅一份。"""
    f = _fake_wd()
    f.set_time_tags([(100, "a", 0, 0, 0, False, None)])
    assert f._line_edge_handles == {(0, 0, 0, False)}


def test_incremental_append_edges_equal_full_rebuild():
    """顺序打轴快路径增量维护的行界集合必须与全量重建一致。"""
    T0 = [
        (100, "a", 0, 0, 0, False, "a"),
        (200, "a", 0, 0, 1, False, None),
        (350, "b", 0, 1, 0, True, None),    # 行 0 句尾
        (500, "c", 1, 0, 0, False, "c"),
    ]
    new = (600, "c", 1, 0, 1, False, None)  # 行 1 新尾
    inc = _fake_wd()
    inc.set_time_tags(T0)
    inc.set_time_tags(T0 + [new])           # 前缀比对命中 → 快路径
    full = _fake_wd()
    full.set_time_tags(T0 + [new])
    assert inc._line_edge_handles == full._line_edge_handles
    assert inc._line_bounds == full._line_bounds
    # 行 1 的尾从首个 checkpoint 顶替为新追加点，行首保留
    assert (1, 0, 1, False) in inc._line_edge_handles
    assert (1, 0, 0, False) in inc._line_edge_handles


def test_append_smaller_ts_replaces_line_head():
    """增量插入更小 ts 到已有行：顶替行首，旧行首退出，行尾不变。"""
    f = _fake_wd()
    f.set_time_tags([
        (100, "a", 0, 0, 0, False, None),
        (300, "b", 0, 1, 0, False, None),
    ])
    f._append_time_tag((50, "b", 0, 1, 1, False, None))
    assert (0, 1, 1, False) in f._line_edge_handles       # 新行首
    assert (0, 0, 0, False) not in f._line_edge_handles   # 旧行首让位
    assert (0, 1, 0, False) in f._line_edge_handles       # 行尾不变

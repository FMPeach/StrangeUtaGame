from __future__ import annotations



import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication

from strange_uta_game.backend.domain import Character, Project, Ruby, RubyPart, Sentence, Singer
from strange_uta_game.frontend.editor.timing import karaoke_preview as preview_module


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_full_width_space_is_skipped_by_wipe():
    assert preview_module._wipe_ink_bounds(None, "\u3000") == (0, 0)


def test_space_without_rhythm_consumes_no_wipe_time(qapp, monkeypatch):
    """回归：无节奏点空格是无渲染字符，不得占用走字时长。

    「あ い。」（あ=1000、い=2000、停顿点 3000，空格 cc=0 无时间戳）：
    旧算法把空格的排版宽度计入加权，空格分走一段时间而绘制层又因无墨水
    跳过它——走字在空格处停顿、后续字符窗口被压缩（前一字加「。」停顿
    标记时空格落入停顿区间才不受影响）。修复后空格得到零时长窗口，
    あ 的 wipe 一直延伸到 い 的起始时间。
    """
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    singer_id = "s"
    end_char = Character(
        char="。", check_count=0, is_sentence_end=True, singer_id=singer_id
    )
    end_char.set_sentence_end_ts(3000)
    sentence = Sentence(
        singer_id=singer_id,
        characters=[
            Character(char="あ", check_count=1, timestamps=[1000], singer_id=singer_id),
            Character(char=" ", check_count=0, singer_id=singer_id),
            Character(char="い", check_count=1, timestamps=[2000], singer_id=singer_id),
            end_char,
        ],
    )
    preview = preview_module.KaraokePreview()
    preview.set_project(_project_from([sentence]))

    wt = preview._sentence_cache[(0, "cur")]["char_wipe_times"]

    # 空格：零时长窗口，瞬时跨过，不占走字时间
    assert wt[1][0] == wt[1][1] == 2000
    # あ：wipe 延伸到 い 的起始（旧算法会提前结束再停顿）
    assert wt[0] == (1000, 2000)
    # 末段不受影响：い 从 2000 起步、。收尾于停顿点 3000
    assert wt[2][0] == 2000
    assert wt[3][1] == 3000


def _char(text: str, ruby: str, *, linked: bool = False) -> Character:
    ch = Character(
        char=text,
        check_count=1,
        timestamps=[1000],
        linked_to_next=linked,
    )
    ch.set_ruby(Ruby(parts=[RubyPart(text=ruby)]))
    ch.push_to_ruby()
    return ch


def _ruby_char(
    text: str,
    parts: list[str],
    *,
    check_count: int,
    timestamps: list[int],
    linked: bool = False,
) -> Character:
    ch = Character(
        char=text,
        check_count=check_count,
        timestamps=timestamps,
        linked_to_next=linked,
    )
    ch.set_ruby(Ruby(parts=[RubyPart(text=part) for part in parts]))
    return ch


@pytest.mark.parametrize(
    "timestamps,expected",
    [
        ([], [("は", False), ("な", False)]),
        ([1000], [("は", True), ("な", False)]),
        ([1000, 1200], [("は", True), ("な", True)]),
    ],
)
def test_ruby_timing_chunks_follow_checkpoint_timestamps(timestamps, expected):
    flower = _ruby_char(
        "花", ["は", "な"], check_count=2, timestamps=timestamps
    )

    assert preview_module._ruby_timing_chunks([flower], [0]) == expected


def test_ruby_timing_chunks_keep_zero_checkpoint_ruby_untimed():
    no_checkpoint = _ruby_char(
        "々", ["お", "な", "じ"], check_count=0, timestamps=[]
    )

    assert preview_module._ruby_timing_chunks([no_checkpoint], [0]) == [
        ("お", False),
        ("な", False),
        ("じ", False),
    ]


def test_linked_ruby_timing_chunks_keep_each_char_part_ownership():
    characters = [
        _ruby_char(
            "頂", ["ちょ", "う"], check_count=2, timestamps=[1000], linked=True
        ),
        _ruby_char("戴", ["だ", "い"], check_count=2, timestamps=[1200, 1400]),
    ]

    assert preview_module._ruby_timing_chunks(characters, [0, 1]) == [
        ("ちょ", True),
        ("う", False),
        ("だ", True),
        ("い", True),
    ]


def test_ruby_timed_spans_keep_multi_codepoint_mora_atomic(qapp):
    preview = preview_module.KaraokePreview()
    fm = preview._fm_ruby

    text, spans = preview_module._ruby_timed_spans(
        fm, [("ちょ", True), ("う", False)]
    )

    assert text == "ちょう"
    assert spans == [(0, fm.horizontalAdvance("ちょ"))]


def test_refresh_ruby_timing_state_preserves_wipe_cache(qapp, monkeypatch):
    """退格清轴只更新 Ruby 底色，已缓存的走字时间轴不变。"""
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    flower = _ruby_char(
        "花", ["は", "な"], check_count=2, timestamps=[1000, 1200]
    )
    sentence = Sentence(singer_id="s", characters=[flower])
    preview = preview_module.KaraokePreview()
    preview.set_duration(2000)
    preview.set_project(_project_from([sentence]))

    entry = preview._sentence_cache[(0, "cur")]
    wipe_times = entry["char_wipe_times"]
    part_anchors = entry["char_part_anchors"]
    group_wipe = entry["group_ruby_wipe"]
    assert entry["char_ruby_timed_spans"][0]

    flower.clear_timestamps()
    preview._refresh_ruby_timing_state(0)

    refreshed_entry = preview._sentence_cache[(0, "cur")]
    assert refreshed_entry is entry
    assert refreshed_entry["char_wipe_times"] is wipe_times
    assert refreshed_entry["char_part_anchors"] is part_anchors
    assert refreshed_entry["group_ruby_wipe"] is group_wipe
    assert refreshed_entry["char_ruby_timed_spans"][0] == []


@pytest.mark.parametrize(
    "color,foreground,background,expected",
    [
        ("#FFFFFF", "#FFFFFF", "#1E1E1E", 1.0),
        ("#888888", "#FFFFFF", "#1E1E1E", (0x88 - 0x1E) / (0xFF - 0x1E)),
        ("#000000", "#000000", "#FFFFFF", 1.0),
        ("#666666", "#000000", "#FFFFFF", (0xFF - 0x66) / 0xFF),
    ],
)
def test_ruby_focus_alpha_follows_existing_line_gray(
    color, foreground, background, expected
):
    actual = preview_module._foreground_alpha_for_color(
        preview_module.QColor(color),
        preview_module.QColor(foreground),
        preview_module.QColor(background),
    )

    assert actual == pytest.approx(expected, abs=0.005)


def test_ruby_timing_base_scales_both_states_with_focus_limit(qapp):
    class RecordingPainter:
        def __init__(self):
            self.pens = []
            self.draw_count = 0

        def setPen(self, color):
            self.pens.append(preview_module.QColor(color))

        def drawText(self, *args):
            self.draw_count += 1

        def save(self):
            pass

        def setClipRect(self, *args):
            pass

        def restore(self):
            pass

    preview = preview_module.KaraokePreview()
    fm = preview._fm_ruby
    foreground = preview_module.QColor("#FFFFFF")
    painter = RecordingPainter()
    text = "はな"

    preview_module._draw_ruby_timing_base(
        painter,
        0,
        fm.ascent() + 2,
        text,
        [(0, fm.horizontalAdvance("は"))],
        fm,
        foreground,
        0.40,
    )

    assert painter.pens[0].name() == foreground.name()
    assert painter.pens[0].alphaF() == pytest.approx(0.12, abs=0.01)
    assert painter.pens[1].name() == foreground.name()
    assert painter.pens[1].alphaF() == pytest.approx(0.40, abs=0.01)
    assert painter.draw_count == 2


def test_ruby_base_restores_plain_color_when_preview_guide_is_disabled(qapp):
    class RecordingPainter:
        def __init__(self):
            self.pens = []
            self.draw_count = 0

        def setPen(self, color):
            self.pens.append(preview_module.QColor(color))

        def drawText(self, *args):
            self.draw_count += 1

        def save(self):
            pytest.fail("关闭打轴指引时不应进入注音状态分层绘制")

    preview = preview_module.KaraokePreview()
    fm = preview._fm_ruby
    fallback_color = preview_module.QColor("#777777")
    painter = RecordingPainter()

    preview_module._draw_ruby_base(
        painter,
        0,
        fm.ascent() + 2,
        "はな",
        [(0, fm.horizontalAdvance("は"))],
        fm,
        preview_module.QColor("#FFFFFF"),
        1.0,
        guide_enabled=False,
        fallback_color=fallback_color,
    )

    assert [color.name() for color in painter.pens] == [fallback_color.name()]
    assert painter.pens[0].alpha() == fallback_color.alpha()
    assert painter.draw_count == 1


def _project_with_linked_word() -> Project:
    singer = Singer(name="default", is_default=True)
    return Project(
        singers=[singer],
        sentences=[
            Sentence(
                singer_id=singer.id,
                characters=[
                    _char("長", "なが", linked=True),
                    _char("連", "れん", linked=True),
                    _char("詞", "し"),
                ],
            )
        ],
    )


class _DummySignal:
    def connect(self, callback):
        pass


class _DummyTheme:
    changed = _DummySignal()


def test_position_and_focus_changes_do_not_invalidate_render_cache(qapp, monkeypatch):
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())

    preview = preview_module.KaraokePreview()
    preview.set_project(_project_with_linked_word())

    assert preview._sentence_cache
    cached_entries = {
        font_key: preview._sentence_cache[(0, font_key)]
        for font_key in ("cur", "ctx")
    }
    global_version = preview._global_version

    preview.set_current_position(0, 1)
    preview.set_focus_position(0, 2)
    preview.scroll_current_line_to_center()
    preview.request_repaint()

    assert preview._global_version == global_version
    for font_key, cached_entry in cached_entries.items():
        assert preview._sentence_cache[(0, font_key)] is cached_entry

    preview._update_display()

    assert preview._global_version == global_version + 1


def test_playback_tick_repaints_only_dynamic_row(qapp, monkeypatch):
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    preview = preview_module.KaraokePreview()
    preview.resize(800, 560)
    preview.set_project(_plain_project(line_count=20, chars_per_line=4))
    preview._is_playing = True
    preview._auto_scroll_enabled = False
    preview._current_line_idx = 6
    preview._scroll_center_line = 6.0
    dirty = []
    monkeypatch.setattr(preview, "update", lambda *args: dirty.append(args))

    preview.set_current_time_ms(1234)

    assert len(dirty) == 1
    assert len(dirty[0]) == 1
    rect = dirty[0][0]
    assert isinstance(rect, preview_module.QRect)
    assert 0 < rect.height() < preview.height()


@pytest.mark.parametrize(
    "scroll_mode,expected_visual",
    [("auto", 6), ("always", 6), ("never", 2)],
)
def test_visual_current_follows_playback_when_auto_scrolling(
    qapp, monkeypatch, scroll_mode, expected_visual
):
    """1.5.0 语义：自动滚动激活时播放行就是视觉当前行（大字号/行号高亮）。

    从不滚动模式没有播放行跟随，视觉当前行回落到编辑光标行。
    """
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    preview = preview_module.KaraokePreview()
    preview.set_project(_plain_project(line_count=8, chars_per_line=1))
    preview._current_line_idx = 2
    preview._last_auto_scroll_line_idx = 6
    preview._is_playing = True
    preview.set_scroll_mode(scroll_mode)

    assert preview._effective_current_line() == expected_visual
    assert preview._current_line_idx == 2


@pytest.mark.parametrize("scroll_mode", ["auto", "always"])
def test_playback_line_change_never_changes_current_line(
    qapp, monkeypatch, scroll_mode
):
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    preview = preview_module.KaraokePreview()
    preview.set_project(_plain_project(line_count=8, chars_per_line=1))
    preview._line_switch_points = [(100, 0), (200, 6)]
    preview._current_line_idx = 2
    preview._scroll_center_line = 2.0
    preview._last_auto_scroll_line_idx = 0
    preview._is_playing = True
    preview.set_scroll_mode(scroll_mode)

    preview.set_current_time_ms(250)

    assert preview._current_line_idx == 2
    assert preview._last_auto_scroll_line_idx == 6
    assert preview._scroll_center_line == 6.0


def test_playback_line_change_never_scrolls_in_never_mode(qapp, monkeypatch):
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    preview = preview_module.KaraokePreview()
    preview.set_project(_plain_project(line_count=8, chars_per_line=1))
    preview._line_switch_points = [(100, 0), (200, 6)]
    preview._current_line_idx = 2
    preview._scroll_center_line = 2.0
    preview._last_auto_scroll_line_idx = 0
    preview._is_playing = True
    preview.set_scroll_mode("never")

    preview.set_current_time_ms(250)

    assert preview._current_line_idx == 2
    assert preview._last_auto_scroll_line_idx == 0
    assert preview._scroll_center_line == 2.0


@pytest.mark.parametrize("scroll_mode", ["auto", "always"])
def test_follow_scroll_repaint_covers_playback_row(qapp, monkeypatch, scroll_mode):
    """自动滚动播放中：播放行（视觉当前行）行带必须被逐帧重绘覆盖。"""
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    preview = preview_module.KaraokePreview()
    preview.resize(800, 560)
    preview.set_project(_plain_project(line_count=8, chars_per_line=1))
    preview._current_line_idx = 2
    preview._last_auto_scroll_line_idx = 6
    preview._is_playing = True
    preview.set_scroll_mode(scroll_mode)
    # 自动滚动激活时视口跟随播放行，居中到行 6
    preview._scroll_center_line = 6.0
    dirty = []
    monkeypatch.setattr(preview, "update", lambda *args: dirty.append(args))

    preview._update_dynamic_playback_rows()

    assert dirty and len(dirty[-1]) == 1
    expected = preview._line_repaint_rect(6)
    assert dirty[-1][0].contains(expected)


def test_never_scroll_playback_wipe_rows_still_repainted(qapp, monkeypatch):
    """回归（1.5.1）：从不滚动模式下播放行走字曾整体冻结。

    播放行不进 ``_last_auto_scroll_line_idx``（never 模式不更新它），
    局部重绘必须通过 wipe 时间区间把播放行带出来；编辑光标行照常刷新。
    """
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    singer_id = "s"
    preview = preview_module.KaraokePreview()
    preview.resize(800, 560)
    preview.set_project(
        _project_from([_stamped_line(singer_id, (i + 1) * 1000) for i in range(8)])
    )
    preview.set_current_position(2, 0)
    preview.set_scroll_mode("never")
    preview._is_playing = True
    preview.set_current_time_ms(6500)  # 首帧：时间从 0 跳入 → 全量

    dirty = []
    monkeypatch.setattr(preview, "update", lambda *args: dirty.append(args))

    preview.set_current_time_ms(6600)  # 行 5 演唱中，正常帧间推进

    assert dirty and len(dirty[-1]) == 1
    rect = dirty[-1][0]
    assert rect.contains(preview._line_repaint_rect(5))   # 播放行走字
    assert rect.contains(preview._line_repaint_rect(2))   # 编辑光标行
    assert 0 < rect.height() < preview.height()           # 仍是局部重绘


def test_seek_jump_requests_full_repaint(qapp, monkeypatch):
    """大跨度 seek 跨过多行 wipe 区间 → 受影响行过多，回退全量重绘。"""
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    singer_id = "s"
    preview = preview_module.KaraokePreview()
    preview.resize(800, 560)
    preview.set_project(
        _project_from([_stamped_line(singer_id, (i + 1) * 1000) for i in range(8)])
    )
    preview.set_scroll_mode("never")
    preview._is_playing = True
    preview.set_current_time_ms(1500)
    dirty = []
    monkeypatch.setattr(preview, "update", lambda *args: dirty.append(args))

    preview.set_current_time_ms(7500)

    assert dirty and dirty[-1] == ()


def test_playback_line_change_requests_full_repaint(qapp, monkeypatch):
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    preview = preview_module.KaraokePreview()
    preview.resize(800, 560)
    project = _plain_project(line_count=3, chars_per_line=1)
    project.sentences[0].characters[0].timestamps = [100]
    project.sentences[1].characters[0].timestamps = [200]
    preview.set_project(project)
    preview.set_playing(True)
    preview._line_switch_points = [(100, 0), (200, 1)]
    preview._last_auto_scroll_line_idx = 0
    dirty = []
    monkeypatch.setattr(preview, "update", lambda *args: dirty.append(args))

    preview.set_current_time_ms(250)

    assert dirty and dirty[-1] == ()


def test_partial_lyric_repaint_matches_full_render(qapp, monkeypatch):
    preview = preview_module.KaraokePreview()
    preview.resize(800, 560)
    preview.set_project(_project_with_linked_word())
    preview.show()
    preview.set_playing(True)
    preview._auto_scroll_enabled = False
    qapp.processEvents()

    preview.set_current_time_ms(1_050)
    qapp.processEvents()
    partial = preview.grab().toImage()

    preview.update()
    qapp.processEvents()
    full = preview.grab().toImage()

    assert partial == full


def test_line_repaint_rect_covers_ruby_ink_and_checkpoint_markers(
    qapp, monkeypatch
):
    """回归（1.5.1）：重绘行带按几何行带（height/可见行数）裁剪时，
    22 号字当前行的 Ruby 顶端会落在重绘区外，走字出现"只有下半在动"。

    行带必须覆盖行内容真实外沿：Ruby wipe 裁剪区顶端（基线上方
    ascent + ruby_spacing + ruby 字高 + 2px）与节奏点 marker 底端
    （基线下方 descent + cp_spacing + marker 字高 + 2px）。
    """
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    preview = preview_module.KaraokePreview()
    preview.resize(800, 560)
    preview.set_project(_plain_project(line_count=8, chars_per_line=4))
    preview._scroll_center_line = 4.0

    for line_idx, is_current_row in ((4, True), (2, False)):
        rect = preview._line_repaint_rect(line_idx)
        line_height = preview.height() / preview._visible_lines
        y_center = (
            preview.height() / 2.0
            + (line_idx - preview._scroll_center_line) * line_height
        )
        fm_main = preview._fm_current if is_current_row else preview._fm_context
        ruby_clip_top = (
            y_center
            - fm_main.ascent()
            - preview._ruby_spacing
            - preview._fm_ruby.ascent()
            - 2
        )
        marker_bottom = (
            y_center
            + fm_main.descent()
            + preview._cp_spacing
            + preview._fm_checkpoint.height()
            + 2
        )
        assert rect.top() <= ruby_clip_top, line_idx
        assert rect.bottom() >= marker_bottom - 1, line_idx
        assert rect.height() < preview.height()


def test_line_invalidation_advances_uncached_line_version(qapp, monkeypatch):
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())

    preview = preview_module.KaraokePreview()
    preview.set_project(_project_with_linked_word())

    assert preview._sentence_cache
    assert preview._line_versions.get(0, 0) == 0

    preview._invalidate_line(0)

    assert preview._line_versions[0] == 1


# ---------- _invalidate_line_and_dependents 闭包语义 ----------

def _skip_line(singer_id: str) -> Sentence:
    """全 cc=0 且无时间戳——next/prev 扫描都会跳过它。"""
    return Sentence(
        singer_id=singer_id,
        characters=[Character(char="·", check_count=0, singer_id=singer_id)],
    )


def _stamped_line(singer_id: str, ts: int) -> Sentence:
    """有时间戳——两种扫描都会停在此行并产出 ts。"""
    return Sentence(
        singer_id=singer_id,
        characters=[
            Character(char="あ", check_count=1, timestamps=[ts], singer_id=singer_id)
        ],
    )


def _barrier_line(singer_id: str) -> Sentence:
    """cc>0 但 timestamps 为空——两种扫描的「未完整打轴」屏障。"""
    return Sentence(
        singer_id=singer_id,
        characters=[Character(char="あ", check_count=1, singer_id=singer_id)],
    )


def _project_from(sentences: list[Sentence]) -> Project:
    singer = Singer(name="default", is_default=True)
    for s in sentences:
        s.singer_id = singer.id
        for ch in s.characters:
            ch.singer_id = singer.id
    return Project(singers=[singer], sentences=sentences)


def _versions_after_invalidate(preview, changed_idx: int) -> dict[int, int]:
    """快照 invalidate 前后的 line_versions 增量。"""
    before = {i: preview._line_versions.get(i, 0) for i in range(len(preview._project.sentences))}
    preview._invalidate_line_and_dependents(changed_idx)
    return {
        i: preview._line_versions.get(i, 0) - before[i]
        for i in range(len(preview._project.sentences))
    }


def test_invalidate_dependents_spans_skipped_lines_both_sides(qapp, monkeypatch):
    """A · B(skip) · C · D(skip) · E：改 C 时 A、E 都应被失效。"""
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    singer_id = "s"
    sentences = [
        _stamped_line(singer_id, 1000),  # 0 = A
        _skip_line(singer_id),            # 1 = B (skip)
        _stamped_line(singer_id, 3000),  # 2 = C (changed)
        _skip_line(singer_id),            # 3 = D (skip)
        _stamped_line(singer_id, 5000),  # 4 = E
    ]
    preview = preview_module.KaraokePreview()
    preview.set_project(_project_from(sentences))

    delta = _versions_after_invalidate(preview, 2)

    # C 自身 + 跨 B 到 A、跨 D 到 E 全部应失效一次
    assert delta == {0: 1, 1: 1, 2: 1, 3: 1, 4: 1}


def test_invalidate_dependents_stops_at_yielding_neighbor(qapp, monkeypatch):
    """A · B(stamped) · C · D(stamped) · E：改 C 时 A、E 不应被失效。"""
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    singer_id = "s"
    sentences = [
        _stamped_line(singer_id, 1000),  # 0 = A (远端，不应受影响)
        _stamped_line(singer_id, 2000),  # 1 = B (yields ts → next-scan 屏障)
        _stamped_line(singer_id, 3000),  # 2 = C
        _stamped_line(singer_id, 4000),  # 3 = D (yields ts → prev-scan 屏障)
        _stamped_line(singer_id, 5000),  # 4 = E (远端，不应受影响)
    ]
    preview = preview_module.KaraokePreview()
    preview.set_project(_project_from(sentences))

    delta = _versions_after_invalidate(preview, 2)

    # C 及 B、D 失效；A、E 不应被波及
    assert delta == {0: 0, 1: 1, 2: 1, 3: 1, 4: 0}


def test_invalidate_dependents_stops_at_barrier(qapp, monkeypatch):
    """A · B(barrier) · C · D(barrier) · E：屏障行本身被失效，再向外不扩散。"""
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    singer_id = "s"
    sentences = [
        _stamped_line(singer_id, 1000),  # 0 = A (远端)
        _barrier_line(singer_id),         # 1 = B (barrier)
        _stamped_line(singer_id, 3000),  # 2 = C
        _barrier_line(singer_id),         # 3 = D (barrier)
        _stamped_line(singer_id, 5000),  # 4 = E (远端)
    ]
    preview = preview_module.KaraokePreview()
    preview.set_project(_project_from(sentences))

    delta = _versions_after_invalidate(preview, 2)

    assert delta == {0: 0, 1: 1, 2: 1, 3: 1, 4: 0}


def test_invalidate_dependents_extends_to_list_boundary(qapp, monkeypatch):
    """C · D(skip) · E(skip)：改 C 时 D、E 直到列表末尾都应被失效。"""
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    singer_id = "s"
    sentences = [
        _stamped_line(singer_id, 1000),  # 0 = C (changed)
        _skip_line(singer_id),            # 1 = D
        _skip_line(singer_id),            # 2 = E
    ]
    preview = preview_module.KaraokePreview()
    preview.set_project(_project_from(sentences))

    delta = _versions_after_invalidate(preview, 0)

    assert delta == {0: 1, 1: 1, 2: 1}


def _plain_project(line_count: int, chars_per_line: int) -> Project:
    singer = Singer(name="default", is_default=True)
    sentences = [
        Sentence(
            singer_id=singer.id,
            characters=[
                Character(char="长", check_count=0, singer_id=singer.id)
                for _ in range(chars_per_line)
            ],
        )
        for _ in range(line_count)
    ]
    return Project(singers=[singer], sentences=sentences)


class _WheelEvent:
    def __init__(self, delta: int, modifiers=Qt.KeyboardModifier.NoModifier):
        self._delta = delta
        self._modifiers = modifiers
        self.accepted = False

    def modifiers(self):
        return self._modifiers

    def angleDelta(self):
        return QPoint(0, self._delta)

    def accept(self):
        self.accepted = True


def test_horizontal_scrollbar_only_appears_for_overflow(qapp, monkeypatch):
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    preview = preview_module.KaraokePreview()
    preview.resize(320, 400)
    preview.show()
    preview.set_project(_plain_project(line_count=1, chars_per_line=40))

    assert preview._horizontal_scrollbar.isVisible()
    assert preview._horizontal_scrollbar.maximum() > 0

    preview.resize(4000, 400)

    assert not preview._horizontal_scrollbar.isVisible()
    assert preview._horizontal_scrollbar.value() == 0


def test_alt_wheel_scrolls_horizontally_without_using_vertical_scroll(
    qapp, monkeypatch
):
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    preview = preview_module.KaraokePreview()
    preview.resize(320, 400)
    preview.show()
    preview.set_project(_plain_project(line_count=8, chars_per_line=40))

    horizontal_before = preview._horizontal_scrollbar.value()
    vertical_before = preview._scroll_center_line
    alt_wheel = _WheelEvent(-120, Qt.KeyboardModifier.AltModifier)
    preview.wheelEvent(alt_wheel)

    assert alt_wheel.accepted
    assert preview._horizontal_scrollbar.value() > horizontal_before
    assert preview._scroll_center_line == vertical_before

    horizontal_after_alt = preview._horizontal_scrollbar.value()
    preview.wheelEvent(_WheelEvent(-120))

    assert preview._horizontal_scrollbar.value() == horizontal_after_alt
    assert preview._scroll_center_line == vertical_before + 1


def test_prewarm_targets_current_line_and_visible_window(qapp, monkeypatch):
    """双路预热：打开只预热 current_line 附近一屏；滚动停稳预热新可视窗口；
    每行保留 cur/ctx 两种状态，版本未失效时直接复用。"""
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    preview = preview_module.KaraokePreview()
    preview.resize(800, 560)
    preview.show()
    total = 60
    preview.set_project(_plain_project(line_count=total, chars_per_line=4))

    # 打开：仅 current_line 附近一屏有缓存，绝不全量
    cached_after_open = len(preview._sentence_cache)
    assert 0 < cached_after_open < total * 2

    # 滚动到远处行，防抖触发（_on_viewport_settled）后新可视窗口被预热
    preview._scroll_center_line = 40.0
    preview._on_viewport_settled()
    assert (40, "cur") in preview._sentence_cache
    assert (40, "ctx") in preview._sentence_cache
    assert len(preview._sentence_cache) > cached_after_open

    # 两种字号排版同时存在，且并非同一份布局结果
    markers = {
        font_key: preview._sentence_cache[(40, font_key)]
        for font_key in ("cur", "ctx")
    }
    assert markers["cur"] is not markers["ctx"]

    # 消费端交替取当前行/上下文行时只选择对应条目，不覆盖另一种状态
    sentence = preview._project.sentences[40]
    assert preview._get_sentence_render_data(
        40, sentence, preview._fm_current, "cur"
    ) is markers["cur"]
    assert preview._get_sentence_render_data(
        40, sentence, preview._fm_context, "ctx"
    ) is markers["ctx"]
    assert preview._sentence_cache[(40, "cur")] is markers["cur"]
    assert preview._sentence_cache[(40, "ctx")] is markers["ctx"]

    # 版本校验：重复 settle 不重复渲染（两种缓存条目均原样复用）
    preview._on_viewport_settled()
    for font_key, marker in markers.items():
        assert preview._sentence_cache[(40, font_key)] is marker

    # 编辑使版本失效（含相邻依赖行）→ settle 后两种状态均按新版本重渲
    preview._invalidate_line_and_dependents(40)
    preview._on_viewport_settled()
    for font_key, marker in markers.items():
        assert preview._sentence_cache[(40, font_key)] is not marker

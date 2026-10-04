"""打轴按钮（btn_tag → _on_tag_now）行为回归测试。

旧实现直连 on_timing_key_pressed/released：未播放时服务 shim 内的裸
engine.play() 会绕过 _on_play 启播，音频在响而界面停留在编辑模式
（不切打轴模式、无位置轮询/时间同步、无按键音）。

锁定重做后的契约：
1. 未播放 → 先走 _on_play() 正常启播，成功后才打点；
2. 启播失败（未加载音频等）→ 不打点；
3. 已播放 → 不再触发 _on_play，直接打点；
4. press/release 语义与键盘 tag_now 一致：按键音普通 cp 在 press 响、
   停顿点 cp 在 release 响，queue_delay_ms 补偿参数透传服务层；
5. 按键名取打轴模式 tag_now 首个绑定键（与按钮文案一致），不再硬编码 SPACE。
"""

from __future__ import annotations

from types import SimpleNamespace

from strange_uta_game.frontend.editor.timing_interface import EditorInterface


class _FakeTimingService:
    def __init__(self, playing: bool = False, tail_sequence: list[bool] | None = None):
        self._playing = playing
        self._tails = list(tail_sequence or [])
        self.play_calls: list[tuple] = []  # (method, key, queue_delay_ms)

    def is_playing(self) -> bool:
        return self._playing

    def is_current_cp_sentence_end_tail(self) -> bool:
        if self._tails:
            return self._tails.pop(0)
        return False

    def on_timing_key_pressed(self, key: str, queue_delay_ms: int = 0) -> None:
        self.play_calls.append(("pressed", key, queue_delay_ms))

    def on_timing_key_released(self, key: str, queue_delay_ms: int = 0) -> None:
        self.play_calls.append(("released", key, queue_delay_ms))


class _FakeKeysound:
    def __init__(self) -> None:
        self.events: list[str] = []

    def play_press(self) -> None:
        self.events.append("press")

    def play_release(self) -> None:
        self.events.append("release")


def _make_editor(service: _FakeTimingService, keysound=None, tag_raw="D:short,F:short"):
    editor = SimpleNamespace(
        _timing_service=service,
        _keysound_player=keysound,
        _shortcut_actions_timing={"tag_now": tag_raw},
        on_play_calls=[],
    )

    def fake_on_play():
        editor.on_play_calls.append(True)
        if service.is_playing() is False and getattr(service, "play_succeeds", True):
            service._playing = True

    editor._on_play = fake_on_play
    editor._tag_now_button_key_name = lambda: EditorInterface._tag_now_button_key_name(editor)
    return editor


def test_tag_button_starts_playback_via_on_play_then_stamps():
    """未播放：先 _on_play 正常启播（打轴模式/时间同步随播放开启），再打点。"""
    service = _FakeTimingService(playing=False, tail_sequence=[False, False])
    keysound = _FakeKeysound()
    editor = _make_editor(service, keysound)

    EditorInterface._on_tag_now(editor)

    assert editor.on_play_calls == [True]
    # press + release 各一次，按键名取 tag_now 首个绑定键
    assert [c[0] for c in service.play_calls] == ["pressed", "released"]
    assert all(c[1] == "D" for c in service.play_calls)
    # queue_delay_ms 补偿参数透传（毫秒整数，>=0）
    assert all(isinstance(c[2], int) and c[2] >= 0 for c in service.play_calls)
    # 普通 cp：press 按键音，release 无
    assert keysound.events == ["press"]


def test_tag_button_playing_skips_on_play_and_stamps():
    """已播放（打轴模式）：不再触发启播，直接打点。"""
    service = _FakeTimingService(playing=True, tail_sequence=[False, False])
    keysound = _FakeKeysound()
    editor = _make_editor(service, keysound)

    EditorInterface._on_tag_now(editor)

    assert editor.on_play_calls == []
    assert [c[0] for c in service.play_calls] == ["pressed", "released"]
    assert keysound.events == ["press"]


def test_tag_button_play_start_failure_skips_stamp():
    """启播失败（如未加载音频，_on_play 后仍未播放）：不打点。"""
    service = _FakeTimingService(playing=False)
    service.play_succeeds = False  # fake_on_play 不翻转播放态
    keysound = _FakeKeysound()
    editor = _make_editor(service, keysound)

    EditorInterface._on_tag_now(editor)

    assert editor.on_play_calls == [True]
    assert service.play_calls == []
    assert keysound.events == []


def test_tag_button_keysound_routes_tail_cp_on_release():
    """停顿点 cp：press 不响按键音（服务层过滤 pressed），release 响。"""
    service = _FakeTimingService(playing=True, tail_sequence=[True, True])
    keysound = _FakeKeysound()
    editor = _make_editor(service, keysound)

    EditorInterface._on_tag_now(editor)

    assert keysound.events == ["release"]
    assert [c[0] for c in service.play_calls] == ["pressed", "released"]


def test_tag_button_normal_then_tail_plays_both_keysounds():
    """普通 cp 后紧跟停顿点 cp：press 响一次，press 推进后 release 再响（同键盘短敲）。"""
    service = _FakeTimingService(playing=True, tail_sequence=[False, True])
    keysound = _FakeKeysound()
    editor = _make_editor(service, keysound)

    EditorInterface._on_tag_now(editor)

    assert keysound.events == ["press", "release"]


def test_tag_button_no_keysound_attribute_is_safe():
    """最小化假对象（无 _keysound_player）不报错，打点照常。"""
    service = _FakeTimingService(playing=True, tail_sequence=[False, False])
    editor = _make_editor(service, keysound=None)
    del editor._keysound_player  # getattr 兜底路径

    EditorInterface._on_tag_now(editor)

    assert [c[0] for c in service.play_calls] == ["pressed", "released"]


def test_tag_now_button_key_name_fallbacks():
    """按键名派生：取首个绑定键；无映射时回退 SPACE。"""
    service = _FakeTimingService(playing=True, tail_sequence=[False, False])
    editor = _make_editor(service, tag_raw="")
    assert EditorInterface._tag_now_button_key_name(editor) == "SPACE"

    editor2 = SimpleNamespace(_shortcut_actions_timing={})
    assert EditorInterface._tag_now_button_key_name(editor2) == "SPACE"

    editor3 = SimpleNamespace()  # 设置未加载
    assert EditorInterface._tag_now_button_key_name(editor3) == "SPACE"

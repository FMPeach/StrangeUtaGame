"""BASS 双引擎修复回归测试（D5 / D6 / D10 / D12）。

BASS 仅 Windows 可用。测试不依赖真实设备状态转换：需要 BASS 全局会话
的地方一律 monkeypatch（BASS_Free 等绝不能真跑，否则杀掉进程级会话影响
其它测试）。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from strange_uta_game.backend.infrastructure.audio import bass_available
from strange_uta_game.backend.infrastructure.audio.base import (
    AudioPlaybackError,
    PlaybackState,
)

pytestmark = pytest.mark.skipif(not bass_available, reason="BASS 引擎仅 Windows 可用")

if bass_available:
    from strange_uta_game.backend.infrastructure.audio.bass_engine import (
        BassEngine,
        _bass,
    )
    from strange_uta_game.backend.infrastructure.audio.bass_tsm_engine import (
        BassTsmEngine,
    )
    from strange_uta_game.backend.infrastructure.audio.keysound_player import (
        KeySoundPlayer,
    )
    from strange_uta_game.backend.infrastructure.audio.metronome_player import (
        MetronomePlayer,
    )
    from strange_uta_game.backend.infrastructure.audio.sample_registry import (
        register_bass_sample_owner,
    )


@pytest.fixture
def tiny_wav(tmp_path) -> str:
    sr = 44100
    t = np.linspace(0, 1.0, sr, endpoint=False)
    p = tmp_path / "tone.wav"
    sf.write(str(p), (np.sin(2 * np.pi * 440 * t) * 0.2).astype(np.float32), sr)
    return str(p)


# ═══════════════════════ D6 bass_engine._recover_device_sync ═══════════════════════


class TestRecoverSyncJoin:
    def test_releases_stream_lock_before_join(self):
        """D6：后台恢复线程需要 _stream_lock 才能推进；持锁 join 必然等满
        3s 超时。修复后先释放本线程的外层锁再 join，后台线程能完成。
        """
        engine = BassEngine()
        engine._playback_path = "fake.wav"
        engine._recovering = True
        engine._tempo_stream = 12345  # join 后按句柄判断恢复成功

        bg_got_lock = threading.Event()

        def background_recovery():
            # 模拟 _run_recovery → _do_recover：需要 _stream_lock
            with engine._stream_lock:
                bg_got_lock.set()

        t = threading.Thread(target=background_recovery, daemon=True)
        engine._recovery_thread = t
        t.start()

        acquired = threading.Event()
        result = {}

        def user_play_path():
            # 模拟 play()：持锁进入 _recover_device_sync
            with engine._stream_lock:
                acquired.set()
                result["ok"] = engine._recover_device_sync("play failed")

        u = threading.Thread(target=user_play_path, daemon=True)
        u.start()
        assert acquired.wait(timeout=2.0)

        # 修复后 join 前释放锁 → bg 拿到锁；旧实现此处 bg 要等满 3s 超时
        assert bg_got_lock.wait(timeout=2.5), "恢复线程未在 2.5s 内拿到锁（持锁 join 未修复）"
        u.join(timeout=5.0)
        assert not u.is_alive()
        assert result["ok"] is True  # _tempo_stream != 0

    def test_join_path_reports_unrestored_when_no_stream(self):
        engine = BassEngine()
        engine._playback_path = "fake.wav"
        engine._recovering = True
        engine._tempo_stream = 0

        done = threading.Event()

        def bg():
            with engine._stream_lock:
                done.set()

        t = threading.Thread(target=bg, daemon=True)
        engine._recovery_thread = t
        t.start()

        result = {}

        def user_play_path():
            with engine._stream_lock:
                result["ok"] = engine._recover_device_sync("play failed")

        u = threading.Thread(target=user_play_path, daemon=True)
        u.start()
        u.join(timeout=5.0)
        assert not u.is_alive()
        assert result["ok"] is False  # 无句柄 → 恢复未成功

    def test_tsm_recover_sync_joins_background_recovery(self):
        """D6 同型（TSM 引擎）：后台恢复线程需要 _stream_lock；join 前必须
        释放本线程的外层锁（公共 API release/acquire 配对），否则等满超时。"""
        engine = BassTsmEngine()
        engine._current_source_path = "src.mp3"
        engine._source_1x_path = "src.mp3"
        engine._recovering = True
        engine._stream = 12345  # join 后按句柄判断恢复成功

        bg_got_lock = threading.Event()

        def background_recovery():
            # 模拟 _run_recovery → _do_recover_locked：需要 _stream_lock
            with engine._stream_lock:
                bg_got_lock.set()

        t = threading.Thread(target=background_recovery, daemon=True)
        engine._recovery_thread = t
        t.start()

        acquired = threading.Event()
        result = {}

        def user_play_path():
            with engine._stream_lock:
                acquired.set()
                result["ok"] = engine._recover_device_sync("play failed")

        u = threading.Thread(target=user_play_path, daemon=True)
        u.start()
        assert acquired.wait(timeout=2.0)

        assert bg_got_lock.wait(timeout=2.5), "恢复线程未在 2.5s 内拿到锁（持锁 join 未修复）"
        u.join(timeout=5.0)
        assert not u.is_alive()
        assert result["ok"] is True  # _stream != 0


# ═══════════════════════ play() 设备丢失后同步恢复回归 ═══════════════════════


class TestPlayRecoveryAfterDeviceLoss:
    """回归：play() 遇到设备丢失必须同步恢复成功后继续播放，而不是恒抛
    AudioPlaybackError。TSM 引擎在恢复后台化后 play() 仍调用返回 None 的
    _recover_device，`if not ...` 恒为真——热拔后再点播放必抛错。"""

    def test_bass_engine_play_recovers_after_device_loss(self, monkeypatch):
        engine = BassEngine()
        engine._playback_path = "fake.wav"
        engine._tempo_stream = 12345
        engine._duration_ms = 1000
        engine._state = PlaybackState.STOPPED

        fail_first = [True]

        def fake_channel_play(handle, restart):
            if fail_first[0]:
                fail_first[0] = False
                return 0  # 第一次播放失败：模拟设备丢失
            return 1  # 恢复后的重试成功

        monkeypatch.setattr(_bass, "BASS_ChannelPlay", fake_channel_play)
        monkeypatch.setattr(engine, "_do_recover", lambda reason=None: True)

        engine.play()  # 修复前：直接抛 AudioPlaybackError

        assert engine._state == PlaybackState.PLAYING

    def test_bass_engine_play_raises_when_recovery_fails(self, monkeypatch):
        engine = BassEngine()
        engine._playback_path = "fake.wav"
        engine._tempo_stream = 12345
        engine._state = PlaybackState.STOPPED

        monkeypatch.setattr(_bass, "BASS_ChannelPlay", lambda handle, restart: 0)
        monkeypatch.setattr(engine, "_do_recover", lambda reason=None: False)

        with pytest.raises(AudioPlaybackError):
            engine.play()
        assert engine._state != PlaybackState.PLAYING

    def test_bass_tsm_play_recovers_after_device_loss(self, monkeypatch):
        """核心回归：_recover_device 返回 None 后 `if not ...` 恒为真，热拔
        后点播放必抛错。play() 必须走同步恢复，成功则继续播放。"""
        engine = BassTsmEngine()
        engine._current_source_path = "src.mp3"
        engine._source_1x_path = "src.mp3"
        engine._stream = 12345
        engine._duration_ms = 1000
        engine._state = PlaybackState.STOPPED

        fail_first = [True]

        def fake_channel_play(handle, restart):
            if fail_first[0]:
                fail_first[0] = False
                return 0  # 第一次播放失败：模拟设备丢失
            return 1  # 恢复后的重试成功

        monkeypatch.setattr(_bass, "BASS_ChannelPlay", fake_channel_play)
        monkeypatch.setattr(engine, "_do_recover_locked", lambda: True)

        engine.play()  # 修复前：恒抛 AudioPlaybackError

        assert engine._state == PlaybackState.PLAYING

    def test_bass_tsm_play_raises_when_recovery_fails(self, monkeypatch):
        engine = BassTsmEngine()
        engine._current_source_path = "src.mp3"
        engine._source_1x_path = "src.mp3"
        engine._stream = 12345
        engine._state = PlaybackState.STOPPED

        monkeypatch.setattr(_bass, "BASS_ChannelPlay", lambda handle, restart: 0)
        monkeypatch.setattr(engine, "_do_recover_locked", lambda: False)

        with pytest.raises(AudioPlaybackError):
            engine.play()
        assert engine._state != PlaybackState.PLAYING

    def test_bass_tsm_play_raises_without_source(self, monkeypatch):
        """未加载任何音频时 play 失败不触发恢复，直接抛错。"""
        engine = BassTsmEngine()
        engine._stream = 12345
        engine._state = PlaybackState.STOPPED

        monkeypatch.setattr(_bass, "BASS_ChannelPlay", lambda handle, restart: 0)

        with pytest.raises(AudioPlaybackError):
            engine.play()


# ═══════════════════════ D10 sample 失效登记表 ═══════════════════════


class TestSampleInvalidationRegistry:
    """BASS_Free 重建会话使进程内全部 sample 句柄失效。恢复流程（无论成败）
    必须经 sample_registry 失效所有登记的按键音/节拍器实例；各实例在下一次
    播放时对新会话惰性重载——自闭合接线，不依赖 UI 层回调。"""

    @staticmethod
    def _stub_recover(monkeypatch, engine):
        """打桩 _do_recover：走 BASS_Free（桩）→ 初始化失败 → finally 失效。"""
        if hasattr(engine, "_free_streams"):
            monkeypatch.setattr(engine, "_free_streams", lambda: None)
        else:
            monkeypatch.setattr(engine, "_free_stream", lambda: None)
        monkeypatch.setattr(_bass, "BASS_Free", lambda: 1)
        monkeypatch.setattr(engine, "_ensure_initialized", lambda: False)

    def test_bass_engine_invalidates_players_even_when_recovery_fails(
        self, monkeypatch
    ):
        keysound = KeySoundPlayer()
        metronome = MetronomePlayer()
        keysound._press_sample = 11
        keysound._release_sample = 12
        metronome._beat_sample = 21
        metronome._accent_sample = 22

        engine = BassEngine()
        engine._playback_path = "fake.wav"
        self._stub_recover(monkeypatch, engine)
        assert engine._do_recover() is False  # 恢复失败

        # BASS_Free 已执行：句柄无论恢复成败都已失效
        assert keysound._press_sample == 0 and keysound._release_sample == 0
        assert metronome._beat_sample == 0 and metronome._accent_sample == 0

    def test_bass_tsm_invalidates_players_even_when_recovery_fails(
        self, monkeypatch
    ):
        keysound = KeySoundPlayer()
        metronome = MetronomePlayer()
        keysound._press_sample = 11
        metronome._accent_sample = 22

        engine = BassTsmEngine()
        self._stub_recover(monkeypatch, engine)
        assert engine._do_recover_locked() is False  # 恢复失败

        assert keysound._press_sample == 0
        assert metronome._accent_sample == 0

    def test_one_bad_owner_does_not_break_invalidation(self, monkeypatch):
        good = KeySoundPlayer()
        good._press_sample = 1

        class _Bad:
            def invalidate(self):
                raise RuntimeError("boom")

        bad = _Bad()  # 持强引用：WeakSet 不会立刻回收它
        register_bass_sample_owner(bad)

        engine = BassEngine()
        engine._playback_path = "fake.wav"
        self._stub_recover(monkeypatch, engine)
        engine._do_recover()  # 单实例失效异常不得外泄

        assert good._press_sample == 0

    def test_keysound_lazy_reload_on_next_play(self, monkeypatch, tmp_path):
        """失效后下一次按键播放时对新 BASS 会话惰性重载，且只触发一次。"""
        monkeypatch.setattr(_bass, "BASS_Init", lambda *a, **k: 1)
        monkeypatch.setattr(_bass, "BASS_SampleGetChannel", lambda *a, **k: 0)

        player = KeySoundPlayer()
        press = tmp_path / "press.wav"
        release = tmp_path / "release.wav"
        press.write_bytes(b"x")
        release.write_bytes(b"x")
        loads = []

        def fake_load_sample(path):
            loads.append(Path(path))
            return 7

        monkeypatch.setattr(player, "_load_sample", fake_load_sample)
        player.load(press, release)
        assert loads == [press, release]

        player.invalidate()  # 模拟引擎恢复后的失效通知
        assert not player.is_loaded()

        loads.clear()
        player.play_press()  # 下一次播放触发惰性重载
        assert loads == [press, release]
        assert player.is_loaded()

        loads.clear()
        player.play_release()  # 已重载：不再重复加载
        assert loads == []

    def test_keysound_no_lazy_reload_when_never_loaded(self, monkeypatch):
        """从未 load 过的实例失效后不应尝试按空路径重载。"""
        monkeypatch.setattr(_bass, "BASS_Init", lambda *a, **k: 1)

        player = KeySoundPlayer()
        player._press_sample = 5  # 模拟旧会话残留句柄
        loads = []
        monkeypatch.setattr(
            player, "_load_sample", lambda path: loads.append(path) or 1
        )

        player.invalidate()
        player.play_press()  # 无源路径：不应触发 _load_sample
        assert loads == []
        assert player._press_sample == 0

    def test_metronome_lazy_reload_on_next_play(self, monkeypatch, tmp_path):
        """节拍器同款惰性重载。"""
        monkeypatch.setattr(_bass, "BASS_Init", lambda *a, **k: 1)
        monkeypatch.setattr(_bass, "BASS_SampleGetChannel", lambda *a, **k: 0)

        player = MetronomePlayer()
        beat = tmp_path / "beat.wav"
        accent = tmp_path / "accent.wav"
        beat.write_bytes(b"x")
        accent.write_bytes(b"x")
        loads = []

        def fake_load_sample(path):
            loads.append(Path(path))
            return 9

        monkeypatch.setattr(player, "_load_sample", fake_load_sample)
        player.load(beat, accent)
        assert loads == [beat, accent]

        player.invalidate()
        assert not player.is_loaded()

        loads.clear()
        player.play_beat()  # 下一次节拍触发惰性重载
        assert loads == [beat, accent]
        assert player.is_loaded()

    def test_free_clears_paths_so_no_stale_reload(self, monkeypatch, tmp_path):
        """free() 清掉路径记忆：实例弃用/换样本后不做过期路径的惰性重载。"""
        player = KeySoundPlayer()
        press = tmp_path / "press.wav"
        release = tmp_path / "release.wav"
        press.write_bytes(b"x")
        release.write_bytes(b"x")
        player._press_path = press
        player._release_path = release
        player._press_sample = 3

        player.free()

        player.invalidate()
        assert player._invalidated is False  # 无路径 → 不标记待重载


# ═══════════════════════ D5 bass_tsm 设备恢复后台化 ═══════════════════════


class TestTsmRecoveryBackground:
    def test_recover_device_spawns_background_thread(self, monkeypatch):
        """D5：恢复绝不能在轮询线程（UI）同步执行——立即返回，后台跑。"""
        engine = BassTsmEngine()
        engine._recovering = False
        engine._last_recovery_attempt = 0.0

        called = []
        done = threading.Event()

        def fake_run(reason):
            called.append(reason)
            engine._recovering = False
            done.set()

        monkeypatch.setattr(engine, "_run_recovery", fake_run)

        t0 = time.monotonic()
        engine._recover_device("device paused")
        elapsed_s = time.monotonic() - t0
        assert elapsed_s < 0.5, "recover_device 必须立即返回，不能同步恢复"
        assert done.wait(timeout=2.0), "后台恢复线程未执行"
        assert called == ["device paused"]

    def test_recover_device_throttles_repeat_calls(self, monkeypatch):
        engine = BassTsmEngine()
        engine._recovering = False
        engine._last_recovery_attempt = time.monotonic()  # 刚刚恢复过
        called = []
        monkeypatch.setattr(engine, "_run_recovery", lambda r: called.append(r))
        engine._recover_device("device paused")
        assert called == [], "1s 节流窗口内的重复请求不应派发"

    def test_run_recovery_holds_stream_lock(self, monkeypatch):
        """D5：恢复在后台线程执行且全程持 _stream_lock，异常不外泄。"""
        engine = BassTsmEngine()

        def fake_do():
            # 由 _run_recovery 在 with self._stream_lock 内调用
            assert engine._stream_lock._is_owned(), "恢复主体必须持 _stream_lock"
            raise RuntimeError("模拟恢复异常")

        monkeypatch.setattr(engine, "_do_recover_locked", fake_do)
        engine._recovering = True
        engine._run_recovery("device lost")  # 异常被吞，_recovering 复位
        assert engine._recovering is False

    def test_switch_to_file_rolls_back_on_build_failure(self, monkeypatch):
        """D5：_switch_to_file 必须检查 _build_stream_locked 返回值；
        失败时回滚模式标志，否则 _effective_scale 与实际流不一致。"""
        engine = BassTsmEngine()
        engine._is_tempo = True
        engine._speed_scale = 1.0
        engine._tempo_speed = 1.0
        engine._current_source_path = "src_1x.mp3"
        engine._state = PlaybackState.PAUSED
        monkeypatch.setattr(engine, "_build_stream_locked", lambda **kw: False)

        engine._switch_to_file(0.5, "rendered_05.mp3")

        assert engine._is_tempo is True
        assert engine._speed_scale == 1.0
        assert engine._current_source_path == "src_1x.mp3"

    def test_switch_to_file_applies_flags_on_success(self, monkeypatch):
        engine = BassTsmEngine()
        engine._is_tempo = True
        engine._speed_scale = 1.0
        engine._current_source_path = "src_1x.mp3"
        engine._state = PlaybackState.PAUSED
        monkeypatch.setattr(engine, "_build_stream_locked", lambda **kw: True)

        engine._switch_to_file(0.5, "rendered_05.mp3")

        assert engine._is_tempo is False
        assert engine._speed_scale == 0.5
        assert engine._current_source_path == "rendered_05.mp3"

    def test_switch_to_tempo_rolls_back_on_build_failure(self, monkeypatch):
        """同类站点：_switch_to_tempo 失败同样回滚。"""
        engine = BassTsmEngine()
        engine._is_tempo = False
        engine._speed_scale = 0.5
        engine._current_source_path = "rendered_05.mp3"
        engine._tempo_speed = 1.0
        engine._state = PlaybackState.PAUSED
        monkeypatch.setattr(engine, "_build_stream_locked", lambda **kw: False)

        engine._switch_to_tempo(1.5)

        assert engine._is_tempo is False
        assert engine._speed_scale == 0.5
        assert engine._current_source_path == "rendered_05.mp3"


# ═══════════════════════ D7 预热不整曲解码 ═══════════════════════


class TestPrewarmCheckOnly:
    def test_prewarm_speeds_dispatches_check_only(self, monkeypatch):
        """D7：预热在 UI 线程（持 _stream_lock）运行，必须走 check_only
        存在性检查——绝不做整曲 MP3 同步解码。"""
        engine = BassTsmEngine()
        calls = []

        class _StubCache:
            def ensure(self, speed, priority=99, check_only=False, **kw):
                calls.append((speed, priority, check_only))
                return None

        engine._cache = _StubCache()
        engine.prewarm_speeds(speed_min=0.2, speed_max=2.0)

        assert calls, "预热应派发速度任务"
        assert all(c[2] is True for c in calls), "预热必须使用 check_only 路径"
        speeds = [c[0] for c in calls]
        assert speeds == [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 1.25, 1.5]
        # 范围过滤：只派发滑块范围内的速度
        calls.clear()
        engine.prewarm_speeds(speed_min=0.5, speed_max=1.0)
        assert [c[0] for c in calls] == [0.9, 0.8, 0.7, 0.6, 0.5]


# ═══════════════════════ D12 _decode_full_pcm 守卫 ═══════════════════════


class TestDecodeFullPcmGuard:
    def test_getinfo_failure_falls_back_to_soundfile(self, tiny_wav, monkeypatch):
        """GetInfo 失败 → 退回 soundfile 解码，不再用未初始化的 info。"""
        monkeypatch.setattr(_bass, "BASS_ChannelGetInfo", lambda *a: 0)

        pcm, sr, ch = BassTsmEngine._decode_full_pcm(tiny_wav)

        data, sf_sr = sf.read(tiny_wav, dtype="float32")
        if data.ndim == 1:
            data = data.reshape(-1, 1)
        assert pcm.shape == data.shape
        assert sr == sf_sr
        assert ch == data.shape[1]

    def test_getlength_failure_falls_back_to_soundfile(self, tiny_wav, monkeypatch):
        """D12：GetLength 失败（c_uint64 下溢为 0xFFFF...）不得 np.empty(OOM)。"""
        monkeypatch.setattr(_bass, "BASS_ChannelGetInfo", lambda *a: 1)
        monkeypatch.setattr(
            _bass, "BASS_ChannelGetLength", lambda *a: (1 << 64) - 1
        )

        pcm, sr, ch = BassTsmEngine._decode_full_pcm(tiny_wav)

        data, sf_sr = sf.read(tiny_wav, dtype="float32")
        if data.ndim == 1:
            data = data.reshape(-1, 1)
        assert pcm.shape == data.shape

    def test_decode_real_wav_matches_soundfile(self, tiny_wav):
        """无 monkeypatch 的真实解码路径（FLOAT wav 无编解码延迟，长度应一致）。"""
        pcm, sr, ch = BassTsmEngine._decode_full_pcm(tiny_wav)
        data, sf_sr = sf.read(tiny_wav, dtype="float32")
        if data.ndim == 1:
            data = data.reshape(-1, 1)
        assert pcm.shape[0] == data.shape[0]
        assert ch == data.shape[1]
        assert sr == sf_sr

"""BASS audio engine — hardware-clock position, real-time tempo, no jitter.

Replaces: sounddevice_engine.py + ring_buffer.py + tsm_cache.py (~1200 lines)
With:     bass.dll + bass_fx.dll direct ctypes (~250 lines)

Key differences from SoundDeviceEngine:
  - get_position_ms() → BASS_ChannelGetPosition (DMA pointer, ±2ms jitter)
  - set_speed()       → BASS_ATTRIB_TEMPO (instant, no offline rendering)
  - No RingBuffer, no Producer thread, no TSMRenderCache.
"""

from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf

from .base import (
    AudioInfo,
    AudioLoadError,
    AudioPlaybackError,
    IAudioEngine,
    PlaybackState,
)

# ═══════════════════════════════════════════════════════════════════
# Load BASS DLLs (x64)
# ═══════════════════════════════════════════════════════════════════

_BASS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "bass" / "x64"

_bass = ctypes.CDLL(str(_BASS_DIR / "bass.dll"))
_bass_fx = ctypes.CDLL(str(_BASS_DIR / "bass_fx.dll"))

# ═══════════════════════════════════════════════════════════════════
# BASS constants (from bass.h / bass_fx.h)
# ═══════════════════════════════════════════════════════════════════

BASS_POS_BYTE = 0
BASS_ACTIVE_STOPPED = 0
BASS_ACTIVE_PLAYING = 1
BASS_ACTIVE_PAUSED = 3
BASS_ATTRIB_VOL = 2
BASS_ATTRIB_TEMPO = 0x10000
BASS_SAMPLE_FLOAT = 256
BASS_STREAM_DECODE = 0x200000
BASS_STREAM_PRESCAN = 0x20000
BASS_FX_FREESOURCE = 0x10000
BASS_UNICODE = 0x80000000

# ═══════════════════════════════════════════════════════════════════
# ctypes signatures — BASS core
# ═══════════════════════════════════════════════════════════════════

_bass.BASS_Init.restype = ctypes.c_int
_bass.BASS_Init.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]

_bass.BASS_StreamCreateFile.restype = ctypes.c_uint
_bass.BASS_StreamCreateFile.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint]

_bass.BASS_ChannelPlay.restype = ctypes.c_int
_bass.BASS_ChannelPlay.argtypes = [ctypes.c_uint, ctypes.c_int]

_bass.BASS_ChannelPause.restype = ctypes.c_int
_bass.BASS_ChannelPause.argtypes = [ctypes.c_uint]

_bass.BASS_ChannelStop.restype = ctypes.c_int
_bass.BASS_ChannelStop.argtypes = [ctypes.c_uint]

_bass.BASS_ChannelGetPosition.restype = ctypes.c_uint64
_bass.BASS_ChannelGetPosition.argtypes = [ctypes.c_uint, ctypes.c_uint]

_bass.BASS_ChannelSetPosition.restype = ctypes.c_int
_bass.BASS_ChannelSetPosition.argtypes = [ctypes.c_uint, ctypes.c_uint64, ctypes.c_uint]

_bass.BASS_ChannelGetLength.restype = ctypes.c_uint64
_bass.BASS_ChannelGetLength.argtypes = [ctypes.c_uint, ctypes.c_uint]

_bass.BASS_ChannelIsActive.restype = ctypes.c_uint
_bass.BASS_ChannelIsActive.argtypes = [ctypes.c_uint]

_bass.BASS_ChannelSetAttribute.restype = ctypes.c_int
_bass.BASS_ChannelSetAttribute.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_float]

_bass.BASS_ChannelBytes2Seconds.restype = ctypes.c_double
_bass.BASS_ChannelBytes2Seconds.argtypes = [ctypes.c_uint, ctypes.c_uint64]

_bass.BASS_ChannelSeconds2Bytes.restype = ctypes.c_uint64
_bass.BASS_ChannelSeconds2Bytes.argtypes = [ctypes.c_uint, ctypes.c_double]

_bass.BASS_StreamFree.restype = ctypes.c_int
_bass.BASS_StreamFree.argtypes = [ctypes.c_uint]

_bass.BASS_ErrorGetCode.restype = ctypes.c_int
_bass.BASS_ErrorGetCode.argtypes = []

# BASS_INFO struct — for reading output buffer latency
class BASS_INFO(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint),
        ("hwsize", ctypes.c_uint),
        ("hwfree", ctypes.c_uint),
        ("freesam", ctypes.c_uint),
        ("free3d", ctypes.c_uint),
        ("minrate", ctypes.c_uint),
        ("maxrate", ctypes.c_uint),
        ("eax", ctypes.c_int),
        ("minbuf", ctypes.c_uint),      # minimum buffer length (ms)
        ("dsver", ctypes.c_uint),
        ("latency", ctypes.c_uint),     # average output delay (bytes)
        ("initflags", ctypes.c_uint),
        ("speakers", ctypes.c_uint),
        ("freq", ctypes.c_uint),
    ]

_bass.BASS_GetInfo.restype = ctypes.c_int
_bass.BASS_GetInfo.argtypes = [ctypes.POINTER(BASS_INFO)]

# ═══════════════════════════════════════════════════════════════════
# ctypes signatures — BASS_FX
# ═══════════════════════════════════════════════════════════════════

_bass_fx.BASS_FX_TempoCreate.restype = ctypes.c_uint
_bass_fx.BASS_FX_TempoCreate.argtypes = [ctypes.c_uint, ctypes.c_uint]


class BassEngine(IAudioEngine):
    """BASS 音频引擎 — 硬件时钟定位、实时变速、零抖动。"""

    def __init__(self) -> None:
        self._state = PlaybackState.STOPPED
        self._file_path: Optional[str] = None
        self._duration_ms: int = 0
        self._speed: float = 1.0
        self._volume: float = 1.0
        self._tempo_stream: int = 0
        self._decode_stream: int = 0
        self._position_callback: Optional[Callable[[int], None]] = None

        # waveform data (read via soundfile, for get_original_samples)
        self._original_data: Optional[np.ndarray] = None
        self._original_sample_rate: int = 44100
        self._channels: int = 2

        # Cached BASS output latency (ms) – queried once on init, stable forever.
        self._output_latency_ms: int = 0

        # Monotonic guard – prevent progress bar flash
        self._last_reported_ms: int = 0

        if not _bass.BASS_Init(-1, 44100, 0, None, None):
            err = _bass.BASS_ErrorGetCode()
            print(f"[BassEngine] BASS_Init failed (error {err}), will retry on load")
        else:
            print("[BassEngine] BASS initialized")
            self._cache_output_latency()

    def _cache_output_latency(self) -> None:
        """Query BASS_INFO once and cache output latency (static, never changes)."""
        info = BASS_INFO()
        if _bass.BASS_GetInfo(ctypes.byref(info)):
            buf_ms = 0
            if info.latency > 0 and self._original_sample_rate > 0:
                buf_ms = int(
                    info.latency
                    / (4 * self._channels * self._original_sample_rate)
                    * 1000
                )
            self._output_latency_ms = buf_ms + int(info.minbuf)

    # ═══════════════════════════════════════════════════════════════
    # IAudioEngine — load / release
    # ═══════════════════════════════════════════════════════════════

    def load(self, file_path: str, progress_cb=None) -> None:
        self.stop()
        self._free_streams()

        if progress_cb:
            progress_cb("读取音频...", 0.0)

        # Read original PCM for waveform display
        try:
            data, sr = sf.read(str(file_path), dtype="float32")
            if data.ndim == 1:
                data = data.reshape(-1, 1)
            self._original_data = np.ascontiguousarray(data, dtype=np.float32)
            self._original_sample_rate = int(sr)
            self._channels = self._original_data.shape[1]
        except Exception as e:
            raise AudioLoadError(f"读取音频文件失败: {e}")

        if progress_cb:
            progress_cb("创建 BASS 流...", 0.3)

        # BASS decode stream
        self._decode_stream = _bass.BASS_StreamCreateFile(
            0, ctypes.c_wchar_p(file_path), 0, 0,
            BASS_STREAM_DECODE | BASS_STREAM_PRESCAN | BASS_UNICODE,
        )
        if not self._decode_stream:
            err = _bass.BASS_ErrorGetCode()
            raise AudioLoadError(f"BASS 无法打开文件 (error {err}): {file_path}")

        # BASS_FX tempo stream → handles speed + playback
        # NOTE: Do NOT use BASS_FX_FREESOURCE — we keep the decode stream alive
        # so get_position_ms() can query it directly (like RL does),
        # avoiding SoundTouch buffer latency.
        self._tempo_stream = _bass_fx.BASS_FX_TempoCreate(
            self._decode_stream, 0  # no BASS_FX_FREESOURCE
        )
        if not self._tempo_stream:
            err = _bass.BASS_ErrorGetCode()
            _bass.BASS_StreamFree(self._decode_stream)
            self._decode_stream = 0
            raise AudioLoadError(f"BASS_FX_TempoCreate 失败 (error {err})")

        # duration from tempo stream
        byte_len = _bass.BASS_ChannelGetLength(self._tempo_stream, BASS_POS_BYTE)
        self._duration_ms = int(
            _bass.BASS_ChannelBytes2Seconds(self._tempo_stream, byte_len) * 1000
        )

        self._file_path = file_path
        self._state = PlaybackState.STOPPED
        self._last_reported_ms = 0

        # Re-cache latency with actual file params
        self._cache_output_latency()

        if progress_cb:
            progress_cb("就绪", 1.0)

    def _free_streams(self) -> None:
        if self._tempo_stream:
            _bass.BASS_StreamFree(self._tempo_stream)
            self._tempo_stream = 0
        if self._decode_stream:
            _bass.BASS_StreamFree(self._decode_stream)
            self._decode_stream = 0

    def release(self) -> None:
        self.stop()
        self._free_streams()
        self._original_data = None
        self._file_path = None
        self._duration_ms = 0
        self._position_callback = None

    def play(self) -> None:
        if self._tempo_stream == 0:
            raise AudioPlaybackError("没有加载音频文件")
        if self._state == PlaybackState.PLAYING:
            return
        _bass.BASS_ChannelPlay(self._tempo_stream, 0)
        self._state = PlaybackState.PLAYING

    def pause(self) -> None:
        if self._state == PlaybackState.PLAYING:
            _bass.BASS_ChannelPause(self._tempo_stream)
            self._state = PlaybackState.PAUSED

    def stop(self) -> None:
        if self._tempo_stream:
            _bass.BASS_ChannelStop(self._tempo_stream)
            _bass.BASS_ChannelSetPosition(self._tempo_stream, 0, BASS_POS_BYTE)
        self._state = PlaybackState.STOPPED
        self._last_reported_ms = 0

    # ═══════════════════════════════════════════════════════════════
    # IAudioEngine — position
    # ═══════════════════════════════════════════════════════════════

    def get_position_ms(self) -> int:
        if self._tempo_stream == 0:
            return 0
        pos = _bass.BASS_ChannelGetPosition(self._tempo_stream, BASS_POS_BYTE)
        ms = int(_bass.BASS_ChannelBytes2Seconds(self._tempo_stream, pos) * 1000)
        ms = max(0, ms - self._output_latency_ms)
        # Monotonic guard: progress bar never goes backwards
        if ms < self._last_reported_ms:
            ms = self._last_reported_ms
        self._last_reported_ms = ms
        return ms

    def set_position_ms(self, position_ms: int) -> None:
        if self._tempo_stream == 0:
            return
        secs = max(0, min(position_ms, self._duration_ms)) / 1000.0
        # Seek on tempo stream — BASS_FX propagates to decode stream automatically.
        byte_pos = _bass.BASS_ChannelSeconds2Bytes(self._tempo_stream, ctypes.c_double(secs))
        _bass.BASS_ChannelSetPosition(self._tempo_stream, byte_pos, BASS_POS_BYTE)
        self._last_reported_ms = position_ms

    def get_duration_ms(self) -> int:
        return self._duration_ms

    # ═══════════════════════════════════════════════════════════════
    # IAudioEngine — state
    # ═══════════════════════════════════════════════════════════════

    def get_playback_state(self) -> PlaybackState:
        return self._state

    def is_playing(self) -> bool:
        return self._state == PlaybackState.PLAYING

    # ═══════════════════════════════════════════════════════════════
    # IAudioEngine — speed (real-time via BASS_FX)
    # ═══════════════════════════════════════════════════════════════

    def set_speed(self, speed: float) -> None:
        if not 0.2 <= speed <= 2.0:
            raise ValueError(f"速度 {speed} 超出范围 [0.2, 2.0]")
        self._speed = float(speed)
        if self._tempo_stream:
            # BASS tempo: 0% = normal, -50% = half, +100% = double
            tempo_pct = (speed - 1.0) * 100.0
            _bass.BASS_ChannelSetAttribute(
                self._tempo_stream, BASS_ATTRIB_TEMPO, ctypes.c_float(tempo_pct)
            )

    def get_speed(self) -> float:
        return self._speed

    # ═══════════════════════════════════════════════════════════════
    # IAudioEngine — volume
    # ═══════════════════════════════════════════════════════════════

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, float(volume)))
        if self._tempo_stream:
            _bass.BASS_ChannelSetAttribute(
                self._tempo_stream, BASS_ATTRIB_VOL, ctypes.c_float(self._volume)
            )

    def get_volume(self) -> float:
        return self._volume

    # ═══════════════════════════════════════════════════════════════
    # IAudioEngine — callbacks
    # ═══════════════════════════════════════════════════════════════

    def set_position_callback(self, callback: Callable[[int], None]) -> None:
        self._position_callback = callback

    def clear_position_callback(self) -> None:
        self._position_callback = None

    def set_render_progress_callback(
        self, callback: Optional[Callable[[float, float], None]] = None
    ) -> None:
        """No-op: BASS has no offline rendering. Speed changes are instant."""
        pass

    # ═══════════════════════════════════════════════════════════════
    # IAudioEngine — info / waveform data
    # ═══════════════════════════════════════════════════════════════

    def get_audio_info(self) -> Optional[AudioInfo]:
        if self._file_path is None:
            return None
        return AudioInfo(
            file_path=self._file_path,
            duration_ms=self._duration_ms,
            sample_rate=self._original_sample_rate,
            channels=(
                self._original_data.shape[1]
                if self._original_data is not None
                else 2
            ),
        )

    def get_original_samples(self) -> Optional[np.ndarray]:
        """Return raw PCM (float32) for waveform display."""
        return self._original_data

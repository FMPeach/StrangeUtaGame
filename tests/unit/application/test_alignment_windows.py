"""LRC 锚点分窗协议与 worker 解码测试（契约不变式，无需真模型）。"""

import pytest

from strange_uta_game.backend.application.ai_timing.alignment import (
    AlignmentRequest,
    AlignmentToken,
    AlignmentValidationError,
    validate_windows,
)
from strange_uta_game.backend.application.ai_timing.planning import (
    AlignWindow,
    plan_windows,
    windows_digest,
)
from strange_uta_game.backend.application.ai_timing.worker.protocol import (
    deserialize_request,
    serialize_request,
)
from strange_uta_game.backend.application.ai_timing.worker.providers import (
    AlignmentProviderError,
    FakeProvider,
    ForcedAlignmentProvider,
)


def _tokens(n: int):
    return [
        AlignmentToken(
            index=i,
            text=f"t{i}",
            raw_reading=f"r{i}",
            location=(0, i, 0),
            line_idx=0,
        )
        for i in range(n)
    ]


def _req(tokens=None, windows=None, **options):
    tokens = tokens if tokens is not None else _tokens(4)
    return AlignmentRequest(
        tokens=tokens,
        options=dict(options),
        windows=windows,
    )


class TestValidateWindows:
    def test_empty_ok(self):
        validate_windows([], n_tokens=3)
        validate_windows(None, n_tokens=3)

    def test_full_partition_ok(self):
        validate_windows(
            [
                {"start_ms": 0, "end_ms": 5000, "token_lo": 0, "token_hi": 2},
                {"start_ms": 5000, "end_ms": 10000, "token_lo": 2, "token_hi": 4},
            ],
            n_tokens=4,
            duration_ms=10000,
        )

    def test_reject_gap_in_token_partition(self):
        with pytest.raises(AlignmentValidationError, match="不连续"):
            validate_windows(
                [
                    {"start_ms": 0, "end_ms": 5000, "token_lo": 0, "token_hi": 1},
                    {"start_ms": 5000, "end_ms": 10000, "token_lo": 2, "token_hi": 4},
                ],
                n_tokens=4,
            )

    def test_reject_incomplete_coverage(self):
        with pytest.raises(AlignmentValidationError, match="未覆盖"):
            validate_windows(
                [{"start_ms": 0, "end_ms": 5000, "token_lo": 0, "token_hi": 2}],
                n_tokens=4,
            )

    def test_reject_end_beyond_duration(self):
        with pytest.raises(AlignmentValidationError, match="超出音频时长"):
            validate_windows(
                [{"start_ms": 0, "end_ms": 99999, "token_lo": 0, "token_hi": 1}],
                n_tokens=1,
                duration_ms=1000,
            )

    def test_reject_negative_start(self):
        with pytest.raises(AlignmentValidationError, match="负"):
            validate_windows(
                [{"start_ms": -1, "end_ms": 100, "token_lo": 0, "token_hi": 1}],
                n_tokens=1,
            )

    def test_reject_descending_start(self):
        with pytest.raises(AlignmentValidationError, match="升序"):
            validate_windows(
                [
                    {"start_ms": 5000, "end_ms": 8000, "token_lo": 0, "token_hi": 1},
                    {"start_ms": 4000, "end_ms": 9000, "token_lo": 1, "token_hi": 2},
                ],
                n_tokens=2,
            )

    def test_request_post_init_validates(self):
        with pytest.raises(AlignmentValidationError):
            _req(
                windows=[
                    {"start_ms": 0, "end_ms": 100, "token_lo": 0, "token_hi": 99},
                ]
            )


class TestProtocolWindowsRoundtrip:
    def test_request_roundtrip_with_windows(self):
        windows = [
            {"start_ms": 0, "end_ms": 400, "token_lo": 0, "token_hi": 2},
            {"start_ms": 400, "end_ms": 1000, "token_lo": 2, "token_hi": 4},
        ]
        req = _req(windows=windows)
        payload = serialize_request(req)
        assert payload["windows"] == windows
        back = deserialize_request(payload)
        assert back.windows == windows
        assert [t.index for t in back.tokens] == [0, 1, 2, 3]

    def test_request_roundtrip_without_windows(self):
        req = _req(windows=None)
        payload = serialize_request(req)
        assert payload["windows"] is None
        back = deserialize_request(payload)
        assert back.windows is None


class _NoWindowProvider(ForcedAlignmentProvider):
    provider_id = "no_window"
    supports_windows = False

    def validate_model(self, model_spec):
        return None

    def load(self, model_spec, progress, cancel):
        return None

    def align(self, request, audio_path, progress, cancel):
        if request.windows:
            raise AlignmentProviderError("当前对齐后端不支持分窗")
        from strange_uta_game.backend.application.ai_timing.alignment import (
            AlignmentResult,
            EmissionSpan,
        )

        return AlignmentResult(
            annotation_digest=request.annotation_digest,
            model_id="no_window",
            spans=[
                EmissionSpan(token_index=t.index, start_ms=i * 10, end_ms=(i + 1) * 10)
                for i, t in enumerate(request.tokens)
            ],
        )


class TestProviderWindowPolicy:
    def test_unsupported_backend_raises_chinese_error(self):
        provider = _NoWindowProvider()
        req = _req(
            windows=[
                {"start_ms": 0, "end_ms": 1000, "token_lo": 0, "token_hi": 4},
            ]
        )
        with pytest.raises(AlignmentProviderError, match="不支持分窗"):
            provider.align(req, "x.wav", lambda *a: None, lambda: False)

    def test_fake_provider_windowed_decode_respects_ranges(self):
        provider = FakeProvider()
        windows = [
            {"start_ms": 0, "end_ms": 400, "token_lo": 0, "token_hi": 2},
            {"start_ms": 400, "end_ms": 1000, "token_lo": 2, "token_hi": 4},
        ]
        req = _req(windows=windows, fake_duration_ms=1000)
        result = provider.align(req, "x.wav", lambda *a: None, lambda: False)
        by = {s.token_index: s for s in result.spans}
        assert by[0].start_ms == 0
        assert by[1].end_ms == 400
        assert by[2].start_ms == 400
        assert by[3].end_ms == 1000
        assert result.spans[0].start_ms <= result.spans[1].start_ms

    def test_fake_rejects_illegal_windows(self):
        provider = FakeProvider()
        req = AlignmentRequest(
            tokens=_tokens(2),
            options={"fake_duration_ms": 1000},
            # post_init 已拦非法；再直接压非法结构走 align 内校验
            windows=None,
        )
        req.windows = [
            {"start_ms": 0, "end_ms": 5000, "token_lo": 0, "token_hi": 2},
        ]
        with pytest.raises(AlignmentValidationError):
            provider.align(req, "x.wav", lambda *a: None, lambda: False)


class TestWindowedCtcEquivalence:
    """用假 emission 打桩 _ctc_align_groups，验证分窗切片与偏移逻辑。"""

    def _provider(self):
        from strange_uta_game.backend.application.ai_timing.worker.providers import (
            _TorchProviderBase,
        )

        class _Stub(_TorchProviderBase):
            provider_id = "stub_ctc"

            def validate_model(self, model_spec):
                return None

            def load(self, model_spec, progress, cancel):
                return None

            def align(self, request, audio_path, progress, cancel):
                raise NotImplementedError

        return _Stub()

    def test_single_full_window_equals_full_decode(self, monkeypatch):
        provider = self._provider()

        calls = []

        def fake_full(emission, token_groups, blank):
            calls.append(("full", len(token_groups), getattr(emission, "frames", None)))
            return [(i, i + 1) for i in range(len(token_groups))]

        def fake_sub(emission, token_groups, blank):
            calls.append(("sub", list(token_groups), getattr(emission, "frames", None)))
            # 子窗内返回相对帧
            return [(0, 1) for _ in token_groups]

        # 单窗覆盖整曲应直接走整曲路径
        monkeypatch.setattr(provider, "_ctc_align_groups", fake_full)
        windows = [{"start_ms": 0, "end_ms": 1000, "token_lo": 0, "token_hi": 4}]

        class _E:
            frames = 50

            def size(self, dim):
                return self.frames if dim == 1 else 1

            def cpu(self):
                return self

            def __getitem__(self, item):
                return self

        grouped = provider._ctc_align_groups_windowed(
            _E(), [[1], [2], [3], [4]], blank=0, windows=windows, duration_ms=1000
        )
        assert calls and calls[0][0] == "full"
        assert grouped == [(0, 1), (1, 2), (2, 3), (3, 4)]

    def test_two_windows_slice_and_offset(self, monkeypatch):
        provider = self._provider()

        def fake_sub(emission, token_groups, blank):
            # 窗内 token 数 → 相对帧区间
            n = len(token_groups)
            return [(0, n) for _ in range(n)]

        monkeypatch.setattr(provider, "_ctc_align_groups", fake_sub)

        class _E:
            frames = 100

            def size(self, dim):
                return self.frames if dim == 1 else 1

            def cpu(self):
                return self

            def __getitem__(self, item):
                return self

        windows = [
            {"start_ms": 0, "end_ms": 500, "token_lo": 0, "token_hi": 2},
            {"start_ms": 500, "end_ms": 1000, "token_lo": 2, "token_hi": 4},
        ]
        grouped = provider._ctc_align_groups_windowed(
            _E(), [[1], [2], [3], [4]], blank=0, windows=windows, duration_ms=1000
        )
        # 窗0: frames 0..50, tokens (0,2)/(0,2)+0 → (0,2)/(0,2)
        # 窗1: frames 50..100, tokens (0,2)/(0,2)+50 → (50,52)/(50,52)
        assert grouped[0][0] == 0
        assert grouped[1][0] == 0
        assert grouped[2][0] == 50
        assert grouped[3][0] == 50
        assert grouped[2][1] == 52


class TestPlanToContractMapping:
    def test_contract_payload_shape(self):
        w = AlignWindow(start_ms=100, end_ms=200, line_lo=0, line_hi=2)
        assert w.to_contract_payload(0, 3) == {
            "start_ms": 100,
            "end_ms": 200,
            "token_lo": 0,
            "token_hi": 3,
        }

    def test_digest_changes_with_anchors(self):
        a = plan_windows([(0, 1000, None), (1, 8000, None)], 20000)
        b = plan_windows([(0, 1000, None), (1, 9000, None)], 20000)
        assert windows_digest(a) != windows_digest(b)
        assert windows_digest([]) == ""

    def test_no_windows_request_is_none(self):
        assert _req().windows is None

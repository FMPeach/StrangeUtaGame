"""AI 打轴 LRC 软锚点窗口规划单测。

移植 KiraKara ``tests/align/test_calibration_planning.py`` 中
``test_lrc_windows_and_groups`` 等用例的数值期望到毫秒语义：
参考 FrameMap(16000, 320) 即 50 帧/秒，1000 ms = 50 帧
（帧 → 毫秒换算由 worker 侧完成，宿主规划只输出毫秒）。
"""

import hashlib
import json

import pytest

from strange_uta_game.backend.application.ai_timing.planning import (
    LEFT_MARGIN_MS,
    RIGHT_MARGIN_MS,
    AlignWindow,
    plan_windows,
    uncovered_line_indices,
    windows_digest,
)


class TestWindowsAndGroups:
    """分组与窗口边界（对照 test_lrc_windows_and_groups 的数值期望）。"""

    def test_reference_numerics(self):
        """参考用例：starts=[2000,6000,None,12000]，margin=1000，时长 20000。

        参考帧期望 → 毫秒（50 fps，1000 ms = 50 帧）：
        W0 = [50,350) 帧 = [1000,7000) ms；W1 = [250,650) 帧 = [5000,13000) ms；
        W2 end = 1000 帧 = 20000 ms（末组用音频终点）。
        """
        lines = [
            (0, 2000, None),
            (1, 6000, None),
            (2, None, None),  # 无锚点行并入前一组（line 1）
            (3, 12000, None),
        ]
        windows = plan_windows(
            lines, 20000, left_margin_ms=1000, right_margin_ms=1000
        )
        # 分组：[line0], [line1+line2], [line3]
        assert [(w.line_lo, w.line_hi) for w in windows] == [(0, 1), (1, 3), (3, 4)]
        assert (windows[0].start_ms, windows[0].end_ms) == (1000, 7000)  # 50..350 帧
        assert (windows[1].start_ms, windows[1].end_ms) == (5000, 13000)  # 250..650 帧
        assert (windows[2].start_ms, windows[2].end_ms) == (11000, 20000)  # end=1000 帧

    def test_default_margins_match_decode_config(self):
        """默认边距 1500 对照 KiraKara DecodeConfig（models.py:355）。"""
        assert LEFT_MARGIN_MS == 1500 and RIGHT_MARGIN_MS == 1500
        windows = plan_windows([(0, 2000, None), (1, 6000, None)], 20000)
        assert (windows[0].start_ms, windows[0].end_ms) == (500, 7500)
        assert (windows[1].start_ms, windows[1].end_ms) == (4500, 20000)


class TestUnanchoredLines:
    """无锚点行的并入规则。"""

    def test_leading_unanchored_joins_first_group(self):
        """开头无锚点行并入第一个有锚点组（不单独成组）。"""
        lines = [
            (0, None, None),
            (1, None, None),
            (2, 5000, None),
            (3, 9000, None),
        ]
        windows = plan_windows(lines, 20000)
        assert [(w.line_lo, w.line_hi) for w in windows] == [(0, 3), (3, 4)]

    def test_leading_unanchored_window_starts_at_audio_zero(self):
        """首个有时间行之前的无锚点行：lo 从音频起点 0 开始，不是锚点-margin。"""
        lines = [(0, None, None), (1, 5000, None)]
        windows = plan_windows(lines, 20000)  # 默认 margin 1500
        # 若不用 0 而用 5000-1500=3500 就错了：这些行可能在锚点之前很久开唱
        assert windows[0].start_ms == 0
        assert windows[0].end_ms == 20000  # 末组用音频终点

    def test_trailing_unanchored_joins_last_group(self):
        """末尾无锚点行并入最后一个有锚点组。"""
        lines = [(0, 2000, None), (1, None, None), (2, None, None)]
        windows = plan_windows(lines, 20000)
        assert [(w.line_lo, w.line_hi) for w in windows] == [(0, 3)]
        assert (windows[0].start_ms, windows[0].end_ms) == (500, 20000)

    def test_all_unanchored_degenerates_to_empty(self):
        """全工程无锚点 → []（退化为整曲解码路径，零回归）。"""
        assert plan_windows([(0, None, None), (1, None, None)], 20000) == []
        assert plan_windows([], 20000) == []


class TestGuards:
    """边界确定性守卫。"""

    def test_audio_duration_nonpositive_returns_empty(self):
        assert plan_windows([(0, 2000, None)], 0) == []
        assert plan_windows([(0, 2000, None)], -5) == []

    def test_non_increasing_line_index_raises(self):
        with pytest.raises(ValueError, match="严格递增"):
            plan_windows([(1, 2000, None), (0, 6000, None)], 20000)


class TestExclude:
    """锚点超出音频的行：标记 exclude，整行不参与。"""

    def test_anchor_beyond_audio_excluded(self):
        """对照 validate_anchors：ms >= 时长视为超出，行不出现在任何窗口。"""
        lines = [(0, 2000, None), (1, 99999, None), (2, 6000, None)]
        windows = plan_windows(
            lines, 20000, left_margin_ms=1000, right_margin_ms=1000
        )
        # line 1 不参与；其余行按剩余序列分组（0 与 2 各成一组）
        assert [(w.line_lo, w.line_hi) for w in windows] == [(0, 1), (2, 3)]
        assert all(not (w.line_lo <= 1 < w.line_hi) for w in windows)

    def test_negative_anchor_excluded(self):
        """ms < 0 同样超出音频（对照 validate_anchors 的 anchor_negative）。"""
        windows = plan_windows([(0, -100, None), (1, 2000, None)], 20000)
        assert [(w.line_lo, w.line_hi) for w in windows] == [(1, 2)]

    def test_excluded_line_splits_window_line_range(self):
        """组跨过 exclude 行时按空洞拆段：时间相同，行区间不吞 exclude 行。"""
        lines = [(0, 2000, None), (1, 99999, None), (2, None, None)]
        windows = plan_windows(lines, 20000)  # line 2 无锚点 → 并入 line 0 的组
        # 组成员为 line 0、line 2（line 1 已 exclude）→ 拆成两段，line 1 不参与
        assert [(w.line_lo, w.line_hi) for w in windows] == [(0, 1), (2, 3)]
        assert windows[0].start_ms == windows[1].start_ms == 500
        assert windows[0].end_ms == windows[1].end_ms == 20000

    def test_all_anchors_excluded_returns_empty(self):
        assert plan_windows([(0, 99999, None)], 20000) == []


class TestOutOfOrderAnchors:
    """乱序锚点降级：锚点须严格大于此前最近保留的锚点，否则降级合并。"""

    def test_later_anchor_demoted_and_merged(self):
        """锚点_i >= 锚点_{i+1} → 后者降级为无锚点并入前一组。"""
        lines = [(0, 5000, None), (1, 3000, None), (2, 7000, None)]
        windows = plan_windows(
            lines, 20000, left_margin_ms=1000, right_margin_ms=1000
        )
        # line 1 的 3000 < 5000 → 降级，并入 line 0 的组
        assert [(w.line_lo, w.line_hi) for w in windows] == [(0, 2), (2, 3)]
        assert (windows[0].start_ms, windows[0].end_ms) == (4000, 8000)

    def test_equal_anchors_demoted(self):
        """相等锚点（锚点_i >= 锚点_{i+1}）同样触发降级（比 KiraKara 更严）。"""
        lines = [(0, 5000, None), (1, 5000, None), (2, 6000, None)]
        windows = plan_windows(
            lines, 20000, left_margin_ms=1000, right_margin_ms=1000
        )
        assert [(w.line_lo, w.line_hi) for w in windows] == [(0, 2), (2, 3)]

    def test_cascading_demotion_against_last_kept(self):
        """级联：降级对照的是最近**保留**的锚点，后续更小锚点一并降级。"""
        lines = [(0, 5000, None), (1, 3000, None), (2, 4000, None)]
        windows = plan_windows(
            lines, 20000, left_margin_ms=1000, right_margin_ms=1000
        )
        # 4000 也不大于保留的 5000 → 全部并入唯一组
        assert [(w.line_lo, w.line_hi) for w in windows] == [(0, 3)]
        assert (windows[0].start_ms, windows[0].end_ms) == (4000, 20000)


class TestContractInvariants:
    """契约不变式：升序、行区间完整分区、裁剪到音频内。"""

    def _mixed_lines(self):
        return [
            (0, None, None),  # 开头无锚点 → 并入 line 1 的组
            (1, 2000, None),
            (2, None, None),
            (3, 6000, None),
            (4, 99999, None),  # 超出音频 → exclude
            (5, 12000, None),
            (6, None, None),
        ]

    def test_order_coverage_and_clip(self):
        lines = self._mixed_lines()
        windows = plan_windows(
            lines, 20000, left_margin_ms=1000, right_margin_ms=1000
        )
        # start_ms 升序（非降）
        starts = [w.start_ms for w in windows]
        assert starts == sorted(starts)
        # 行区间对非 exclude 行构成连续完整分区：首尾相接、无空洞、无重叠
        covered = []
        for w in windows:
            covered.extend(range(w.line_lo, w.line_hi))
        assert covered == sorted(covered)
        assert len(set(covered)) == len(covered)
        assert set(covered) == {0, 1, 2, 3, 5, 6}  # exclude 的 line 4 不参与
        # 裁剪：0 <= start <= end <= 音频时长（end 开区间）
        for w in windows:
            assert 0 <= w.start_ms <= w.end_ms <= 20000

    def test_adjacent_windows_overlap_by_left_plus_right(self):
        """修订后契约（钉住 plan_lrc 数值语义）：相邻组窗口在音频时间上
        必然重叠 left+right 毫秒——W_i.end = a_{i+1}+right，
        W_{i+1}.start = a_{i+1}-left。「互不重叠」只约束 token 半开区间
        （连续完整分区），音频时间重叠是搜索边界/上下文边距，不是错误。
        """
        windows = plan_windows(
            [(0, 2000, None), (1, 6000, None), (2, 12000, None)],
            20000,
            left_margin_ms=1000,
            right_margin_ms=1000,
        )
        assert (windows[0].start_ms, windows[0].end_ms) == (1000, 7000)
        assert (windows[1].start_ms, windows[1].end_ms) == (5000, 13000)
        assert windows[0].end_ms - windows[1].start_ms == 2000 == 1000 + 1000


class TestPayloadAndDigest:
    """契约 dict 形状与缓存摘要。"""

    def test_to_payload_excludes_token_range(self):
        window = AlignWindow(1000, 7000, 0, 1)
        assert window.to_payload() == {
            "start_ms": 1000,
            "end_ms": 7000,
            "line_lo": 0,
            "line_hi": 1,
        }
        # token 区间由执行链阶段补齐，宿主侧 payload 不含 token 键
        assert not any(k.startswith("token") for k in window.to_payload())

    def test_digest_is_stable_sha256_of_payloads(self):
        lines = [(0, 2000, None), (1, 6000, None)]
        windows = plan_windows(lines, 20000)
        digest = windows_digest(windows)
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
        # 确定性：同输入同摘要
        assert digest == windows_digest(plan_windows(lines, 20000))
        # 摘要 = 契约 dict 紧凑 JSON 的 sha256
        raw = json.dumps(
            [w.to_payload() for w in windows],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        assert digest == hashlib.sha256(raw.encode("utf-8")).hexdigest()
        # 内容变化 → 摘要变化
        assert digest != windows_digest(
            plan_windows([(0, 2500, None), (1, 6000, None)], 20000)
        )

    def test_digest_empty_windows_is_blank_for_cache_key(self):
        """空列表 → 空串：拼接到既有缓存键不改变字节（契约不变式 4）。"""
        assert windows_digest([]) == ""
        assert windows_digest(plan_windows([(0, None, None)], 20000)) == ""


class TestUncoveredLineIndices:
    """exclude 行探测：锚点越界的行不产生窗口，但 token 仍在请求里。

    宿主侧（service）探测到未覆盖行必须退化整曲，不得硬走分区映射
    （PR #130 评审 P1-2：越界锚点导致 _windows_payload 断言崩溃）。
    """

    def test_middle_out_of_range_anchor_leaves_hole(self):
        wins = plan_windows(
            [(0, 1000, None), (1, 90000, None), (2, 5000, None)], 20000
        )
        assert uncovered_line_indices(wins, [0, 1, 2]) == [1]
        # 无 token 的行号不在探测范围（探测的是「有 token 的行」）
        assert uncovered_line_indices(wins, [0, 2]) == []

    def test_leading_out_of_range_anchor(self):
        wins = plan_windows([(0, 25000, None), (1, 8000, None)], 20000)
        assert uncovered_line_indices(wins, [0, 1]) == [0]

    def test_trailing_out_of_range_anchor(self):
        wins = plan_windows([(0, 1000, None), (1, 25000, None)], 20000)
        assert uncovered_line_indices(wins, [0, 1]) == [1]

    def test_complete_coverage_reports_nothing(self):
        wins = plan_windows([(0, 1000, None), (1, 8000, None)], 20000)
        assert uncovered_line_indices(wins, [0, 1]) == []
        # 无锚点行由前一组窗口覆盖
        wins = plan_windows(
            [(0, 1000, None), (1, None, None), (2, 8000, None)], 20000
        )
        assert uncovered_line_indices(wins, [0, 1, 2]) == []

    def test_no_windows_means_all_uncovered(self):
        assert uncovered_line_indices([], [0, 3]) == [0, 3]

    def test_result_is_sorted_and_deduplicated(self):
        wins = plan_windows([(0, 1000, None), (1, 90000, None)], 20000)
        assert uncovered_line_indices(wins, [1, 1, 0]) == [1]

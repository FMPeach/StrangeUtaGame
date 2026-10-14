"""AI 打轴 LRC 软锚点窗口规划（宿主侧，移植自 MiliKara 的 plan_lrc）。

第三方归属：分组与窗口构建语义移植自 MiliKara（MIT License，
Copyright (c) 2026 BilyHurington，https://github.com/BilyHurington/MiliKara ，
``kara_align/align/planning.py`` 的 ``plan_lrc``）。MIT 许可要求在软件的
实质部分保留版权与许可声明，完整声明见仓库根目录 THIRD_PARTY_NOTICES.md。

数据流（阶段 C 之前）：

    逐行锚点（LRC 导入 / 先前打轴的最早时间戳）
        ↓ plan_windows（按锚点分组 → 组窗口 → 裁剪）
    List[AlignWindow]（毫秒，行区间半开）
        ↓ 执行链：按行区间映射 token 区间，写入 AlignmentRequest.windows
    worker 按窗口帧区间解码（帧换算在 worker 侧完成，宿主不感知帧）

参考实现（只读）：MiliKara ``kara_align/align/planning.py`` 的 ``plan_lrc``
（第 134 行起）；参考测试：``tests/align/test_calibration_planning.py`` 的
``test_lrc_windows_and_groups``。本模块只做纯逻辑移植：不加载模型、不读
音频、不触碰 Project 写路径。

锚点语义：行锚点 = 该行现有最早时间戳（``start_ms``）；为 ``None`` 表示该
行无时间戳。全工程无任何锚点 → 返回空列表（退化为整曲解码路径，零回归）。

分组与窗口（逐条对照 plan_lrc）：

- 有锚点的行开新组，其后的无锚点行并入前一组；
- 开头的无锚点行并入第一个有锚点组；
- 组窗口 lo = 首锚点 - left_margin；hi = 下一组首锚点 + right_margin
  （末组用音频终点）；
- 首个有时间行之前的无锚点行（并入后组首行无锚点），窗口 lo 从音频起点
  0 开始（它们可能在首个锚点之前很久就开唱，不能用 first_anchor - margin）；
- 窗口裁剪到 [0, audio_duration_ms]；锚点超出音频的行标记 exclude
  （对照 MiliKara ``validate_anchors``：``kara_align/align/calibration.py``
  第 234-240 行——ms < 0 或 ms >= 时长），整行不参与（既非目标也非上下文）；
- 无任何锚点 → 返回 []（退化现状）。

边界确定性（本移植新增，plan_lrc 依赖上游校验保证）：

- 锚点乱序：锚点必须**严格大于**此前最近保留的锚点；若 锚点_i >= 锚点_{i+1}
  （含相等），则后者降级为无锚点并入前一组（保留先出现的锚点，级联生效）。
  注：MiliKara ``validate_anchors`` 仅对 ms < 上一行报错（相等不报），本移植
  按契约取更严的 ``>=`` 口径；
- ``audio_duration_ms <= 0`` → 返回 []。

窗口重叠语义（搜索边界 vs 提交边界）：plan_lrc 的组窗口 hi 延伸到下一组
首锚点之后 right_margin，而下一组 lo 从该锚点之前 left_margin 开始——相邻
窗口在音频时间上**必然**重叠 left+right 毫秒（参考测试数值 W0=[1000,7000)
与 W1=[5000,13000) 即如此）。本模块保留该参考语义。原版中重叠区是解码的
**搜索边距**（MiliKara 每窗解码 participating 行、只提交 retained 行，跨窗
context 行的 span 被丢弃）；本移植不移植 context/retained 双集合，改由
worker 侧按「提交范围」仲裁（``alignment.window_commit_ranges``：相邻窗
重叠区中点 = 默认对称边距下的下一锚点），各窗 span 裁到提交范围以内再
拼接，保证全局时间轴单调。跨代理契约不变式：

- 「按 start_ms 升序」约束窗口顺序；
- 「互不重叠」只约束 token 半开区间（不变式 2：对 [0, N) 的连续完整分区）；
- 音频时间范围允许 left/right margin 重叠（搜索边界语义），worker 在提交
  前把 span 裁到相邻窗重叠区中点以内。

test_adjacent_windows_overlap_by_left_plus_right 显式钉住该数值语义，
避免回退成错误的「强制裁剪不重叠」实现。

exclude 行与 token 分区：锚点越界被 exclude 的行不产生窗口，但其 token
仍在 AlignmentRequest 里；执行链用 ``uncovered_line_indices`` 探测到未覆盖
行时**必须安全退化为整曲对齐**（锚点数据不可信，不再分窗），不得直接走
token 分区映射（会因覆盖不完整而失败）。

v1 范围（刻意不做，对照 plan_lrc 逐项）：

- 不做 context/retained 双集合：严格按组分区，每组恰好一个窗口
  （plan_lrc 的 ``joint_context_lines`` / ``extra_context`` / ``force_joint``
  语义未移植，v1 等价于 joint_context_lines=0；跨窗单调性由提交范围仲裁
  保证，见上）；
- 不做 interlude end-marker：``imported_end_ms`` / ``end_marker_margin_ms``
  截断与 free_tail 留待能量跳段阶段；输入行的 ``end_ms`` 字段 v1 仅占位接
  收、不参与计算；
- 组间不合并：相邻有锚点组永不合并（与 plan_lrc 分组逻辑一致），仅开头无
  锚点行并入首个有锚点组；
- 不区分 soft/hard 锚点与 tolerance（SUG 行锚点为单一时间戳，plan_lrc 的
  hard anchor 窗口扩张未移植）；
- 不按 voice 分流（plan_lrc 按 ``Line.voice`` 分组；SUG 输入契约是单一流，
  多声部由调用方自行拆分传入）。

默认边距 1500 ms 对照 MiliKara ``DecodeConfig``（``kara_align/models.py``
第 355-357 行：``left_margin_ms = right_margin_ms = 1500``）。
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

LEFT_MARGIN_MS = 1500
"""组窗口左扩边距默认值（对照 DecodeConfig.left_margin_ms，来源见模块 docstring）。"""

RIGHT_MARGIN_MS = 1500
"""组窗口右扩边距默认值（对照 DecodeConfig.right_margin_ms，来源见模块 docstring）。"""

LineInput = Tuple[int, Optional[int], Optional[int]]
"""输入行：(line_idx, start_ms|None, end_ms|None)。

start_ms 即行锚点（该行现有最早时间戳）；end_ms 为行结束标记占位，
v1 不参与窗口计算（interlude end-marker 留待能量跳段阶段）。
"""


# ──────────────────────────────────────────────
# 数据模型
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class AlignWindow:
    """一个解码窗口（毫秒，end 开区间；行区间半开 [line_lo, line_hi)）。

    Attributes:
        start_ms: 窗口起点（>= 0，含）。
        end_ms: 窗口终点（<= 音频时长，不含）。
        line_lo: 覆盖的首行索引（含）。
        line_hi: 覆盖的末行索引之后（不含）——区间内不含任何 exclude 行。
    """

    start_ms: int
    end_ms: int
    line_lo: int
    line_hi: int

    def to_payload(self) -> Dict[str, int]:
        """行区间 payload（供 digest / 调试；不含 token 区间）。

        执行链用 :meth:`to_contract_payload` 产出 AlignmentRequest.windows
        的契约 dict（补 token_lo / token_hi）。
        """
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "line_lo": self.line_lo,
            "line_hi": self.line_hi,
        }

    def to_contract_payload(self, token_lo: int, token_hi: int) -> Dict[str, int]:
        """契约 window dict：``{start_ms, end_ms, token_lo, token_hi}``。

        token 区间为半开；调用方（service）保证全部窗口的 token 区间构成
        ``[0, len(tokens))`` 连续完整分区。
        """
        return {
            "start_ms": int(self.start_ms),
            "end_ms": int(self.end_ms),
            "token_lo": int(token_lo),
            "token_hi": int(token_hi),
        }


# ──────────────────────────────────────────────
# 窗口规划
# ──────────────────────────────────────────────


def plan_windows(
    lines: Sequence[LineInput],
    audio_duration_ms: int,
    left_margin_ms: int = LEFT_MARGIN_MS,
    right_margin_ms: int = RIGHT_MARGIN_MS,
) -> List[AlignWindow]:
    """按行锚点规划解码窗口（plan_lrc 的毫秒语义移植，v1 严格按组分区）。

    Args:
        lines: 逐行 (line_idx, start_ms|None, end_ms|None)，须按文档顺序
            行索引严格递增（违反抛 ValueError）。start_ms 为行锚点。
        audio_duration_ms: 音频时长（毫秒）；<= 0 时无从规划，返回 []。
        left_margin_ms: 组窗口左扩（默认对照 DecodeConfig，见模块 docstring）。
        right_margin_ms: 组窗口右扩（同上）。

    Returns:
        按 start_ms 非降序排列的窗口列表；行区间对非 exclude 行构成连续
        完整分区（首尾相接、无空洞、无重叠）。无任何锚点时为 []。

    Raises:
        ValueError: 行索引未严格递增。
    """
    if audio_duration_ms <= 0 or not lines:
        return []

    # 1) 过滤 exclude + 乱序锚点降级（保持输入顺序）
    entries: List[Tuple[int, Optional[int]]] = []
    prev_idx: Optional[int] = None
    last_anchor: Optional[int] = None
    for line_idx, start_ms, _end_ms in lines:
        if prev_idx is not None and line_idx <= prev_idx:
            raise ValueError(
                f"行索引必须严格递增：{line_idx} 不晚于前一行 {prev_idx}"
            )
        prev_idx = line_idx
        # exclude：锚点超出音频 [0, 时长) 的行整行不参与（对照 validate_anchors）
        if start_ms is not None and not (0 <= start_ms < audio_duration_ms):
            continue
        # 乱序降级：不严格大于此前最近保留的锚点（含相等）→ 无锚点，合并
        if start_ms is not None and last_anchor is not None and start_ms <= last_anchor:
            start_ms = None
        if start_ms is not None:
            last_anchor = start_ms
        entries.append((line_idx, start_ms))

    # 2) 分组：有锚点的行开新组，无锚点行并入前一组
    groups: List[List[Tuple[int, Optional[int]]]] = []
    for entry in entries:
        if entry[1] is not None or not groups:
            groups.append([entry])
        else:
            groups[-1].append(entry)
    if not groups:
        return []
    # 开头的无锚点行并入第一个有锚点组
    if len(groups) > 1 and groups[0][0][1] is None:
        groups[1] = groups[0] + groups[1]
        groups.pop(0)
    if not any(start is not None for group in groups for _, start in group):
        return []  # 全工程无锚点：退化为整曲解码路径

    # 3) 组窗口：lo = 首锚点 - left；hi = 下一组首锚点 + right（末组=音频终点）
    def group_anchor(group: List[Tuple[int, Optional[int]]]) -> Optional[int]:
        for _, start in group:
            if start is not None:
                return start
        return None

    windows: List[AlignWindow] = []
    for gi, group in enumerate(groups):
        if gi + 1 < len(groups):
            nxt = group_anchor(groups[gi + 1])
            hi_ms = nxt + right_margin_ms if nxt is not None else audio_duration_ms
        else:
            hi_ms = audio_duration_ms
        if group[0][1] is None:
            # 首个有时间行之前的无锚点行：nothing bounds them but the audio start
            lo_ms = 0
        else:
            lo_ms = group[0][1] - left_margin_ms
        lo_ms = max(0, lo_ms)
        hi_ms = min(audio_duration_ms, max(hi_ms, lo_ms))
        # 行区间按 exclude / 输入空洞拆分：同一组窗口可拆成多段（时间相同），
        # 保证每个 [line_lo, line_hi) 恰好覆盖连续的参与行
        run_lo = group[0][0]
        run_prev = group[0][0]
        for idx, _ in group[1:]:
            if idx != run_prev + 1:
                windows.append(AlignWindow(lo_ms, hi_ms, run_lo, run_prev + 1))
                run_lo = idx
            run_prev = idx
        windows.append(AlignWindow(lo_ms, hi_ms, run_lo, run_prev + 1))
    return windows


# ──────────────────────────────────────────────
# 缓存摘要
# ──────────────────────────────────────────────


def uncovered_line_indices(
    windows: Sequence[AlignWindow],
    line_indices: Iterable[int],
) -> List[int]:
    """有 token 的行中未被任何窗口行区间覆盖的行号（升序去重）。

    规划器会把锚点越界（``ms < 0`` 或 ``>= 音频时长``）的行整行 exclude，
    这些行的 token 仍会留在对齐请求里；若不检查，执行链的 token 分区
    映射会因行覆盖不完整而断言失败。宿主侧用本函数探测后应安全退化
    为整曲对齐（锚点数据不可信时不再分窗），而不是崩溃。
    """
    covered: set = set()
    for w in windows:
        covered.update(range(w.line_lo, w.line_hi))
    return sorted({int(i) for i in line_indices if int(i) not in covered})


def windows_digest(windows: Sequence[AlignWindow]) -> str:
    """窗口列表的内容摘要（缓存键组成部分）。

    对契约 dict（:meth:`AlignWindow.to_payload`）的紧凑 JSON 序列化做
    sha256，输出十六进制。空列表返回空串——调用方直接拼接到既有缓存键
    不会改变字节（契约不变式 4：windows 缺省/为空 → 缓存键不变）。
    """
    if not windows:
        return ""
    payload = [window.to_payload() for window in windows]
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = [
    "LEFT_MARGIN_MS",
    "RIGHT_MARGIN_MS",
    "LineInput",
    "AlignWindow",
    "plan_windows",
    "uncovered_line_indices",
    "windows_digest",
]

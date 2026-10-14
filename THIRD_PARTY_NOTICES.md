# 第三方归属声明

本仓库在以下位置使用了第三方项目的代码与设计。各项目原始许可文本
以其仓库内的 LICENSE 文件为准。

## MiliKara（MIT License）

- 项目：<https://github.com/BilyHurington/MiliKara>
- 许可：MIT License — Copyright (c) 2026 BilyHurington
- 使用位置：`src/strange_uta_game/backend/application/ai_timing/planning.py`
- 说明：LRC 锚点分窗规划的分组与窗口构建语义移植自该项目的
  `kara_align/align/planning.py`（`plan_lrc`）与
  `kara_align/align/calibration.py`（`validate_anchors` 的越界剔除口径）、
  `kara_align/models.py`（`DecodeConfig` 的默认边距 1500 ms）。
  本仓库为语义移植而非逐字复制，未移植其 participating/retained
  context 机制（跨窗单调性由本仓库的提交范围仲裁保证）。
- MIT 许可要求在软件的实质部分保留上述版权与许可声明。

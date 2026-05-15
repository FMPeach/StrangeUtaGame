```md
# StrangeUtaGame 实装“自动更新（EXE + _internal）”方案记录（基于 March7thAssistant 调研）

## 1. 已调研到的 March7thAssistant 更新方式（信息点）
### 1.1 触发入口
- 有独立更新器：`March7th Updater.exe`
- 入口在：
  - 图形界面“检测更新”按钮
  - 或直接双击 `March7th Updater.exe`
- 配置中也支持 `auto_update`（可在满足条件时自动触发）

### 1.2 更新配置能力（对齐我们需要的“可扩展更新策略”）
从其示例配置（`config.example.yaml`）可抽象出关键能力：
- `check_update`: 是否检查更新
- `auto_update`: 是否自动更新
- `update_source`: 更新源，可选 `GitHub`（海外）或 `MirrorChyan`（国内加速）
- `update_prerelease_enable`: 是否走预览渠道
- `update_full_enable`: 是否下载完整包（全量）
- 还有更新相关的定时/条件字段（例如“指定运行时间”的问题出过）

### 1.3 网络环境适配（对我们最重要的经验）
- 明确提供“更新源切换”（GitHub / MirrorChyan），用来对冲“用户网络不可达”的问题
- 因此我们需要“主源 + 备源”的读取/降级策略，而不是只依赖 GitHub

---

## 2. 我们项目现状（基于本次代码扫过的信息）
### 2.1 打包形态
- 使用 PyInstaller 的 `--onedir` 打包（`build.py` 里明确 `--onedir`）
- 运行时用户可能通过 `.config_redirect` 把 `config.json/dictionary.json/singers.json` 重定向到用户目录
- 主程序是 PyQt6 桌面应用：`main.py` / `MainWindow`

### 2.2 当前“在线更新”缺失
- 当前代码里看到的是：
  - “词典版本升级”这种离线覆盖（对 `dictionary_version` vs `applied_dictionary_version`）
  - 没有看到“程序自身版本检测/下载/替换重启”的统一更新器实现
- 也没有看到“update_source/auto_update/check_update”这类更新策略字段或 UI

---

## 3. 目标范围（按你的确认）
- 只做：程序自身自动更新
- 更新内容：`StrangeUtaGame.exe` + 打包产物内部的 `_internal`（或你们实际的内部目录名）
- 不更新：用户配置目录下的 `config.json/dictionary.json/singers.json`（避免覆盖用户数据）

---

## 4. 总体方案（架构级）
### 4.1 增加：更新器 Updater 独立进程
原因：
- Windows 上“正在运行的进程”会锁住自身 EXE/目录文件，主进程内边下载边覆盖容易失败
- PyInstaller onedir 也要求更谨慎的替换流程

职责切分：
- 主程序（StrangeUtaGame）：
  - 负责按策略“检查是否有新版本”
  - 在需要更新时：启动 `Updater.exe` 并触发退出/等待（MVP 建议：检测到更新后提示并退出，让 updater 接管）
- `Updater.exe`：
  - 拉取 manifest（最新版本清单）
  - 下载更新包
  - 校验（sha256 / 可选签名）
  - 覆盖 EXE + `_internal`
  - 启动新版本并退出自身

### 4.2 增加：manifest（小文件、便于弱网）
manifest 建议字段（示例）：
- `version`: 最新版本号（semver 或发布 tag）
- `channel`: stable / prerelease（可选）
- `sha256`: 更新包校验和
- `download_url`: 更新包地址
- `size`: 可选，便于进度/校验失败定位
- `changelog`: 可选（用于 UI 显示）

### 4.3 增加：双源网络策略（解决“用户不一定能访问 GitHub”）
更新器或主程序读取策略建议：
- 优先请求主源（GitHub）
- 超时/失败立刻降级到备源（国内镜像/自建分发）
- manifest 级别失败就“跳过更新”，不影响主功能启动

实现方式（你后续可决定）：
- 在配置里支持 `update_source` 或维护 `primary_url + backup_url`
- 或支持“数组列表：主->备->再失败则放弃”

---

## 5. 替换策略（只覆盖 EXE + _internal）
Updater 覆盖规则（MVP）：
- 只覆盖：
  - 主程序根目录下的 `StrangeUtaGame.exe`
  - `_internal/**`（或实际目录名）
- 不覆盖/不动：
  - 用户目录下的 `config.json/dictionary.json/singers.json`
  - 不覆盖任何会受 `.config_redirect` 指向影响的文件

Updater 需要在运行时能定位：
- 主程序所在目录（建议通过 updater 启动参数传入 `--app-dir`，避免猜测）
- `_internal` 实际目录名（如果你们不是 `_internal`，需替换为真实名称）

---

## 6. 主程序里需要接入的点（不涉及具体代码改动，只列落点）
### 6.1 版本号可读性
需要一个“当前程序版本号”的来源，供主程序与 updater 比较：
- MVP 可用“构建产物自带版本文件”（例如 `app_version.txt`）或从打包时注入的 `version.json`
- 关键是：必须是更新器可读、可稳定读取

> 现状：我们项目目前主要在 UI 展示里有“版本文案”，但不确定是否有“可用于更新判断的结构化版本字段”。

### 6.2 设置 UI / 配置项（参考 March7thAssistant）
建议最小配置项：
- `check_update`: 启动时/手动检查开关
- `auto_update`: 是否自动触发
- `update_source`: 主源/备源（至少二选一）
- `update_prerelease_enable`: 预览渠道（可选，MVP 可先不做）

落点：
- `SettingsInterface` 的“关于”区域或新增“更新”分组按钮/开关

### 6.3 启动时策略
- 在主程序启动完成后做轻量检查（弱网失败不阻塞）
- 若检测到更新：
  - MVP 建议：提示用户并退出，交由 updater 完成替换
  - 若强制“无感自动”（不建议 MVP）：需要更复杂的进程退出/锁处理策略

---

## 7. 弱网/失败处理策略（必须明确）
- manifest 拉取失败：跳过更新（不弹窗或仅后台记录）
- 下载失败/超时：不替换，保持旧版本可用
- sha256 不匹配：不替换，保持旧版本可用
- 替换成功但启动失败：回滚（可选）或提示用户手动处理（MVP 可先不做回滚）

---

## 8. 仍待你和其他 MoI 成员对齐的细节（我需要你们确认）
1. `_internal` 的真实目录名：是 `_internal` 还是 PyInstaller 生成的其他内部数据目录名？
2. 更新包的生成与内容规范：
   - 是否打成单 zip（含 EXE+internal），还是直接发布目录结构？
3. 用户目录数据隔离是否“硬规则”：
   - 除了 config/dictionary/singers，是否还有别的用户可写目录（比如缓存、日志）需要排除？
4. 触发策略偏好：
   - MVP：检测到更新后“提示并退出”
   - 还是你们要“无提示强制替换”（这会显著增加锁处理难度）

（确认上述后，就能把方案具体到：Updater 参数、覆盖路径列表、manifest 字段、构建/发布产物格式。）
```
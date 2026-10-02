# -*- coding: utf-8 -*-
"""翻译资源流水线：扫描 .ts → 恢复误标 vanished 的活条目 → 编译 .qm。

用法（仓库根目录）::

    python scripts/update_translations.py

背景（为什么不能只跑 pylupdate6）：

- pylupdate6 只能静态识别 ``self.tr("字面量")`` 这类直呼形态；
- 项目里相当一部分 UI 字符串走「别名 + 枚举」（``_tr = self.tr`` 后
  ``_tr("...")``，见按类型删除/补全时间戳对话框）或「字典/列表 +
  动态 ``tr(变量)``」分发，扫描器看不见 → 会被整批误标
  ``type="vanished"``，而 lrelease 不编译 vanished 条目 → 运行时丢翻译；
- 本脚本在 pylupdate6 扫描后，按「该 source 字符串仍以 tr 族调用字面量
  形态存在于 frontend 源码」的判据，把这些条目恢复为 active 并回填
  location（文件:行号取自源码扫描），再统一跑 lrelease。

真正已从源码消失的字符串保持 vanished（lrelease 自然忽略），这是正确
的清理语义——下次确认无回滚需求后可手动删除或加 ``--prune`` 处理。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_GLOB = "src/strange_uta_game/frontend/**/*.py"
TS_DIR = ROOT / "src/strange_uta_game/frontend/localization/translations"
TS_FILES = ["app.zh_CN.ts", "app.en_US.ts", "app.ja_JP.ts"]

# tr 族调用：self.tr(...) / 局部别名 _tr(...) / 裸 tr(...)，
# 参数为单/双引号字符串字面量（拼接串的每个片段各自计一条）
_TR_LITERAL_RE = re.compile(
    r"(?:self\.tr|(?<![\w.])_tr|(?<![\w.])tr)\(\s*"
    r"(\"|')((?:\\.|(?!\1).)*?)\1"
)


def _frontend_py_files() -> list:
    return sorted((ROOT / "src/strange_uta_game/frontend").rglob("*.py"))


def _collect_live_literals() -> dict:
    """扫描 frontend 源码，返回 {字符串: [(相对路径, 行号), ...]}。"""
    live: dict = {}
    for path in _frontend_py_files():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in _TR_LITERAL_RE.finditer(line):
                lit = m.group(2)
                if lit:
                    live.setdefault(lit, []).append((rel, lineno))
    return live


def _collect_alive_contexts() -> set:
    """源码中仍存活的翻译上下文名：class 定义名 + translate("Ctx") 字面量。"""
    ctxs: set = set()
    for path in _frontend_py_files():
        text = path.read_text(encoding="utf-8")
        ctxs.update(re.findall(r"^class\s+(\w+)", text, re.M))
        ctxs.update(re.findall(r"""translate\(\s*["']([^"']+)["']""", text))
    return ctxs


def _run(cmd: list) -> None:
    print("  $", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"命令失败: {cmd}\n{proc.stdout}\n{proc.stderr}")


def _find_tool(*names: str) -> str:
    for name in names:
        if shutil.which(name):
            return name
    sys.exit(f"未找到工具（尝试过: {', '.join(names)}），请先安装 PySide6/PyQt6 工具链")


def restore_live_entries(ts_path: Path, live: dict, alive_ctx: set) -> tuple[int, int]:
    """把「仍存在于源码」的 vanished 条目恢复为 active 并回填 location。

    上下文级判据：source 字符串仍以 tr 族字面量存活，**且**条目所属
    上下文在源码中存活（class 定义或 translate("Ctx") 引用）——避免把
    死上下文（改过名/删掉的类）里的同名条目误救回来。
    返回 (恢复数, 剩余 vanished 数)。文本级手术修改，保持 pylupdate6 的
    序列化格式不被重排。
    """
    text = ts_path.read_text(encoding="utf-8")

    # 上下文名按 <context><name> 分段预取
    ctx_spans: list = []
    for m in re.finditer(r"<context>\s*<name>(.*?)</name>(.*?)</context>", text, re.S):
        ctx_spans.append((m.start(2), m.end(2), m.group(1)))

    def _ctx_at(pos: int) -> str:
        for start, end, name in ctx_spans:
            if start <= pos < end:
                return name
        return ""

    restored = 0
    remaining = 0

    def _fix(m: re.Match) -> str:
        nonlocal restored, remaining
        block = m.group(0)
        if ' type="vanished"' not in block:
            return block  # active 条目，不动也不计数
        src = re.search(r"<source>(.*?)</source>", block, re.S)
        lit = src.group(1) if src else None
        if lit in live and _ctx_at(m.start()) in alive_ctx:
            restored += 1
            block = block.replace(' type="vanished"', "", 1)
            if "<location" not in block:
                locs = "".join(
                    f'\n        <location filename="{f}" line="{n}" />'
                    for f, n in live[lit]
                )
                block = block.replace("<message>", "<message>" + locs, 1)
        else:
            remaining += 1
        return block

    text = re.sub(
        r"<message>(?:(?!</message>).)*?</message>", _fix, text, flags=re.S
    )
    ts_path.write_text(text, encoding="utf-8")
    return restored, remaining


def main() -> int:
    lupdate = _find_tool("pylupdate6", "pyside6-lupdate")
    lrelease = _find_tool("pyside6-lrelease", "lrelease", "lrelease6")

    live = _collect_live_literals()
    alive_ctx = _collect_alive_contexts()
    print(f"源码 tr 族字面量：{len(live)} 个；存活上下文：{len(alive_ctx)} 个")

    py_files = [str(p) for p in _frontend_py_files()]
    for name in TS_FILES:
        ts_path = TS_DIR / name
        print(f"\n== {name} ==")
        _run([lupdate, *py_files, "-ts", str(ts_path)])
        restored, remaining = restore_live_entries(ts_path, live, alive_ctx)
        print(f"  恢复误标 vanished：{restored}（真实 vanished 保留：{remaining}）")
        _run([lrelease, str(ts_path)])
    print("\n完成：.ts 已刷新（location 为自动生成），.qm 已重新编译。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

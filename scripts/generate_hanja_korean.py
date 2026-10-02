# -*- coding: utf-8 -*-
"""从 Unicode Unihan 数据生成「汉字 → 韩音谚文」映射表。

数据来源：https://www.unicode.org/Public/UCD/latest/ucd/Unihan.zip
的 Unihan_Readings.txt ``kHangul`` 字段（依 Unicode Terms of Use 分发，
允许再分发；本表为其派生数据）。

读音选择规则（多读音字）：
1. 优先取来源标签含 ``E`` 的读音——实测为韩文词典词目形（ 불/룡/리/
   략），词首두음법칙变体（부/용/이/약）由运行时的
   ``KoreanHanjaAnalyzer`` 按词首位置动态应用；
2. 无 ``E`` 标签时取首个读音（如 劉 류）。

用法（在仓库根目录）::

    python scripts/generate_hanja_korean.py [Unihan_Readings.txt 路径]

未提供路径时从 unicode.org 下载。输出固定写入
``src/strange_uta_game/config/hanja_korean.json``。
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

UNIHAN_URL = "https://www.unicode.org/Public/UCD/latest/ucd/Unihan.zip"
OUTPUT = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "strange_uta_game"
    / "config"
    / "hanja_korean.json"
)


def _read_unihan(path: str | None) -> list[str]:
    if path:
        return Path(path).read_text(encoding="utf-8").splitlines()
    import io
    import zipfile

    print(f"下载 {UNIHAN_URL} ...")
    with urllib.request.urlopen(UNIHAN_URL) as resp:
        with zipfile.ZipFile(io.BytesIO(resp.read())) as zf:
            return zf.read("Unihan_Readings.txt").decode("utf-8").splitlines()


def build_table(lines: list[str]) -> dict[str, str]:
    """Unihan_Readings 行 → {汉字: 韩音谚文}（0E 标签优先，否则首读音）。"""
    table: dict[str, str] = {}
    for line in lines:
        parts = line.rstrip("\n").split("\t")
        if len(parts) != 3 or parts[1] != "kHangul":
            continue
        char = chr(int(parts[0][2:], 16))
        best = None
        for reading in parts[2].split():
            value, _, tag = reading.partition(":")
            if not value:
                continue
            if tag.endswith("E"):
                best = value
                break
            if best is None:
                best = value
        if best:
            table[char] = best
    return table


def main(argv: list[str]) -> int:
    lines = _read_unihan(argv[1] if len(argv) > 1 else None)
    table = build_table(lines)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(table, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"写入 {OUTPUT}（{len(table)} 条，{OUTPUT.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

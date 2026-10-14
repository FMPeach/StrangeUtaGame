# -*- coding: utf-8 -*-
"""中文拼音 → 片假名近似音注音转换器（2026-10）。

用途：为中文歌词生成「按中文发音最近的日语音」片假名注音
（zhōng→ジョーン、hǎo→ハーオ、rén→レン），日文歌词本/中文学习
站点对中文歌常见的标注口径，供「自动注音管理 → 中文片假名注音」
入口使用。

设计：
- 复用 :class:`PinyinAnalyzer`（jieba 分词 + pypinyin）得到带声调
  拼音，借此保留词组上下文的多音字判断（重→zhòng/chóng），
  本模块只负责「一个拼音音节 → 片假名」的音节级转写。
- 先剥离声调、再做「整音节特例表 → 声母 + 韵母拼接」两段式查表；
  两段都未命中时回退原拼音串（不丢弃信息，便于用户手改）。
- 长音（ー）：中文单音节在日语里约当二拍，带声调音节按惯例在
  「主元音」后补一个「ー」拉长（zhōng→ジョーン、nǐ→ニー、
  hǎo→ハーオ）。这是日中音译的通行做法（参考「中国地名・人名の
  書き方の表」及中文歌词片假名标注惯例），无调音节（轻声/查表兜底）
  不补。
- 逐音节转写，不建模跨音节音变（儿化、变调等）；唱法特殊可直接
  编辑 ruby。

韵母拼写按 pypinyin 默认风格（``Style.TONE``）：ü 在 l/n 后写作
``ü``（lǜ→lǜ），在 j/q/x/y 后写作 u（xuě/jué）；本模块统一按
「去声调 → 查表」处理，两种写法都在表中收录。
"""

from __future__ import annotations

import unicodedata
from typing import Dict, List, Optional, Tuple

from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
    PinyinAnalyzer,
    RubyResult,
)

# 长音标记（片假名长音符）
_LONG = "ー"

# ── 声调剥离 ──
# pypinyin 返回带声调符号的拼音（āáǎà 等单码位字符，Unicode 归类
# COMBINING 之外的预组合形）。NFD 分解后去掉组合记号即可还原无调
# 音节；pypinyin 亦用 ü 的带调形式（ǖǘǚǜ），同样被分解剥离。
_TONE_MARKS = frozenset("\u0304\u0301\u030c\u0300")  # ˉ ˊ ˇ ˋ


def strip_tone(pinyin: str) -> str:
    """剥离拼音声调符号，返回无声调形式（zhōng → zhong）。

    NFD 分解后去掉组合声调记号，再 NFC 重组——重组是必要的：
    ü（U+00FC）分解为 u + U+0308，若不去掉分音符则必须重组回 ü，
    否则查表命中不了 ü 韵母（lǜ → lü 的 ü 需保持单码位）。
    """
    if not pinyin:
        return ""
    decomposed = unicodedata.normalize("NFD", pinyin)
    stripped = "".join(c for c in decomposed if c not in _TONE_MARKS)
    return unicodedata.normalize("NFC", stripped)


def _normalize(syllable: str) -> str:
    """标准化无声调音节：ü 变体统一为 ü，便于查表。"""
    s = strip_tone(syllable).lower()
    # 常见 ü 的 ASCII 写法：nu: / nv → nü
    s = s.replace("u:", "ü").replace("v", "ü")
    return s


def _has_tone(pinyin: str) -> bool:
    """音节是否带声调（含组合声调记号）。轻声/无调返回 False。

    以 NFD 分解后是否残留声调组合记号判定——pypinyin ``Style.TONE``
    输出带调（zhōng），轻声音节 pypinyin 亦给无调形（如「的」de）。
    """
    if not pinyin:
        return False
    return any(c in _TONE_MARKS for c in unicodedata.normalize("NFD", pinyin))


# ── 整音节特例表 ──
# 这些音节无法用「声母 + 韵母」直接拼出日语音（舌尖元音 zhi/chi/shi/ri、
# 卷舌 er、零声母整读等），单列特例。
_WHOLE_SYLLABLE: Dict[str, str] = {
    # 舌尖元音（-i 实际不是 i 音）
    "zhi": "ジ", "chi": "チ", "shi": "シ", "ri": "リ",
    "zi": "ズ", "ci": "ツ", "si": "ス",
    # 卷舌儿化
    "er": "アル",
    # 零声母整读（y/w 起头在拼写上是介音，避免叠成两拍）
    "yi": "イ", "ya": "ヤ", "ye": "イェ", "yao": "ヤオ",
    "you": "ヨウ", "yan": "イェン", "yin": "イン",
    "yang": "ヤン", "ying": "イン", "yong": "ヨン",
    "yu": "ユ", "yue": "ユエ", "yuan": "ユエン", "yun": "ユン",
    "wu": "ウ", "wa": "ワ", "wo": "ウォ", "wai": "ワイ",
    "wei": "ウェイ", "wan": "ワン", "wen": "ウェン",
    "wang": "ワン", "weng": "ウォン",
    # 零声母 a/o/e 起头
    "a": "ア", "o": "オ", "e": "エ",
    "ai": "アイ", "ei": "エイ", "ao": "アオ", "ou": "オウ",
    "an": "アン", "en": "エン", "ang": "アン", "eng": "エン",
    "ong": "オン",
}

# ── 声母表 ──
# 每个声母给出「行」的五个元音段假名（a/i/u/e/o 序），日语五十音
# 排列；转写时按韵母首个元音取对应段。零声母不在此表（走上文整读）。
_INITIAL_ROWS: Dict[str, Tuple[str, str, str, str, str]] = {
    #        a     i     u     e     o
    "b":  ("バ", "ビ", "ブ", "ベ", "ボ"),
    "p":  ("パ", "ピ", "プ", "ペ", "ポ"),
    "m":  ("マ", "ミ", "ム", "メ", "モ"),
    "f":  ("ファ", "フィ", "フ", "フェ", "フォ"),
    "d":  ("ダ", "ディ", "ドゥ", "デ", "ド"),
    "t":  ("タ", "ティ", "トゥ", "テ", "ト"),
    "n":  ("ナ", "ニ", "ヌ", "ネ", "ノ"),
    "l":  ("ラ", "リ", "ル", "レ", "ロ"),
    "g":  ("ガ", "ギ", "グ", "ゲ", "ゴ"),
    "k":  ("カ", "キ", "ク", "ケ", "コ"),
    "h":  ("ハ", "ヒ", "フ", "ヘ", "ホ"),
    "j":  ("ジャ", "ジ", "ジュ", "ジェ", "ジョ"),
    "q":  ("チャ", "チ", "チュ", "チェ", "チョ"),
    "x":  ("シャ", "シ", "シュ", "シェ", "ショ"),
    "zh": ("ジャ", "ジ", "ジュ", "ジェ", "ジョ"),
    "ch": ("チャ", "チ", "チュ", "チェ", "チョ"),
    "sh": ("シャ", "シ", "シュ", "シェ", "ショ"),
    "r":  ("ラ", "リ", "ル", "レ", "ロ"),
    "z":  ("ザ", "ズィ", "ズ", "ゼ", "ゾ"),
    "c":  ("ツァ", "ツィ", "ツ", "ツェ", "ツォ"),
    "s":  ("サ", "スィ", "ス", "セ", "ソ"),
    "y":  ("ヤ", "イ", "ユ", "イェ", "ヨ"),
    "w":  ("ワ", "ウィ", "ウ", "ウェ", "ウォ"),
}

# 声母按长度降序（先匹配 zh/ch/sh 再匹配 z/c/s）
_INITIALS_BY_LEN = sorted(_INITIAL_ROWS.keys(), key=len, reverse=True)

# ── 韵母 → 段索引 ──
# 返回 (段索引, 尾音)：段索引 0/1/2/3/4 = a/i/u/e/o，尾音为附加假名（ン 等）。
# 复合韵母拆成「主元音段 + 韵尾」，如 ai→(0,"イ") 即 a段+イ；介音 i/u/ü
# 由整表处理（ia/ua/üe 等），故此处只列常见核韵母。
_FINALS: Dict[str, Tuple[int, str]] = {
    # 单元音
    "a": (0, ""), "i": (1, ""), "u": (2, ""), "e": (3, ""), "o": (4, ""),
    # 复元音
    "ai": (0, "イ"), "ei": (3, "イ"), "ao": (0, "オ"), "ou": (4, "ウ"),
    # 鼻韵尾
    "an": (0, "ン"), "en": (3, "ン"), "in": (1, "ン"), "un": (2, "ン"),
    "ang": (0, "ン"), "eng": (3, "ン"), "ing": (1, "ン"), "ong": (4, "ン"),
}

# 带介音（i/u/ü 起头）的韵母：整韵母直接给假名，避免把介音拆成
# 大假名（jiā 若走 i段+ヤ 会得「ジヤ」，应为拗音「ジャ」）。
# 值 = (段标签, 附加假名)；段标签见 _SEG_INDEX，另支持：
#   "ü" = 该声母的 ü 段（l/n 为「i段+ュ」拗音，其余同 u 段）。
# j/q/x/y 拼写里 ü 写作 u（xuě/jué），故 ue/uan/ün 与 üe/üan/ün 等价；
# 两套写法都表在此，值统一用 "ü" 标签消歧。
# 小假名（ャュョ）只用于 i 介音真拗音（jiā→ジャ、liú→リュウ）；
# u/ü 介音取大假名（guó→グオ、xuě→シュエ），避免「グォ/シュェ」
# 这类日语不用的拗音写法。
_GLIDE_FINALS: Dict[str, Tuple[str, str]] = {
    # 韵母: (使用的段标签, 附加假名)
    "ia": ("i", "ャ"), "ie": ("i", "ェ"), "iao": ("i", "ャオ"),
    "iu": ("i", "ュウ"),
    "ian": ("i", "エン"), "iang": ("i", "ャン"), "iong": ("i", "ョン"),
    "ua": ("u", "ア"), "uo": ("u", "オ"), "uai": ("u", "アイ"),
    "ui": ("u", "イ"),
    "uan": ("u", "アン"), "uang": ("u", "アン"), "ueng": ("u", "オン"),
    # ü 系（及 j/q/x 的 u 写法）
    "ü": ("ü", ""), "üe": ("ü", "エ"), "üan": ("ü", "エン"), "ün": ("ü", "ン"),
    "ue": ("ü", "エ"), "uen": ("ü", "ン"),
}

# 声母行的段标签 → 列索引
_SEG_INDEX = {"a": 0, "i": 1, "u": 2, "e": 3, "o": 4}

# l/n 的 ü 段：日语无 [ly]/[ny]，取拗音「リュ/ニュ」（ル/ヌ 会丢介音）。
_ÜN_OVERRIDE: Dict[str, str] = {"l": "リュ", "n": "ニュ"}

# i 介音取小假名（真拗音）的声母：这些声母的 i 段本就是ジ/チ/シ，
# 拼小 ャ/ェ/ョ 得标准拗音（jiān→ジェン、xiǎo→シャオ）。其余声母
# 的 i 段是ビ/ディ/リ 等，拼小假名虽成立但汉语「i+韵」更接近大字
# 连读（diàn→ディエン、lián→リエン），故按此集合区分。
_GLIDE_IO_INITIALS = frozenset({"j", "q", "x", "zh", "ch", "sh"})

# 小假名 → 大假名（非拗音声母的 i 介音尾音放大用）
_SMALL_TO_LARGE = {
    "ャ": "ヤ", "ュ": "ユ", "ョ": "ヨ", "ァ": "ア", "ィ": "イ", "ゥ": "ウ",
    "ェ": "エ", "ォ": "オ",
}


def _build_kana(norm: str) -> Optional[str]:
    """无声调音节 → 片假名（不含长音）。未命中返回 None。

    统一入口：整音节特例表 → 声母 + 韵母拼接。
    """
    if not norm:
        return None
    if norm in _WHOLE_SYLLABLE:
        return _WHOLE_SYLLABLE[norm]
    initial, final = _split_initial_final(norm)
    if initial is None:
        row = _FINALS.get(norm)
        if row is not None:
            seg, tail = row
            return ("アイウエオ"[seg] if seg >= 0 else "") + tail
        return None
    row = _INITIAL_ROWS.get(initial)
    if row is None:
        return None
    if not final:
        return row[0]
    glide = _GLIDE_FINALS.get(final)
    if glide is not None:
        label, tail = glide
        if label == "ü":
            base = _ÜN_OVERRIDE.get(initial) or row[_SEG_INDEX["u"]]
            return base + tail
        if label == "i" and initial not in _GLIDE_IO_INITIALS:
            tail = _SMALL_TO_LARGE.get(tail, tail)
        return row[_SEG_INDEX[label]] + tail
    findex = _FINALS.get(final)
    if findex is None:
        return None
    seg, tail = findex
    if seg < 0:
        return None
    return row[seg] + tail


# 「主元音已出现的 kana 前缀长度」判据：长音ー插在音节首个
# 承载主元音的音节拍（mora）之后。日语一拍 = 主体假名 + 可选小假名
# （ジ+ュ = ジュ 一拍），故需吃掉主体假名**及其后随的小假名**再定位：
#   ジャ→ジャー、ハオ→ハーオ、シェン→シェーン、チュアン→チューアン。
# 拨音 ン 不是元音承载位，单独处理。
_SMALL_KANA = frozenset("ャュョァィゥェォヮ")


def _long_vowel_pos(kana: str) -> int:
    """返回长音「ー」应插入的下标（首个元音拍之后）。"""
    i = 0
    n = len(kana)
    while i < n:
        ch = kana[i]
        if ch == "ン":
            # 拨音：非元音承载位，跳过（极端音节如「ン」开头时不在此插）
            i += 1
            continue
        # 找到主体假名：吃掉它以及其后的所有小假名（同属一拍）
        i += 1
        while i < n and kana[i] in _SMALL_KANA:
            i += 1
        return i
    return n


def pinyin_syllable_to_katakana(syllable: str) -> str:
    """单个拼音音节 → 片假名近似音（带声调时标长音）。

    先剥离声调判有无长音，再查整音节特例 / 声母+韵母拼接得到基础
    假名；带声调音节在「主元音」后补「ー」（日中音译惯例：中文单
    音节约当日语二拍）。无声调（轻声）或未命中时回退，不补长音。

    Example:
        >>> pinyin_syllable_to_katakana("zhōng")
        'ジョーン'
        >>> pinyin_syllable_to_katakana("hǎo")
        'ハーオ'
        >>> pinyin_syllable_to_katakana("rén")
        'レーン'
        >>> pinyin_syllable_to_katakana("ni")
        'ニ'
    """
    if not syllable:
        return syllable
    norm = _normalize(syllable)
    if not norm:
        return syllable
    kana = _build_kana(norm)
    if kana is None:
        return syllable
    # 带声调 → 长音；轻声（无声调）不补
    if _has_tone(syllable):
        pos = _long_vowel_pos(kana)
        kana = kana[:pos] + _LONG + kana[pos:]
    return kana


def _split_initial_final(norm: str) -> Tuple[Optional[str], str]:
    """把无声调音节拆成 (声母, 韵母)。无匹配声母时返回 (None, 原串)。"""
    for ini in _INITIALS_BY_LEN:
        if norm.startswith(ini) and len(norm) > len(ini):
            return ini, norm[len(ini):]
    return None, norm


class PinyinKatakanaAnalyzer(PinyinAnalyzer):
    """中文拼音片假名近似音分析器。

    继承 :class:`PinyinAnalyzer`（jieba 分词 + pypinyin 多音字消歧），
    仅把每个汉字位置的拼音读数改写为片假名近似音；非汉字字符仍回退
    原字符（与父类口径一致）。
    """

    def analyze(self, text: str) -> List[RubyResult]:
        results = super().analyze(text)
        converted: List[RubyResult] = []
        for r in results:
            if r.reading and r.reading != r.text:
                reading = pinyin_syllable_to_katakana(r.reading)
            else:
                reading = r.reading
            converted.append(
                RubyResult(
                    text=r.text,
                    reading=reading,
                    start_idx=r.start_idx,
                    end_idx=r.end_idx,
                    morpheme_span=r.morpheme_span,
                )
            )
        return converted

    def get_reading(self, text: str) -> str:
        if not text:
            return ""
        return " ".join(r.reading for r in self.analyze(text))


def create_pinyin_katakana_analyzer() -> PinyinKatakanaAnalyzer:
    """创建中文拼音片假名近似音分析器。"""
    return PinyinKatakanaAnalyzer()


__all__ = [
    "PinyinKatakanaAnalyzer",
    "create_pinyin_katakana_analyzer",
    "pinyin_syllable_to_katakana",
    "strip_tone",
]

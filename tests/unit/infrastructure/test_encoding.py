"""encoding.decode_lyric_bytes — 编码检测测试（E1）。

历史实现是固定顺序回退链（utf-8-sig → cp932 → gb18030 → big5）：
Big5 歌词总被 gb18030 先"成功"解码成私用区乱码（big5 分支沦为死
代码），GBK 也常被 cp932 拦截。现实现以 charset-normalizer 为主检测、
碰撞家族 {cp932, gb18030, big5} 内做确定性启发式决胜。

测试样本全部用 codecs 真实编码，并断言解码文本与原文完全一致
（round-trip），而不只是"不抛错"。
"""

import codecs

import pytest

from strange_uta_game.backend.infrastructure.parsers.encoding import decode_lyric_bytes


class TestDecodeLyricBytes:
    def test_utf8_plain(self):
        text, enc = decode_lyric_bytes("あいう".encode("utf-8"))
        assert text == "あいう"
        assert enc == "utf-8-sig"

    def test_utf8_chinese_plain(self):
        # 无 BOM 的 UTF-8 中文同样直接采信（这些字节在 gb18030 下也常合法）
        text, enc = decode_lyric_bytes("月亮代表我的心".encode("utf-8"))
        assert text == "月亮代表我的心"
        assert enc == "utf-8-sig"

    def test_utf8_bom_stripped(self):
        text, enc = decode_lyric_bytes(codecs.BOM_UTF8 + "歌詞".encode("utf-8"))
        assert text == "歌詞"
        assert enc == "utf-8-sig"

    def test_utf16_le_bom(self):
        text, enc = decode_lyric_bytes("歌詞".encode("utf-16"))
        assert text == "歌詞"
        assert enc == "utf-16"

    def test_utf16_be_bom(self):
        data = codecs.BOM_UTF16_BE + "歌詞".encode("utf-16-be")
        text, enc = decode_lyric_bytes(data)
        assert text == "歌詞"
        assert enc == "utf-16"

    def test_utf32_le_bom(self):
        text, enc = decode_lyric_bytes("歌詞".encode("utf-32"))
        assert text == "歌詞"
        assert enc == "utf-32"

    def test_shift_jis(self):
        text, enc = decode_lyric_bytes("歌詞".encode("cp932"))
        assert text == "歌詞"
        assert enc == "cp932"

    def test_empty_bytes(self):
        text, _enc = decode_lyric_bytes(b"")
        assert text == ""

    def test_undecodable_raises_unicode_decode_error(self):
        # 0x81 0x00 在 cp932/gb18030/big5 中都不是合法序列
        with pytest.raises(UnicodeDecodeError):
            decode_lyric_bytes(b"\x81\x00\x81\x01")


class TestCollisionFamily:
    """碰撞家族 {cp932, gb18030, big5}：固定回退链的死穴。

    每个样本的 bytes 在多个家族编码下都能"成功"解码（碰撞），
    断言必须选中正确编码且文本 round-trip 回原文。
    """

    def test_shift_jis_with_kana(self):
        original = "検索機能"
        text, enc = decode_lyric_bytes(original.encode("cp932"))
        assert text == original
        assert enc == "cp932"

    def test_shift_jis_kana_beats_gb18030(self):
        # 带假名的日文行：gb18030 也能"成功"解码成汉字乱码（碰撞），
        # 全宽假名的存在必须把判定拉回 cp932
        original = "月亮代表我的心の歌"
        text, enc = decode_lyric_bytes(original.encode("cp932"))
        assert text == original
        assert enc == "cp932"

    def test_gb18030_sample(self):
        # 评审的原始失败样本之一：gb18030 字节也曾被 cp932 拦截成
        # 半角假名乱码；big5 也能硬解出汉字乱码（双重碰撞）
        original = "月亮代表我的心"
        text, enc = decode_lyric_bytes(original.encode("gb18030"))
        assert text == original
        assert enc == "gb18030"

    def test_gbk_lyrics_no_longer_crash(self):
        """GBK 编码歌词（此前 utf-8 失败、shift_jis 也常失败 → ParseError）
        现在能被检测为 gb18030（GBK 的超集），文本逐字还原。"""
        original = "从此我不能听见你的温柔"
        text, enc = decode_lyric_bytes(original.encode("gbk"))
        assert text == original
        assert enc == "gb18030"

    def test_big5_sample(self):
        # 评审用真实 Python 验证的失败样本：3/3 Big5 样本被 gb18030
        # 先"成功"解码成私用区乱码，big5 分支沦为死代码
        original = "編碼測試"
        text, enc = decode_lyric_bytes(original.encode("big5"))
        assert text == original
        assert enc == "big5"

    def test_big5_no_pua_garbage(self):
        # gb18030 硬解 Big5 字节会落进私用区（U+E000-F8FF），
        # 正确的 big5 解码不得出现私用区字符
        original = "感謝你的愛世界"
        text, enc = decode_lyric_bytes(original.encode("big5"))
        assert text == original
        assert enc == "big5"
        assert not any(0xE000 <= ord(ch) <= 0xF8FF for ch in text)

    def test_big5_file(self):
        original = "\n".join(
            ["[00:01:00]感謝你的愛世界", "[00:02:00]編碼測試時間", "[00:03:00]誰人在調弦"]
        )
        text, enc = decode_lyric_bytes(original.encode("big5"))
        assert text == original
        assert enc == "big5"

    def test_gb18030_file(self):
        original = "\n".join(
            ["[00:01:00]月亮代表我的心", "[00:02:00]从此我不能听见你的温柔"]
        )
        text, enc = decode_lyric_bytes(original.encode("gb18030"))
        assert text == original
        assert enc == "gb18030"

    def test_shift_jis_file(self):
        original = "\n".join(
            ["[00:01:00]ふるさとの歌", "[00:02:00]月の明かり", "[00:03:00]検索機能のテスト"]
        )
        text, enc = decode_lyric_bytes(original.encode("cp932"))
        assert text == original
        assert enc == "cp932"

# 英文发音音节

开启「按音节 Check 英文单词」时，节奏点来自发音，而不再来自 Pyphen 的排版断字。
`english_ruby.get_syllable_start_offsets` 保持原接口，导入、普通注音和中韩模式的英文
自动节奏点共用此入口。关闭选项仍为每词一个自动节奏点。英文用户词典仅在 Phase 5
实际命中并写入 RubyPart 时覆盖节奏点；跳过词典的中韩模式不受词条影响。
重算时保留已注音英文连词块实际的 RubyPart 分段和节奏点布局，包括自动注音中
无 ruby 但带节奏点的后续字母；无注音词才重新计算发音节奏点。更换自动拆分设置后
需要「全部重新分析」。仅分析未注音字符及已有时间戳的保护沿用原有流程。
英文词条无需开启片假名英文注音选项即可生效。

## 流程

1. 规范化撇号（等长替换），保留原文字母、大小写及偏移。
2. 查询随应用分发的 `cmudict-0.7b`，采用首选发音。
3. 未收录的普通英文字母单词使用本地 g2p-en GRU 预测；不修改拼写、不联网下载。
4. 以元音核划分发音音节，辅音按合法节首最大化分配；有词典发音证据时保留复合词和
   派生词边界，例如 heart-ache、blind-ed。缩写保留 does-n't。
5. 通过带权字母组/音素动态规划，消费全部字母和音素，将边界映射回原词。
   优先对齐明确的元音字母；无法完成时才允许成音辅音，以区分 po-em 和 rhythm。
   双写辅音使用正常节首划分（run-ning、hap-pen），不推断 hap + en 一类后缀。
   字母组合、不发音字母均参与对齐，不按字母数量平均切分。
6. 无发音、无法可靠映射、异常长输入或演唱拖长拼写保留整词一个节奏点。

AI 打轴仅查 CMU 并复用相同音素分音节器；不调用 G2P，以免把片假名 ruby 转出的
罗马字或日语罗马字歌词猜成英语。词典未命中时沿用原有 e2k/拼写回退。
不再把已命中的发音音节合并到 Pyphen 的断字段数。
既有工程的节奏点结构仍由工程本身决定；一个已有节奏点可以覆盖多个对齐音节。
旧 e2k/拼写回退保留在 AI 转写的最终兜底路径，不控制新的英文节奏点数。

## 例子与边界

- open → o-pen；ideology → i-de-o-lo-gy；individuality → in-di-vi-du-a-li-ty。
- heartache → heart-ache；abandoned → a-ban-doned；gonna → gon-na。
- crazy → cra-zy；zero → ze-ro；running → run-ning；hidden → hid-den。
- poem → po-em；cruel → cru-el；science → sci-ence；ruin → ru-in；client → cli-ent。
- 生词 backdown 由本地模型预测为两节。
- 拼错的词不自动更正。例如 riduculously 的预测无法映射到原字母时保留整词，
  可修正拼写或用用户词典手动指定；ridiculously 为五个发音音节。

CMU 是美式词典，首选发音未必等于特定歌曲的读法。算法不从音频判定吞音、合音、
一字多音符；需要时使用原有手工节奏点/用户词典功能。字母边界是发音的显示对应，
不保证与教材或排版词典的字形分节一致。

## 离线模型来源

g2p-en by Kyubyong Park and Jongseok Kim，Apache-2.0。

- 上游：<https://github.com/Kyubyong/g2p>
- 版本：commit `c6439c274c42b9724a7fee1dc07ca6a4c68a0538`
- 原文件：`g2p_en/checkpoint20.npz`
- 随包文件：`src/strange_uta_game/config/g2p_en_checkpoint20.npz`（3,342,298 字节）
- SHA-256：`b8af35e4596d8dd5836dfd3fe9b2ba4f97b9c311efe8879544cbcfcbd566d8c6`
- 许可全文：`src/strange_uta_game/config/g2p_en_LICENSE.txt`

`english_g2p.py` 是基于上游 NumPy GRU 推理的精简适配：仅保留单词预测，移除 NLTK、
词性标注与下载逻辑；使用已有 NumPy 依赖，懒加载权重并缓存词级分析。
解码增加长度上限并拒绝未终止输出。资源随 `config/*` 和 PyInstaller 的应用数据一起分发。

## 验证

```powershell
python -m pytest tests/unit/infrastructure/test_english_syllables.py tests/unit/application/test_english_syllable_regressions.py tests/unit/application/test_auto_check_service.py tests/unit/application/test_ai_timing_transcription.py tests/unit/application/test_ai_timing_alignment.py tests/unit/application/test_ai_timing_pronunciation.py
python build.py --variant main
```

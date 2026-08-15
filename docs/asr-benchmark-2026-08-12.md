# 电话 ASR 统一基准（2026-08-12）

## 范围

在 Ryzen 7 3700U CPU、4 线程、CPU 推理下，使用 4 句 Windows 中文 TTS，分别生成纯净、
12% 音量、开头裁切 120 ms、混合粉红噪声四种变体，共 16 个 16 kHz 单声道 PCM 样本。
字符错误率（CER）忽略空格和标点；标点准确率按 `，。？！：；` 序列的编辑距离计算。

这组数据用于同音频筛选模型，不能替代真实手机、真实说话人、扬声器回声和设备 AEC 测试。

## 结果

| 引擎 | CER | 首字正确率 | 标点准确率 | 平均实时系数 |
|---|---:|---:|---:|---:|
| X-ASR 480 ms Zipformer int8 | 0.00% | 100.00% | 61.88% | 0.313 |
| SenseVoice int8 2025 | 2.76% | 100.00% | 0.00% | 0.316 |
| Paraformer int8 2025 | 5.71% | 100.00% | 0.00% | 0.269 |
| whisper.cpp base-q5_1 | 41.14% | 68.75% | 62.50% | 0.518 |
| whisper.cpp small-q5_1 | 28.94% | 93.75% | 74.38% | 1.541 |

X-ASR 结束输入前补入 1 秒静音右上下文；否则流式编码器末尾 token 未冲刷，会产生假性漏尾字。
这与电话采集持续输入静音的行为一致。

## 结论

- 当前候选中保留 X-ASR 作为本地电话模型，不能用 Whisper 默认替换。
- SenseVoice 可做整句校正，但它和 Paraformer 本身不输出标点。
- whisper.cpp Base 错字和繁体输出明显；Small 仍有较高 CER，且低于实时速度。
- Android 只自动优先选择声明 `supports_partial=true` 的 Gateway ASR。默认关闭 partial 的
  whisper.cpp 继续保留为实验 provider，但不再仅凭 `available=true` 覆盖电话主链路。
- 现有两段无真值电话录音 RMS 约为 -26 dBFS。SenseVoice 和 X-ASR 均识别出相同主要语义，
  但样本不足以评估真实 CER 或回声消除质量。

## 复现

语料和模型放在 `.nexus-test/asr-benchmark`，不会进入 APK。生成语料：

```powershell
.\scripts\generate_asr_benchmark_corpus.ps1
```

统一计算工具为 `scripts/benchmark_asr.py`，支持 Sherpa online/offline 与 whisper.cpp CLI。

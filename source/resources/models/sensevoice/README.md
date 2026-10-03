# SenseVoiceSmall model files

These files are the INT8 ONNX conversion dated 2024-07-17 of the `SenseVoiceSmall` model published by FunAudioLLM / Alibaba Group as `iic/SenseVoiceSmall` on ModelScope. The sherpa-onnx model documentation records that this model was converted with `export-onnx.py`:

- [sherpa-onnx pretrained SenseVoice model documentation](https://github.com/k2-fsa/sherpa/blob/master/docs/source/onnx/sense-voice/pretrained.rst)
- [ModelScope iic/SenseVoiceSmall](https://www.modelscope.cn/models/iic/SenseVoiceSmall)
- [SenseVoice upstream project](https://github.com/FunAudioLLM/SenseVoice)
- [FunASR MODEL_LICENSE source](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE)
- [FunAudioLLM/SenseVoice model-license clarification](https://github.com/QwenAudio/SenseVoice/issues/279)

The complete FunASR Model Open Source License Agreement v1.1 is copied to [`LICENSE`](LICENSE). It expressly allows sharing subject to its conditions; it is a model-specific agreement, **not MIT**. Redistribution requires retaining source/author attribution and the relevant model name under §2.2 and complying with the other restrictions and terms. The project's own MIT license does not change these model terms. If a distributor cannot comply with the model agreement, omit these model files from the public package and have users obtain them separately.

`silero_vad.onnx` is a separately licensed Silero VAD artifact. Its full MIT text is in [`../../licenses/Silero-VAD-MIT.txt`](../../licenses/Silero-VAD-MIT.txt); it is not covered by the FunASR model license.

`manifest.json` records the artifact names, byte sizes, and SHA-256 checksums. It has not been changed as part of this notice update.

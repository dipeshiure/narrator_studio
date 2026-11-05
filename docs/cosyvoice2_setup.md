# CosyVoice 2 on Apple Silicon (tools/cosyvoice2_tts.py)

Status: written, syntax-checked, NOT yet run against the real model (needs the repo + a ~2 GB model download).

    # 1. environment (Python 3.10 as in the CosyVoice README)
    conda create -n cosyvoice -y python=3.10 && conda activate cosyvoice
    conda install -y -c conda-forge pynini==2.1.5        # only if `pip install wetext` fails to build

    # 2. code (+ Matcha-TTS submodule)
    git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git ~/CosyVoice

    # 3. dependencies: default macOS torch wheels include MPS; the repo's CUDA/server-only pins are left out
    pip install torch torchaudio
    pip install conformer==0.3.2 diffusers==0.29.0 hydra-core==1.3.2 HyperPyYAML==1.2.3 inflect==7.3.1 \
        librosa==0.10.2 lightning==2.2.4 modelscope==1.20.0 numpy==1.26.4 omegaconf==2.3.0 onnx==1.16.0 \
        onnxruntime==1.18.0 openai-whisper==20231117 protobuf==4.25 pyarrow pyworld==0.3.4 soundfile==0.12.1 \
        transformers==4.51.3 x-transformers==2.11.24 wetext==0.0.4 wget==3.2 networkx==3.1 rich pydantic==2.7.0

    # 4. model (about 2 GB, once; do it on Wi-Fi)
    pip install -U huggingface_hub
    huggingface-cli download FunAudioLLM/CosyVoice2-0.5B --local-dir ~/CosyVoice/pretrained_models/CosyVoice2-0.5B

    # 5. check that MPS is used
    python tools/cosyvoice2_tts.py check --repo ~/CosyVoice --model ~/CosyVoice/pretrained_models/CosyVoice2-0.5B

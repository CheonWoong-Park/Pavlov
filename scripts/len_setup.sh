#!/bin/bash
# 빈 A100 인스턴스 부트스트랩: venv + 생성에 필요한 패키지 (transformers 4.51.3 고정)
set -e
cd /root
python3 -m venv /root/.venv
source /root/.venv/bin/activate
pip install -U pip -q
pip install -q torch==2.9.1 --index-url https://download.pytorch.org/whl/cu130
pip install -q transformers==4.51.3 peft==0.19.1 accelerate safetensors sentencepiece einops
python -c "import transformers,torch,peft;print('versions', transformers.__version__, torch.__version__, peft.__version__)"
echo SETUP_DONE

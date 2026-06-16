# 학습 (1단계 skeleton 복원기)

두 arm을 `src/train_lora.py` 하나로 학습한다 (`--arm ar|diff`). base는 frozen, LoRA
adapter만 학습한다. 학습은 GPU 머신(A100 80GB 기준)에서 수행한다.

## 환경

```bash
uv venv .venv --python 3.13
uv pip install -r requirements-lock.txt --python .venv/bin/python
# torch는 학습 머신 드라이버에 맞는 CUDA index로 (A100 CUDA 13 기준 cu130):
uv pip install torch --index-url https://download.pytorch.org/whl/cu130 --python .venv/bin/python
export HF_HOME=<대용량 디스크>/hf-cache
```

**transformers는 4.51.3을 쓴다.** transformers 5.x는 DiffuCoder remote code(DreamModel)의
RoPE 초기화와 충돌한다(`KeyError: 'default'`). 두 arm은 같은 transformers 버전으로 통일한다.

핵심 버전: torch 2.9.1+cu130, transformers 4.51.3, peft 0.19.1, accelerate 1.14.0.

모델은 첫 실행 시 자동 다운로드된다(`apple/DiffuCoder-7B-Base`, `Qwen/Qwen2.5-Coder-7B`,
각 ~15GB). GPU 과금 전에 `hf download`로 캐시해두면 시간을 아낀다.

## 데이터

| 파일 | 용도 |
|---|---|
| `data/matched/pilot2k_balanced.jsonl` | Gate 1 pilot (2,000건) |
| `data/matched/balanced_train.jsonl` | 본 학습 (17,760건, 4096 token 이하) |
| `data/matched/balanced_val400.jsonl` | 검증 (400건, 학습 미사용) |

데이터 포맷(jsonl, 한 줄=한 샘플):
`{"input": <Ghidra pseudocode>, "target": <anonymized skeleton>, "mapping": {...}, "project", "binary", "opt", "func_name", "file", "source"}`

## 주요 옵션 (`train_lora.py`)

| 플래그 | 의미 |
|---|---|
| `--arm ar\|diff` | AR(Qwen, CE) 또는 diffusion(DiffuCoder, masked diffusion loss) |
| `--quant bf16\|nf4` | bf16 full-precision LoRA(기본 권장, A100) / nf4 4-bit QLoRA(소 VRAM fallback) |
| `--grad-checkpoint` | gradient checkpointing. A100에서 batch 8·seq 4096엔 켜야 OOM 안 남 |
| `--bucket` | length-bucketed batching (micro-batch>1의 padding 낭비 제거) |
| `--micro-batch / --grad-accum` | 곱이 effective batch. 본 학습은 16 유지 |
| `--seq-len` | 본 학습 4096 (2048로 두면 2048–4096 token 샘플이 skip되어 데이터 손실) |
| `--gen-len` | diff arm 고정 canvas 길이(기본 512). response를 EOS로 이 길이까지 padding(LLaDA SFT recipe). 두 arm 공통으로 response가 이보다 긴 샘플은 제외(동일 subset). **평가의 max-new-tokens와 일치시킨다** |
| `--max-steps / --seed / --lora-r / --lora-alpha` | step 수 / seed / LoRA r·alpha |

런마다 `<out>/run_config.json`이 저장되고, step별 loss·VRAM은 `<out>/train_log.jsonl`에
기록된다. checkpoint는 `save-every`(기본 100) step마다 LoRA adapter로 저장된다.

## Gate 1 — pilot (본 학습 전 필수)

200 step 정도. 통과 기준: **loss가 뚜렷이 감소하고 VRAM이 예산 안**. diff arm을 먼저
돌려 (a) DiffuCoder remote code 로드, (b) batch>1에서 `diff_loss`의 mask·logits 처리,
(c) VRAM을 확인한다.

```bash
PY=.venv/bin/python; export HF_HOME=<...>; export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
$PY src/train_lora.py --arm diff --data data/matched/pilot2k_balanced.jsonl \
  --out checkpoints/diff_pilot --seed 0 --max-steps 200 --seq-len 2048 \
  --quant bf16 --micro-batch 8 --grad-accum 2 --bucket --grad-checkpoint
```

## 본 학습

```bash
for arm in diff ar; do
  $PY src/train_lora.py --arm $arm --data data/matched/balanced_train.jsonl \
    --out checkpoints/${arm}_s0 --seed 0 --max-steps 2000 --seq-len 4096 --gen-len 512 \
    --quant bf16 --micro-batch 8 --grad-accum 2 --bucket --grad-checkpoint
done
```

**diff arm의 EOS-padding (필수).** masked-diffusion 모델은 생성 시 *전부 mask된 고정 길이
canvas*에서 시작해 점진적으로 unmask한다. 따라서 학습도 같은 형태여야 한다 — response를
EOS로 `gen-len`까지 padding하고 그 EOS tail까지 예측 대상에 포함시켜야, 모델이 (1) 빈
canvas에서 처음부터 생성하고 (2) EOS로 종료하는 법을 배운다. 이 padding이 없으면 모델은
"문맥 주어진 구멍 메우기"만 학습해 loss는 떨어지지만 **생성 시 collapse**한다(반복 토큰).
`--gen-len`은 평가의 `--max-new-tokens`와 같은 값을 쓴다.

- LoRA r=32 / alpha=64, 대상 모듈 q/k/v/o/gate/up/down — 두 arm 동일(스크립트 기본값).
- A100 80GB 실측: bf16 + grad-checkpoint, seq 4096에서 VRAM peak는 arm에 따라 약 35–47GB.
  ar arm은 activation이 커서 micro-batch를 낮춰 메모리를 줄일 수 있다(effective batch는 16
  유지: 예 `--micro-batch 4 --grad-accum 4`).
- seed를 추가하려면 `--seed 1`로 같은 두 명령을 반복한다(예산이 허용할 때).

## 산출물

- `checkpoints/*/step*/` — LoRA adapter (`adapter_config.json` + `adapter_model.safetensors`)
- `checkpoints/*/train_log.jsonl`, `run_config.json`

평가에는 base 모델에 이 adapter를 얹어 사용한다(`docs/evaluation.md`). adapter weight는
GitHub Release로 백업한다.

# 학습 핸드오프 가이드

이 머신(RTX 5070 12GB)에서는 데이터 구축과 스크립트 검증까지 끝냈다.
실제 학습 — Gate 1 pilot부터 본 학습 4런까지 — 은 학습용 머신에서 진행한다.

## 1. 가져갈 것

| 항목 | 경로 | 비고 |
|---|---|---|
| 코드 전체 | `/mnt/d/DEV/pavlov` (src/, scripts/, tests/, docs/) | git 저장소가 아니므로 통째로 복사 |
| pilot 학습셋 | `data/matched/pilot2k_balanced.jsonl` | 2,000건 (O0–O3 각 500) |
| 본 학습셋 | `data/matched/balanced_train.jsonl` | 17,760건 (O0–O3 각 4,440), 4096 token 이하 |
| 검증셋 | `data/matched/balanced_val400.jsonl` | 400건 (O0–O3 각 100), 학습에 쓰지 않음 |
| 의존성 lock | `requirements-lock.txt` | 아래 2절 참고 |

`data/matched/pilot2k.jsonl`과 `miniset_train_tok.jsonl`은 O0만 들어 있는 이전
버전이라 가져갈 필요 없음.

데이터 포맷 (jsonl, 한 줄이 한 샘플):
`{"input": <Ghidra pseudocode>, "target": <anonymized skeleton>, "mapping": {...}, "project", "binary", "opt", "func_name", "file", "source"}`

## 2. 환경 구축

```bash
uv venv .venv --python 3.12
uv pip install -r requirements-lock.txt --python .venv/bin/python
# torch는 학습 머신 GPU에 맞는 CUDA 인덱스로 설치 (이 머신에서는 cu128 사용)
uv pip install torch --index-url https://download.pytorch.org/whl/cu128 --python .venv/bin/python
export HF_HOME=<대용량 디스크>/hf-cache
```

핵심 버전: torch 2.11.0+cu128, transformers 5.11.0, peft 0.19.1,
bitsandbytes 0.49.2, accelerate 1.14.0.

주의 — transformers 5.x와 DiffuCoder remote code의 호환은 확인하지 못했다.
`AutoModel.from_pretrained("apple/DiffuCoder-7B-Base", trust_remote_code=True)`
로드가 실패하면 transformers를 4.46~4.51 범위로 내릴 것. 이 경우 두 arm 모두
같은 버전으로 통일해야 비교가 공정하다.

## 3. 이 머신에서 검증해 둔 것

- `train_lora.py --arm ar`: tokenize → collate → CE loss → AdamW → 로그 → checkpoint까지
  전체 루프를 Qwen2.5-Coder-0.5B 4-bit로 실행해 확인 (동작 확인용 smoke test)
- `diff_loss` (LLaDA 방식 masked diffusion SFT): 단위테스트 5건 통과
  (`tests/test_diff_loss.py`) — target 토큰만 mask, 샘플당 최소 1개 mask,
  oracle이면 loss ≈ 0, seed 고정 시 재현
- DiffuCoder config: `mask_token_id=151666`, `auto_map: AutoModel→DreamModel`
  → train_lora.py의 모델 로드와 mask id 탐색 로직이 그대로 맞음
- 데이터 파이프라인: Gate 0 통과 (matching yield 83.7%, 기준 60% 이상),
  skeleton 변환 성공률 99.9%

## 4. Gate 1 — pilot (본 학습 전 필수)

DiffuCoder QLoRA로 200 step 정도. 통과 기준: **loss가 뚜렷하게 감소하고 VRAM이 예산 안**.

```bash
# diffusion arm
.venv/bin/python src/train_lora.py --arm diff \
  --data data/matched/pilot2k_balanced.jsonl --out checkpoints/diff_pilot \
  --seed 0 --max-steps 200 --seq-len 2048
# AR arm (대조)
.venv/bin/python src/train_lora.py --arm ar \
  --data data/matched/pilot2k_balanced.jsonl --out checkpoints/ar_pilot \
  --seed 0 --max-steps 200 --seq-len 2048
```

- 24GB GPU(4090 등)라면 계획서 원안대로 VRAM 기준 20GB 이하. 여유가 보이면
  `--seq-len 4096`으로 올리되 두 arm에 똑같이 적용.
- 12GB GPU라면 seq 2048 유지, VRAM 기준 11.5GB 이하.
- loss와 VRAM은 step마다 `<out>/train_log.jsonl`에 기록된다.
- diff arm 첫 step에서 `model(...).logits`가 나오는지 확인할 것. DreamModel의
  forward가 logits를 반환해야 하는데, 만약 시그니처가 다르면 `diff_loss`의
  모델 호출부를 remote code에 맞게 고쳐야 한다.

## 5. 본 학습 — 4런 (Gate 1 통과 후)

```bash
for seed in 0 1; do
  .venv/bin/python src/train_lora.py --arm diff --data data/matched/balanced_train.jsonl \
    --out checkpoints/diff_s$seed --seed $seed --max-steps 2000 --seq-len <pilot에서 정한 값>
  .venv/bin/python src/train_lora.py --arm ar --data data/matched/balanced_train.jsonl \
    --out checkpoints/ar_s$seed --seed $seed --max-steps 2000 --seq-len <같은 값>
done
```

- LoRA r=32 / alpha=64, 대상 모듈 q/k/v/o/gate/up/down — 두 arm 동일 (스크립트 기본값).
- 런마다 `run_config.json`이 자동 저장된다. checkpoint는 save_every(기본 100) step마다.
- 학습셋을 계획 목표인 100k로 늘리려면: volume 추가 다운로드 →
  `select_miniset.py`(opt별) → `run_ghidra_batch.sh` → `match_functions.py` →
  `build_dataset.py` 순서 그대로. 지금 17.8k는 opt별 첫 volume에서 프로젝트당
  바이너리 1개씩만 처리한 결과다. 실측 기준 바이너리당 usable 약 600쌍,
  Ghidra 3병렬에서 바이너리당 약 1.5분이므로, opt별 40개쯤 추가하면 100k에
  도달한다 (Ghidra 2–3시간 + 필요시 volume 002/006/011/015 다운로드 8GB).

## 6. 학습이 끝나면 가져올 것

- `checkpoints/*/step*/` — LoRA adapter (런당 수십 MB)
- `checkpoints/*/train_log.jsonl`, `run_config.json`

이후 Gate 3(skeleton parse rate 80% 이상)과 2단계 평가(filler model,
re-executability)는 평가 harness를 만든 다음 진행한다. 평가셋은 decompile-eval에
들어 있는 ghidra_pseudo를 그대로 쓴다.

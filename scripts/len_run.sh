#!/bin/bash
# 길이 실험 생성: diff(tps1) + ar, 둘 다 bf16/gen_len512, heldout_O0_240
source /root/.venv/bin/activate
cd /root/Pavlov
export HF_HOME=/root/.cache/huggingface
mkdir -p results logs
EVAL=data/length_exp/heldout_O0_240.json
: > logs/len.out

echo "DIFF start $(date -u)" >> logs/len.out
python src/eval_generate.py --arm diff --adapter /root/adapters/diff_s0/step2000 \
  --eval-json $EVAL --out results/len_diff_gen.jsonl \
  --quant bf16 --max-new-tokens 512 --tokens-per-step 1 > logs/len_diff.out 2>&1
echo "DIFF_DONE $(date -u)" >> logs/len.out

echo "AR start $(date -u)" >> logs/len.out
python src/eval_generate.py --arm ar --adapter /root/adapters/ar_s0/step2000 \
  --eval-json $EVAL --out results/len_ar_gen.jsonl \
  --quant bf16 --max-new-tokens 512 > logs/len_ar.out 2>&1
echo "AR_DONE $(date -u)" >> logs/len.out

echo "ALL_DONE $(date -u)" >> logs/len.out

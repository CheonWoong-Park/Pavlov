# 평가

학습된 두 arm을 같은 자로 재서 비교한다. 평가는 추론만 하므로 소 VRAM에서도 된다
(7B bf16 ~14GB, nf4 ~6GB). 평가셋은 `tools/LLM4Decompile/legacy-test/`의
`decompile-eval-executable-gcc-ghidra.json` (humaneval 656건 = 164 함수 × O0–O3).
각 항목: `input_asm_prompt`(Ghidra pseudocode, 입력), `c_func`(정답 C), `c_test`(assert
기반 테스트 main), `type`(O0–O3).

흐름:

```
[eval_generate] skeleton 생성 ──┬─[eval_gate3]   parse rate (Gate 3)
                                └─[eval_filler] → C 복원 ─[eval_reexec] re-executability + 위반율
```

두 arm(diff, ar)에 **동일 precision·동일 설정**으로 돌려야 비교가 공정하다. 학습이 bf16
이었으므로 평가도 bf16이 이상적이다(소 VRAM이면 두 arm 모두 nf4).

## 1. skeleton 생성 — `eval_generate.py`

base + adapter를 로드해 각 항목에서 skeleton을 생성한다. ar arm은 AR 디코딩, diff arm은
`DreamModel.diffusion_generate`(iterative denoising; `--steps`가 품질/속도 트레이드오프).
프롬프트는 학습과 동일(`### Pseudocode:` … `### Skeleton:`).

```bash
PY=.venv-eval/bin/python
EVAL=tools/LLM4Decompile/legacy-test/decompile-eval-executable-gcc-ghidra.json

$PY src/eval_generate.py --arm diff --adapter checkpoints_from_a100/diff_s0/step2000 \
    --eval-json $EVAL --out results/diff_gen.jsonl --quant nf4 --steps 256
$PY src/eval_generate.py --arm ar --adapter checkpoints_from_a100/ar_s0/step2000 \
    --eval-json $EVAL --out results/ar_gen.jsonl --quant nf4
```

출력: `{task_id, type, skeleton}`. `--limit N`으로 소규모 스모크 먼저 돌려 아이템당 시간을
잰다(diff arm은 denoising step×forward라 느릴 수 있음).

## 2. Gate 3 — parse rate — `eval_gate3.py`

생성된 skeleton이 C로 parse되는 비율(`anonymize.parses_clean`, tree-sitter ERROR/MISSING
없음). Gate 3 통과 기준은 80% 이상.

```bash
$PY src/eval_gate3.py results/diff_gen.jsonl results/ar_gen.jsonl
```

전체 및 opt별 parse rate와 PASS/FAIL을 출력한다.

## 3. filler — `eval_filler.py`

frozen `Qwen2.5-Coder-7B-Instruct`가 skeleton + 원본 pseudocode를 받아 placeholder를 채워
compilable C를 만든다. 학습하지 않고 두 arm에 동일 적용한다. 테스트 harness와 링크되도록
entry 함수를 `func0`로 복원하게 한다.

```bash
$PY src/eval_filler.py --gen results/diff_gen.jsonl --eval-json $EVAL \
    --out results/diff_filled.jsonl --quant nf4
$PY src/eval_filler.py --gen results/ar_gen.jsonl --eval-json $EVAL \
    --out results/ar_filled.jsonl --quant nf4
```

출력: `{task_id, type, code}`.

## 4. re-executability + skeleton 위반율 — `eval_reexec.py`

filler가 만든 C를 `c_test`와 함께 컴파일·실행해 assert 통과(exit 0) 비율을 잰다. 동시에
filler 출력을 다시 anonymize해 1단계 skeleton과 비교, filler가 구조를 바꾼 비율(위반율)을
집계한다.

```bash
$PY src/eval_reexec.py --filled results/diff_filled.jsonl --gen results/diff_gen.jsonl \
    --eval-json $EVAL --out results/diff_reexec.jsonl
$PY src/eval_reexec.py --filled results/ar_filled.jsonl --gen results/ar_gen.jsonl \
    --eval-json $EVAL --out results/ar_reexec.jsonl
```

전체·opt별 re-executability, status breakdown(pass/run_fail/compile_error/timeout),
skeleton 위반율을 출력한다.

> **격리 필수.** 이 단계는 모델이 만든 코드를 컴파일·실행한다. timeout과 CPU/메모리/파일
> 크기 rlimit이 걸려 있지만, **반드시 격리된 머신(컨테이너/VM)에서만** 돌린다. `gcc` 필요.

## 지표 요약

| 지표 | 스크립트 | 단계 |
|---|---|---|
| parse rate (Gate 3) | `eval_gate3.py` | skeleton 단독 |
| AST edit distance | (예정) | skeleton 단독 |
| re-executability | `eval_reexec.py` | end-to-end |
| skeleton 위반율 | `eval_reexec.py` | 분해 건전성 |

## sanity check

본 비교 전에 B0(Ghidra 원본)·B1(Instruct 직접 생성)을 같은 harness로 재서 공개 보고치와
대조한다(전통 도구 R2I ~40, LLM 디컴파일러 ~60–70, LLM4Decompile re-executability ~39%).
크게 어긋나면 harness를 점검한 뒤 본실험에 들어간다.

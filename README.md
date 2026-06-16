# Pavlov: AST 구조 복원을 위한 Diffusion 기반 Decompilation

Ghidra가 내놓는 pseudocode를 읽을 만한 C로 복원하는 문제를 두 단계로 나눈다. 핵심은
AST 구조를 복원하는 1단계이고, 모델이 만드는 **anonymized skeleton**은 이름과 literal을
지워 구조만 남긴, AST와 1:1로 대응되는 텍스트 표현이다. 이 1단계를 **diffusion LLM과
AR LLM으로 각각 학습해 같은 조건에서 비교**하는 것이 연구의 목적이다.

```
Ghidra pseudocode
   └─[1단계: skeleton 복원기 — diffusion vs AR, 학습 대상]→ anonymized skeleton
        └─[2단계: filler — frozen Instruct, 비교 변인 아님]→ 복원된 C
```

- **Stage 1 (skeleton 복원)**: pseudocode → anonymized skeleton. 식별자는 `VAR_n`/`FUNC_n`,
  literal은 `INT_LIT` 등 placeholder로 치환된 구조만 복원. diffusion arm과 AR arm을 동일
  backbone 계열·동일 LoRA 예산·동일 데이터로 학습해 비교한다.
- **Stage 2 (filler)**: skeleton의 placeholder를 실제 이름/값으로 채움. frozen instruct
  모델이라 두 arm에 동일하게 적용되어, 결과 차이를 1단계로 귀속시킨다.

## 문서

| 문서 | 내용 |
|---|---|
| [`docs/research.md`](docs/research.md) | 연구 개요와 방법 — 배경, RQ, 데이터 전처리·학습·평가 전 과정, 실험 설계, 한계 |
| [`docs/data_pipeline.md`](docs/data_pipeline.md) | 데이터 파이프라인 — 추출 → Ghidra → matching → anonymization → 학습셋 |
| [`docs/training.md`](docs/training.md) | 학습 재현 — 환경, 커맨드, 하이퍼파라미터 |
| [`docs/evaluation.md`](docs/evaluation.md) | 평가 harness — 생성, Gate 3, filler, re-executability |

## 클론

`tools/LLM4Decompile`는 submodule이다.

```bash
git clone --recurse-submodules <repo-url>
git submodule update --init --recursive   # 이미 clone한 경우
```

학습된 LoRA adapter는 GitHub Release `v0.1-adapters`에 보관한다 (weight는 git 본문에
넣지 않음). `data/matched/*.jsonl` 학습셋은 저장소에 포함하고, raw 바이너리·Ghidra
pseudocode·대용량 캐시는 `.gitignore`로 제외한다.

## 디렉토리 구조

```
pavlov/
├── README.md
├── requirements-lock.txt    # 의존성 lock
├── docs/research.md         # 연구 개요와 방법 (배경·RQ·데이터·학습·평가·한계)
├── src/
│   ├── zipsplit_extract.py  # split zip 부분 추출 (Zip64 central directory 파싱)
│   ├── select_miniset.py    # 바이너리 선정·추출 (opt별, 프로젝트 단위)
│   ├── match_functions.py   # Ghidra pseudocode ↔ dataset source 함수 matching
│   ├── anonymize.py         # tree-sitter-c anonymizer (source → skeleton, parses_clean)
│   ├── build_dataset.py     # matched pair → 학습 예제 (parse/길이 필터)
│   ├── train_lora.py        # 1단계 학습 (--arm ar: CE / --arm diff: masked diffusion)
│   ├── eval_generate.py     # 평가: skeleton 생성 (ar=AR 디코딩, diff=diffusion sampling)
│   ├── eval_gate3.py        # 평가: skeleton parse rate (Gate 3)
│   ├── eval_filler.py       # 평가: 2단계 filler (frozen Instruct, skeleton → C)
│   └── eval_reexec.py       # 평가: re-executability + skeleton 위반율 (격리 실행)
├── scripts/
│   ├── ExportPseudoC.java   # Ghidra headless postScript (함수별 pseudocode → jsonl)
│   └── run_ghidra_batch.sh  # analyzeHeadless 배치 러너 (병렬·timeout·skip-existing)
├── tests/test_diff_loss.py  # masked diffusion loss 단위테스트
├── data/matched/            # matching·학습셋 산출물 (jsonl)
├── checkpoints_from_a100/   # 학습된 adapter 회수본 (weight는 Release에 백업)
└── tools/LLM4Decompile/     # 평가 스크립트·벤치마크 submodule
```

## 파이프라인 한눈에

```bash
# 1. 데이터 구축       → docs/data_pipeline.md
# 2. 1단계 학습         → docs/training.md
#    diff_s0 (DiffuCoder-7B masked diffusion) + ar_s0 (Qwen2.5-Coder-7B AR)
# 3. 평가               → docs/evaluation.md
#    생성 → Gate 3(parse rate) → filler → re-executability
```

## 원칙

- 모든 런에 seed·config 기록 (`run_config.json` 자동 저장), checkpoint 매 런 저장.
- **외부 바이너리 실행(re-executability)은 격리 환경 + timeout + 리소스 제한 필수.**
  Ghidra 추출은 정적 분석만 하며 바이너리를 실행하지 않는다.
- 비용이 큰 작업 전 소규모 pilot을 먼저 돌린다.
- 가설과 반대되는 결과도 해석 가설과 함께 보고한다.

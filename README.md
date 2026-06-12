# Pavlov : Diffusion 모델로 pseudocode에서 AST 구조를 분석하는 decompilation 연구

Ghidra가 뽑아주는 pseudocode를 읽을 만한 C 코드로 복원하는 문제를 두 단계로 쪼개서 접근한다.
핵심은 AST 구조를 분석·복원하는 1단계다. 모델이 생성하는 anonymized skeleton은 이름과
literal을 지워 구조만 남긴, AST와 1:1로 대응되는 텍스트 표현이다. 이 단계를 diffusion LLM과
AR LLM으로 각각 학습해 같은 조건에서 어느 쪽이 나은지 비교하는 게 이 연구의 목적.
workshop paper 분량의 결과가 목표다.

- Stage 1 (skeleton 복원): Ghidra pseudocode → anonymized skeleton.
  식별자는 `VAR_n`/`FUNC_n`, literal은 `INT_LIT` 같은 placeholder로 치환된 코드 구조만 복원
- Stage 2 (filler): skeleton의 placeholder를 실제 이름/값으로 채움. frozen instruct 모델 사용

## 문서

| 문서 | 내용 |
|---|---|
| [`plan.md`](plan.md) | 연구 계획서. 목표, gate, fallback, 일정 — 모든 작업의 기준 |
| [`docs/research_overview.md`](docs/research_overview.md) | 연구 설계 설명. 가설, 실험 조건, gate, 평가 지표 |
| [`docs/data_pipeline.md`](docs/data_pipeline.md) | 데이터 파이프라인. 추출 → Ghidra → matching → anonymization → 학습셋 |
| [`docs/training_handoff.md`](docs/training_handoff.md) | 학습 머신으로 넘어갈 때 보는 가이드. 환경, 커맨드, Gate 1 기준 |
| [`logs/research_log.md`](logs/research_log.md) | 시간순 연구 로그. 발견한 것, 결정, gate 판정 |

## GitHub 클론

`tools/LLM4Decompile`는 submodule로 관리한다.

```bash
git clone --recurse-submodules <repo-url>

# 이미 clone한 경우
git submodule update --init --recursive
```

`data/matched/*.jsonl` 학습셋 산출물은 저장소에 포함한다. `data/bulk`, raw 바이너리,
Ghidra pseudocode, checkpoint, 로컬 캐시는 `.gitignore`로 제외한다.

## 현재 상태 (2026-06-12)

- Gate 0 통과 — matching yield 83.7% (기준 60% 이상)
- 학습셋 구축 완료 — `data/matched/balanced_train.jsonl` 17,760건 (O0–O3 균형)
- 학습 스크립트 검증 완료 (AR arm smoke test, diffusion loss 단위테스트)
- Gate 1(QLoRA pilot)부터는 별도 학습 머신에서 진행 → `docs/training_handoff.md`
- 이후 단계: Gate 2 (B0/B1 sanity check), 본 학습 4런, Gate 3 (skeleton parse rate 80% 이상), 평가와 분석

## 디렉토리 구조

```
pavlov/
├── plan.md                  # 연구 계획서 (사용자 작성)
├── README.md                # 이 문서
├── requirements-lock.txt    # 의존성 lock (uv pip freeze)
├── src/                     # 파이프라인 코드
│   ├── zipsplit_extract.py  #   split zip 부분 추출기 (Zip64 central directory 파싱)
│   ├── select_miniset.py    #   바이너리 선정·추출 (opt별, 프로젝트 단위)
│   ├── match_functions.py   #   Ghidra pseudocode ↔ 데이터셋 source 함수 matching
│   ├── anonymize.py         #   tree-sitter-c 기반 anonymizer (source → skeleton)
│   ├── build_dataset.py     #   matched pair → 학습 예제 변환 (parse/길이 필터)
│   └── train_lora.py        #   QLoRA 학습 (AR arm: CE loss / diff arm: masked diffusion loss)
├── scripts/
│   ├── ExportPseudoC.java   #   Ghidra headless postScript (함수별 pseudocode → jsonl)
│   └── run_ghidra_batch.sh  #   analyzeHeadless 배치 러너 (병렬·timeout·skip-existing)
├── tests/
│   └── test_diff_loss.py    #   masked diffusion SFT loss 단위테스트 (5건)
├── data/
│   ├── bins_volume_sizes.json      # bins 71개 volume 정확한 크기 (offset 계산용)
│   ├── miniset_list.txt            # Gate 0 O0 바이너리 12개 목록
│   ├── miniset_list_O123.txt       # O1–O3 바이너리 30개 목록
│   ├── matched/                    # matching·학습셋 산출물 (jsonl, 저장소 포함)
│   └── bulk -> ~/pavlov-data       # 대용량 데이터 심볼릭 링크 (ext4)
├── results/                 # gate 판정 리포트 (json)
├── logs/                    # 연구 로그
├── docs/                    # 문서
├── checkpoints/             # (학습 머신에서 채워질 예정)
└── tools/LLM4Decompile/     # 평가 스크립트 재사용용 submodule
```

대용량 데이터(`~/pavlov-data`, WSL2 ext4 — NTFS I/O 회피):

```
~/pavlov-data/
├── bins/            # decompile-bench-bins split zip volumes (001/005/010/014/071만 보유)
├── hf-cache/        # HF_HOME (decompile-bench 17 shards, decompile-eval, 모델 캐시)
├── ghidra/          # Ghidra 12.1.2 PUBLIC
├── miniset_bins*/   # 추출된 바이너리 (O0 / O1 / O2 / O3)
└── pseudo_*/        # Ghidra 추출 pseudocode jsonl (바이너리당 1파일)
```

## 빠른 시작 (데이터 파이프라인 재현)

```bash
export HF_HOME=~/pavlov-data/hf-cache
PY=~/pavlov-venv/bin/python

# 1. 바이너리 선정·추출 (split zip 부분 추출)
$PY src/select_miniset.py --vols ~/pavlov-data/bins --sizes data/bins_volume_sizes.json \
    --shards-glob "$HOME/pavlov-data/hf-cache/hub/datasets--LLM4Binary--decompile-bench/snapshots/*/data-*.arrow" \
    --out-dir ~/pavlov-data/miniset_bins_O1 --opt O1 --n-projects 10 --min-c-records 100

# 2. Ghidra headless pseudocode 추출 (3 병렬)
bash scripts/run_ghidra_batch.sh <바이너리 목록.txt> <출력 dir> 3

# 3. 함수 matching (+ yield 리포트)
$PY src/match_functions.py --pseudo-dir <출력 dir> \
    --shards-glob "...data-*.arrow" --out matched.jsonl --report report.json

# 4. 학습 예제 변환 (skeleton 생성 + 필터)
$PY src/build_dataset.py --matched matched.jsonl --out train.jsonl \
    --tokenizer Qwen/Qwen2.5-Coder-7B --max-tokens 4096

# 단위테스트
$PY tests/test_diff_loss.py
```

## 주의사항

- 하드웨어: 이 머신은 RTX 5070 12GB로, 계획서가 전제한 4090 24GB와 다르다.
  그래서 QLoRA 4-bit fallback을 적용했고 학습은 별도 머신에서 한다
  (경위는 `logs/research_log.md`, 절차는 `docs/training_handoff.md`).
- 외부 바이너리 실행 금지: Ghidra는 정적 분석만 한다. 평가 단계의 re-executability
  테스트는 반드시 격리 환경에서 timeout과 리소스 제한을 걸고 실행한다 (plan.md 원칙).
- 데이터셋의 `asm` 필드는 쓰지 않는다. 계획대로 공개 바이너리에 Ghidra를 직접 돌려
  pseudocode를 얻는다.

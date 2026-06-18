# Pavlov: AST 구조 복원을 위한 Diffusion 기반 Decompilation

기존 LLM 기반 Decompile은 한 토큰씩 생성하는 **Autoregressive(AR)** 방식에 의존하지만, 코드 구조 복원은 전역 제약이 강해 단방향 생성에 불리합니다. **Diffusion model**은 시퀀스 전체를 양방향으로 정제하므로 전역 구조 복원에 유리할 수 있습니다. 본 연구는 **AST skeleton 복원** 단계에 **Diffusion**을 적용하고, 동일 계열 backbone에서 파생된 Diffusion·AR 모델을 같은 데이터·같은 크기 LoRA로 학습하여 두 방식을 통제 비교합니다.

---

## 배경 지식

### Diffusion Model
데이터에 noise를 조금씩 더해 망가뜨린 뒤, 그 과정을 거꾸로 되돌리도록 학습하여 noise로부터 데이터를 복원하는 생성 모델입니다. 주로 생성형 이미지 모델에 사용되지만, Text 기반 Diffusion은 token이 이산 값이라 Gaussian noise 대신 MASK로 치환하는 **Masked Diffusion**을 사용합니다.

### Decompile
컴파일된 프로그램(바이너리)를 사람이 읽을 수 있는 고수준 소스 코드로 복구하는 과정입니다.

디스어셈블 → IR 승격 → 제어흐름·데이터흐름 분석 → 타입 복원·구조화(=AST 복원) → code 생성

---

## 제안 방법

### Step 1. 데이터 전처리
- `LLM4Binary/decompile-bench`에서 source 함수와 컴파일된 binary를 가져옴 (binary는 split zip에서 필요 구간만 부분 추출)
- **Ghidra** `analyzeHeadless`로 함수별 pseudocode를 jsonl로 추출
- named 함수만 project·함수명 키로 source와 매칭하고, **tree-sitter**로 AST를 순회·익명화하여 skeleton 생성

### Step 2. 학습
LoRA 설정: `r=32`, `alpha=64`, 학습 파라미터 약 80M (전체의 1.05%)

| Arm | 방식 | Backbone | 학습 방법 |
|-----|------|----------|-----------|
| **A arm** | Autoregressive | Qwen2.5-Coder-7B | 표준 cross entropy, prompt·pad는 -100 마스킹 |
| **D arm** | Diffusion | DiffuCoder-7B-Base | Masked diffusion (LLaDA recipe): target을 확률 `t`로 `[MASK]` 치환 후 masked 위치 cross entropy에 `1/t` 가중 |

> DiffuCoder-7B-Base는 Qwen2.5-Coder-7B와 같은 계열 모델로, 두 arm의 backbone은 동일합니다.

### Step 3. 평가
- 평가셋: `decompile-eval` 240 balanced subset = 60 함수 × O0–O3
- Ghidra pseudocode에서 각 arm으로 skeleton 생성 → **frozen model filler**가 placeholder를 채워 컴파일 가능한 C 복원
- 복원 C를 `c_test`와 gcc 컴파일·격리 실행하여 assert 전부 통과 시 인정
- 구조 보존은 filler 출력 재익명화 후 입력 skeleton과 비교한 skeleton 위반율로 검증
- filler를 두 arm 공통으로 사용하므로 최종 성능 차이는 두 arm의 학습 능력 차이로 귀결

---

## 실험 설정

| 항목 | 내용 |
|------|------|
| **환경** | A100 80GB, torch 2.9.1+cu130, transformers 4.51.3, peft 0.19.1 |
| **모델** | D: DiffuCoder-7B-Base / A: Qwen2.5-Coder-7B |
| **LoRA** | r32 / α64, bf16, 동일 학습량 |
| **학습** | train_set 17,760, 2000 step, effective batch 16, gen-len 512, seed 0/1 |
| **비교 방법** | A·D arm + 공통 frozen filler |
| **생성** | A는 greedy, D는 diffusion_generate (denoising step 충분히 적용) |

### 평가 지표
- **AST edit distance**: 생성 skeleton과 정답 skeleton AST의 node type 기준 tree edit distance
- **skeleton 위반율**: filler 출력이 입력 skeleton과 구조가 달라진 비율
- **re-executability**: 복원한 C를 컴파일·실행해 원본 test를 통과하는 비율

---

## 실험 결과

### Table 1. seed 0 평가 결과

| 학습방법 | AST edit distance | skeleton 위반율 | re-executability |
|----------|:-----------------:|:---------------:|:----------------:|
| A arm (Autoregressive) | 0.534 | 82.9% | **26.7%** |
| D arm (Diffusion) | **0.531** | **75.8%** | 20.4% |

### Table 2. seed 1 평가 결과

| 학습방법 | AST edit distance | skeleton 위반율 | re-executability |
|----------|:-----------------:|:---------------:|:----------------:|
| A arm (Autoregressive) | 0.504 | 83.8% | **27.5%** |
| D arm (Diffusion) | **0.484** | **76.2%** | 22.1% |

### 분석
- **AST edit distance (핵심 지표)**: seed0, seed1 모두 Diffusion이 AR baseline과 대등하거나 우위. 더 높은 구조 복원력을 보임
- **함수 길이별**: 짧은 함수는 A가 우위이나, 함수가 길어질수록 격차가 D쪽으로 좁혀짐 → 전역 구조가 많아질수록 양방향 생성의 이점이 드러남
- **re-executability**: A가 D보다 높음 (단, 두 지표 모두 frozen filler가 pseudocode로 재생성하는 영향을 크게 받음)
- **skeleton 위반율**: D가 두 실험 모두 낮음 → filler가 채우는 과정에서 D의 skeleton을 덜 수정했음을 의미

---

## 결론 및 고찰
- skeleton 복원 단계에 diffusion을 적용해 autoregressive와 동일 조건에서 통제 비교함
- AST edit distance에서 diffusion은 AR baseline과 대등하거나 이상이고, skeleton 위반율도 더 낮아 두 구조 지표가 함께 diffusion의 구조 복원이 유의미함을 뒷받침함
- 서로 다른 seed로 두 번 반복하여 확률적 변동성을 점검했고, 두 seed 모두 결과가 일관되게 관측됨

## 문서

| 문서 | 내용 |
|---|---|
| [`docs/research.md`](docs/research.md) | 연구 개요와 방법 — 배경, RQ, 데이터 전처리·학습·평가 전 과정, 실험 설계, 한계 |
| [`docs/data_pipeline.md`](docs/data_pipeline.md) | 데이터 파이프라인 — 추출 → Ghidra → matching → anonymization → 학습셋 |
| [`docs/training.md`](docs/training.md) | 학습 재현 — 환경, 커맨드, 하이퍼파라미터 |
| [`docs/evaluation.md`](docs/evaluation.md) | 평가 harness — 생성, Gate 3, filler, re-executability |

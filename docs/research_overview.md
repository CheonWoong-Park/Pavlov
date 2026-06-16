# 설계 개요

전체 계획은 [`../plan.md`](../plan.md). 이 문서는 설계를 한눈에 보는 요약이다.

## 문제와 가설

Ghidra decompiler의 pseudocode는 컴파일은 되지만 가독성이 낮다(무의미한 변수명, 캐스트
범벅, goto 등). LLM decompilation은 보통 pseudocode → source를 한 번에 생성하는데, 이
과제는 성격이 다른 두 하위 문제의 결합이다:

1. **구조 복원** — 제어 흐름·식 구조를 재구성 (전역적, 구조적)
2. **이름 복원** — 변수/함수 이름과 literal 의미 부여 (국소적, 지식 의존적)

**가설**: 둘을 분리하면 각 단계에 더 적합한 모델을 쓸 수 있다. 특히 구조 복원은 출력
전체를 양방향으로 정제하는 **diffusion LLM**이 left-to-right로 한 토큰씩 확정하는
**AR LLM**보다 유리할 수 있다. 이를 같은 backbone 계열·adapter 예산·데이터로 통제 비교한다.

## 2단계 분해

```
Ghidra pseudocode ─[1단계: 학습 모델]→ anonymized skeleton ─[2단계: frozen filler]→ C
```

### 1단계 — skeleton 복원 (비교 대상)

- 입력: Ghidra pseudocode → 출력: anonymized skeleton (식별자·literal을 placeholder로
  치환, 구조만 남김). placeholder가 유효한 C 식별자라 skeleton도 parse 가능하고 AST와
  1:1 대응된다.
- 두 arm 모두 LoRA(r=32, α=64), backbone만 다름:
  - **D arm**: `apple/DiffuCoder-7B-Base` + masked diffusion SFT (LLaDA recipe)
  - **A arm**: `Qwen/Qwen2.5-Coder-7B` + 표준 CE

### 2단계 — filler (고정, 비교 변인 아님)

- `Qwen/Qwen2.5-Coder-7B-Instruct` frozen. skeleton + 원본 pseudocode를 주고 placeholder를
  실제 이름/값으로 채운다. 두 arm에 동일 적용되므로 1단계 차이만 결과에 반영된다.

## 실험 조건

| 조건 | 내용 | 역할 |
|---|---|---|
| B0 | Ghidra pseudocode 그대로 | 하한 baseline |
| B1 | Instruct가 pseudocode→C 직접 생성 | 비분해 baseline (RQ2) |
| A | AR skeleton + filler | 분해 + AR |
| D | diffusion skeleton + filler | 분해 + diffusion (핵심 비교) |

## 평가 지표

- skeleton 단독: parse rate(Gate 3), AST edit distance, 구조 일치도
- end-to-end: re-executability, skeleton 위반율
- 비용: 학습/추론 시간·VRAM, diffusion denoising step 수
- 통계: 조건×함수 paired bootstrap

## 알려진 제약

- **단일 언어(C)**: anonymizer·가독성 지표를 C로 통일. 한계 절 명시.
- **DiffuCoder의 추가 사전학습**: D는 Qwen2.5-Coder를 masked diffusion으로 적응시킨
  모델로, adaptation 단계의 continued-pretraining(약 130B token)이 두 arm 간 통제되지
  않는 변인이다 → 주장을 model/paradigm 레벨로 한정하고 한계 절에 정량 명시.
- **filler quantization**: frozen이라 모든 조건에 동일 적용 → 공정성엔 무영향, 한계 절 명시.

# 연구 개요

## 1. 문제 의식과 가설

Ghidra 같은 decompiler가 내놓는 pseudocode는 컴파일은 가능해도 가독성이 낮다
(의미 없는 변수명 `uVar1`, 타입 캐스트 범벅, goto 등). LLM 기반 decompilation
연구들은 pseudocode → source를 한 번에 생성하는데, 이 과제는 사실 성격이 다른
두 하위 문제의 결합이다:

1. **구조 복원** — 제어 흐름·식 구조를 사람이 쓰는 형태로 재구성 (전역적, 구조적)
2. **이름 복원** — 변수/함수 이름과 literal 의미 부여 (국소적, 지식 의존적)

**핵심 가설**: 이 둘을 분리하면 각 단계에 더 적합한 모델을 쓸 수 있다. 특히
구조 복원은 출력 전체를 동시에 다듬는 **diffusion LLM**의 생성 방식이
left-to-right로 한 토큰씩 확정하는 **AR LLM**보다 유리할 수 있다.
이를 같은 backbone 규모·같은 adapter 예산·같은 데이터로 통제 비교한다.

## 2. 2단계 분해 설계

```
Ghidra pseudocode ──[Stage 1: 학습 모델]──> anonymized skeleton ──[Stage 2: frozen filler]──> C source
```

### Stage 1 — skeleton 복원 (이 연구의 비교 대상)

- 입력: Ghidra pseudocode
- 출력: **anonymized skeleton** — 원본 source에서 식별자·literal을 placeholder로
  치환한 것. 구조(제어 흐름, 식, 선언)만 남는다.
  - 식별자: `VAR_0, VAR_1, …` / 함수: `FUNC_0, …` / 타입: `TYPE_0, …` /
    필드: `FIELD_0, …` / label: `LABEL_0, …`
  - literal: `INT_LIT`, `FLOAT_LIT`, `STR_LIT`, `CHAR_LIT`
  - placeholder는 모두 유효한 C 식별자 → skeleton도 tree-sitter로 parse 가능
  - 즉 skeleton은 AST와 1:1로 대응되는 텍스트 표현이고, 이 단계는 사실상
    pseudocode에서 AST 구조를 분석해내는 작업이다
- 학습: 두 arm 모두 LoRA(r=32, α=64) — backbone만 다름
  - **D arm**: `apple/DiffuCoder-7B-Base` + masked diffusion SFT loss (LLaDA recipe)
  - **A arm**: `Qwen/Qwen2.5-Coder-7B` + 표준 CE loss
  - seed 2개씩 → 총 4런

### Stage 2 — filler (고정, 비교 변인 아님)

- `Qwen/Qwen2.5-Coder-7B-Instruct` **frozen** — 학습하지 않음
- skeleton + 원본 pseudocode를 주고 placeholder를 실제 이름/값으로 채우게 함
- 모든 조건에 동일하게 적용되므로 Stage 1 차이만 결과에 반영됨

## 3. 실험 조건

| 조건 | 내용 | 역할 |
|---|---|---|
| **B0** | Ghidra pseudocode 그대로 | 하한 baseline |
| **B1** | Instruct 모델이 pseudocode→source 직접 생성 | 1단계(비분해) baseline |
| **A** | AR arm skeleton + filler | 분해 + AR |
| **D** | Diffusion arm skeleton + filler | 분해 + diffusion (핵심 비교 대상) |
| B2 | (선택) gold skeleton + filler | 분해 상한 (oracle) |

## 4. 데이터

- **학습**: `LLM4Binary/decompile-bench` — 공개된 바이너리(140GB, split zip)에
  **Ghidra headless를 직접 실행**해 pseudocode를 얻고, demangled 함수 이름으로
  데이터셋의 source 함수와 matching. C 함수만. O0–O3 균형. seq ≤4096 token.
  (계획 목표 100k; 현재 17,760건 구축, 확장 경로는 `data_pipeline.md` 참고)
- **평가**: `LLM4Binary/decompile-eval`에서 600건을 stratified sampling으로 추출.
  `ghidra_pseudo`/`opt`/test가 이미 포함되어 있어 평가용으로 Ghidra를 돌릴 필요가 없다.
  - HumanEval/MBPP split: **re-executability** (테스트 통과율) — 격리 환경 필수
  - GitHub2025 split: 학습 데이터 누출(leakage)에서 안전한 분석용 — 별도로 보고

## 5. 평가 지표

- skeleton 지표: parse rate, 구조 일치도
- end-to-end: re-executability, R2I
- 통계: seed×조건 paired bootstrap
- 비용: 학습/추론 시간·VRAM
- skeleton 위반율: filler가 채우기만 하지 않고 구조를 바꿔버린 비율
- sanity check 기준치 (발표된 수치와 크게 어긋나지 않는지 확인용):
  IDA R2I 약 40, LLM R2I 60–70, LLM4Decompile re-executability 약 39%

## 6. Gate와 fallback (plan.md 준수)

| Gate | 기준 | 미달 시 fallback | 상태 |
|---|---|---|---|
| **Gate 0** | matching yield 60% 이상 | matching 기준 완화, 추출 범위 확대 | 통과 (O0 83.7%, O1–O3 68.6%) |
| **Gate 1** | LoRA loss 감소 + VRAM 예산 내 | r16 → QLoRA | 학습 머신에서 진행 예정 (QLoRA는 하드웨어 제약으로 먼저 적용됨) |
| **Gate 2** | B0/B1 결과가 발표된 수치와 비슷한 수준인지 | 평가 harness 점검 | 대기 |
| **Gate 3** | 두 arm 모두 skeleton parse rate 80% 이상 | decoding/후처리 점검 | 대기 (전처리 쪽 선행 지표는 99.9%) |

게이트 미달 시: **진행을 멈추고 fallback 적용 후 보고** (연구 수행 원칙).

## 7. 연구 수행 원칙 (plan.md)

- 모든 런에 seed·config·commit hash 기록 (`run_config.json` 자동 생성)
- 체크포인트 매 런 저장
- **외부 바이너리 실행(평가 harness)은 격리 환경 + timeout + 리소스 제한 필수**
- 비싼 작업 전 20–50 샘플 pilot 선행
- 가설과 반대되는 결과도 해석 가설과 함께 충실히 보고
- `albertan017/LLM4Decompile`의 평가 스크립트 최대한 재사용
  (`tools/LLM4Decompile/decompile-bench/metrics/`)

## 8. 알려진 제약·리스크

- **하드웨어 불일치**: 계획서는 RTX 4090 24GB 전제, 로컬 머신은 RTX 5070 12GB →
  G1 fallback(QLoRA 4-bit)을 처음부터 적용. 학습은 별도 머신에서 수행하기로 결정
  (2026-06-12). filler model도 양자화 필요 — 모든 조건에 동일 적용해 공정성 유지,
  논문 한계 절에 명시 예정.
- transformers 5.11 ↔ DiffuCoder remote code(DreamModel) 호환 미검증 —
  학습 머신에서 로드 실패 시 4.46~4.51로 다운그레이드 (양 arm 동일 버전).
- matched pair 중 source가 단독 parse되지 않는 비율 ~12% (macro/K&R 등) → 제외됨.
  duplicate key((project, func, opt))는 대부분 동일 코드, 충돌은 <1% → 제거.

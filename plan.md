# Pavlov — 연구 계획

## Diffusion 모델로 pseudocode에서 AST 구조를 분석하는 decompilation 연구
### skeleton 복원에 대한 생성 패러다임(diffusion vs AR) 통제 비교

---

## 1. 개요

Decompilation을 **skeleton 복원**(AST 구조 분석)과 **토큰 채우기**(filling)의 두 단계로
분해하고, skeleton 복원 단계를 diffusion LLM과 autoregressive(AR) LLM으로 각각 학습해
비교한다. 두 모델은 동일 backbone 계열(Qwen2.5-Coder-7B)에서 파생된 동일 크기 쌍이므로,
성능 차이를 생성 패러다임으로 귀속할 수 있다. 산출물은 workshop/단편 논문 1편.

## 2. 배경과 문제

Ghidra 등 전통 도구의 pseudocode 출력은 타입·식별자가 손실되어 가독성이 낮고, 이를
LLM으로 개선하는 연구(LLM4Decompile, SK²Decompile, ReF Decompile 등)가 활발하다. 그러나
기존 LLM 디컴파일러는 전부 토큰을 좌→우로 생성하는 AR 방식이다.

코드 복원에는 전역 제약이 많다. 함수 후반부의 변수 사용이 전반부의 타입 선언을 결정하고,
중괄호 짝과 분기 구조는 함수 전체에 걸쳐 일관되어야 한다. 단방향 생성은 뒤를 보기 전에
앞을 확정해야 하므로 이런 제약에 원리적으로 불리하다. diffusion LLM은 시퀀스 전체를
양방향으로 보며 여러 step에 걸쳐 정제하므로 전역 구조를 잡는 데 유리할 수 있으나, 세부
토큰 품질과 추론 비용에서는 AR에 밀리는 경우가 많다. diffusion LLM을 decompilation에
적용한 연구는 공백 상태다. 본 연구는 "diffusion이 잘할 만한 하위 과제(AST 구조 복원)만
골라 맡기고 그 효과를 통제 측정한다"는 방식으로 이 공백을 메운다.

## 3. 연구 질문과 가설

- **RQ1.** 동일 backbone·크기·학습 예산에서 diffusion skeleton 복원기는 AR 대비 skeleton
  품질(구문 유효성, 구조 일치)과 최종 decompilation 품질(re-executability)에서 어떤
  차이를 보이는가?
- **RQ2.** 2단계 분해(skeleton 복원 → 채우기)가 단일 단계 직접 decompilation 대비 이득이
  있는가?
- **RQ3.** diffusion과 AR의 격차는 함수 길이·중첩 깊이·컴파일러 최적화 수준에 따라 어떻게
  달라지는가?

**가설.** diffusion의 양방향 문맥과 반복 정제는 전역 제약이 빡빡한 조건(긴 함수, 깊은
중첩, 고최적화 O2/O3)에서 구조 유효율의 우위로 나타나고, 이것이 re-executability로
전이된다. 짧고 단순한 함수에서는 격차가 작거나 없을 것이다. 가설이 기각되어도 단계별
지표로 "어느 단계에서 왜 밀렸는지"를 진단할 수 있어, 결과 방향과 무관하게 "diffusion LLM이
decompilation에서 언제·왜 다르게 동작하는가"에 대한 첫 통제 분석으로 성립한다.

## 4. 관련 연구와 위치

| 분야 | 대표 연구 | 본 연구와의 관계 |
|---|---|---|
| AR LLM decompilation | LLM4Decompile, ReF Decompile, Idioms | baseline 패러다임. 평가 프로토콜(re-executability) 차용 |
| 2단계 분해 decompilation | SK²Decompile (skeleton→skin) | 분해 구조 차용. skeleton 단계의 패러다임 교체가 차별점 |
| 코드용 diffusion LLM | DiffuCoder, Dream-Coder, TreeDiff | diffusion arm의 base·학습 recipe 차용. decompilation 적용은 본 연구가 처음 |
| 데이터·벤치마크 | Decompile-Bench / decompile-eval | 데이터·평가 원천 |

## 5. 방법

### 5.1 파이프라인

```
decompile-bench 공개 바이너리 (디버그 정보 포함)
   → [Ghidra headless] → 함수 단위 pseudocode
   → [1단계: skeleton 복원기 (학습 대상)] → anonymized skeleton
   → [2단계: filler (frozen)] → 최종 C
```

입력은 Ghidra pseudocode다. 데이터셋의 `asm` 필드(어셈블리)는 쓰지 않고, 공개 바이너리에
Ghidra headless를 직접 돌려 함수 단위 pseudocode를 추출한 뒤 demangled 함수명으로 정답
소스와 matching한다.

### 5.2 skeleton 표현

tree-sitter-c로 소스를 파싱해 식별자를 `VAR_n`/`FUNC_n`/`TYPE_n`/`FIELD_n`/`LABEL_n`으로,
literal을 `INT_LIT`/`FLOAT_LIT`/`STR_LIT`/`CHAR_LIT`로 치환한 텍스트를 skeleton으로
정의한다. 제어흐름·중첩·타입 골조·연산자 구조는 보존된다. placeholder가 전부 유효한 C
식별자이므로 skeleton은 항상 다시 parse 가능하고 **AST와 1:1로 대응**된다 — 즉 1단계는
pseudocode에서 AST 구조를 분석해내는 작업이다. AST/CFG는 생성 대상이 아니라 평가 지표로만
쓴다 (`src/anonymize.py`).

### 5.3 모델과 학습 (1단계)

| 항목 | diffusion arm (D) | AR arm (A) |
|---|---|---|
| base | `apple/DiffuCoder-7B-Base` | `Qwen/Qwen2.5-Coder-7B` |
| loss | masked diffusion (LLaDA recipe: t~U(ε,1)로 target 토큰 mask, masked 위치 CE를 1/t 가중, target 길이 정규화) | 표준 CE (prompt 토큰 제외) |
| 공통 | bf16 base + LoRA r=32 / α=64 (attention+MLP 전층), gradient checkpointing, AdamW, effective batch 16 | 동일 |

DiffuCoder-7B는 Qwen2.5-Coder-7B를 masked diffusion으로 적응시킨 모델이라 두 arm은
backbone·크기·계열이 동일하다. 데이터·step·adapter 예산도 동일하게 맞춰 결과 차이의
귀속 대상을 생성 패러다임 하나로 좁힌다. 단, diffusion adaptation 단계에서 D가 받은 추가
continued-pretraining(약 130B token)은 두 arm 간 통제되지 않는 변인이므로 한계 절에
명시한다(주장은 "model/paradigm 레벨 비교"로 한정). 구현은 `src/train_lora.py`(두 arm
공용 `--arm ar|diff`), diffusion loss는 `tests/test_diff_loss.py`로 검증. 절차는
`docs/training.md`.

### 5.4 filler (2단계, 고정)

`Qwen/Qwen2.5-Coder-7B-Instruct`를 **frozen**으로 쓰며, skeleton 보존 제약을 명시한
프롬프트로 식별자·literal만 채운다. 학습 변인을 1단계에만 격리하기 위해 filler는 학습하지
않고 두 arm에 동일 적용한다 (`src/eval_filler.py`). quantization도 모든 조건에 동일
적용되므로 공정성에 영향이 없다.

### 5.5 데이터

| 자원 | 위치 | 내용 |
|---|---|---|
| 학습 원천 | HF `LLM4Binary/decompile-bench` | 허가형 GitHub 프로젝트 컴파일분. 바이너리는 split zip 71 volumes(140GB)로 별도 공개 |
| 평가 원천 | HF `LLM4Binary/decompile-eval` | `ghidra_pseudo`/`opt`/test 포함 → 평가용 Ghidra 재실행 불필요 |
| 스크립트·벤치마크 | `tools/LLM4Decompile` | 평가 harness, 기존 모델 보고치 |

- **언어 필터: C 함수만** (anonymizer·가독성 지표를 단일 언어로 통일).
- **바이너리 부분 확보**: split zip은 volume을 이어붙인 하나의 Zip64 archive이므로 central
  directory가 있는 마지막 volume과 필요 opt 구간 volume만 받아 부분 추출한다
  (`src/zipsplit_extract.py`).
- 학습셋은 O0–O3 균형, pseudocode+skeleton 합 4096 token 이하. 동일 파이프라인으로 규모
  확장 가능. 상세는 `docs/data_pipeline.md`.
- 평가셋은 decompile-eval의 ghidra 변형을 그대로 사용한다 (humaneval split 656건 =
  164 함수 × O0–O3).

## 6. 실험 설계

### 6.1 비교 조건

| 조건 | 1단계 | 2단계 | 답하는 질문 |
|---|---|---|---|
| B0 | 없음 (Ghidra 원본) | 없음 | 하한 기준 |
| B1 | 없음 | Instruct가 pseudocode→C 직접 생성 | 분해 자체의 이득 (RQ2) |
| A | AR skeleton 복원기 | frozen filler | RQ1 비교군 |
| D | diffusion skeleton 복원기 | frozen filler | RQ1 실험군 |

### 6.2 평가 지표

**skeleton 단독 (1단계 품질)** — `src/eval_gate3.py`
- parse 성공률: tree-sitter 파싱 통과 비율 (Gate 3 기준)
- AST edit distance: 정답 skeleton 대비 tree edit distance
- 제어흐름 골조 일치율: 분기·루프 구조의 그래프 수준 일치

**end-to-end (최종 품질)** — `src/eval_reexec.py`
- re-executability: GCC 재컴파일 후 원본 테스트 통과율
- skeleton 위반율: filler 출력이 입력 skeleton 구조를 벗어난 비율 (분해 건전성 점검)

**비용**
- 학습 GPU 시간, 추론 지연 (diffusion denoising step 수 명시)

**sanity check.** B0·B1 측정치를 공개 보고치와 대조해 harness 정상 동작을 검증한다.
기준점: 전통 도구 R2I 약 40, LLM 디컴파일러 약 60–70, LLM4Decompile re-executability
약 39%. 크게 벗어나면 harness를 점검한 뒤 본실험에 들어간다.

### 6.3 통계

A vs D 차이에 paired bootstrap 유의성 검정(함수 단위 쌍대 비교), 표마다 분산 병기.

## 7. 분석 계획

1. **분해 분석 (RQ3).** A vs D 격차를 함수 길이·중첩 깊이·최적화 레벨별로 분해. 가설대로면
   전역 제약이 강해질수록 격차가 벌어지는 단조 패턴이 나와야 한다.
2. **단계 귀속.** skeleton 단독 지표와 end-to-end 지표를 교차해, 최종 품질 차이가 1단계에서
   났는지 2단계 filling에서 희석/증폭됐는지 분리.
3. **정성 사례.** denoising 중간 snapshot을 추출해 반복 정제가 어떤 구조 오류(괄호 불일치,
   중첩 꼬임 등)를 고치는지 제시.
4. **실패 분석.** D가 A에 밀리는 구간의 공통 특성을 기술.

## 8. Gate

| Gate | 기준 | fallback |
|---|---|---|
| G0 | 추출–matching yield ≥ 60% | matching 기준 완화, 추출 범위 확대 |
| G1 | 학습 loss 감소 + VRAM 예산 내 | seq 축소(두 arm 동일) → 학습 구성 재검토 |
| G3 | 두 arm 모두 skeleton parse rate ≥ 80% | skeleton 어휘 축소 후 재학습, denoising step 상향 |

Gate 미달 시 진행을 멈추고 fallback을 적용한 뒤 보고한다.

## 9. 리스크와 대응

| 리스크 | 대응 |
|---|---|
| diffusion arm skeleton 품질 미달 (G3) | skeleton 어휘 축소, denoising step 상향. 미달 시 "diffusion이 실패하는 지점" 자체를 분석으로 전환 |
| filler의 skeleton 위반 | 프롬프트 제약 강화 + 위반율 상시 집계, 한계 절 보고 |
| LoRA가 diffusion 적응에 불충분 | 두 arm 동일 adapter 예산이라 비교 공정성 유지, "동일 예산 하 비교"로 명시 |
| diffusion 추론 비용 과다 | skeleton이 짧아 denoising 토큰 수가 적음. step 수–품질 곡선으로 투명화 |
| D arm의 추가 사전학습(130B token) confound | 주장을 model/paradigm 레벨로 한정, 한계 절 정량 명시 |

## 10. 기대 기여

1. diffusion LLM을 decompilation에 적용한 첫 연구이자, diffusion–AR을 동일 backbone 쌍으로
   통제 비교한 첫 사례.
2. "전역 구조 과제에는 diffusion, 지역 채우기에는 AR"이라는 분업 설계의 실증 검증.
3. skeleton 단독 / end-to-end / 비용의 3층 평가와 조건별 분해를 통한 패러다임 차이의 작동
   조건 규명.

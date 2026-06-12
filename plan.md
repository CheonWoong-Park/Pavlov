# 연구계획서 v2

## Pavlov : Diffusion 모델로 pseudocode에서 AST 구조를 분석하는 decompilation 연구
### — skeleton 복원에 대한 생성 패러다임(diffusion vs AR) 통제 비교

v1 (착수 전 계획) → v2 (2026-06-12 개정). 주요 변경:
하드웨어가 계획과 달라 fallback을 선적용 (10절), Gate 0 통과 실측치 반영 (8절),
평가셋 조사 결과로 평가 파이프라인 단순화 (5.4절), 학습을 별도 머신으로 분리 (10절),
연구 수행 원칙을 본문에 명문화 (12절).

---

## 1. 개요

Decompilation을 **skeleton 복원**(AST 구조 분석)과 **토큰 채우기**(filling)의 두 단계로
분해하고, skeleton 복원 단계를 diffusion LLM과 autoregressive(AR) LLM으로 각각 학습하여
비교한다. 두 모델은 동일한 backbone(Qwen2.5-Coder-7B)에서 파생된 동일 크기의 쌍이므로,
성능 차이는 생성 패러다임의 차이로 귀속된다. 목표 산출물은 workshop/단편 논문 1편.
일정은 8주. 데이터 구축·평가는 로컬 머신, 학습은 별도 GPU 머신에서 수행한다.

---

## 2. 배경과 문제

Decompilation은 컴파일된 바이너리를 사람이 읽을 수 있는 소스코드로 복원하는 작업으로,
악성코드 분석·레거시 유지보수·취약점 연구의 기반 기술이다. Ghidra 등 전통 도구의
pseudocode 출력은 타입과 식별자가 손실되어 가독성이 낮고, 이를 LLM으로 개선하는 연구
(LLM4Decompile, SK²Decompile, ReF Decompile 등)가 활발하다. 그러나 기존 LLM 디컴파일러는
전부 토큰을 좌에서 우로 생성하는 AR 방식이다.

코드 복원에는 전역 제약이 많다. 함수 후반부의 변수 사용 방식이 전반부의 타입 선언을
결정하고, 중괄호 짝과 분기 구조는 함수 전체에 걸쳐 일관되어야 한다. 단방향 생성은 뒤를
보기 전에 앞을 확정해야 하므로 이런 제약에 원리적으로 불리하다. 반면 diffusion LLM은
시퀀스 전체를 양방향으로 보며 여러 step에 걸쳐 정제하는 방식이라 전역 구조를 잡는 데
유리할 수 있으나, 세부 토큰 품질과 추론 비용에서는 AR에 밀리는 경우가 많다.

diffusion LLM을 decompilation에 적용한 연구는 현재 공백 상태다. 본 연구는 이 공백을
"diffusion이 잘할 만한 하위 과제(AST 구조 복원)만 골라 맡기고, 그 효과를 통제된
조건에서 측정한다"는 방식으로 메운다.

---

## 3. 연구 질문과 가설

**RQ1.** 동일 backbone·동일 크기·동일 학습 예산 조건에서, diffusion skeleton 복원기는
AR skeleton 복원기 대비 skeleton 품질(구문 유효성, 구조 일치)과 최종 decompilation
품질(re-executability, 가독성)에서 어떤 차이를 보이는가?

**RQ2.** 2단계 분해(skeleton 복원 → 토큰 채우기) 자체가 단일 단계 직접 decompilation
대비 이득이 있는가?

**RQ3.** diffusion과 AR의 격차는 함수 길이, 중첩 깊이, 컴파일러 최적화 수준에 따라
어떻게 달라지는가?

**가설.** diffusion의 양방향 문맥과 반복 정제는 전역 제약이 빡빡한 조건 — 긴 함수,
깊은 중첩, 고최적화(O2/O3) 바이너리 — 에서 구조 유효율의 우위로 나타나고, 이것이 최종
re-executability로 전이된다. 짧고 단순한 함수에서는 격차가 작거나 없을 것이다.

가설이 기각되더라도 단계별 지표 덕분에 어느 단계에서 왜 밀렸는지 진단할 수 있으므로,
결과 방향과 무관하게 "diffusion LLM이 decompilation에서 언제·왜 다르게 동작하는가"에
대한 첫 통제 분석으로 성립한다.

---

## 4. 관련 연구와 본 연구의 위치

| 분야 | 대표 연구 | 본 연구와의 관계 |
|---|---|---|
| AR LLM decompilation | LLM4Decompile, ReF Decompile, Idioms | baseline 패러다임. 평가 프로토콜(re-executability)과 공개 보고치 차용 |
| 2단계 분해 decompilation | SK²Decompile (skeleton→skin) | 분해 구조 차용. skeleton 단계의 패러다임 교체가 본 연구의 차별점 |
| 코드용 diffusion LLM | DiffuCoder, Dream-Coder, Stable-DiffCoder, TreeDiff | diffusion arm의 base 모델 및 학습 recipe 차용. decompilation 적용은 본 연구가 처음 |
| 데이터·벤치마크 | Decompile-Bench (NeurIPS 2025 D&B) | 데이터 원천. 신규 수집 불필요 (5.4절) |

---

## 5. 방법

### 5.1 전체 파이프라인

```
decompile-bench 공개 바이너리 (디버그 정보 포함)
        → [Ghidra headless (analyzeHeadless)] → pseudocode (함수 단위)
        → [1단계: skeleton 복원기 (학습 대상)] → anonymized skeleton
        → [2단계: filler (frozen)] → 최종 소스코드
```

주의: 데이터셋의 `asm` 필드는 어셈블리이며 본 연구의 입력이 아니다. 본 연구의 입력은
Ghidra pseudocode이므로, 데이터셋과 함께 공개된 바이너리에 Ghidra headless를 직접
실행하여 함수 단위 pseudocode를 추출한다. 추출된 pseudocode는 데이터셋의 함수명
(demangled name) 기준으로 정답 소스와 matching한다.

### 5.2 skeleton 표현

tree-sitter(tree-sitter-c)로 소스를 파싱하여 식별자를 `VAR_n`/`FUNC_n`으로, literal을
타입별 placeholder(`INT_LIT`, `STR_LIT` 등)로 치환한 텍스트를 skeleton으로 정의한다.
제어흐름, 중첩, 타입 골조, 연산자 구조는 보존된다. placeholder가 전부 유효한 C
식별자이므로 skeleton은 항상 다시 parse 가능하고, **AST와 1:1로 대응되는 텍스트
표현**이다 — 즉 1단계는 pseudocode에서 AST 구조를 분석해내는 작업이다.

AST/CFG를 직접 생성 대상으로 삼지 않는 이유: 그래프 생성은 별도 연구 영역이며, 텍스트
skeleton은 기존 masked diffusion 학습 recipe를 그대로 적용할 수 있고 SK²Decompile의
중간 표현과도 비교 가능하다. AST/CFG는 생성 표현이 아니라 평가 지표로 사용한다.

### 5.3 모델과 학습

| 항목 | diffusion arm (D) | AR arm (A) |
|---|---|---|
| base | DiffuCoder-7B-Base | Qwen2.5-Coder-7B |
| loss | masked diffusion loss (LLaDA recipe: t~U(ε,1)로 target 토큰 mask, masked 위치 CE를 1/t 가중, target 길이로 정규화) | 표준 cross-entropy (prompt 토큰 제외) |
| 공통 설정 | **QLoRA: 4-bit NF4 base + bf16 compute** (v2 변경, 10절), LoRA r=32 / alpha=64, attention+MLP 전층, gradient checkpointing, AdamW, micro-batch 1–2 + gradient accumulation | 동일 |
| seq 길이 | pilot은 2048. 학습 머신 VRAM이 허용하면 4096으로 상향하되 두 arm에 동일 적용 | 동일 |
| seed | 2개 | 2개 |

DiffuCoder-7B는 Qwen2.5-Coder-7B를 diffusion으로 적응시킨 모델이므로 두 arm은
backbone·크기·계열이 동일하다. 학습 데이터·epoch·adapter 예산도 동일하게 맞춘다.
따라서 결과 차이의 귀속 대상은 생성 패러다임 하나로 좁혀진다.

v1과의 차이: v1은 bf16 base + 16-bit LoRA(예상 17–19GB)였으나 하드웨어 사정(10절)으로
G1 fallback인 QLoRA 4-bit를 선적용했다. 두 arm에 동일하게 적용되므로 비교 공정성은
유지되며, "동일 quantization·동일 adapter 예산 하 비교"로 논문에 명시한다.

2단계 filler는 Qwen2.5-Coder-7B-Instruct(frozen)를 사용하며, skeleton 보존 제약을
명시한 프롬프트로 식별자·literal만 채우게 한다. 학습 변수를 1단계에만 격리하기 위해
filler는 학습하지 않는다. filler도 GPU 사정에 따라 4-bit/8-bit quantization을 쓸 수
있는데, frozen 모델이라 모든 조건에 동일 적용되어 공정성에는 영향이 없다 (한계 절 명시).

구현: `src/train_lora.py` (두 arm 공용, `--arm ar|diff`). diffusion loss는 단위테스트로
검증됨 (`tests/test_diff_loss.py`). 학습 절차와 환경은 `docs/training_handoff.md`.

### 5.4 데이터

**출처 (전부 공개, 신규 수집 없음).**

| 자원 | 위치 | 내용 |
|---|---|---|
| 학습 원천 | HF `LLM4Binary/decompile-bench` | 허가형 라이선스 GitHub 프로젝트에서 컴파일한 바이너리-소스 함수 쌍 약 223만 개 (arrow 17 shards, 8.5GB). 바이너리는 별도 공개: 85,250개, 140GB split zip 71 volumes |
| 평가 원천 | HF `LLM4Binary/decompile-eval` | (1) HumanEval·MBPP 컴파일분 — 테스트 케이스 보유, (2) 누출 방지용 2025년 이후 GitHub 컴파일분. **조사 결과 `ghidra_pseudo`/`ida_pseudo`/`opt` 필드가 이미 포함** → 평가용 Ghidra 실행 불필요, B0 조건도 그대로 확보 (v2 변경) |
| 스크립트 | GitHub `albertan017/LLM4Decompile` | 평가 harness, Ghidra 스크립트, 기존 모델 보고치 (`tools/`에 clone) |

**언어 필터: C 함수만.** 가독성 지표 R2I가 C 전용이고 anonymizer도 단일 언어
(tree-sitter-c)로 통일하는 것이 workshop 범위에 적절하다. 한계 절에 명시.

**바이너리 확보 (v2 추가).** 140GB를 전부 받지 않는다. split zip은 volume들을 이어붙인
하나의 Zip64 archive이므로, central directory가 있는 마지막 volume과 필요 opt 구간의
volume만 받아 부분 추출한다 (`src/zipsplit_extract.py`). 현재 vol 001/005/010/014
(각 opt 시작 구간) + 071로 약 8GB만 사용.

**구축 (현황 포함).**
- 학습 목표 100,000쌍, O0–O3 균형, pseudocode+skeleton 합 4096 token 이하.
  Ghidra 추출–matching 손실을 감안해 원천에서 1.5–2배 여유 추출.
- **현재 17,760쌍 구축 완료** (O0–O3 각 4,440, 중복 키 제거, tokenizer 기준 길이 필터).
  실측 처리율(바이너리당 usable 약 600쌍, Ghidra 3병렬 1.5분/개) 기준으로 opt별
  바이너리 40개씩 추가하면 100k 도달 — Ghidra 2–3시간 + volume 8GB 추가 다운로드.
  상세는 `docs/data_pipeline.md`.
- 평가: 600 함수, decompile-eval에서 stratified sampling (최적화 레벨 × 함수 길이 ×
  중첩 깊이, 256 token 경계 양쪽 포함). re-executability는 HumanEval·MBPP 분할에서,
  누출 안전 분석(skeleton 지표·가독성·컴파일 성공률)은 GitHub2025 분할에서 측정하고
  분할별로 분리 보고한다.

---

## 6. 실험 설계

### 6.1 비교 조건

| 조건 | 1단계 | 2단계 | 답하는 질문 |
|---|---|---|---|
| B0 | 없음 (Ghidra 원본) | 없음 | 하한 기준 |
| B1 | 없음 | Instruct가 pseudocode→소스 직접 생성 | 분해 자체의 이득 (RQ2) |
| A | AR skeleton 복원기 (×2 seed) | frozen filler | RQ1 비교군 |
| D | diffusion skeleton 복원기 (×2 seed) | frozen filler | RQ1 실험군 |
| B2 (선택) | DiffusionGemma-26B-A4B 4-bit zero-shot | frozen filler | 부록 참고점. 일정 여유 시에만 |

### 6.2 평가 지표

**skeleton 단독 (1단계 품질)**
- parse 성공률: tree-sitter 파싱 통과 비율
- AST edit distance: 정답 skeleton 대비 tree edit distance
- 제어흐름 골조 일치율: 분기·루프 구조의 그래프 수준 일치

**end-to-end (최종 품질)**
- re-executability: GCC 재컴파일 후 원본 테스트 통과율 (HumanEval·MBPP 분할)
- R2I: decompilation 전용 상대 가독성 지표, C 전용 (BLEU 류 텍스트 유사도는
  decompilation 평가에 부적절하므로 배제)
- skeleton 위반율: filler 출력이 입력 skeleton의 구조를 벗어난 비율
  (분해 파이프라인의 건전성 점검)

**비용**
- 학습 GPU 시간, 추론 지연 (diffusion denoising step 수 명시)

**측정 타당성 점검 (sanity check).** B0·B1 측정치를 Decompile-Bench 논문 공개 보고치와
대조하여 harness의 정상 동작을 검증한다. 기준점: 전통 도구(IDA) R2I 약 40, LLM 기반
디컴파일러 약 60–70, LLM4Decompile-DCBench-6.7b re-executability 약 39%. 우리 측정치가
이 수준에서 크게 벗어나면 harness를 점검한 뒤에 본실험에 들어간다.

### 6.3 통계 처리

- A vs D 차이에 paired bootstrap 유의성 검정 (함수 단위 쌍대 비교)
- seed 간 분산을 모든 표에 병기

---

## 7. 분석 계획

1. **분해 분석 (RQ3, 핵심 그림).** A vs D 격차를 함수 길이, 중첩 깊이, 최적화 레벨별로
   분해. 가설대로면 전역 제약이 강해질수록 격차가 벌어지는 단조 패턴이 나타나야 한다.
2. **단계 귀속.** skeleton 단독 지표와 end-to-end 지표를 교차하여, 최종 품질 차이가
   1단계에서 발생했는지 2단계 filling에서 희석/증폭됐는지 분리.
3. **정성 사례.** denoising 중간 snapshot 3건을 추출하여 반복 정제가 실제로 어떤 구조
   오류(괄호 불일치, 중첩 꼬임 등)를 고치는지 제시.
4. **실패 분석.** D가 A에 밀리는 구간이 있다면 그 구간의 공통 특성(예: 짧은 함수,
   특정 제어 패턴)을 기술.

---

## 8. 작업과 gate

| 작업 | gate | 상태 (2026-06-12) |
|---|---|---|
| Ghidra headless 추출 + matching + anonymization 파이프라인, 미니셋 전체 통과, 학습 pilot(2k) | **G0**: 추출–matching yield 60% 이상 / **G1**: DiffuCoder LoRA loss 감소 + VRAM 예산 내 | **G0 통과** — O0 미니셋 83.7%, O1–O3 68.6% (`results/gate0*.json`). pilot 데이터 준비 완료, G1은 학습 머신에서 |
| 학습셋 100k 구축, 컴파일·테스트 harness(최대 공수 구간), B0·B1 측정 + 공개 보고치 대조 | **G2**: B0·B1이 공개 보고치와 비슷한 수준 | 17.8k 구축, 100k 확장 경로 확정. harness 미착수 |
| A·D × 2 seed 학습 (총 4런) | **G3**: 두 arm 모두 skeleton parse 성공률 80% 이상 | 대기. 전처리 쪽 선행 지표(skeleton 변환 99.9%)는 양호 |
| 2단계 통합, end-to-end 측정, 유의성 검정 | — | 대기 |
| 분해 분석, 정성 사례. 여유 시 B2 | — | 대기 |
| 집필 | — | 대기 |

**gate fallback.**
- G0 미달 시: matching 기준 완화(이름+시그니처 → 이름만) 또는 여유 추출 배수 상향 — 불필요해짐 (통과)
- G1: r=16 → QLoRA 순서였으나 하드웨어 사정으로 QLoRA를 선적용 (10절). QLoRA에서도
  실패하면 seq 축소(두 arm 동일) → 그래도 실패하면 학습 구성 재검토
- G3 미달 시: skeleton 표현 단순화(placeholder 어휘 축소) 후 재학습

---

## 9. 리스크와 대응

| 리스크 | 가능성 | 대응 |
|---|---|---|
| ~~Ghidra 추출–matching yield 미달~~ | 해소 | G0 통과로 해소. 100k 확장 시 동일 파이프라인 재사용 |
| transformers 5.x ↔ DiffuCoder remote code(DreamModel) 비호환 (v2 추가) | 중 | 학습 머신에서 로드 실패 시 transformers 4.46–4.51로 통일 (두 arm 동일 버전). `docs/training_handoff.md`에 절차 기재 |
| 컴파일·테스트 harness 공수 초과 | 중 | 최우선 배치. albertan017/LLM4Decompile 공개 평가 스크립트 최대 재사용 |
| diffusion arm의 skeleton 품질 미달 (G3) | 중 | skeleton 어휘 축소, denoising step 수 상향. 그래도 미달 시 "diffusion이 이 과제에서 실패하는 지점" 자체를 분석 결과로 전환 |
| filler의 skeleton 위반 | 중 | 프롬프트 제약 강화 + 위반율 상시 감시, 위반 사례 별도 집계 후 한계 절 보고 |
| LoRA가 diffusion 적응에 불충분 | 저–중 | 두 arm 동일 adapter 예산이므로 비교 공정성 유지. "동일 adapter 예산 하 비교"로 명시 |
| diffusion 추론 비용 과다 | 저 | skeleton이 짧아 denoising 토큰 수 자체가 적음. step 수–품질 곡선 보고로 투명화 |
| 로컬–학습 머신 환경 불일치 (v2 추가) | 저–중 | `requirements-lock.txt`로 의존성 고정, 핸드오프 문서에 환경 구축 절차 명시, 런마다 `run_config.json` 자동 기록 |

---

## 10. 자원 (v2 전면 수정)

v1은 RTX 4090 24GB 단일 데스크탑을 전제했으나, 실제 로컬 GPU는 **RTX 5070 12GB**
(sm_120, 시스템 RAM 15GB, WSL2)로 확인되었다. 이에 따라:

- **로컬 머신 (RTX 5070 12GB)**: 데이터 구축, anonymization, Ghidra 추출, 스크립트
  검증, 추후 평가 harness. bf16+16-bit LoRA(17–19GB)는 물리적으로 불가능하므로
  G1 fallback인 QLoRA 4-bit를 선적용했고, **학습 자체는 별도 머신에서 수행**하기로
  결정 (2026-06-12).
- **학습 머신 (별도 확보)**: Gate 1 pilot부터 본 학습 4런까지. 절차는
  `docs/training_handoff.md`. 24GB급이면 VRAM gate 20GB, 12GB급이면 11.5GB 적용.
- 데이터: `LLM4Binary/decompile-bench`(+bins), `LLM4Binary/decompile-eval` — 전부 공개.
  대용량은 WSL2 ext4(`~/pavlov-data`)에 저장 (NTFS I/O 회피).
- 도구: Ghidra 12.1.2 headless + OpenJDK 21, tree-sitter(-c), HF Transformers/PEFT,
  bitsandbytes, DiffuCoder 공개 코드, albertan017/LLM4Decompile 평가 스크립트
- 외부 비용: 없음 (선택적으로 최종 런의 클라우드 재검증 시 $50 내외)

---

## 11. 기대 기여

1. diffusion LLM을 decompilation에 적용한 첫 연구이자, diffusion–AR을 동일 backbone
   쌍으로 통제 비교한 첫 사례.
2. "전역 구조 과제에는 diffusion, 지역 채우기 과제에는 AR"이라는 분업 설계의 실증적
   검증 (긍정이든 부정이든).
3. skeleton 단독 / end-to-end / 비용의 3층 평가와 조건별 분해 분석을 통한, 패러다임
   차이의 작동 조건 규명.
4. 소비자급 GPU에서 재현 가능한 전체 파이프라인 공개 (데이터 구축은 12GB GPU로 충분).

---

## 12. 연구 수행 원칙 (v2에서 명문화)

- 모든 런에 seed·config·commit hash를 기록한다 (`run_config.json` 자동 저장).
- checkpoint는 매 런 저장한다.
- **외부 바이너리 실행(평가 harness)은 격리 환경 + timeout + 리소스 제한 필수.**
  Ghidra 추출은 정적 분석만 수행하며 바이너리를 실행하지 않는다.
- 비용이 큰 작업 전에 20–50 샘플 pilot을 먼저 돌린다.
- gate 미달 시 진행을 멈추고 fallback을 적용한 뒤 보고한다.
- 가설과 반대되는 결과도 해석 가설과 함께 충실히 보고한다.
- 결정·실측치는 `logs/research_log.md`에 시간순으로 남긴다.

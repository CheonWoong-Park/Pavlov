# Pavlov — 연구 개요와 방법

Diffusion으로 pseudocode에서 AST 구조를 복원하는 decompilation 연구. skeleton 복원 단계에서
생성 패러다임인 diffusion과 autoregressive를 통제 비교한다.

## 1. 개요

Decompilation을 구조 복원과 토큰 채우기 두 단계로 분해하고, 구조 복원 단계를 diffusion LLM과
autoregressive LLM으로 각각 학습해 비교한다. 두 모델은 동일 계열 backbone에서 파생된 동일
크기 쌍이므로, 성능 차이를 생성 패러다임으로 귀속할 수 있다.

## 2. 배경과 문제

Ghidra 같은 도구의 pseudocode는 컴파일은 되지만 가독성이 낮다. 의미 없는 변수명, cast 범벅,
goto 등이 그대로 남는다. 기존 LLM decompiler는 모두 pseudocode에서 source를 좌에서 우로 한
토큰씩 생성하는 autoregressive 방식이다.

코드 복원에는 전역 제약이 많다. 함수 후반부의 변수 사용이 전반부의 타입 선언을 결정하고,
중괄호 짝과 분기 구조는 함수 전체에 걸쳐 일관되어야 한다. 단방향 생성은 뒤를 보기 전에 앞을
확정해야 하므로 이런 제약에 원리적으로 불리하다. diffusion LLM은 시퀀스 전체를 양방향으로
보며 여러 step에 걸쳐 정제하므로 전역 구조를 잡는 데 유리할 수 있다. diffusion을
decompilation에 적용한 연구는 공백 상태이며, 본 연구는 diffusion이 잘할 만한 하위 과제인 AST
구조 복원만 골라 맡기고 그 효과를 통제 측정한다.

decompilation 파이프라인은 디스어셈블 → IR 승격 → 제어흐름·데이터흐름 분석 → 타입 복원·구조화
→ pseudocode 생성으로 이어진다. 마지막 pseudocode는 이미 이름과 타입이 깎인 결과이며, 본
연구는 그 pseudocode에서 출발해 구조화 단계를 LLM으로 다시 보강한다.

## 3. 연구 질문과 가설

- RQ1. 동일 backbone, 동일 크기, 동일 학습 예산에서 diffusion 복원기는 autoregressive 복원기
  대비 skeleton 품질과 최종 decompilation 품질에서 어떤 차이를 보이는가.
- RQ2. 2단계 분해가 단일 단계 직접 decompilation 대비 이득이 있는가.
- RQ3. 두 방식의 격차는 함수 길이, 중첩 깊이, 최적화 레벨에 따라 어떻게 달라지는가.

가설은, diffusion의 양방향 문맥과 반복 정제가 전역 제약이 빡빡한 조건인 긴 함수, 깊은 중첩,
고최적화 빌드에서 구조 유효율의 우위로 나타나고 이것이 re-executability로 전이된다는 것이다.
가설이 기각되더라도 단계별 지표로 어느 단계에서 왜 밀렸는지 진단할 수 있어, 결과 방향과
무관하게 diffusion이 decompilation에서 언제 왜 다르게 동작하는가에 대한 첫 통제 분석으로
성립한다.

## 4. 관련 연구와 위치

AR LLM decompilation인 LLM4Decompile, ReF Decompile 등에서 평가 프로토콜인 re-executability를
차용한다. 2단계 분해 decompilation인 SK²Decompile에서 분해 구조를 차용하되, skeleton 단계의
패러다임을 교체하는 것이 본 연구의 차별점이다. 코드용 diffusion LLM인 DiffuCoder, Dream에서
diffusion arm의 base와 학습 recipe를 가져오며, decompilation 적용은 본 연구가 처음이다.

---

## 5. 방법

전체 파이프라인은 다음과 같다. 입력은 Ghidra pseudocode, 정답은 anonymized skeleton이다.

```
원본 source → compile → binary → Ghidra → pseudocode        ← 입력
원본 source → tree-sitter 익명화 → skeleton                  ← 정답
[입력 pseudocode, 정답 skeleton] 쌍으로 학습
추론: pseudocode → 복원기 → skeleton → filler → 복원 C → 컴파일·테스트로 채점
```

### 5.1 데이터 전처리 (Step 1)

목표는 입력이 Ghidra pseudocode이고 정답이 anonymized skeleton인 쌍을 대량 생성하는 것이다.

**원천 데이터.** LLM4Binary/decompile-bench는 공개 GitHub 프로젝트 컴파일분으로, source 함수
약 223만 개가 arrow 포맷으로 구성되어 있다. 필드 name, code, asm, file 중 asm은 사용하지
않는다. LLM4Binary/decompile-bench-bins는 컴파일된 binary 85,250개로 구성된 140GB 크기의
split zip 71 volumes다.

**binary 부분 추출.** split zip은 volume들을 이어 붙인 하나의 Zip64 archive다.
zipsplit_extract.py가 central directory가 든 마지막 volume과 필요한 opt 구간 시작 volume만 받아
부분 추출하며, 140GB 중 약 8GB만 사용한다.

**binary 선정.** select_miniset.py가 opt별로, 해당 opt binary가 로컬에 모두 있는 프로젝트 중 C
record가 많고 30MB 이하인 것을 선정한다. Ghidra 분석 시간을 아끼기 위해 프로젝트당 binary
1개만 선택한다.

**Ghidra 추출.** run_ghidra_batch.sh가 ExportPseudoC.java와 함께 analyzeHeadless를 사용해
binary를 import하고 디컴파일한 뒤, 함수별 pseudocode를 jsonl 형태로 출력한다. thunk와 external
함수는 제외하며 함수당 60초의 시간 제한을 둔다. 정적 분석만 수행하고 binary를 실행하지는
않으며, 3병렬 처리로 binary당 약 1.5분이 소요된다.

**함수 matching.** match_functions.py가 Ghidra 산출 함수 중 FUN_*, _INIT_* 같은 auto-name을
제외한 named 함수만 추출해, 데이터셋 source와 project 및 함수명을 키로 매칭한다. Gate 0
단계에서는 matching yield 60퍼센트 이상을 기준으로 삼는데, 최적화 빌드는 inlining으로 함수가
사라져 O0 빌드보다 수치가 낮다.

**익명화.** anonymize.py가 tree-sitter-c로 원본 source를 파싱해 AST를 순회하며 치환한다.
identifier는 VAR_n으로, function 위치 identifier는 FUNC_n으로 바꾼다. type_identifier는
TYPE_n으로 치환하되 size_t, undefined4, ulong, byte 같은 표준 및 Ghidra 타입은 보존한다. field는
FIELD_n, goto label은 LABEL_n으로 바꾸며, number는 INT_LIT 또는 FLOAT_LIT, string과 char는 각각
STR_LIT와 CHAR_LIT로 치환하고 주석은 제거한다. 같은 이름은 같은 placeholder를 사용해 mapping을
보존하며, 치환은 byte offset 기준 오른쪽에서 왼쪽으로 진행한다. placeholder가 모두 valid C
identifier이므로 결과물도 다시 parse 된다. source가 parse되는 쌍을 기준으로 변환 성공률은 약
99.9퍼센트다.

**학습셋 구성.** build_dataset.py가 레코드를 input, target, mapping, project, binary, opt,
func_name, file, source 필드로 구성한다. source 단독 parse 실패가 약 12퍼센트, skeleton re-parse
실패가 약 0.1퍼센트이며 tokenizer 기준 길이 초과 항목과 함께 필터링해 제외한다. 또한 project,
func_name, opt 기준으로 중복을 제거한다. 최종 산출물은 balanced_train.jsonl 17,760개로 O0부터
O3까지 각 4,440개이고, 학습에 쓰지 않는 balanced_val400.jsonl 400개, pilot용
pilot2k_balanced.jsonl 2,000개다.

**학습 입력 형식.**
```text
### Pseudocode:
{pseudocode}
### Skeleton:
{skeleton}<eos>
```

### 5.2 학습 (Step 2)

목표는 pseudocode에서 skeleton으로 변환하는 복원기를 autoregressive와 diffusion 두 방식으로
학습하고 동일 조건에서 통제 비교하는 것이다. 구현은 train_lora.py 하나로 두 arm을 모두 다룬다.

**공통 설정.** base 모델은 frozen 상태로 두고 LoRA만 학습한다. 설정값은 r 32, alpha 64, dropout
0.05이며 대상 모듈은 q, k, v, o, gate, up, down이다. trainable 파라미터는 약 80M으로 전체의
1.05퍼센트 수준이다. bf16 정밀도를 사용하고 optimizer는 AdamW로 betas 0.9와 0.95, weight decay
0.01, lr 2e-4, warmup 20 step 후 cosine decay를 적용한다. effective batch는 micro-batch와
grad-accum의 곱으로 16, seq-len 4096, gen-len 512, max-steps 2000, seed 1로 설정해 A100 80GB
환경에서 진행한다. prompt 토큰은 loss 계산에서 제외하고 skeleton 부분만 학습한다. 환경은 torch
2.9.1+cu130, transformers 4.51.3, peft 0.19.1이다. transformers는 4.51.3을 쓰는데, 5.x는
DiffuCoder remote code의 RoPE init과 충돌하기 때문이다.

**A arm, autoregressive.** Qwen/Qwen2.5-Coder-7B 모델과 AutoModelForCausalLM을 사용한다. 표준
cross entropy를 적용하며 prompt와 pad 부분은 label -100으로 마스킹한다. micro-batch 4와
grad-accum 4를 사용하는데, micro-batch 8은 activation 크기로 인해 OOM이 발생하기 때문이다.

**D arm, diffusion.** apple/DiffuCoder-7B-Base 모델과 trust_remote_code를 적용한 AutoModel로
DreamModel을 구성한다. DiffuCoder는 Qwen2.5-Coder를 약 130B token으로 diffusion에 적응시킨 동일
계열 모델이다. masked diffusion loss는 LLaDA recipe를 따른다. 먼저 샘플마다 t를 eps 1e-3에서
1까지 균등분포에서 추출하고, target 토큰을 확률 t로 mask token, 즉 id 151666으로 치환한다.
forward를 거쳐 masked 위치의 logits를 구한 뒤 masked 위치에서 원래 토큰에 대한 cross entropy를
계산하고, 가중치 1/t를 곱해 target 길이로 정규화한 후 batch 단위로 평균을 낸다. 각 샘플당 최소
1개의 mask가 보장되며 micro-batch 8과 grad-accum 2를 사용한다.

**정합 핵심 두 가지.** diffusion arm의 생성 collapse를 해결한 두 가지가 핵심이다. 첫째는 logits
shift 정합이다. DreamModel은 position i의 토큰을 logits i-1에서 읽고 생성도 이 방식대로 shift해
진행하므로, 학습 loss에서도 동일하게 logits를 한 칸 shift해 계산해야 한다. 이를 맞추지 않으면
학습과 생성의 좌표계가 한 칸 어긋나 생성이 반복 토큰으로 붕괴한다. 둘째는 EOS 고정 canvas다.
생성 과정이 전부 mask로 채워진 고정 길이 canvas에서 시작하므로, 학습에서도 응답을 gen-len인
512까지 EOS로 패딩하고 그 EOS tail 전체를 예측 대상에 포함시킨다. 두 arm 모두 응답 길이가 512
이하인 동일한 subset을 사용한다.

**산출물.** LoRA adapter로 step별 폴더 안에 adapter_config.json과 adapter_model.safetensors가
산출되며, 용량은 fp32 저장 기준 약 309MB다. 부가로 step별 loss와 VRAM이 기록된
train_log.jsonl, 하이퍼파라미터가 기록된 run_config.json이 생성된다. diff_s0의 final loss는 약
0.10, ar_s0는 약 0.012였다. 두 모델의 loss 함수가 다르므로 절대값 직접 비교는 무의미하며, 정확한
채점은 평가 단계에서 진행한다.

### 5.3 평가 (Step 3)

목표는 두 모델이 복원한 결과를 동일한 기준으로 채점해 diffusion과 autoregressive를 비교하는
것이다.

**평가셋.** decompile-eval의 ghidra 분할인 humaneval 656개를 사용하며, 164개 함수를 O0부터
O3까지 컴파일한 것이다. 각 항목은 Ghidra pseudocode 형태의 입력인 input_asm_prompt, 정답 C인
c_func, assert 기반 main인 c_test, 최적화 레벨인 type으로 구성된다. 정밀도는 학습과 일치하도록
bf16을 사용하며 두 arm을 동일 조건에서 평가한다.

**skeleton 생성.** eval_generate.py가 base 모델과 adapter를 로드해 각 항목의 pseudocode로
skeleton을 생성한다. 프롬프트는 학습 때와 동일하다. A arm은 greedy 방식의 generate를 쓰며
max_new_tokens는 512다. D arm은 diffusion_generate를 쓰며 max_new_tokens 512, steps는
max_new_tokens를 tokens-per-step으로 나눈 값, temperature 0.2, top_p 0.95, alg entropy를
적용한다. tokens-per-step은 denoising step의 밀도를 정하는 품질과 속도의 트레이드오프로, 1일 때
품질이 가장 높고 올릴수록 빠르지만 결과가 거칠어진다.

**Gate 3 parse rate.** eval_gate3.py가 생성된 skeleton을 tree-sitter로 파싱해 ERROR나 MISSING
노드가 없으면 valid로 판정한다. 전체 및 opt 레벨별 비율을 측정하며 통과 기준은 80퍼센트
이상이다.

**filler.** eval_filler.py에서 frozen 상태의 Qwen/Qwen2.5-Coder-7B-Instruct가 skeleton과 원본
pseudocode를 입력받아 placeholder를 실제 이름과 값으로 채워 컴파일 가능한 C를 생성한다. 테스트
harness와 링크되도록 entry 함수는 func0으로 지정한다. 이 모델은 별도로 학습하지 않고 두 arm의
평가에 공통으로 사용되므로, 최종 성능 차이는 오직 학습 단계의 복원 능력 차이로 귀속된다.

**re-executability와 skeleton 위반율.** eval_reexec.py가 filler 출력 C와 c_test를 합쳐 gcc로
컴파일한 뒤, timeout과 CPU, 메모리, 파일 크기 rlimit가 설정된 격리 환경에서 실행한다. 모든
assert를 통과해 exit 0을 반환하면 pass로 처리하며, 원본과 동작이 일치하는지를 나타내는
re-executability를 이 pass 비율로 정의한다. skeleton 위반율은 filler 출력을 다시 익명화했을 때
입력 skeleton과 구조가 다르면 위반으로 판정해, filler가 단순히 내용만 채웠는지 검증한다.

---

## 6. 실험 설계와 지표

비교 조건은 네 가지다. B0은 Ghidra 원본, B1은 Instruct 모델이 pseudocode에서 C를 직접 생성, A는
autoregressive 복원기와 filler, D는 diffusion 복원기와 filler다. B0과 B1은 분해 자체의 이득인
RQ2를 위한 baseline이다.

주요 지표는 skeleton 단독 품질로 parse rate와 AST edit distance, end-to-end 품질로
re-executability와 skeleton 위반율, 그리고 학습 GPU 시간과 추론 지연 같은 비용이다. 본 비교
전에 B0과 B1 측정치를 공개 보고치와 대조해 평가 harness가 정상인지 점검한다. 기준점은 전통 도구
R2I 약 40, LLM decompiler 약 60에서 70, LLM4Decompile re-executability 약 39퍼센트다.

## 7. 분석 계획

A와 D의 격차를 함수 길이, 중첩 깊이, 최적화 레벨별로 분해한다. 가설대로면 전역 제약이 강해질수록
격차가 벌어지는 단조 패턴이 나타나야 한다. skeleton 단독 지표와 end-to-end 지표를 교차해 최종
품질 차이가 학습 단계에서 났는지 filling에서 희석되거나 증폭됐는지 분리한다. denoising 중간
snapshot으로 반복 정제가 어떤 구조 오류를 고치는지 정성 사례를 제시하고, D가 밀리는 구간의 공통
특성을 기술한다.

## 8. 한계와 confound

DiffuCoder는 Qwen2.5-Coder를 약 130B token으로 추가 diffusion 적응시킨 모델이라, 학습 방식 외에
추가 사전학습량 차이가 두 arm 간에 남는다. 따라서 주장을 model 또는 paradigm 레벨 비교로 한정해
명시한다. anonymizer와 가독성 지표를 C로 통일하고 canvas 길이를 제한해 짧은 함수에 집중했으며,
이는 평가셋 특성과는 맞으나 다언어와 긴 함수 확장은 이후 과제다. filler의 quantization은 frozen
모델이라 모든 조건에 동일하게 적용되므로 공정성에는 영향이 없다.

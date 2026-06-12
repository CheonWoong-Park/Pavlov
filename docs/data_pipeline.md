# 데이터 파이프라인 상세

학습셋이 만들어지는 전 과정과 각 컴포넌트의 동작 원리, 실측 수치를 기록한다.

```
decompile-bench-bins (split zip, 140GB)          decompile-bench (arrow, 2.23M쌍)
        │                                                  │
[1] zipsplit_extract.py ── 필요 volume만 부분 추출          │
        │                                                  │
[2] select_miniset.py ──── opt별 바이너리 선정·추출         │
        │                                                  │
[3] run_ghidra_batch.sh ── Ghidra headless pseudocode 추출 │
        │                                                  │
[4] match_functions.py ◄───────── (project, 함수명) 키 ─────┘
        │  matched.jsonl                  ← Gate 0: yield 측정 지점
[5] build_dataset.py ── anonymize.py로 skeleton 생성 + 필터
        │
   train.jsonl  {input: pseudocode, target: skeleton, mapping, meta}
```

## 0. 원천 데이터

| 데이터셋 | 내용 | 사용 |
|---|---|---|
| `LLM4Binary/decompile-bench` | 2,233,092 함수쌍, arrow 17 shards (8.5GB). 필드: `name`/`code`/`asm`/`file` | source 함수 공급. **asm 필드는 사용 금지** (계획) |
| `LLM4Binary/decompile-bench-bins` | 컴파일된 바이너리 85,250개, **140GB split zip 71 volumes** (각 2,000,000,000 bytes) | Ghidra 입력 |
| `LLM4Binary/decompile-eval` | humaneval(656)/mbpp(3,896)/github(43,281) C 함수. `ghidra_pseudo`/`ida_pseudo`/`opt`/test 포함 | 평가 전용 (Ghidra 재실행 불필요, B0 직접 가능) |

스냅샷 고정: bench `4b708c2211cd…`, eval `b9271fae3c…`.
저장 위치: `~/pavlov-data/hf-cache` (HF_HOME, ext4 — NTFS I/O 회피).

### 바이너리 이름 규약 (matching의 근거)

```
바이너리 이름:  <user>[P]<repo>[P]build_<OPT>[P]<binname>
데이터셋 file:  /<user>[P]<repo>/<repo 내 경로>
project 키   =  "<user>[P]<repo>"
match 키     =  (project, 함수명)
```

volume 배치: O0은 vol 001부터, O1은 005, O2는 010, O3는 014부터. opt별 각 ~21k개.

## 1. split zip 부분 추출 — `src/zipsplit_extract.py`

140GB 전체를 받지 않기 위해 만든 컴포넌트. 7z식 split zip은 **volume들을 이어붙인
것이 하나의 Zip64 archive**라는 점을 이용한다.

- `SplitVolumes`: volume 파일들을 전역 offset으로 읽는 가상 파일. 로컬에 없는
  volume은 skip 처리 (있는 범위만 접근 가능).
- `parse_central_directory`: 마지막 volume(071, 61MB)의 EOCD → Zip64 EOCD locator →
  central directory를 파싱해 **전체 85,250 entry의 이름·offset·크기**를 얻는다.
  Zip64 extra field(comp/uncomp/local header offset) 처리 포함.
- `entry_available`: entry가 로컬 보유 volume 범위에 완전히 들어있는지 판정.
- `extract_entry`: local file header 파싱 후 raw deflate(zlib)로 압축 해제.

→ **실제 다운로드: vol 001/005/010/014 (각 2GB) + 071 (CD) ≈ 8GB로 140GB 대체.**
volume 크기 목록은 `data/bins_volume_sizes.json` (offset 계산에 필수).

## 2. 바이너리 선정 — `src/select_miniset.py`

- 해당 opt의 바이너리가 **전부 로컬 volume에 들어있는 프로젝트**만 후보로
  (yield 분모가 의미를 갖도록), 데이터셋 C record가 `--min-c-records` 이상,
  바이너리 ≤30MB 조건으로 상위 `--n-projects`개 선정 후 추출.
- C record 판정: `file`이 `.c`로 끝나고 함수명에 `::` 없음.
- Ghidra 배치에는 **프로젝트당 최대 크기 바이너리 1개**만 사용 (시간 절약;
  목록은 `data/miniset_list*.txt`).

## 3. Ghidra pseudocode 추출 — `scripts/`

- `ExportPseudoC.java` (GhidraScript): thunk/external 제외 전 함수를
  DecompInterface로 decompile (함수당 60s 제한),
  `{program, name, demangled, entry, size, pseudo}` jsonl로 출력.
- `run_ghidra_batch.sh`: `analyzeHeadless <임시 프로젝트> -import <bin>
  -postScript ExportPseudoC.java <출력> -deleteProject`,
  바이너리당 timeout 1800s, `xargs -P`로 병렬, 기존 출력은 skip (재시작 안전).
- 정적 분석만 수행 — **바이너리를 실행하지 않는다.**
- 실측 처리율: 3 병렬에서 바이너리당 ~1.5분, 바이너리당 함수 ~750–1,300개.

## 4. 함수 matching — `src/match_functions.py` (Gate 0 측정 지점)

- 데이터셋 17 shards를 `pyarrow.memory_map`으로 읽어
  `index[(project, 함수명)] → [{code, file}]` 구성 (C만).
- Ghidra 산출 함수 중 auto-name(`FUN_*`, `_INIT_*`, `__*`, `entry` 등)을 제외한
  **named 함수**를 index와 대조, 일치 시 `{project, binary, opt, func_name,
  pseudo, code, file}`를 기록.
- 리포트 2개 yield:
  - `yield_dataset_coverage` = matched unique key / 데이터셋 unique key — **Gate 0 기준**
  - `yield_ghidra_named_matched` = matched / Ghidra named 함수 (참고용 — 분모에
    정적 링크된 라이브러리 함수 등이 포함되어 낮게 나옴)

### Gate 0 실측 (2026-06-12)

| 배치 | 바이너리 | matched 쌍 | dataset coverage | 판정 |
|---|---|---|---|---|
| O0 미니셋 | 12 (12 프로젝트) | 9,259 | **83.7%** | 통과 (기준 60% 이상) |
| O1–O3 | 30 (10 프로젝트×3 opt) | 18,550 | **68.6%** | 통과 (inlining으로 O0보다 낮은 것은 정상) |

리포트: `results/gate0_report.json`, `results/gate0_O123_report.json`.
duplicate key는 대부분 동일 코드(같은 함수가 여러 바이너리에 링크됨), 충돌 <1%.

## 5. Anonymization — `src/anonymize.py`

tree-sitter-c AST를 순회하며 source → skeleton 변환. `anonymize_c(source)`가
`(skeleton, mapping)`을 반환하고 `parses_clean()`으로 결과를 검증한다.

| AST 노드 | placeholder | 비고 |
|---|---|---|
| identifier | `VAR_n` | `function_declarator`/`call_expression` 위치면 `FUNC_n` |
| type_identifier | `TYPE_n` | 표준/Ghidra 타입(`size_t`, `undefined4`, `ulong`, `byte` 등)은 보존 |
| field_identifier | `FIELD_n` | |
| statement_identifier | `LABEL_n` | goto label |
| number literal | `INT_LIT` / `FLOAT_LIT` | hex는 suffix와 무관하게 INT (버그 수정됨: `0xFFFFFFFFUL`이 FLOAT로 오분류되던 문제) |
| string / char | `STR_LIT` / `CHAR_LIT` | |
| comment | 제거 | 빈 줄 collapse |

- 같은 이름은 같은 placeholder (mapping 보존 → Stage 2 filler 학습·평가에 사용)
- 치환은 byte offset 기준 **오른쪽→왼쪽**으로 적용 (offset 무효화 방지)
- placeholder가 전부 유효한 C 식별자라 skeleton도 다시 parse 가능

검증: 실데이터 50/50 parse 통과, literal 분류 단위테스트 9건 통과.

## 6. 학습 예제 변환 — `src/build_dataset.py`

matched.jsonl → train.jsonl. 각 record:

```json
{"input": "<Ghidra pseudocode>", "target": "<anonymized skeleton>",
 "mapping": {"VAR_0": "...", ...},
 "project": "...", "binary": "...", "opt": "O2", "func_name": "...",
 "file": "...", "source": "<원본 C>"}
```

필터링 (실측, O0+O123 합산 27,809쌍 기준):

| 단계 | 탈락 | 비율 |
|---|---|---|
| source가 단독 parse 안 됨 (macro/K&R 등) | 3,441 | 12.4% |
| skeleton re-parse 실패 | 20 | **0.07%** (변환 성공률 99.9%) |
| 길이 초과 (Qwen tokenizer 기준 pseudo+skel >4096 tok) | 774 | 2.8% |
| **최종 kept** | **23,574** | 84.8% |

## 7. 최종 학습셋 (균형·중복 제거)

(project, func_name, opt) 중복 제거 후 opt별 최소치(4,540)로 cap, opt당 100건을
validation으로 hold-out. 샘플링 seed 42.

| 파일 | 건수 | 구성 |
|---|---|---|
| `data/matched/balanced_train.jsonl` | 17,760 | O0–O3 각 4,440 |
| `data/matched/balanced_val400.jsonl` | 400 | opt당 100, 학습 미사용 |
| `data/matched/pilot2k_balanced.jsonl` | 2,000 | opt당 500 (Gate 1 pilot용) |
| `data/matched/pilot2k.jsonl` 등 | — | O0 전용 구버전 (대체됨) |

### 100k 확장 경로 (계획 목표치)

현재 17.8k는 opt별 첫 volume의 프로젝트당 1개 바이너리에서 나온 것.
실측 처리율(바이너리당 usable ~600쌍, Ghidra 3병렬 ~1.5분/개) 기준:

1. volume 002/006/011/015 추가 다운로드 (~8GB)
2. `select_miniset.py`로 opt별 ~40 프로젝트 추가 선정·추출
3. `run_ghidra_batch.sh` (~2–3시간) → matching → build → 균형 재구성

## 8. 학습 입력 형식 (참고: `src/train_lora.py`)

```
### Pseudocode:
{pseudo}
### Skeleton:
{skeleton}<eos>
```

- AR arm: prompt 토큰 label -100 처리, target에만 CE
- diff arm: target 토큰만 t~U(ε,1) 확률로 mask, masked 위치 CE를 1/t 가중·길이
  정규화 (LLaDA recipe; 단위테스트 `tests/test_diff_loss.py`)
- 자세한 학습 절차는 `docs/training_handoff.md`

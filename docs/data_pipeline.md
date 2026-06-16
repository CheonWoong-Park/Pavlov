# 데이터 파이프라인

학습셋이 만들어지는 과정과 각 컴포넌트의 동작 원리.

```
decompile-bench-bins (split zip)               decompile-bench (arrow, source 함수)
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
| `LLM4Binary/decompile-bench` | 함수쌍 약 2.23M, arrow 17 shards. 필드: `name`/`code`/`asm`/`file` | source 함수 공급. **asm 필드는 사용 안 함** |
| `LLM4Binary/decompile-bench-bins` | 컴파일 바이너리 85,250개, 140GB split zip 71 volumes (각 2,000,000,000 bytes) | Ghidra 입력 |
| `LLM4Binary/decompile-eval` | humaneval(656)/mbpp/github C 함수. `ghidra_pseudo`/`ida_pseudo`/`opt`/test 포함 | 평가 전용 (Ghidra 재실행 불필요) |

### 바이너리 이름 규약 (matching 근거)

```
바이너리 이름:  <user>[P]<repo>[P]build_<OPT>[P]<binname>
데이터셋 file:  /<user>[P]<repo>/<repo 내 경로>
project 키   =  "<user>[P]<repo>"
match 키     =  (project, 함수명)
```

volume 배치: O0은 vol 001부터, O1은 005, O2는 010, O3는 014부터. opt별 각 약 21k개.

## 1. split zip 부분 추출 — `src/zipsplit_extract.py`

140GB 전체를 받지 않기 위한 컴포넌트. split zip은 **volume들을 이어붙인 하나의 Zip64
archive**라는 점을 이용한다.

- `SplitVolumes`: volume 파일들을 전역 offset으로 읽는 가상 파일. 로컬에 없는 volume은
  skip (있는 범위만 접근).
- `parse_central_directory`: 마지막 volume(071, 61MB)의 EOCD → Zip64 EOCD locator →
  central directory를 파싱해 전체 85,250 entry의 이름·offset·크기를 얻는다 (Zip64 extra
  field 처리 포함).
- `entry_available`: entry가 로컬 보유 volume 범위에 완전히 들어있는지 판정.
- `extract_entry`: local file header 파싱 후 raw deflate로 압축 해제.

→ central directory가 있는 volume(071)과 필요 opt 구간의 시작 volume만 받으면 140GB를
부분 대체한다. volume 크기 목록은 offset 계산에 필요하다(`data/bins_volume_sizes.json`).

## 2. 바이너리 선정 — `src/select_miniset.py`

- 해당 opt의 바이너리가 **전부 로컬 volume에 들어있는 프로젝트**만 후보로(yield 분모가
  의미를 갖도록), 데이터셋 C record가 `--min-c-records` 이상, 바이너리 ≤30MB 조건으로 상위
  `--n-projects`개 선정 후 추출.
- C record 판정: `file`이 `.c`로 끝나고 함수명에 `::` 없음.
- Ghidra 배치에는 프로젝트당 최대 크기 바이너리 1개만 사용(시간 절약).

## 3. Ghidra pseudocode 추출 — `scripts/`

- `ExportPseudoC.java` (GhidraScript): thunk/external 제외 전 함수를 DecompInterface로
  decompile(함수당 60s 제한), `{program, name, demangled, entry, size, pseudo}` jsonl로 출력.
- `run_ghidra_batch.sh`: `analyzeHeadless ... -postScript ExportPseudoC.java ...`,
  바이너리당 timeout 1800s, `xargs -P`로 병렬, 기존 출력은 skip(재시작 안전).
- **정적 분석만 수행 — 바이너리를 실행하지 않는다.**
- 처리율: 3 병렬에서 바이너리당 약 1.5분, 바이너리당 함수 약 750–1,300개.

## 4. 함수 matching — `src/match_functions.py` (Gate 0 측정 지점)

- 데이터셋 17 shards를 `pyarrow.memory_map`으로 읽어 `index[(project, 함수명)] → [{code, file}]`
  구성(C만).
- Ghidra 산출 함수 중 auto-name(`FUN_*`, `_INIT_*`, `__*`, `entry` 등)을 제외한 named 함수를
  index와 대조, 일치 시 `{project, binary, opt, func_name, pseudo, code, file}` 기록.
- yield 2종:
  - `yield_dataset_coverage` = matched unique key / 데이터셋 unique key — **Gate 0 기준**
  - `yield_ghidra_named_matched` = matched / Ghidra named 함수 (참고용; 분모에 정적 링크된
    라이브러리 함수가 포함되어 낮게 나옴)

Gate 0 기준은 dataset coverage 60% 이상. 최적화 빌드는 inlining으로 함수가 사라져 O0보다
yield가 낮은 것이 정상이다. duplicate key는 대부분 동일 코드(같은 함수가 여러 바이너리에
링크됨)이고 충돌은 1% 미만이라 제거한다.

## 5. Anonymization — `src/anonymize.py`

tree-sitter-c AST를 순회하며 source → skeleton 변환. `anonymize_c(source)`가
`(skeleton, mapping)`을 반환하고 `parses_clean()`으로 결과를 검증한다.

| AST 노드 | placeholder | 비고 |
|---|---|---|
| identifier | `VAR_n` | `function_declarator`/`call_expression` 위치면 `FUNC_n` |
| type_identifier | `TYPE_n` | 표준/Ghidra 타입(`size_t`, `undefined4`, `ulong`, `byte` 등)은 보존 |
| field_identifier | `FIELD_n` | |
| statement_identifier | `LABEL_n` | goto label |
| number literal | `INT_LIT` / `FLOAT_LIT` | hex는 suffix와 무관하게 INT |
| string / char | `STR_LIT` / `CHAR_LIT` | |
| comment | 제거 | 빈 줄 collapse |

- 같은 이름은 같은 placeholder(mapping 보존 → 2단계 filler 평가에 사용).
- 치환은 byte offset 기준 오른쪽→왼쪽으로 적용(offset 무효화 방지).
- placeholder가 전부 유효한 C 식별자라 skeleton도 다시 parse 가능. source가 파싱되는 쌍
  기준 skeleton 변환 성공률 약 99.9%.

## 6. 학습 예제 변환 — `src/build_dataset.py`

matched.jsonl → train.jsonl. 각 record:

```json
{"input": "<Ghidra pseudocode>", "target": "<anonymized skeleton>",
 "mapping": {"VAR_0": "...", ...},
 "project": "...", "binary": "...", "opt": "O2", "func_name": "...",
 "file": "...", "source": "<원본 C>"}
```

필터: source가 단독 parse 안 되는 경우(매크로/K&R 등) 약 12% 제외, skeleton re-parse 실패
약 0.1% 제외, Qwen tokenizer 기준 pseudo+skeleton 합 4096 token 초과 제외.

## 7. 학습셋

(project, func_name, opt) 중복 제거 후 opt별로 균형을 맞추고 opt당 일부를 validation으로
hold-out한다.

| 파일 | 건수 | 구성 |
|---|---|---|
| `data/matched/balanced_train.jsonl` | 17,760 | O0–O3 각 4,440 |
| `data/matched/balanced_val400.jsonl` | 400 | opt당 100, 학습 미사용 |
| `data/matched/pilot2k_balanced.jsonl` | 2,000 | opt당 500 (pilot용) |

### 규모 확장

동일 파이프라인으로 더 키울 수 있다: 추가 volume 다운로드 → `select_miniset.py`로 opt별
프로젝트 추가 선정·추출 → `run_ghidra_batch.sh` → matching → build → 균형 재구성. 처리율
기준 바이너리당 usable 약 600쌍.

## 8. 학습 입력 형식 (참고)

```
### Pseudocode:
{pseudo}
### Skeleton:
{skeleton}<eos>
```

- AR arm: prompt 토큰 label -100, target에만 CE.
- diff arm: target 토큰만 t~U(ε,1) 확률로 mask, masked 위치 CE를 1/t 가중·길이 정규화
  (LLaDA recipe; `tests/test_diff_loss.py`).
- 학습 절차는 `training.md`.

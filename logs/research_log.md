# 연구 로그

## 2026-06-12 — 세션 1: 환경 점검, 1단계 착수

### 중대 발견: 하드웨어가 계획서와 다름
- 계획서는 RTX 4090 24GB 전제였는데 실제 GPU는 **RTX 5070 12GB** (sm_120 Blackwell). 시스템 RAM 15GB, WSL2.
- 설치돼 있던 torch는 CPU 빌드(2.11.0+cpu)라 CUDA 빌드로 재설치 필요.
- 결과적으로 계획서의 bf16 base + 16-bit LoRA 구성(예상 VRAM 17–19GB)은 이 GPU에서 불가능.
- 계획서 G1 fallback 순서에 따라 r=16 단계는 건너뛰고 **QLoRA 4-bit를 처음부터 적용**하기로 함.
  - 7B 4-bit NF4 weight 약 4.5GB + LoRA/optimizer + activation(gradient checkpointing) → 목표 VRAM 11GB 이하
  - seq 4096에서 OOM이 나면 2048로 줄이되, 두 arm에 똑같이 적용해 비교 공정성 유지
- G1 gate의 VRAM 기준도 현실에 맞게 수정: 20GB 이하 → **11.5GB 이하**
- filler model(Qwen2.5-Coder-7B-Instruct, bf16 약 15GB)도 12GB를 넘으므로 4-bit 또는 8-bit
  quantization이 필요. frozen 모델이라 모든 조건에 동일하게 적용되므로 비교 공정성은
  유지되지만, 논문 한계 절에 명시할 것.

### 데이터 조사
- `LLM4Binary/decompile-bench`: 함수쌍 2,233,092개, arrow 포맷, 필드는 name/code/asm/file.
  asm 필드는 계획대로 쓰지 않음.
- `LLM4Binary/decompile-bench-bins`: 컴파일된 바이너리 모음, 140GB split zip.
- `LLM4Binary/decompile-eval`: humaneval/mbpp/github 세 split. **`ghidra_pseudo` 필드가
  이미 들어 있어서** 평가셋에는 Ghidra를 돌릴 필요가 없음. Ghidra 실행은 학습셋(bins)에만.
- 평가셋에는 opt(O0–O3)와 ida_pseudo도 있어 B0 조건과 stratified sampling에 바로 쓸 수 있음.

### 결정 사항
1. Gate 0용 미니셋은 140GB를 다 받는 대신 **volume 001(2GB)과 마지막 volume(central
   directory 포함)만 받아 split zip에서 부분 추출**로 바이너리를 확보. 100k 구축 때
   필요한 volume만 추가 다운로드.
2. 대용량 데이터는 ext4 쪽(`~/pavlov-data`, 저장소에서 `data/bulk`로 심볼릭 링크)에 저장.
   WSL2에서 NTFS(/mnt/d) I/O가 느리기 때문.
3. Ghidra 12.1.2 + OpenJDK 21 조합 사용 (호환 확인).
4. 평가는 decompile-eval의 ghidra_pseudo/ida_pseudo를 그대로 사용해 작업량 절감.

### 추가로 확인한 것
- bins는 71개 volume (처음에 73개로 본 것은 목록이 잘려서 생긴 오인). volume당 정확히
  2,000,000,000 bytes.
- central directory 파싱 성공: 바이너리 85,250개, opt별 약 21k개씩. O0은 vol 001부터,
  O1은 005, O2는 010, O3는 014부터 시작.
- 바이너리 이름은 `유저[P]저장소[P]build_OPT[P]바이너리`, 데이터셋의 file 필드는
  `/유저[P]저장소/경로` 형식이라 프로젝트 단위로 바로 대응됨.
- decompile-eval의 C 함수 수: humaneval 656 (164×4 opt), mbpp 3,896, github 43,281
  → 600개 stratified sampling에 여유 충분.
- 평가셋 ghidra_pseudo의 tree-sitter 파싱 성공률 49/50 (C 샘플), source→skeleton 변환은 50/50.

### 세션 1 진행 상황
- [x] venv 구성 (sudo가 없어 uv로 우회), 데이터셋 8.5GB + 평가셋 다운로드, LLM4Decompile clone
- [x] split zip 부분 추출기 (`src/zipsplit_extract.py`) — central directory Zip64 파싱 검증
- [x] tree-sitter-c anonymizer (`src/anonymize.py`) — 실데이터 50/50 파싱 통과, literal 분류 단위테스트 통과
- [x] Ghidra postScript (`scripts/ExportPseudoC.java`)와 배치 러너 (`scripts/run_ghidra_batch.sh`)
- [x] matching (`src/match_functions.py`), 미니셋 선정 (`src/select_miniset.py`), 학습셋 변환 (`src/build_dataset.py`)
- [ ] bins vol 001/005/010/014 다운로드 (진행 중), Ghidra 12.1.2 다운로드 (진행 중)
- [ ] 미니셋으로 파이프라인 전체 통과 → Gate 0 판정
- [ ] CUDA torch 설치 (네트워크 경합으로 1회 실패, 재시도 예정) → 2k pilot + QLoRA Gate 1

## 2026-06-12 — 세션 2: Gate 0 통과, 균형 학습셋, 학습 핸드오프

### 방향 변경 (사용자 지시)
- **학습은 이 머신에서 하지 않기로 함.** Gate 1(QLoRA pilot)부터는 별도 학습 머신에서 진행.
- 이 머신의 역할은 데이터 구축, 스크립트 검증, 핸드오프 문서(`docs/training_handoff.md`)까지.

### Gate 0 통과
- O0 미니셋 12개 바이너리: matching yield **83.7%** (기준 60% 이상), 9,259쌍.
- O1–O3 30개 바이너리: yield 68.6%, 18,550쌍. 최적화 빌드에서는 inlining으로 함수가
  사라지므로 O0보다 낮은 것이 정상.
- 중간 점검 때 59.9%가 나왔던 것은 프로젝트별 바이너리 일부만 처리한 상태에서
  분모(프로젝트 전체 record 수)가 부풀려져 있었기 때문. 전체 처리 후 해소 확인.
- skeleton 변환: source가 파싱되는 쌍 기준 99.9% 성공 (실패 20 / 27,809).
- 탈락의 주원인은 데이터셋 원본 코드가 함수 단독으로는 파싱되지 않는 경우(매크로,
  K&R 스타일 등)로 약 12%.

### 학습셋 산출물
tokenizer 기준 4096 token 이하, (project, func_name, opt) 중복 제거.
- `data/matched/balanced_train.jsonl` — 17,760건, O0–O3 각 4,440
- `data/matched/balanced_val400.jsonl` — 400건 (opt당 100, 학습에 쓰지 않음)
- `data/matched/pilot2k_balanced.jsonl` — pilot용 2,000건 (opt당 500), 샘플링 seed 42

### 학습 스크립트 사전 검증 (학습 머신으로 옮기기 전에 버그를 잡아두는 목적)
- AR arm: Qwen2.5-Coder-0.5B 4-bit로 학습 루프 전체를 3 step 돌려 확인
  (loss 1.09→0.96, VRAM 4.3GB). 연구 결과와는 무관한 동작 확인용.
- diff arm: masked diffusion loss 단위테스트 5/5 통과 (`tests/test_diff_loss.py`) —
  target 토큰만 mask되는지, 샘플당 최소 1개 mask, oracle 모델이면 loss가 0에 가는지,
  seed 고정 시 재현되는지.
- DiffuCoder config 확인: mask_token_id=151666, AutoModel→DreamModel. 스크립트의 로드
  경로·mask id 탐색 로직과 일치.
- CUDA torch 2.11.0+cu128 설치 완료, RTX 5070(sm_120) 정상 인식.
- `requirements-lock.txt`로 의존성 고정. 단, transformers 5.11과 DiffuCoder remote code의
  호환은 7B를 로드해 봐야 알 수 있어 미확인 — 핸드오프 문서에 대응법 기재.

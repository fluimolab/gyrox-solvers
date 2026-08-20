# 경로 C 변환기·solve 러너 승격 계획 (S4 — 사전 등재 정본)

> 근거: `gyrox/m1/S4-EXEC-PLAN.md` rev9 §0-②·DESIGN §7.2·DETAIL §7.2·§13-⑦("승격 계획 문서 — 이관 PR 이전
> 커밋"). **본 문서의 케이스 등재는 불변이다** — 등재 후 파라미터 변경 금지, 미재현 시 실행계획 개정·새
> ID·해당 캠페인 재수행(무단 조정 = 사전 등재 무효). ①′ 문서 리뷰(codex max)가 본 문서의 실재·산술을
> 판정한다(미비 시 PR 병합 불가).
> 스펙 표기: `gyrox` 본체 contracts 스키마(geometry-spec.v1·discretization-spec.v1·solve-spec.v1) 어휘.
> fixture 정본 = `gyrox` anchors ①(gyroid-core-30@v1 — envelope 0.03³·cell 4mm·wall 0.4mm·pitch 1e-4 m·
> maxIters 2000·운전점 hot 0.023490376702 kg/s@333.15 K·cold 0.023821510837 kg/s@293.15 K).

## 1. 승격 검증 절차 (⒜⒝ — DESIGN §7.2 축자)

- **⒜ 결정론·품질**: fixture 5회 + 아래 외부 3세트 각 3회 변환 — 회차 간 `meshCombinedSha` **단일성** +
  checkMesh(`-allRegions -meshQuality`) **전항 위반 0**(임계 = discretization qa const).
- **⒝ 동결 재현**: fixture(CL2 labels.vti, sha256 `919d7f52…`) 변환의 `meshCombinedSha` =
  Phase C 동결 `9f3eceb5…`(anchors ②) — 대조 소재 = convert-report 계약 필드·독립 재계산(DT-MC-01) 병행.
- **전향 핀**: 이미지 digest + `openfoam/mesh/PINNED.lock`(Python·numpy exact) — **핀 변경 = ⒜⒝ 재검증**.
- allowlist `mesh` 등재는 ⒜⒝ PASS 기록 링크 동반만(D-D12).

## 2. 외부 3세트 (fixture 밖 — mesh 승격 ⒜ 대상. N = envelope V / pitch³ 근사(라벨≠0 점유율 ≈100% — φ 사전 합))

| id | envelope sizeM | cell | wall | pitch | N(근사) | 용도 |
|---|---|---|---|---|---|---|
| **EXT-A** | [0.012, 0.012, 0.012] | 4 mm | 0.4 mm | 2e-4 m | ≈216,000 | mesh ⒜ ×3회 + **파일럿·정상성 음성·int-s4 소형 솔브 기하 겸용** |
| **EXT-B** | [0.03, 0.03, 0.03] | 3 mm | 0.3 mm | 1e-4 m | ≈27.0M | mesh ⒜ ×3회(CL2급 규모 축 — 변환 peak ≈6 GiB·솔브 없음) |
| **EXT-C** | [0.03, 0.024, 0.018] | 5 mm | 0.5 mm | 1.5e-4 m | ≈3.84M | mesh ⒜ ×3회 + **비정육면체 — 포트 면적 축 판별(CT-CR 판별 fixture 실물 축)** |

공통: gyroid·splitting fullwall·counterflow-x 포트 4종(fixture 위상 동형 — patch-map 11행형 불변).

## 3. 파일럿 케이스 (E9 사정 입력 — CP-10)

- **기하 = EXT-A** · **maxIters 550**(계약 하한 — 절단 후 440 ≥ 2W=400 성립·리뷰 검증 완료) · 운전점 =
  fixture mdot × (0.012/0.03)² = **hot 3.7585e-3 kg/s@333.15 K·cold 3.8114e-3 kg/s@293.15 K**(면적비 축소 —
  BC 전 시간 상수) · numerics = 카탈로그 const(EC25 포함) · 분해수 16.
- **예상 소요**: t_iter ≈ N/(6334.6×16) ≈ **2.1 s/iter** → 550 iter ≈ **20 min**(전처리·수확 포함 ≤1 h —
  **파일럿 총 예산 ≤4 h [초안] 내**).
- **측정 provenance(리포트 스키마 — S4-EXEC-PLAN E9)**: phase별(render/decompose/solve/reconstruct)
  wall/exec clock = NDJSON phase 이벤트+로그 타임스탬프 · start/end iter = functions.cfg 시계열 ·
  peak RSS = cgroup `memory.peak` · cells/cores·이미지 3식별자·스케일 불확도(N 선형 외삽의 캐시·대역폭
  비선형 병기) — `m1/s4-campaign/pilot-report.json`.

## 4. 정상성 음성 케이스 (CP-04 — 불변 등재)

- **기하 = EXT-A** · **maxIters 600 [초안 — 시험 fixture 파라미터]** · 운전점 = 파일럿과 동일(**BC 전 시간
  상수 — 시계열 forcing 없음**: 미정착 유도 축 = 짧은 maxIters의 초기 과도 구간 종료) · 분해수 16.
- **기대 붕괴 축 = dpHot drift**: 판정 술어(S4-EXEC-PLAN §2 축자) = `convergenceAxes.perQoI.dpHotPa.drift.pass
  = false ∨ span.pass = false` ∧ eb·mass·sign·finite 전부 pass ∧ ¬insufficient ∧ exit 10.
- 이미지 digest = 실행 전 `pre-run.json` 고정(빌드 record 참조 — 최종 cp-manifest가 해시·조상 검증).
- **미재현 처분**: 케이스 무단 조정 금지 — 실행계획 개정·새 ID·해당 캠페인 재수행.
- 산술(등재 근거): n=600 → transientFrac 0.2 절단 후 480 ≥ 2W=400(insufficient 미발동)·600 ≥ 하한 550·
  minIterationsForJudgment 400 충족 — 리뷰 양측 산술 검증 완료(계획 §8).

## 5. int-s4 소형 솔브 기하 (참고 — 시험 fixture 축)

실 러너 integration(XT-RR solve-bearing 8건)의 기하 = EXT-A·maxIters 550~600 대역(케이스별 시험 파일이
등재값 인용) — 예상 케이스당 ≈20~25 min은 D-I24 testTimeout 600 s와 별개 축(시험은 취소·checkpoint 등
**완주 불요 시나리오**가 다수: 취소는 grace 내 종결·checkpoint는 주기 1회 산출 시점까지만 — 완주 필요
시나리오(재개 소비 등)는 시험 내 maxIters 하한 550 축소 기하(pitch 2.5e-4 → N ≈ 110,592·t_iter ≈ 1.1 s →
완주 ≈10 min)를 별도 등재값으로 사용).

| id | envelope | pitch | N(근사) | t_iter(근사) | 용도 |
|---|---|---|---|---|---|
| **EXT-A2** | [0.012, 0.012, 0.012]·cell 4 mm·wall 0.4 mm | 2.5e-4 m | ≈110,592 | ≈1.1 s | int-s4 완주 필요 시나리오 전속 |

## 6. 산출·기록

⒜⒝ 산출 = `gyrox/m1/s4-campaign/promotion-{a,b}.json`(캠페인 러너 기계 생성·CP-01/02) · 본 문서의 케이스
등재값은 CP 매니페스트·pre-run.json이 spec 해시로 결속 · 실측 확정치는 `S4-EXEC-PLAN §10`에 전사.

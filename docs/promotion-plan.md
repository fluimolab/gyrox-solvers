# 경로 C 변환기·solve 러너 승격 계획 (S4 — 사전 등재 정본)

> 근거: `gyrox/m1/S4-EXEC-PLAN.md` rev9 §0-②·DESIGN §7.2·DETAIL §7.2·§13-⑦("승격 계획 문서 — 이관 PR 이전
> 커밋"). **본 문서의 케이스 등재는 불변이다** — 등재 후 파라미터 변경 금지, 미재현 시 실행계획 개정·새
> ID·해당 캠페인 재수행(무단 조정 = 사전 등재 무효). ①′ 문서 리뷰(codex max)가 본 문서의 실재·산술을
> 판정한다(미비 시 PR 병합 불가).
> 스펙 표기: `gyrox` 본체 contracts 스키마(geometry-spec.v1·discretization-spec.v1·solve-spec.v1·post-spec.v1) 어휘.
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

- **기하 = EXT-A** · **maxIters 550**(스키마 하한 500 + resolve 교차 가드 500+50 = **유효 실행 하한 550** — 두 하한 구분(①′ m1)·절단 후 440 ≥ 2W=400 성립) · 운전점 =
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
- **기대 붕괴 축 = dpHot drift/span 단독 붕괴**(①′ m2 표제 정합): 판정 술어(S4-EXEC-PLAN §2 축자) = `convergenceAxes.perQoI.dpHotPa.drift.pass
  = false ∨ span.pass = false` ∧ eb·mass·sign·finite 전부 pass ∧ ¬insufficient ∧ exit 10.
- 이미지 digest = 실행 전 `pre-run.json` 고정(빌드 record 참조 — 최종 cp-manifest가 해시·조상 검증).
- **미재현 처분**: 케이스 무단 조정 금지 — 실행계획 개정·새 ID·해당 캠페인 재수행.
- 산술(등재 근거): n=600 → transientFrac 0.2 절단 후 480 ≥ 2W=400(insufficient 미발동)·600 ≥ 하한 550·
  minIterationsForJudgment 400 충족 — 리뷰 양측 산술 검증 완료(계획 §8).

## 5. int-s4 실 러너 케이스 고정표 (①′ 리뷰 M2 처분 — 9건 ID별(solve-bearing 8건) 기하·maxIters·종료조건 불변 등재. EXT-A2 폐기 → **EXT-A3**(§7.3 — N = 46,656·t_iter ≈ 0.46 s·완주 ≈ 4.2 min·testTimeout 600 s 내 여유 347 s). **checkpoint 원천 = 취소 체인 최종 checkpoint publish**(DESIGN §6.8 + §6.3 ⓒ(①′ R2 인용 정정) — 주기 900 s 도달 불요: 시험은 주기 생산에 의존하지 않는다))

| ID | 기하 | maxIters | 종료조건(완주 여부) | 예상 소요 |
|---|---|---|---|---|
| XT-RR-01(취소·checkpoint 보존 V4) | EXT-A3 | 550 | **progress iter ≥ 20 관측 시 취소 발동**(checkpoint iter = 취소 시점 — XT-RR-04 fixture 원천·**i ≤ 30 관측 조건**) — 완주 불요 | ≤2 min |
| XT-RR-02(specHash 변조 exit 40) | EXT-A3 | 550 | 재개 스테이징 즉시 판정(XT-RR-01 산출 checkpoint 재사용·meta 변조) — 완주 불요 | ≤2 min |
| XT-RR-03(sidecar 부재 콜드) | EXT-A3 | 550 | 콜드 재계산 개시 관측 — 완주 불요 | ≤3 min |
| XT-RR-04(재개 소비 V5) | EXT-A3 | **550(XT-RR-01과 동일 spec — ①′ R3 M1: specHash 일치가 재개 전제·러너는 자격 검사 비수행(executor 소관 — S3 더미 기검증))** | **러너 전용 재개 시험 명시(①′ R2 M2)** — checkpoint 원천 = XT-RR-01 취소 산출물(교차 fixture·iter i = 취소 시점 — **i ≤ 30 관측 조건 assert**: 재개 후 신규 표본 550−i ≥ 520 → transientFrac 절단 후 ≥416 ≥ 2W=400·판정 성립)·/work/input 직접 스테이징 → 재개 완주·창 리셋 판정. **제품 경로 주기 checkpoint 재개는 소형 유도 불가**(주기 900 s 계약 상수 — 한계 정직 기재): 생산 축 = 관통 캠페인 관측·소비 축 = 본 러너 시험 | ≤8 min |
| XT-RR-05(decompN 콜드) | EXT-A3 | 550 | **RR-01 산출 checkpoint+유효 sidecar 재사용·specHash `20f867…` 불변·decompN만 불일치(checkpoint 16 → 재개 분해수 8 주입)** — exit 40 미발동 ∧ 콜드 재계산 전환 assert·완주 불요(①′ R4 명세) | ≤3 min |
| XT-RR-06(캡·취소 동시) | EXT-A3 | 550 | 축소 maxWallClockSec 주입(시험 executorPolicy — 해시 불참여)·완주 불요 | ≤3 min |
| XT-RR-07(mesh publish 실검증) | EXT-A3 mesh 산출 | — (solve 없음) | publish 가드 판정 | ≤2 min |
| XT-RR-08(관통 manifest 대조) | **EXT-A4**(§7.4 — post z=0.015 내부 성립·①′ R5 재선정) | 550 | **완주 필요**(4스테이지 관통·manifest 대조 — post 실행 포함·testTimeout 600 s 기본값 내 산술 성립) | ≤8 min(solve ≈5.9 min+여유 248 s — 파일럿 검증) |
| XT-RR-09(estimate 정합) | EXT-A3 | 550 | 제출·admit 단계 판정 — solve 기동 불요 | ≤1 min |

## 6. 산출·기록

⒜⒝ 산출 = `gyrox/m1/s4-campaign/promotion-{a,b}.json`(캠페인 러너 기계 생성·CP-01/02) · 본 문서의 케이스
등재값은 CP 매니페스트·pre-run.json이 spec 해시로 결속 · 실측 확정치는 `S4-EXEC-PLAN §10`에 전사.

## 7. 4-spec canonical 전문 (①′ 리뷰 M1 처분 — 기계 판독 등재. 공통(§7.1~7.3): geometry = EXT-A 기하·materials/numerics/convergence = fixture 전개값 불변·mdot = fixture × 면적비 0.16 정확 십진 리터럴(hot 0.00375846027232 · cold 0.00381144173392) — **예외 §7.4 = EXT-A4 전용 기하·면적비 0.18**. 변형(§7.2·§7.3·§7.4) spec 해시 = pre-run.json 기계 산출·결속)

### 7.1 파일럿 (EXT-A·pitch 2e-4·maxIters 550) — resolver 산출 spec 해시(①′ R2 검증기 실측 등재): geometry `a9d780ba7f492e9849e370a388f51c4f098556c4606c7664a3df079da1b4798d` · discretization `b16de52d35a1ca669619c41b0154638bc055b788018512a388138614b4bbf93f` · solve `20f867b63871a487fba1d1babe048dfd2d9a6bc308e001e694ec934e842d5cb4` · post `50a010dd49da635960234e107b641d9bd101f3f938153fcb67c7737f5d570893`

> post-spec `offsetM`은 **계약 const 0.015 전사**(스키마 상수 — ①′ R2 M1 정정). **파일럿·음성은 post 스테이지
> 비실행**(solve 러너 단위 캠페인 축 — 단면 추출과 무관·4-spec 완결성은 해시·resolve 정합용). 변형 케이스
> (§7.2·§7.3)의 spec 해시는 캠페인 `pre-run.json`이 기계 산출·결속.

```json
{"geometry": {
 "kind": "geometry-spec",
 "payload": {
  "cellSizeM": {
   "kind": "const",
   "valueM": 0.004
  },
  "envelope": {
   "kind": "box",
   "sizeM": [
    0.012,
    0.012,
    0.012
   ]
  },
  "pattern": "gyroid",
  "ports": [
   {
    "domain": "cold",
    "face": "+x",
    "id": "cold-inlet"
   },
   {
    "domain": "cold",
    "face": "-x",
    "id": "cold-outlet"
   },
   {
    "domain": "hot",
    "face": "-x",
    "id": "hot-inlet"
   },
   {
    "domain": "hot",
    "face": "+x",
    "id": "hot-outlet"
   }
  ],
  "splitting": "fullwall",
  "wallThicknessM": {
   "kind": "const",
   "valueM": 0.0004
  }
 },
 "schemaVersion": 1
},
 "discretization": {
 "kind": "discretization-spec",
 "payload": {
  "qa": {
   "maxBoundarySkewness": 20,
   "maxConcave": 80,
   "maxInternalSkewness": 4,
   "maxNonOrtho": 65,
   "minArea": -1,
   "minDeterminant": 0.001,
   "minEdgeLength": -1,
   "minFaceWeight": 0.05,
   "minTetQuality": 1e-15,
   "minTriangleTwist": -1,
   "minTwist": 0.02,
   "minVol": 1e-18,
   "minVolRatio": 0.01
  },
  "route": "voxel-hexa",
  "voxelPitchM": 0.0002
 },
 "schemaVersion": 1
},
 "solve": {
 "kind": "solve-spec",
 "payload": {
  "backend": "openfoam-cht",
  "bc": [
   {
    "featureRef": "port:hot-inlet",
    "kind": "flowRateInlet",
    "mdotKgS": 0.00375846027232,
    "tK": 333.15
   },
   {
    "featureRef": "port:cold-inlet",
    "kind": "flowRateInlet",
    "mdotKgS": 0.00381144173392,
    "tK": 293.15
   }
  ],
  "convergence": {
   "divergenceCheckScope": "judgedQoI+signRules",
   "ebMaxPct": 1,
   "ebWarnPct": 0.7,
   "finiteRequired": true,
   "instSpanPct": 0.5,
   "judgedQoI": [
    "dpHotPa",
    "dpColdPa",
    "qW"
   ],
   "massImbalanceMaxPct": 0.1,
   "minIterationsForJudgment": 400,
   "residualBlowupFactor": 1000,
   "signRules": {
    "flowDirection": {
     "cold": {
      "inlet": "negative",
      "outlet": "positive"
     },
     "hot": {
      "inlet": "negative",
      "outlet": "positive"
     }
    },
    "pressureDirection": {
     "dpColdPa": "positive",
     "dpHotPa": "positive"
    },
    "wallHeatFluxDirection": {
     "cold": "positive",
     "hot": "negative"
    }
   },
   "signSanity": true,
   "tOutDriftK": 0.1,
   "tOutSpanK": 0.1,
   "transientFrac": 0.2,
   "windowIters": 200,
   "windowMeanDriftPct": 0.5,
   "windowMeanMinIters": 500,
   "windowMeanSpanPct": 0.5,
   "windowMeanTailW": 200
  },
  "limits": {
   "maxIters": 550
  },
  "materials": {
   "cold": {
    "cpJKgK": 4182,
    "muPaS": 0.001002,
    "prandtl": 7.01,
    "rhoKgM3": 998.2
   },
   "hot": {
    "cpJKgK": 4183,
    "muPaS": 0.000467,
    "prandtl": 2.99,
    "rhoKgM3": 983.2
   },
   "solid": {
    "cpJKgK": 900,
    "kWMK": 237,
    "rhoKgM3": 2700
   }
  },
  "numerics": {
   "energyCouplingIters": 25,
   "hTol": 1e-06,
   "relaxation": {
    "fluid": {
     "U": 0.3,
     "h": 0.5,
     "pRgh": 0.7,
     "rho": 1
    },
    "solid": {
     "h": 0.7
    }
   },
   "writePrecision": 10
  },
  "solver": "chtMultiRegionSimpleFoam"
 },
 "schemaVersion": 1
},
 "post": {
 "kind": "post-spec",
 "payload": {
  "tier1": {
   "summary": true,
   "timeseries": true
  },
  "tier2": {
   "slices": [
    {
     "axis": "z",
     "fields": [
      "p",
      "T"
     ],
     "offsetM": 0.015
    }
   ]
  }
 },
 "schemaVersion": 1
}}
```

### 7.2 정상성 음성 — base = §7.1 전문·**RFC 6902 JSON Patch**(①′ R2 M3 — 기계 판독 형식)

```json
[{"op": "replace", "path": "/solve/payload/limits/maxIters", "value": 600}]
```

### 7.3 int-s4 소형 완주 기하 EXT-A3 — base = §7.1 전문·**RFC 6902 JSON Patch**(①′ R5 재선정 — **geometry 하드 가드 `wall < 1.5×pitch` 충족**: pitch 2.5e-4·1.5×0.25 mm = 0.375 ≤ wall 0.4 mm·envelope [0.009]³ = 36³ 정수 분할·인렛 면적비 0.09 mdot 재산정)

```json
[{"op": "replace", "path": "/geometry/payload/envelope/sizeM", "value": [0.009, 0.009, 0.009]},
 {"op": "replace", "path": "/solve/payload/bc/0/mdotKgS", "value": 0.00211413390318},
 {"op": "replace", "path": "/solve/payload/bc/1/mdotKgS", "value": 0.00214393597533},
 {"op": "replace", "path": "/discretization", "value": {
 "kind": "discretization-spec",
 "payload": {
  "qa": {
   "maxBoundarySkewness": 20,
   "maxConcave": 80,
   "maxInternalSkewness": 4,
   "maxNonOrtho": 65,
   "minArea": -1,
   "minDeterminant": 0.001,
   "minEdgeLength": -1,
   "minFaceWeight": 0.05,
   "minTetQuality": 1e-15,
   "minTriangleTwist": -1,
   "minTwist": 0.02,
   "minVol": 1e-18,
   "minVolRatio": 0.01
  },
  "route": "voxel-hexa",
  "voxelPitchM": 0.00025
 },
 "schemaVersion": 1
}}]
```

(적용 대상 ID별 Patch 규칙(①′ R3 M1 지시): XT-RR-01·02·03·04·05·06·09 = base(§7.1) + 본 §7.3 Patch·solve
maxIters 550 = base 그대로 / XT-RR-08 = §7.4 / XT-RR-07 = §7.3의 mesh 산출까지만. **N = 36³ = 46,656 ·
t_iter ≈ 0.460 s · 완주 550 ≈ 253 s(4.2 min)** — testTimeout 600 s 내 여유 347 s(§5 표).)

### 7.4 XT-RR-08 전용 기하 EXT-A4 — base = §7.1 전문·RFC 6902 JSON Patch (①′ R5 재선정 — 하드 가드 충족 pitch 2.5e-4·envelope [0.0075, 0.0075, 0.018](30×30×72 정수 분할)·post z=0.015 내부 성립·인렛 면적 y·z = 0.0075×0.018 = 1.35e-4 m² → 면적비 0.15·900 s 문구 철회 유지)

```json
[{"op": "replace", "path": "/geometry/payload/envelope/sizeM", "value": [0.0075, 0.0075, 0.018]},
 {"op": "replace", "path": "/discretization/payload/voxelPitchM", "value": 0.00025},
 {"op": "replace", "path": "/solve/payload/bc/0/mdotKgS", "value": 0.0035235565053},
 {"op": "replace", "path": "/solve/payload/bc/1/mdotKgS", "value": 0.00357322662555}]
```

(N = 30×30×72 = **64,800** · t_iter ≈ **0.639 s** · 완주 550 ≈ **352 s(5.9 min)** — 전후처리·기동·수확 여유
**248 s**: 파일럿 phase별 실측이 충분성 검증(초과 반증 시 본 문서 개정·재수행). mdot = fixture × 0.15 정확
십진 리터럴. 변형 spec 해시 = pre-run.json 기계 산출·결속.)

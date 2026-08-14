#!/usr/bin/env python3
"""QoI 머지 게이트 검사 — 게이트 ③′(c)ⅱ (D-G01-5).

규칙(qoi-merge-gate.md 동결본이 정본): 제품 표면 코드에서 QoI 토큰을 노출하는 파일은
같은 파일 안에 검증 상태 고지 마커를 동반해야 한다 (ADR-006 D8-2 동반성 규칙의 기계 검사).

사용:
  qoi_merge_gate.py --files f1 f2 ...      # 명시 파일 검사 (CI: PR diff 파일 목록)
  qoi_merge_gate.py --scope dir1 dir2 ...  # 트리 전체 검사

종료 코드: 0 = 통과, 30 = 위반 (파일·행 목록 출력), 2 = 사용 오류.
결정론: 등록 토큰·마커 외 어떤 휴리스틱도 쓰지 않는다. 목록 변경 = 게이트 개정(③′(e) 절차).
"""
import argparse, json, re, sys
from pathlib import Path

# 등록 QoI 노출 토큰 (D8-1/D8-2 대상 QoI — 대소문자 무시, 단어 경계)
QOI_TOKENS = [
    r"qWall", r"q_wall", r"heatOutput", r"heat_output", r"heatTransfer", r"heat_transfer",
    r"epsilonNTU", r"effectiveness", r"\bNTU\b",
    r"dpCold", r"dp_cold", r"deltaPCold", r"dpHot", r"dp_hot", r"deltaPHot",
    r"\bdpHotPa\b", r"\bdpColdPa\b", r"\bqW\b", r"\bqHotW\b", r"\bqColdW\b",
    r"\bntu\b", r"\buaWK\b", r"\bjFactor\b", r"\bfFactor\b", r"\btOutHotK\b",
    r"\btOutColdK\b", r"\benergyBalancePct\b", r"\bmdotHotKgS\b", r"\bmdotColdKgS\b",
]
# 등록 고지 마커 — 하나 이상 존재해야 함 (D8-2 정본 문구 식별자 / API 필드 규약 / 규격 참조)
NOTICE_MARKERS = [
    "D8-2", "D8-1", "QOI_VERIFICATION_NOTICE", "unverified_underprediction",
    "judgment_not_established", "qoi-verification-notice",
]
# 검사 대상 확장자 (제품 표면 코드·스키마·템플릿)
EXTS = {".ts", ".tsx", ".js", ".jsx", ".py", ".cs", ".sql", ".html", ".vue", ".svelte", ".json", ".yaml", ".yml"}

QOI_RE = re.compile("|".join(QOI_TOKENS), re.IGNORECASE)
def has_marker(text: str) -> bool:
    return any(m in text for m in NOTICE_MARKERS)

def scan_file(p: Path):
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [{"file": str(p), "line": 0, "token": f"<read error: {e}>"}]
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        m = QOI_RE.search(line)
        if m:
            hits.append({"file": str(p), "line": i, "token": m.group(0)})
    if hits and has_marker(text):
        return []  # 노출 있으나 고지 마커 동반 — 통과
    return hits    # 노출 있고 마커 없음 — 위반 (빈 리스트면 노출 자체가 없음)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--scope", nargs="*", default=[])
    ap.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    a = ap.parse_args()
    targets = [Path(f) for f in a.files]
    for s in a.scope:
        targets += [p for p in Path(s).rglob("*") if p.suffix in EXTS and p.is_file()]
    if not targets:
        print("no targets", file=sys.stderr); sys.exit(2)
    violations = []
    for p in sorted(set(targets)):
        violations += scan_file(p)
    result = {"checked": len(set(targets)), "violations": violations, "pass": not violations}
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        for v in violations:
            print(f"VIOLATION {v['file']}:{v['line']} token={v['token']} — QoI 노출에 고지 마커 부재 (D8-2 동반성)")
        print(f"[qoi-merge-gate] checked={result['checked']} violations={len(violations)} pass={result['pass']}")
    sys.exit(0 if result["pass"] else 30)

if __name__ == "__main__":
    main()

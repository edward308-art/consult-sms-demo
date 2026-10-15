"""상담 맥락 기반 상품 추천 (규칙 기반, 설명 가능).

- 금리·한도 같은 수치는 문자에 넣지 않는다(변동·오안내 위험). 조건은 상품설명서/영업점 확인으로 안내.
- 추천은 '직원이 검토해 선택'하고, 고객의 광고성 정보 수신 동의가 있을 때만 문자에 포함된다.
- 상품 정보는 데모용 가상 데이터이다. 실제 도입 시 상품 마스터(DB/API)로 교체한다.
"""

PRODUCTS = [
    {"id": "P01", "name": "매일모으기 자유적금", "category": "적금", "kind": "예금성",
     "tagline": "금액·날짜 자유 납입, 급여이체 시 우대 혜택",
     "topics": ["savings"], "signals": ["travel", "wedding", "monthly_saving", "salary"], "blockers": []},
    {"id": "P02", "name": "든든 목돈 정기예금", "category": "예금", "kind": "예금성",
     "tagline": "목돈을 일정 기간 안정적으로 예치",
     "topics": ["deposit"], "signals": ["lump_sum"], "blockers": []},
    {"id": "P03", "name": "월급통장 플러스", "category": "입출금", "kind": "예금성",
     "tagline": "급여이체 시 수수료 면제·우대 혜택",
     "topics": ["account"], "signals": ["salary"], "blockers": []},
    {"id": "P04", "name": "전세 안심 대출", "category": "대출", "kind": "대출",
     "tagline": "임대차계약 기반 전세자금 대출",
     "topics": ["jeonse"], "signals": ["house"], "blockers": ["debt_risk"]},
    {"id": "P05", "name": "비상금 마이너스통장", "category": "대출", "kind": "대출",
     "tagline": "급할 때 쓰는 소액 한도 대출",
     "topics": [], "signals": ["emergency"], "blockers": ["debt_risk"]},
    {"id": "P06", "name": "트래블 체크카드", "category": "카드", "kind": "카드",
     "tagline": "해외 결제·환전 우대, 여행 시 편리",
     "topics": ["fx"], "signals": ["travel", "fx"], "blockers": []},
    {"id": "P07", "name": "생활혜택 신용카드", "category": "카드", "kind": "카드",
     "tagline": "마트·주유·통신 생활 영역 혜택",
     "topics": ["card"], "signals": ["benefit_card"], "blockers": ["debt_risk"]},
    {"id": "P08", "name": "연금저축·IRP", "category": "연금", "kind": "투자성",
     "tagline": "노후 준비와 세액공제 혜택을 함께",
     "topics": ["pension"], "signals": ["retire"], "blockers": []},
    {"id": "P09", "name": "아이키움 적금", "category": "적금", "kind": "예금성",
     "tagline": "자녀 교육·성장 자금 준비",
     "topics": ["child"], "signals": ["child"], "blockers": []},
    {"id": "P10", "name": "안심거래 서비스(지연이체·한도관리)", "category": "서비스", "kind": "서비스",
     "tagline": "보이스피싱 예방, 이체 지연·한도 설정",
     "topics": ["lost"], "signals": ["fraud"], "blockers": []},
    {"id": "P11", "name": "사장님 사업자통장·운영자금", "category": "사업자", "kind": "예금성/대출",
     "tagline": "사업자 전용 입출금 및 운영자금 상담",
     "topics": ["biz"], "signals": ["biz"], "blockers": ["debt_risk"]},
    {"id": "P12", "name": "외화 환전·외화예금", "category": "외환", "kind": "예금성",
     "tagline": "앱 환율 우대 환전, 외화 보관",
     "topics": ["fx"], "signals": ["fx", "travel"], "blockers": []},
]

KIND_NOTE = {
    "대출": "대출상품: 적합성 확인·설명의무 대상(신용영향 안내). 직원 확인 필수",
    "투자성": "투자성 상품: 적합성·적정성 확인 및 설명서 교부 필수",
    "카드": "카드상품: 연회비·혜택 조건 안내 필요",
}

MIN_SCORE = 2.5


def recommend(analysis: dict, limit: int = 3):
    topic_by_id = {t["id"]: t for t in analysis["topics"]}
    signals = analysis["signals"]
    out = []
    for p in PRODUCTS:
        if any(b in signals for b in p["blockers"]):
            continue
        score, evid = 0.0, []
        for tid in p["topics"]:
            t = topic_by_id.get(tid)
            if t:
                score += 2 + min(t["score"], 4) * 0.5
                evid += t["hits"]
        for sid in p["signals"]:
            if sid in signals:
                score += 2
                evid += signals[sid]
        if score < MIN_SCORE:
            continue
        uniq = []
        for e in evid:
            if e not in uniq:
                uniq.append(e)
        out.append({
            "id": p["id"], "name": p["name"], "category": p["category"], "kind": p["kind"],
            "tagline": p["tagline"], "score": round(score, 1),
            "reason": "상담 중 " + ", ".join(f"'{e}'" for e in uniq[:3]) + " 관련 언급",
            "note": KIND_NOTE.get(p["kind"], ""),
        })
    out.sort(key=lambda r: -r["score"])
    return out[:limit]

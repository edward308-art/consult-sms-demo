"""상담 내용 분석·요약.

기본은 규칙 기반(외부 전송 없음). 환경변수 ANTHROPIC_API_KEY 가 있으면 LLM 요약을 시도하되
입력은 반드시 '마스킹된 텍스트'만 사용하고, 결과도 다시 마스킹한다. 실패하면 규칙 기반으로 폴백.
"""
import json
import os
import re
import urllib.request

TOPICS = [
    ("savings", "적금", ["적금", "자유적금", "정기적금", "매달", "월 납입", "납입"]),
    ("deposit", "예금", ["정기예금", "예금", "목돈", "예치", "만기"]),
    ("loan", "대출", ["대출", "신용대출", "담보", "상환", "한도", "DSR", "거치"]),
    ("jeonse", "전세자금대출", ["전세", "보증금", "임대차", "이사"]),
    ("card", "카드", ["카드", "체크카드", "신용카드", "결제일", "리볼빙", "포인트"]),
    ("lost", "분실·보안", ["분실", "도난", "재발급", "해킹", "보이스피싱", "지급정지", "지연이체"]),
    ("fx", "환전·해외", ["환전", "외화", "해외", "환율", "송금"]),
    ("pension", "연금·노후", ["연금", "IRP", "퇴직", "노후", "세액공제"]),
    ("mobile", "인터넷·모바일뱅킹", ["인터넷뱅킹", "모바일", "공동인증", "비대면"]),
    ("biz", "사업자·소상공인", ["사업자", "소상공인", "가게", "창업", "법인"]),
    ("child", "자녀·교육", ["자녀", "아이", "학자금", "교육", "출산", "육아"]),
    ("account", "계좌·입출금", ["통장", "계좌 개설", "급여", "월급", "자동이체", "수수료"]),
]

SIGNALS = {
    "travel": ["여행", "출국", "휴가"],
    "lump_sum": ["목돈", "퇴직금", "상속", "보너스", "만기"],
    "salary": ["월급", "급여", "직장"],
    "wedding": ["결혼", "혼수"],
    "house": ["전세", "주택", "보증금", "이사"],
    "child": ["자녀", "아이", "출산", "육아"],
    "retire": ["노후", "은퇴", "퇴직", "연금"],
    "biz": ["사업", "가게", "창업"],
    "fraud": ["보이스피싱", "피싱", "사기", "해킹", "지연이체"],
    "fx": ["환전", "환율", "달러", "외화", "해외"],
    "emergency": ["비상금", "급전", "급하게"],
    "benefit_card": ["혜택", "캐시백", "마트", "주유", "포인트"],
    "debt_risk": ["연체", "체납", "신용불량", "채무조정", "회생"],
    "monthly_saving": ["매달", "조금씩", "모으", "적금", "월 납입"],
}

SPEAKER = re.compile(r"^\s*(상담원|직원|행원|창구직원|텔러|상담사|고객|손님)\s*[:：]\s*(.*)$")
AGENT_ROLES = {"상담원", "직원", "행원", "창구직원", "텔러", "상담사"}

INFO_KW = re.compile(r"금리|이율|수수료|한도|기간|조건|우대|만기|납입|이자|환율|면제|혜택|상환|가능|선택|적용|\d")
ACTION_KW = re.compile(r"신청|방문|지참|서류|접수|발송|재발급|제출|예약|확인 후|해지|등록|설정|안내드립니다|배송|도와드리겠습니다")
REQ_KW = re.compile(r"희망|원해|알아보|궁금|하고 싶|싶어|필요|문의|찾고|잃어버|분실")
GREET = re.compile(r"안녕하세요|감사합니다|도와드릴까요|무엇을 도와|좋은 하루|수고하세요|문의사항|말씀해 주시|말씀하세요|어떤 문의")
FILLER = re.compile(r"^(?:네+|예|아+|음+|그래서|그럼|그러면|자|그리고|좋네요|알겠습니다)[,.\s]+")


def parse_turns(text: str):
    turns = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        m = SPEAKER.match(line)
        if m:
            role = "agent" if m.group(1) in AGENT_ROLES else "customer"
            turns.append([role, m.group(2).strip()])
        elif turns:
            turns[-1][1] += " " + line.strip()
        else:
            turns.append(["unknown", line.strip()])
    return [(r, u) for r, u in turns]


def split_sentences(u: str):
    parts = re.split(r"(?<=[.?!])\s+|(?<=[요다])\s+(?=[가-힣A-Z0-9])", u)
    return [p.strip() for p in parts if p and p.strip()]


def _clean(s: str, limit: int = 72) -> str:
    s = s.strip()
    for _ in range(3):
        s2 = FILLER.sub("", s)
        if s2 == s:
            break
        s = s2
    s = re.sub(r"\s+", " ", s).strip(" ,")
    if len(s) > limit:
        cut = s.rfind(" ", 0, limit)
        s = s[: cut if cut > 30 else limit].rstrip(" ,.") + "…"
    if s and s[-1] not in ".?!…":
        s += "."
    return s


def analyze(text: str):
    """토픽/생활 신호와 근거 키워드를 추출한다."""
    topics = []
    for tid, label, kws in TOPICS:
        hits = [k for k in kws if k in text]
        if hits:
            topics.append({"id": tid, "label": label, "score": sum(text.count(k) for k in hits), "hits": hits})
    topics.sort(key=lambda t: -t["score"])
    signals = {}
    for sid, kws in SIGNALS.items():
        hits = [k for k in kws if k in text]
        if hits:
            signals[sid] = hits
    return {"topics": topics, "signals": signals}


def _pick(cands, n):
    seen, out = set(), []
    for s in cands:
        key = re.sub(r"\W", "", s)[:14]
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= n:
            break
    return out


def summarize_rule(masked: str, analysis: dict) -> str:
    turns = parse_turns(masked)
    agent_s, cust_s = [], []
    for role, u in turns:
        for s in split_sentences(u):
            if "*" in s or len(s) < 8 or s.endswith("?"):
                continue
            if GREET.search(s) and not re.search(r"\d", s):
                continue
            (agent_s if role in ("agent", "unknown") else cust_s).append(s)

    info = [s for s in agent_s if INFO_KW.search(s) and not ACTION_KW.search(s)]
    info.sort(key=lambda s: -len(INFO_KW.findall(s)))
    info = [s for s in agent_s if s in set(info[:3])]
    action = [s for s in agent_s if ACTION_KW.search(s)]
    req = [s for s in cust_s if REQ_KW.search(s)]

    ids = {t["id"] for t in analysis["topics"]}
    tops = [t for t in analysis["topics"] if not (t["id"] == "loan" and "jeonse" in ids)]
    top = [t["label"] for t in tops[:2]]
    lines = []
    head = ", ".join(top) + " 관련 상담" if top else "일반 상담"
    topic_block = [f"- {head}"] + [f"- {_clean(s)}" for s in _pick(req, 1)]
    lines.append("■ 상담 내용\n" + "\n".join(topic_block))
    ib = [f"- {_clean(s)}" for s in _pick(info, 3)]
    if ib:
        lines.append("■ 안내드린 내용\n" + "\n".join(ib))
    ab = [f"- {_clean(s)}" for s in _pick([a for a in action if a not in info], 3)]
    if ab:
        lines.append("■ 후속 안내\n" + "\n".join(ab))
    return "\n\n".join(lines)


_SYSTEM = (
    "당신은 은행 상담 내용을 고객에게 보낼 문자(LMS) 요약으로 정리하는 도우미입니다. "
    "입력은 이미 개인정보가 마스킹된 상담 텍스트입니다. 규칙: (1) 개인정보나 마스킹 토큰을 복원·추측하지 말 것, "
    "(2) 상담에 없는 금리·한도·수수료 수치를 만들지 말 것, (3) 수익 보장·단정 표현 금지, "
    "(4) 아래 형식만 출력, 전체 450자 이내.\n"
    "■ 상담 내용\n- ...\n\n■ 안내드린 내용\n- ...\n\n■ 후속 안내\n- ..."
)


def summarize_llm(masked: str):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or os.environ.get("PUBLIC_DEMO") == "1":
        return None
    try:
        body = json.dumps({
            "model": os.environ.get("BANK_SMS_MODEL", "claude-sonnet-5-5"),
            "max_tokens": 700,
            "system": _SYSTEM,
            "messages": [{"role": "user", "content": masked}],
        }).encode()
        req = urllib.request.Request(
            os.environ.get("ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages"),
            data=body,
            headers={"content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
        return text if "■" in text else None
    except Exception:
        return None


def summarize(masked: str, analysis: dict):
    """(본문, 방식) 반환. 방식: 'llm' | 'rule'"""
    llm = summarize_llm(masked)
    if llm:
        return llm, "llm"
    return summarize_rule(masked, analysis), "rule"

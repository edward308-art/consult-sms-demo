"""개인정보(PII) 마스킹 엔진.

원칙
- 원문은 이 모듈을 통과한 뒤 즉시 폐기하고, 저장/요약/LLM 입력에는 마스킹본만 사용한다.
- 결과에는 "무엇이 몇 건 마스킹되었는지"만 남기고 원본 값은 남기지 않는다.
- 과마스킹(false positive)이 미마스킹(false negative)보다 안전하다는 전제로 설계했다.
"""
import re

_SURN = (
    "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구나민진지엄채원천방공현"
    "함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예경봉사부가복태목"
)

# 성+이름처럼 보이지만 일반 단어인 경우(과마스킹 방지)
_NAME_STOP = {
    "이번달", "이번주", "이번엔", "이용한", "이용료", "이자율", "정기예", "최대한", "최소한", "나머지",
    "주택담", "신용카", "전세자", "한도초", "금리인", "우대금", "이체한", "국민연", "기본금", "주거래",
    "정상적", "한도내", "고정금", "변동금", "성실하", "전자금", "방문하", "신청하", "연체없", "조건부",
}
_CTX_STOP = {"확인", "본인", "동일", "다른", "변경", "일치", "불일치", "기재", "입력", "필요", "가능"}

_PROV = (
    r"(?:서울(?:특별시)?|부산(?:광역시)?|대구(?:광역시)?|인천(?:광역시)?|광주(?:광역시)?|대전(?:광역시)?|"
    r"울산(?:광역시)?|세종(?:특별자치시)?|경기도?|강원(?:특별자치도|도)?|충청북도|충청남도|충북|충남|"
    r"전북(?:특별자치도)?|전라북도|전라남도|전남|경상북도|경상남도|경북|경남|제주(?:특별자치도|도)?)"
)


def _stars(n: str) -> str:
    return n[0] + "*" * (len(n) - 1)


def _keep_last(s: str, keep: int = 4) -> str:
    total = sum(c.isdigit() for c in s)
    seen = 0
    out = []
    for c in s:
        if c.isdigit():
            seen += 1
            out.append(c if seen > total - keep else "*")
        else:
            out.append(c)
    return "".join(out)


def _all_stars(s: str) -> str:
    return "".join("*" if c.isdigit() else c for c in s)


def _addr(m):
    tail = m.group(0)
    trail = tail[len(tail.rstrip()):]
    return f"{m.group('prov')} ***{trail}"


def _title_name(m):
    name = m.group("n")
    if name in _NAME_STOP:
        return m.group(0)
    return m.group(0).replace(name, _stars(name), 1)


def _ctx_name(m):
    name = m.group("n")
    if name in _CTX_STOP:
        return m.group(0)
    return f"{m.group('k')}{m.group('sep')}{_stars(name)}"


# (유형, 정규식, 치환함수) — 순서가 중요하다(구체적인 패턴 → 일반 패턴 → 이름)
RULES = [
    ("주민/외국인등록번호",
     re.compile(r"(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])[-\s]?[1-8]\d{6}(?!\d)"),
     lambda m: "******-*******"),
    ("카드번호",
     re.compile(r"(?<!\d)(?:\d{4}[- ]){3}\d{4}(?!\d)|(?<!\d)\d{16}(?!\d)"),
     lambda m: _keep_last(m.group(0))),
    ("운전면허번호",
     re.compile(r"(?<!\d)\d{2}-\d{2}-\d{6}-\d{2}(?!\d)"),
     lambda m: _all_stars(m.group(0))),
    ("사업자등록번호",
     re.compile(r"(?<!\d)\d{3}-\d{2}-\d{5}(?!\d)"),
     lambda m: _all_stars(m.group(0))),
    ("휴대전화번호",
     re.compile(r"(?<!\d)01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)"),
     lambda m: _keep_last(m.group(0))),
    ("일반전화번호",
     re.compile(r"(?<!\d)0(?:2|[3-6]\d)[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)"),
     lambda m: _keep_last(m.group(0))),
    ("계좌번호",
     re.compile(r"(?<!\d)\d{3,6}-\d{2,6}-\d{4,8}(?:-\d{1,3})?(?!\d)"),
     lambda m: _keep_last(m.group(0))),
    ("계좌번호",
     re.compile(r"(?P<k>계좌(?:번호)?\s*(?:는|은|가|이|:)?\s*)(?P<n>\d{10,14})(?!\d)"),
     lambda m: m.group("k") + _keep_last(m.group("n"))),
    ("생년월일",
     re.compile(r"(?P<k>생년월일|생일)(?P<s>\s*(?:은|는|이|가|:)?\s*)"
                r"(?:\d{2,4}\s*[년.\-/]\s*\d{1,2}\s*[월.\-/]\s*\d{1,2}\s*일?|\d{6,8})"),
     lambda m: f"{m.group('k')}{m.group('s')}****"),
    ("비밀번호/인증정보",
     re.compile(r"(?P<k>비밀번호|비번|OTP|인증번호|CVC|CVV|보안코드|PIN)(?P<s>\s*(?:는|은|가|이|:)?\s*)\d{3,8}"),
     lambda m: f"{m.group('k')}{m.group('s')}****"),
    ("이메일",
     re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
     lambda m: m.group(0)[0] + "***@" + m.group(0).split("@", 1)[1]),
    ("주소",
     re.compile(
         r"(?P<prov>" + _PROV + r")\s+(?:[가-힣0-9]+(?:시|군|구|읍|면|동|로|길|리|가)\s*){1,4}"
         r"(?:(?:\d+동|\d+호|\d+층|\d+(?:-\d+)?(?:번지|번길)?|[가-힣]+(?:아파트|빌라|오피스텔|맨션|타워))\s*){0,6}"),
     _addr),
    # --- 이름(문맥 기반) ---
    ("이름",
     re.compile(r"(?P<k>성함|성명|이름|고객명|예금주)(?P<sep>\s*(?:은|는|이|가|:)?\s*)"
                r"(?P<n>[가-힣]{2,3}?)(?=입니다|이에요|예요|이고|이라고|라고|이며|씨|님|고객|\s|[,.]|$)"),
     _ctx_name),
    ("이름",
     re.compile(r"(?<![가-힣])(?P<n>[" + _SURN + r"][가-힣]{2})(?=\s?(?:님|씨|고객))"),
     _title_name),
    ("이름",
     re.compile(r"(?<![가-힣])(?P<n>[" + _SURN + r"][가-힣]{2})(?=입니다|이에요|예요|이라고 합니다|라고 합니다)"),
     _title_name),
]


def mask(text: str, known_names=(), extra_terms=()):
    """마스킹된 텍스트와 {유형: 건수} 를 반환한다."""
    counts: dict[str, int] = {}

    def bump(k, n=1):
        counts[k] = counts.get(k, 0) + n

    out = text or ""

    # 1) 직원/시스템이 이미 알고 있는 고객명(성, 이름 각각 포함)
    for name in known_names:
        name = (name or "").strip()
        if len(name) < 2:
            continue
        variants = {name}
        if len(name) >= 3:
            variants.add(name[1:])
        for v in sorted(variants, key=len, reverse=True):
            c = out.count(v)
            if c:
                out = out.replace(v, _stars(v))
                bump("이름(고객명 지정)", c)

    # 2) 직원이 검토 중 추가 지정한 문구
    for term in extra_terms:
        term = (term or "").strip()
        if len(term) < 1:
            continue
        c = out.count(term)
        if c:
            out = out.replace(term, "[비공개]")
            bump("직원 추가 지정", c)

    # 3) 패턴 기반
    for label, rx, fn in RULES:
        def repl(m, fn=fn, label=label):
            res = fn(m)
            if res != m.group(0):
                bump(label)
            return res
        out = rx.sub(repl, out)
    return out, counts


def residual_pii(text: str, allow=()):
    """최종 문자에 마스킹되지 않은 개인정보 패턴이 남아있는지 검사한다.
    allow: 회사 대표번호 등 허용 문자열."""
    t = text or ""
    for a in allow:
        if a:
            t = t.replace(a, "")
    _, counts = mask(t)
    return counts

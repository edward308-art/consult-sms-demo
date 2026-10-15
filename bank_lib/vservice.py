"""Vercel(서버리스)용 무상태 업무 로직.

서버에는 아무것도 저장하지 않는다. 케이스 상태는 HMAC 서명과 함께 브라우저가 들고 다니며,
요청마다 서명을 검증하므로 브라우저에서 상태(승인 여부, 최종 문자 등)를 위·변조할 수 없다.
→ "승인 전 발송 불가", "승인 후 수정 시 승인 취소" 규칙이 그대로 서버에서 강제된다.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from masking import mask, residual_pii
from recommend import recommend
from summarize import analyze, summarize

KST = timezone(timedelta(hours=9))
MOBILE = re.compile(r"^01[016789]-?\d{3,4}-?\d{4}$")


def cfg():
    return {
        "bank_name": os.environ.get("BANK_NAME", "예시은행"),
        "cs_phone": os.environ.get("CS_PHONE", "1588-0000"),
        "optout_phone": os.environ.get("OPTOUT_PHONE", "080-000-0000"),
        "four_eyes": os.environ.get("FOUR_EYES", "0") == "1",
        "night_check": os.environ.get("NIGHT_AD_CHECK", "1") == "1",
    }


class Blocked(Exception):
    def __init__(self, reasons, state=None):
        super().__init__("; ".join(reasons))
        self.reasons = reasons
        self.state = state


def _secret():
    s = os.environ.get("APP_SECRET", "")
    if len(s) < 16:
        raise RuntimeError("APP_SECRET 환경변수(16자 이상)가 설정되지 않았습니다.")
    return s.encode()


def secret_ok():
    return len(os.environ.get("APP_SECRET", "")) >= 16


def _canon(case):
    return json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def pack(case):
    return {"case": case, "sig": hmac.new(_secret(), _canon(case), hashlib.sha256).hexdigest()}


def unpack(state):
    try:
        case, sig = state["case"], state["sig"]
    except Exception:
        raise ValueError("상태 정보가 없습니다. 상담을 다시 생성하세요.")
    good = hmac.new(_secret(), _canon(case), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(good, str(sig)):
        raise ValueError("상태 서명이 올바르지 않습니다(변조 또는 서버 설정 변경). 상담을 다시 생성하세요.")
    return case


def now_kst():
    return datetime.now(KST)


def _digits(s):
    return re.sub(r"\D", "", s or "")


def mask_phone(p):
    d = _digits(p)
    return f"{d[:3]}-****-{d[-4:]}" if len(d) >= 10 else "***"


def msg_bytes(text):
    return len(text.encode("cp949", errors="replace"))


def _allow():
    c = cfg()
    return [c["cs_phone"], c["optout_phone"]]


def _has_ad(t):
    return "(광고)" in t


def compose(body, recs, selected, consent, now=None):
    c = cfg()
    now = now or now_kst()
    head = f"[{c['bank_name']}] 상담 요약 안내\n{now.month}/{now.day} 상담 내용을 정리해 드립니다.\n\n"
    foot = f"\n\n※ 본 문자는 상담 요약이며 약관·상품설명서가 우선합니다. 문의 {c['cs_phone']}"
    ad = ""
    chosen = [r for r in recs if r["id"] in selected] if consent else []
    if chosen:
        lines = "\n".join(f"{i}) {r['name']} - {r['tagline']}" for i, r in enumerate(chosen, 1))
        ad = f"\n\n(광고) 맞춤 상품 안내\n{lines}\n자세한 조건은 앱·영업점에서 확인하세요.\n무료수신거부 {c['optout_phone']}"
    return head + body.strip() + foot + ad


def _audit(case, actor, action, detail=""):
    case["audit"].append({"ts": now_kst().isoformat(timespec="seconds"), "actor": actor, "action": action, "detail": detail})


def view(case):
    recs = [dict(r) for r in case["recs"]]
    for r in recs:
        r["selected"] = r["id"] in case["selected"] and bool(case["marketing_consent"])
    b = msg_bytes(case["final_text"])
    return {
        "id": case["id"], "created_at": case["created_at"], "status": case["status"],
        "consultant": case["consultant"], "reviewer": case.get("reviewer"),
        "recipient_masked": case["recipient_masked"], "marketing_consent": bool(case["marketing_consent"]),
        "transcript_masked": case["transcript_masked"], "findings": case["findings"],
        "topics": case["analysis"]["topics"], "summary_method": case["summary_method"],
        "body": case["body"], "recs": recs, "final_text": case["final_text"], "bytes": b,
        "msg_type": "SMS" if b <= 90 else "LMS", "has_ad": _has_ad(case["final_text"]),
        "approved_at": case.get("approved_at"), "sent_at": case.get("sent_at"),
        "residual": residual_pii(case["final_text"], _allow()), "audit": case["audit"],
    }


def _result(case, extra=None):
    out = {"view": view(case), "state": pack(case)}
    if extra:
        out.update(extra)
    return out


# ---------- 생성 ----------
def create_case(transcript, consultant, recipient, customer_name="", marketing_consent=False, now=None):
    transcript = (transcript or "").strip()
    consultant = (consultant or "").strip()
    if len(transcript) < 20:
        raise ValueError("상담 내용이 너무 짧습니다(20자 이상).")
    if len(transcript) > 6000:
        raise ValueError("상담 내용이 너무 깁니다(6,000자 이하).")
    if not consultant:
        raise ValueError("직원 ID를 입력하세요.")
    if not MOBILE.match((recipient or "").strip()):
        raise ValueError("고객 휴대폰 번호 형식이 올바르지 않습니다.")

    masked, findings = mask(transcript, known_names=[customer_name])
    del transcript
    analysis = analyze(masked)
    body, method = summarize(masked, analysis)  # PUBLIC_DEMO=1 이므로 항상 규칙 기반
    body, _ = mask(body, known_names=[customer_name])
    recs = recommend(analysis)
    for r in recs:  # 서명 안정성을 위해 소수는 문자열로 보관
        r["score"] = str(r["score"])
    selected = [r["id"] for r in recs[:2]] if marketing_consent else []
    ts = now or now_kst()
    case = {
        "id": f"C-{ts:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}", "created_at": ts.isoformat(timespec="seconds"),
        "status": "DRAFT", "consultant": consultant, "reviewer": None,
        "recipient": _digits(recipient), "recipient_masked": mask_phone(recipient),
        "marketing_consent": bool(marketing_consent), "transcript_masked": masked, "findings": findings,
        "analysis": analysis, "summary_method": method, "body": body, "recs": recs, "selected": selected,
        "final_text": compose(body, recs, selected, marketing_consent, ts), "approved_hash": None,
        "approved_at": None, "sent_at": None, "audit": [],
    }
    _audit(case, consultant, "CREATED",
           f"마스킹 {sum(findings.values())}건 {json.dumps(findings, ensure_ascii=False)}, 요약={method}, 추천={len(recs)}건")
    return _result(case)


# ---------- 수정 ----------
def edit_case(state, actor, body=None, extra_terms=(), selected=None, now=None):
    case = unpack(state)
    actor = (actor or "").strip()
    if not actor:
        raise ValueError("직원 ID를 입력하세요.")
    if case["status"] in ("SENT", "REJECTED"):
        raise Blocked([f"{case['status']} 상태의 건은 수정할 수 없습니다."])
    old_body = case["body"]
    extra = [t for t in (extra_terms or []) if t and t.strip()]
    if body is not None:
        case["body"] = body
    if extra:
        case["transcript_masked"], _ = mask(case["transcript_masked"], extra_terms=extra)
        case["body"], _ = mask(case["body"], extra_terms=extra)
    if selected is not None:
        ids = {r["id"] for r in case["recs"]}
        case["selected"] = [s for s in selected if s in ids]
    case["final_text"] = compose(case["body"], case["recs"], case["selected"], case["marketing_consent"], now)
    case.update(status="DRAFT", reviewer=None, approved_hash=None, approved_at=None)
    detail = []
    if body is not None and body != old_body:
        detail.append("본문 수정")
    if extra:
        detail.append(f"추가 마스킹 {len(extra)}건")
    if selected is not None:
        detail.append(f"추천 선택 {case['selected']}")
    _audit(case, actor, "EDITED", ", ".join(detail) or "변경 없음(재저장)")
    return _result(case)


# ---------- 승인/반려 ----------
def approve_case(state, reviewer, checklist):
    case = unpack(state)
    reviewer = (reviewer or "").strip()
    if not reviewer:
        raise ValueError("검토자 ID를 입력하세요.")
    reasons = []
    if case["status"] != "DRAFT":
        reasons.append(f"현재 상태({case['status']})에서는 승인할 수 없습니다.")
    if cfg()["four_eyes"] and reviewer == case["consultant"]:
        reasons.append("상담원과 검토자는 서로 달라야 합니다(4-eyes).")
    need = ["masking_ok", "content_ok", "recipient_ok"] + (["ad_ok"] if _has_ad(case["final_text"]) else [])
    if [k for k in need if not (checklist or {}).get(k)]:
        reasons.append("검토 체크리스트를 모두 확인해야 합니다.")
    left = residual_pii(case["final_text"], _allow())
    if left:
        reasons.append(f"최종 문자에 개인정보로 보이는 값이 남아 있습니다: {', '.join(left)}")
    if reasons:
        _audit(case, reviewer, "APPROVE_BLOCKED", " / ".join(reasons))
        raise Blocked(reasons, _result(case))
    h = hashlib.sha256(case["final_text"].encode()).hexdigest()
    case.update(status="APPROVED", reviewer=reviewer, approved_hash=h, approved_at=now_kst().isoformat(timespec="seconds"))
    _audit(case, reviewer, "APPROVED", f"hash={h[:12]}")
    return _result(case)


def reject_case(state, reviewer, reason):
    case = unpack(state)
    reviewer = (reviewer or "").strip()
    if not reviewer or not (reason or "").strip():
        raise ValueError("검토자 ID와 반려 사유를 입력하세요.")
    if case["status"] in ("SENT", "REJECTED"):
        raise Blocked([f"{case['status']} 상태의 건은 반려할 수 없습니다."])
    case.update(status="REJECTED", reviewer=reviewer)
    _audit(case, reviewer, "REJECTED", reason.strip())
    return _result(case)


# ---------- 발송(항상 모의) ----------
def send_case(state, actor, now=None):
    case = unpack(state)
    actor = (actor or "").strip()
    if not actor:
        raise ValueError("직원 ID를 입력하세요.")
    now = now or now_kst()
    reasons = []
    if case["status"] != "APPROVED":
        reasons.append("직원 검토·승인이 완료되지 않아 발송할 수 없습니다.")
    elif hashlib.sha256(case["final_text"].encode()).hexdigest() != case["approved_hash"]:
        reasons.append("승인 후 문자 내용이 변경되었습니다. 다시 검토·승인하세요.")
    left = residual_pii(case["final_text"], _allow())
    if left:
        reasons.append(f"개인정보로 보이는 값이 남아 있습니다: {', '.join(left)}")
    if _has_ad(case["final_text"]):
        if not case["marketing_consent"]:
            reasons.append("광고성 정보 수신 동의가 없어 추천 상품을 포함할 수 없습니다.")
        if cfg()["night_check"] and (now.hour >= 21 or now.hour < 8):
            reasons.append("야간(21:00~08:00) 광고성 정보 전송 제한 시간입니다. 추천 상품을 제외하거나 08:00 이후 발송하세요.")
    if not case.get("recipient") or not MOBILE.match(case["recipient"]):
        reasons.append("수신번호가 유효하지 않습니다.")
    if reasons:
        _audit(case, actor, "SEND_BLOCKED", " / ".join(reasons))
        raise Blocked(reasons, _result(case))
    entry = {"case_id": case["id"], "to_masked": case["recipient_masked"], "text": case["final_text"],
             "sent_at": now.isoformat(timespec="seconds"), "provider": "mock"}
    case.update(status="SENT", sent_at=now.isoformat(timespec="seconds"), recipient=None)
    _audit(case, actor, "SENT", f"mock(실제 발송 안 함), {msg_bytes(case['final_text'])}bytes, 수신 {case['recipient_masked']}")
    return _result(case, {"outbox": entry})

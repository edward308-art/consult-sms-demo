"""Vercel Python 함수: 모든 /api/* 요청을 처리한다. (서버에 데이터를 저장하지 않음)"""
import hashlib
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bank_lib"))
os.environ["PUBLIC_DEMO"] = "1"  # LLM 호출 비활성화(규칙 기반 요약만 사용)

import vservice as vs  # noqa: E402
from recommend import PRODUCTS  # noqa: E402
from samples import SAMPLES  # noqa: E402

ACCESS = os.environ.get("DEMO_ACCESS_CODE", "")


def _token():
    return hashlib.sha256(("bank-sms-v:" + ACCESS + os.environ.get("APP_SECRET", "")).encode()).hexdigest()


class handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 요청 본문/경로는 로그에 남기지 않는다
        pass

    def _send(self, code, obj, cookie=None):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cookie:
            self.send_header("Set-Cookie", f"tok={cookie}; HttpOnly; SameSite=Lax; Path=/; Max-Age=43200; Secure")
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        if not ACCESS:
            return True
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "tok" and hmac.compare_digest(v, _token()):
                return True
        return False

    def _route(self):
        p = self.path.split("?")[0].rstrip("/")
        return p[p.find("/api/"):] if "/api/" in p else p

    def do_GET(self):
        p = self._route()
        if p == "/api/config":
            c = vs.cfg()
            return self._send(200, {"bank_name": c["bank_name"], "four_eyes": c["four_eyes"], "night_check": c["night_check"],
                                    "secret_ok": vs.secret_ok(), "auth_required": bool(ACCESS), "authed": self._authed()})
        if not self._authed():
            return self._send(401, {"error": "접속 코드를 입력하세요."})
        if p == "/api/samples":
            return self._send(200, SAMPLES)
        if p == "/api/products":
            return self._send(200, PRODUCTS)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        p = self._route()
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                return self._send(400, {"error": "요청이 너무 큽니다."})
            b = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"error": "잘못된 요청입니다."})
        if p == "/api/login":
            if not ACCESS:
                return self._send(200, {"ok": True})
            if hmac.compare_digest(str(b.get("code", "")), ACCESS):
                return self._send(200, {"ok": True}, cookie=_token())
            return self._send(401, {"error": "접속 코드가 올바르지 않습니다."})
        if not self._authed():
            return self._send(401, {"error": "접속 코드를 입력하세요."})
        try:
            if p == "/api/cases":
                return self._send(200, vs.create_case(b.get("transcript"), b.get("consultant"), b.get("recipient"),
                                                      b.get("customer_name", ""), bool(b.get("marketing_consent"))))
            if p == "/api/act":
                a, st, actor = b.get("action"), b.get("state"), b.get("actor", "")
                if a == "edit":
                    return self._send(200, vs.edit_case(st, actor, b.get("body"), b.get("extra_terms") or [], b.get("selected")))
                if a == "approve":
                    return self._send(200, vs.approve_case(st, actor, b.get("checklist") or {}))
                if a == "reject":
                    return self._send(200, vs.reject_case(st, actor, b.get("reason", "")))
                if a == "send":
                    return self._send(200, vs.send_case(st, actor))
            return self._send(404, {"error": "not found"})
        except vs.Blocked as e:
            out = {"error": "처리할 수 없습니다.", "reasons": e.reasons}
            if isinstance(e.state, dict):
                out.update(e.state)
            return self._send(409, out)
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except RuntimeError as e:
            return self._send(500, {"error": str(e)})
        except Exception:
            return self._send(500, {"error": "서버 오류가 발생했습니다."})

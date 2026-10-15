"""로컬 미리보기: python3 dev_server.py  →  http://127.0.0.1:3000
(Vercel 없이 같은 코드를 로컬에서 실행해 보는 용도. 배포에는 사용되지 않습니다.)"""
import os
import sys
from http.server import HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "api"))
os.environ.setdefault("APP_SECRET", "local-dev-secret-change-me-1234567890")
import index  # noqa: E402


class Dev(index.handler):
    def do_GET(self):
        p = self.path.split("?")[0]
        if p.startswith("/api/"):
            return super().do_GET()
        f = os.path.join(HERE, "public", "index.html")
        data = open(f, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "3000"))
    print(f"로컬 미리보기: http://127.0.0.1:{port}  (종료: Ctrl+C)")
    HTTPServer(("127.0.0.1", port), Dev).serve_forever()

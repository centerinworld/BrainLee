"""routes/llm_proxy.py — Stock LLM(Brian_RAG, 127.0.0.1:8888) 역방향 프록시 (2026-09-27, 사이트 전면 개편 §Stock LLM)

`stock.leanguy.cloud/llm/...` → `http://127.0.0.1:8888/...`. 별도 도메인·터널 설정 없이 기존 터널로 Stock LLM을 노출하되, **관리자 로그인 후에만** 통과시킨다
(인터넷 경유 요청만 검사 — 이 PC에서 직접 부르는 로컬 요청은 무검사, security_gate와 같은 규칙).
  · 문서(HTML) 요청이 미인증이면 /admin?next=/llm/ 로 보내고, 그 밖의 요청은 401 JSON.
  · 관리자 쿠키·토큰·Cloudflare 헤더는 업스트림으로 넘기지 않는다(Brian_RAG는 인증을 모름).
  · Brian_RAG 화면은 `/llm` 접두사를 감지해 API를 `/llm/api/...`로 호출한다(Brian_RAG/web_app.py의 BASE).
환경변수 LLM_UPSTREAM(기본 http://127.0.0.1:8888).
"""
import os

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

import security_gate as sg

router = APIRouter()
UPSTREAM = os.environ.get("LLM_UPSTREAM", "http://127.0.0.1:8888").rstrip("/")
_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length"}
_STRIP_REQ = {"cookie", "authorization", "x-api-token", "cf-access-jwt-assertion", "accept-encoding"}
TIMEOUT = httpx.Timeout(180.0, connect=5.0)


@router.api_route("/llm", methods=["GET", "HEAD"], include_in_schema=False)
async def llm_root_redirect():
    return RedirectResponse("/llm/", status_code=307)


@router.api_route("/llm/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False)
async def llm_proxy(path: str, request: Request):
    if sg._via_tunnel(request) and not await sg._owner_ok(request):
        if "text/html" in request.headers.get("accept", "") and request.method in ("GET", "HEAD"):
            return RedirectResponse("/admin?next=/llm/", status_code=302)
        return JSONResponse({"detail": "admin_login_required"}, status_code=401)
    url = f"{UPSTREAM}/{path}"
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _HOP and k.lower() not in _STRIP_REQ and not k.lower().startswith("cf-") and not k.lower().startswith("x-forwarded")}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:   # 로컬 연결이라 요청마다 생성해도 비용이 작다(루프 수명 문제 없음)
            up = await client.request(request.method, url, params=request.query_params, content=await request.body(), headers=headers)
    except httpx.HTTPError as e:
        return JSONResponse({"detail": "llm_upstream_unavailable", "error": type(e).__name__}, status_code=502)
    out = {k: v for k, v in up.headers.items() if k.lower() not in _HOP and k.lower() not in ("content-encoding", "set-cookie")}
    return Response(content=up.content, status_code=up.status_code, headers=out)

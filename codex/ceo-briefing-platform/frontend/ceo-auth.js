/**
 * ceo-auth.js — CEO 브리핑 플랫폼 로그인 (2026-09-26)
 *
 * 서버(backend/role_gate.py)는 인터넷(터널) 경유 요청에 로그인 세션을 요구하고, role은 세션에서만 정한다. 이 스크립트는
 *  1) API 요청에 `Authorization: Bearer <세션 토큰>`을 자동으로 붙이고,
 *  2) 서버가 401(login_required)로 답하면 아이디·PIN 로그인 창을 띄운다(모바일 인앱 브라우저에서 window.prompt가 막혀 있어 화면에 직접 그린다).
 * 토큰은 이 탭(sessionStorage)에만 저장된다. PIN은 저장하지 않는다. 로컬(127.0.0.1) 사용 중 서버가 401을 주지 않으면 아무 창도 뜨지 않는다.
 */
(function () {
  if (window.__ceoAuthInstalled) return;
  window.__ceoAuthInstalled = true;
  const TOKEN = "ceoSessionToken", ROLE = "ceoRole", USER = "ceoUser";
  const original = window.fetch.bind(window);
  let loginPromise = null;

  const isApi = (u) => /^https?:\/\/(api\.newsinfo\.cloud|127\.0\.0\.1:8011|localhost:8011)(\/|$)/i.test(u);
  const urlOf = (input) => (typeof input === "string" ? input : (input && input.url) || "");

  function el(tag, props, children) {
    const n = document.createElement(tag);
    Object.assign(n, props || {});
    (children || []).forEach((c) => n.append(c));
    return n;
  }

  function askLogin(origin, message) {
    if (loginPromise) return loginPromise;
    loginPromise = new Promise((resolve) => {
      const overlay = el("div");
      overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;z-index:99999;font-family:system-ui,sans-serif";
      const box = el("form");
      box.style.cssText = "background:#fff;color:#17233a;padding:22px;border-radius:12px;width:min(340px,88vw);box-shadow:0 10px 40px rgba(0,0,0,.3)";
      const title = el("h3", { textContent: "🔒 관리자 로그인" });
      title.style.cssText = "margin:0 0 6px;font-size:18px";
      const note = el("p", { textContent: message || "관리자 비밀번호를 입력하세요." });
      note.style.cssText = "margin:0 0 12px;font-size:13px;color:#5f6368";
      const style = "width:100%;box-sizing:border-box;padding:10px;margin:0 0 10px;border:1px solid #cbd5e1;border-radius:8px;font-size:15px";
      // 2026-09-27: 아이디·PIN 계정 폐지 — stock 사이트와 같은 관리자 비밀번호 하나(10자 이상)로 로그인한다.
      const user = { value: "admin", focus() {} };
      const pin = el("input", { placeholder: "관리자 비밀번호", type: "password", autocomplete: "current-password", required: true }); pin.style.cssText = style;
      const err = el("p"); err.style.cssText = "margin:0 0 8px;font-size:12px;color:#dc2626;min-height:14px";
      const ok = el("button", { type: "submit", textContent: "로그인" });
      ok.style.cssText = "width:100%;padding:11px;border:0;border-radius:8px;background:#1a73e8;color:#fff;font-weight:700;font-size:15px;cursor:pointer";
      const cancel = el("button", { type: "button", textContent: "닫기" });
      cancel.style.cssText = "width:100%;padding:8px;margin-top:8px;border:0;background:transparent;color:#5f6368;cursor:pointer";
      [title, note, pin, err, ok, cancel].forEach((n) => box.append(n));
      overlay.append(box);
      document.body.append(overlay);
      pin.focus();
      const done = (v) => { overlay.remove(); loginPromise = null; resolve(v); };
      cancel.onclick = () => done(false);
      box.onsubmit = async (e) => {
        e.preventDefault();
        ok.disabled = true; err.textContent = "";
        try {
          const r = await original(origin + "/app-login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: user.value.trim(), pin: pin.value }) });
          const j = await r.json().catch(() => ({}));
          if (!r.ok) {
            err.textContent = r.status === 429 ? "시도 횟수가 많습니다. 잠시 후 다시 시도하세요." : r.status === 503 ? "관리자 비밀번호가 아직 설정되지 않았습니다." : "비밀번호가 올바르지 않습니다.";
            ok.disabled = false; pin.value = ""; pin.focus();
            return;
          }
          sessionStorage.setItem(TOKEN, j.token);
          sessionStorage.setItem(ROLE, j.role || "");
          sessionStorage.setItem(USER, j.display_name || j.username || "");
          if (j.role === "admin") sessionStorage.setItem("agiSessionToken", j.token);   // kai.js의 AGI 작업 지시 로그인과 공유
          done(true);
        } catch (e2) {
          err.textContent = "서버에 연결할 수 없습니다."; ok.disabled = false;
        }
      };
    });
    return loginPromise;
  }

  window.fetch = async function (input, init) {
    const url = urlOf(input);
    if (!isApi(url) || /\/app-login(\?|$)/.test(url)) return original(input, init);
    const withAuth = (token) => {
      const headers = new Headers((init && init.headers) || (typeof input !== "string" && input.headers) || undefined);
      if (token && !headers.has("Authorization")) headers.set("Authorization", "Bearer " + token);
      return [input, { ...(init || {}), headers }];
    };
    const res = await original(...withAuth(sessionStorage.getItem(TOKEN)));
    if (res.status !== 401) return res;
    let detail = "";
    try { detail = (await res.clone().json()).detail || ""; } catch (e) { /* not json */ }
    if (detail !== "login_required" && !String(detail).includes("로그인")) return res;
    sessionStorage.removeItem(TOKEN);
    const origin = url.match(/^https?:\/\/[^/]+/)[0];
    const logged = await askLogin(origin, sessionStorage.getItem(USER) ? "세션이 만료되었습니다. 다시 로그인하세요." : undefined);
    if (!logged) return res;
    location.reload();                                    // 화면이 로그인한 사용자의 역할로 다시 구성되도록 새로고침
    return new Promise(() => {});
  };

  window.ceoLogout = function () {
    [TOKEN, ROLE, USER, "agiSessionToken"].forEach((k) => sessionStorage.removeItem(k));
    location.reload();
  };
  window.addEventListener("DOMContentLoaded", () => {
    const name = sessionStorage.getItem(USER);
    if (!name || !sessionStorage.getItem(TOKEN)) return;
    const pill = el("button", { textContent: name + " · 로그아웃" });
    pill.style.cssText = "position:fixed;right:10px;bottom:10px;z-index:9999;padding:6px 12px;border:0;border-radius:999px;background:rgba(23,35,58,.85);color:#fff;font-size:12px;cursor:pointer";
    pill.onclick = window.ceoLogout;
    document.body.append(pill);
  });
})();

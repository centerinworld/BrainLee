/**
 * apiToken.js — API 토큰 자동 첨부 (HANDOFF §11 S0 ②, 2026-09-25 / V1 개정 2026-09-26)
 *
 * 서버(security_gate.py)는 인터넷(터널) 경유의 /api·/hs·/semiconductor-lab 요청을 GET까지 전부 API_WRITE_TOKEN으로 보호한다.
 * localStorage 'api_write_token'에 저장된 값을 해당 요청에 `X-API-Token`으로 붙이고, 서버가 401(api_token_required)로 답하면
 * 입력창을 **한 번만** 띄운다(페이지 로드 시 동시에 수십 개 요청이 401을 받아도 창은 하나, 나머지는 그 결과를 기다렸다가 재시도).
 * 입력을 취소하면 60초 동안 다시 묻지 않는다. 저장된 토큰이 틀려 다시 401이 나면 저장값을 지운다. 토큰은 코드·저장소에 넣지 않는다.
 */
const KEY = 'api_write_token';
const CANCEL_COOLDOWN_MS = 60_000;

let pendingPrompt = null;      // 진행 중인 입력 창(동시 요청이 공유)
let cancelledAt = 0;

const readToken = () => {
  try { return localStorage.getItem(KEY) || ''; } catch { return ''; }
};
const writeToken = (v) => {
  try { if (v) localStorage.setItem(KEY, v); else localStorage.removeItem(KEY); } catch { /* storage blocked */ }
};

const isApi = (input) => {
  const url = typeof input === 'string' ? input : (input && input.url) || '';
  return url.startsWith('/api') || url.startsWith('/hs') || url.startsWith('/semiconductor-lab');
};

const askToken = () => {
  if (Date.now() - cancelledAt < CANCEL_COOLDOWN_MS) return Promise.resolve('');
  if (!pendingPrompt) {
    pendingPrompt = Promise.resolve().then(() => {
      const entered = (window.prompt('API 토큰이 필요합니다. 서버 .env의 API_WRITE_TOKEN 값을 입력하세요.') || '').trim();
      if (entered) writeToken(entered); else cancelledAt = Date.now();
      return entered;
    }).finally(() => { pendingPrompt = null; });
  }
  return pendingPrompt;
};

export function installApiTokenFetch() {
  if (typeof window === 'undefined' || window.__apiTokenFetchInstalled) return;
  window.__apiTokenFetchInstalled = true;
  const original = window.fetch.bind(window);

  const withToken = (input, init, token) => {
    if (!token) return [input, init];
    const headers = new Headers((init && init.headers) || (typeof input !== 'string' && input.headers) || undefined);
    headers.set('X-API-Token', token);
    return [input, { ...(init || {}), headers }];
  };

  const needsToken = async (res) => {
    if (res.status !== 401) return false;
    try { return ((await res.clone().json()).detail || '') === 'api_token_required'; } catch { return false; }
  };

  window.fetch = async (input, init) => {
    if (!isApi(input)) return original(input, init);
    const sent = readToken();
    const res = await original(...withToken(input, init, sent));
    if (!(await needsToken(res))) return res;
    if (sent) writeToken('');                       // 저장된 토큰이 거부됨 → 지우고 다시 묻는다
    const token = await askToken();
    if (!token) return res;
    return original(...withToken(input, init, token));
  };
}

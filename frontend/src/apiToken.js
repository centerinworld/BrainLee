/**
 * apiToken.js — 쓰기·민감 API 토큰 자동 첨부 (HANDOFF §11 S0 ②, 2026-09-25)
 *
 * 서버(security_gate.py)는 인터넷(터널) 경유의 쓰기 요청과 민감 GET(/api/portfolio 등)에 API_WRITE_TOKEN을 요구한다.
 * localStorage 'api_write_token'에 저장된 값을 /api 요청에 `X-API-Token`으로 붙이고, 서버가 401(api_token_required)로 답하면
 * 한 번만 입력창을 띄워 저장한 뒤 재시도한다. 토큰은 코드·저장소에 넣지 않는다(사용자가 직접 입력).
 */
const KEY = 'api_write_token';

const readToken = () => {
  try { return localStorage.getItem(KEY) || ''; } catch { return ''; }
};

const isApi = (input) => {
  const url = typeof input === 'string' ? input : (input && input.url) || '';
  return url.startsWith('/api') || url.startsWith('/hs') || url.startsWith('/semiconductor-lab');
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

  window.fetch = async (input, init) => {
    if (!isApi(input)) return original(input, init);
    const res = await original(...withToken(input, init, readToken()));
    if (res.status !== 401) return res;
    let detail = '';
    try { detail = (await res.clone().json()).detail || ''; } catch { /* not json */ }
    if (detail !== 'api_token_required') return res;
    const entered = window.prompt('API 토큰이 필요합니다. 서버 .env의 API_WRITE_TOKEN 값을 입력하세요.');
    if (!entered) return res;
    try { localStorage.setItem(KEY, entered.trim()); } catch { /* storage blocked */ }
    return original(...withToken(input, init, entered.trim()));
  };
}

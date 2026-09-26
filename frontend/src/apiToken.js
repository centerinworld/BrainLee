/**
 * apiToken.js — API 토큰 자동 첨부 (HANDOFF §11 S0 ②, 2026-09-26 개정)
 *
 * 서버(security_gate.py)는 인터넷(터널) 경유 요청 중 **수정(POST/PUT/PATCH/DELETE)과 민감 조회(보유·계좌·주문·후보·다운로드 등)**에만 인증을 요구한다.
 * 일반 조회(시세·랭킹·차트 등)는 토큰이 필요 없다. Cloudflare Access 로그인 + 서버의 CF_ACCESS_* 설정이 있으면 토큰 입력도 필요 없다.
 *
 * 동작
 *  · localStorage 'api_write_token'(막혀 있으면 세션 메모리)에 저장된 값을 /api 요청에 `X-API-Token`으로 붙인다.
 *  · 서버가 401(api_token_required)로 답하면 입력창을 **한 번만** 띄운다(동시 요청은 그 결과를 기다렸다가 재시도).
 *  · 입력값은 붙여 넣기 실수를 정리한다: 앞뒤 공백·따옴표, `API_WRITE_TOKEN=` 접두사 제거.
 *  · 저장된 토큰이 거부되면 지우고, 이유를 알려 주는 문구로 다시 묻는다. 입력을 취소하면 10분간 묻지 않는다.
 * 토큰은 코드·저장소에 넣지 않는다.
 */
const KEY = 'api_write_token';
const CANCEL_COOLDOWN_MS = 10 * 60_000;

let memoryToken = '';          // 저장소(localStorage)가 막힌 브라우저(일부 사파리·시크릿 모드)에서도 탭이 열려 있는 동안 유지
let pendingPrompt = null;      // 진행 중인 입력 창(동시 요청이 공유)
let cancelledAt = 0;

const readToken = () => {
  try { const v = localStorage.getItem(KEY); if (v) return v; } catch { /* storage blocked */ }
  return memoryToken;
};
const writeToken = (v) => {
  memoryToken = v || '';
  try { if (v) localStorage.setItem(KEY, v); else localStorage.removeItem(KEY); } catch { /* storage blocked */ }
};

export const cleanToken = (raw) => {
  let t = String(raw || '').trim();
  t = t.replace(/^API_WRITE_TOKEN\s*=\s*/i, '');
  t = t.replace(/^["'`“”‘’]+|["'`“”‘’]+$/g, '').trim();
  return t;
};

const isApi = (input) => {
  const url = typeof input === 'string' ? input : (input && input.url) || '';
  return url.startsWith('/api') || url.startsWith('/hs') || url.startsWith('/semiconductor-lab');
};

const askToken = (rejected) => {
  if (Date.now() - cancelledAt < CANCEL_COOLDOWN_MS) return Promise.resolve('');
  if (!pendingPrompt) {
    pendingPrompt = Promise.resolve().then(() => {
      const msg = (rejected
        ? '저장된 API 토큰이 서버에서 거부되었습니다(값이 다르거나 서버가 바뀐 경우).\n'
        : '이 요청은 데이터를 수정하거나 민감한 정보를 조회하므로 API 토큰이 필요합니다.\n')
        + '서버 .env 파일의 API_WRITE_TOKEN= 뒤의 값만 그대로 붙여 넣으세요(따옴표·공백 없이).\n취소하면 10분 동안 다시 묻지 않습니다.';
      const entered = cleanToken(window.prompt(msg));
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
    if (sent) writeToken('');                       // 저장된 토큰이 거부됨 → 지우고 이유를 알려 다시 묻는다
    const token = await askToken(Boolean(sent));
    if (!token) return res;
    const retry = await original(...withToken(input, init, token));
    if (await needsToken(retry)) writeToken('');    // 방금 입력한 값도 틀렸다면 저장하지 않는다
    return retry;
  };
}

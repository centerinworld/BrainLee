/**
 * apiToken.js — API 토큰 자동 첨부 (2026-10-02 모바일 모달 개정)
 *
 * 서버(security_gate.py)는 인터넷(터널) 경유 요청 중 수정(POST/PUT/PATCH/DELETE)과
 * 민감 조회(보유·계좌·주문·후보·다운로드 등)에만 인증을 요구한다.
 * 일반 조회(시세·랭킹·차트 등)는 토큰이 필요 없다.
 *
 * 동작
 *  · localStorage 'api_write_token'에 저장된 값을 /api 요청에 X-API-Token으로 붙인다.
 *  · 서버가 401(api_token_required)로 답하면 인라인 모달을 한 번만 띄운다(window.prompt는
 *    모바일에서 동작하지 않는 브라우저가 있어 DOM 모달로 교체, 2026-10-02).
 *  · 저장된 토큰이 거부되면 지우고 이유를 알려 다시 묻는다.
 *  · 입력을 취소하면 10분간 묻지 않는다.
 */
const KEY = 'api_write_token';
const CANCEL_COOLDOWN_MS = 10 * 60_000;

let memoryToken = '';
let pendingPrompt = null;
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

/** DOM 인라인 모달로 토큰 입력 — window.prompt 대체 (모바일 호환) */
const showTokenModal = (rejected) => new Promise((resolve) => {
  const overlay = document.createElement('div');
  overlay.style.cssText = [
    'position:fixed;inset:0;z-index:99999',
    'background:rgba(15,23,42,0.6)',
    'display:flex;align-items:center;justify-content:center',
    'padding:1rem',
  ].join(';');

  const box = document.createElement('div');
  box.style.cssText = [
    'background:#fff;color:#0b1220;border-radius:10px',
    'padding:1.5rem;max-width:420px;width:100%',
    'box-shadow:0 8px 32px rgba(0,0,0,0.25)',
    'font-family:system-ui,-apple-system,sans-serif',
  ].join(';');

  const title = document.createElement('div');
  title.style.cssText = 'font-size:1rem;font-weight:700;margin-bottom:0.6rem';
  title.textContent = 'API 토큰 필요';

  const desc = document.createElement('div');
  desc.style.cssText = 'font-size:0.85rem;color:#475569;margin-bottom:1rem;line-height:1.5';
  desc.textContent = rejected
    ? '저장된 토큰이 서버에서 거부되었습니다. .env의 API_WRITE_TOKEN 값을 다시 입력하세요.'
    : '이 요청은 API 토큰이 필요합니다. 서버 .env 파일의 API_WRITE_TOKEN= 뒤 값을 붙여 넣으세요.';

  const input = document.createElement('input');
  input.type = 'text';
  input.placeholder = 'API 토큰 붙여 넣기';
  input.autocomplete = 'off';
  input.style.cssText = [
    'width:100%;box-sizing:border-box',
    'border:1.5px solid #cbd5e1;border-radius:6px',
    'padding:0.6rem 0.75rem;font-size:0.9rem',
    'outline:none;margin-bottom:1rem',
  ].join(';');
  input.addEventListener('focus', () => { input.style.borderColor = '#3b82f6'; });
  input.addEventListener('blur', () => { input.style.borderColor = '#cbd5e1'; });

  const btnRow = document.createElement('div');
  btnRow.style.cssText = 'display:flex;gap:0.5rem;justify-content:flex-end';

  const btnCancel = document.createElement('button');
  btnCancel.textContent = '취소 (10분)';
  btnCancel.style.cssText = 'padding:0.5rem 1rem;border-radius:6px;border:1.5px solid #e2e8f0;background:#f8fafc;font-size:0.85rem;cursor:pointer';

  const btnOk = document.createElement('button');
  btnOk.textContent = '확인';
  btnOk.style.cssText = 'padding:0.5rem 1.2rem;border-radius:6px;border:none;background:#2563eb;color:#fff;font-size:0.85rem;cursor:pointer;font-weight:600';

  const finish = (value) => {
    document.body.removeChild(overlay);
    resolve(value);
  };

  btnCancel.addEventListener('click', () => { cancelledAt = Date.now(); finish(''); });
  btnOk.addEventListener('click', () => finish(cleanToken(input.value)));
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') finish(cleanToken(input.value)); if (e.key === 'Escape') { cancelledAt = Date.now(); finish(''); } });
  overlay.addEventListener('click', (e) => { if (e.target === overlay) { cancelledAt = Date.now(); finish(''); } });

  btnRow.append(btnCancel, btnOk);
  box.append(title, desc, input, btnRow);
  overlay.appendChild(box);
  document.body.appendChild(overlay);
  setTimeout(() => input.focus(), 50);
});

const askToken = (rejected) => {
  if (Date.now() - cancelledAt < CANCEL_COOLDOWN_MS) return Promise.resolve('');
  if (!pendingPrompt) {
    pendingPrompt = showTokenModal(rejected)
      .then((entered) => { if (entered) writeToken(entered); return entered; })
      .finally(() => { pendingPrompt = null; });
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
    if (sent) writeToken('');
    const token = await askToken(Boolean(sent));
    if (!token) return res;
    const retry = await original(...withToken(input, init, token));
    if (await needsToken(retry)) writeToken('');
    return retry;
  };
}

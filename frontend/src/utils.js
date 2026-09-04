export const API = (path) => path;

export const isKRMarketOpen = () => {
  const now = new Date();
  const day = now.getDay();
  if (day===0||day===6) return false;
  const kst = new Date(now.toLocaleString('en-US',{timeZone:'Asia/Seoul'}));
  const t = kst.getHours()*100+kst.getMinutes();
  return t>=900 && t<=1535;
};
export const isUSMarketOpen = () => {
  const now = new Date();
  const est = new Date(now.toLocaleString('en-US',{timeZone:'America/New_York'}));
  const day = est.getDay();
  if (day===0||day===6) return false;
  const t = est.getHours()*100+est.getMinutes();
  return t>=930 && t<=1600;
};
export const anyMarketOpen = () => isKRMarketOpen()||isUSMarketOpen();

export const isDisclosureTime = () => {
  const now = new Date();
  const kst = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Seoul' }));
  const day  = kst.getDay();
  if (day === 0 || day === 6) return false;
  const t = kst.getHours() * 100 + kst.getMinutes();
  return t >= 800 && t <= 2000;
};

export const _lsGet = (key, fallback) => { try { const v = localStorage.getItem(key); return v !== null ? JSON.parse(v) : fallback; } catch { return fallback; } };
export const _lsSet = (key, val) => { try { localStorage.setItem(key, JSON.stringify(val)); } catch {} };

// fmtKrw: 원화 가격 표시는 소수점 없이 정수 원 단위로 표시 (App.jsx SignalBoard 구현과 동일)
export const fmtKrw = (v) => {
  if (v == null || v === '' || Number.isNaN(Number(v))) return '-';
  return `${Math.round(Number(v)).toLocaleString('ko-KR')}원`;
};

// fmtPctUs: 미국주식 등락률(부호 포함, 소수점 둘째자리) (App.jsx USStocksView 구현과 동일)
export const fmtPctUs = (v) => {
  if (v == null) return '-';
  const n = Number(v); if (isNaN(n)) return '-';
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
};

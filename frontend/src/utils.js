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

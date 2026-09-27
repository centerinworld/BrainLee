import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, RefreshCw } from 'lucide-react';
import { MODULES } from './modules';

/**
 * 관리자 › 시스템 현황 (2026-09-27)
 * 서버(system_map.py)가 소스에서 자동 집계한 지도를 15초마다 조회한다. 코드가 바뀌면 fingerprint가 달라져 정적 부분이 다시 내려오고
 * (수정할 때마다 자동 갱신), 서비스·데이터 신선도·수집 실행은 매번 새로 받는다.
 */
const POLL_MS = 15000;
const fmt = (n) => (n == null ? '–' : Number(n).toLocaleString('ko-KR'));
const ago = (ts, now) => {
  if (!ts) return '–';
  const s = Math.max(0, Math.round(now - ts));
  if (s < 60) return `${s}초 전`;
  if (s < 3600) return `${Math.floor(s / 60)}분 전`;
  if (s < 86400) return `${Math.floor(s / 3600)}시간 전`;
  return `${Math.floor(s / 86400)}일 전`;
};
const mb = (v) => (v == null ? '–' : v >= 1024 ? `${(v / 1024).toFixed(1)} GB` : `${fmt(v)} MB`);
const STATUS = { healthy: ['정상', 'ok'], partial: ['부분', 'warn'], stale: ['지연', 'bad'], error: ['오류', 'bad'], missing: ['없음', 'bad'] };
const Badge = ({ s }) => { const [t, c] = STATUS[s] || [s || '–', 'muted']; return <span className={`sm-badge ${c}`}>{t}</span>; };

function Bars({ rows, valueKey, labelKey, unit = '' }) {
  const max = Math.max(1, ...rows.map((r) => r[valueKey]));
  return (
    <div className="sm-bars">
      {rows.map((r) => (
        <div key={r[labelKey]} className="sm-bar-row">
          <span className="sm-bar-label" title={r[labelKey]}>{r[labelKey]}</span>
          <span className="sm-bar-track"><i style={{ width: `${(r[valueKey] / max) * 100}%` }} /></span>
          <span className="sm-bar-val">{fmt(r[valueKey])}{unit}</span>
        </div>
      ))}
    </div>
  );
}

export default function AdminSystemMap() {
  const [data, setData] = useState({ static: null, live: null, fingerprint: '' });
  const [err, setErr] = useState('');
  const [tab, setTab] = useState('overview');
  const [q, setQ] = useState('');
  const [now, setNow] = useState(Math.floor(Date.now() / 1000));
  const [flash, setFlash] = useState('');
  const fpRef = useRef('');

  const load = useCallback(async () => {
    try {
      const r = await fetch(`/api/sysmap/overview?fp=${fpRef.current}`, { credentials: 'same-origin' });
      if (!r.ok) throw new Error(r.status === 401 ? '관리자 로그인이 필요합니다.' : `HTTP ${r.status}`);
      const d = await r.json();
      const hadFp = !!fpRef.current;
      fpRef.current = d.fingerprint;
      setData((prev) => ({ static: d.static || prev.static, live: d.live, fingerprint: d.fingerprint }));
      setNow(d.server_time);
      setErr('');
      if (d.changed && hadFp) { setFlash('코드 변경을 감지해 지도를 갱신했습니다'); setTimeout(() => setFlash(''), 4000); }
    } catch (e) { setErr(e.message || '조회 실패'); }
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const { static: st, live } = data;
  const freshBy = useMemo(() => Object.fromEntries((live?.freshness || []).map((f) => [f.key, f])), [live]);
  const totalLoc = st ? st.code.areas.reduce((a, b) => a + b.lines, 0) : 0;
  const svcUp = live ? live.services.filter((s) => s.up).length : 0;
  const healthy = live ? live.freshness.filter((f) => f.status === 'healthy').length : 0;
  const tabCount = Object.values(MODULES).reduce((a, m) => a + m.sections.reduce((b, s) => b + s.tabs.length, 0), 0);
  const lastEdit = st?.recent?.[0]?.mtime;

  if (!st || !live) return <div className="sm-empty">{err || '시스템 지도를 불러오는 중…'}</div>;

  const kpis = [
    { label: '서비스 가동', value: `${svcUp}/${live.services.length}`, sub: svcUp === live.services.length ? '모두 정상' : '점검 필요', tone: svcUp === live.services.length ? 'ok' : 'bad' },
    { label: 'API 엔드포인트', value: fmt(st.endpoints.total), sub: `${st.endpoints.group_count}개 그룹` },
    { label: '스케줄러 잡', value: fmt(st.scheduler.active), sub: `전체 ${st.scheduler.total} · 비활성 ${st.scheduler.disabled}` },
    { label: 'DB', value: mb(live.db.size_mb), sub: `${fmt(live.db.table_count)}개 테이블` },
    { label: '데이터 계약', value: `${healthy}/${live.freshness.length}`, sub: healthy === live.freshness.length ? '모두 최신' : '지연·부분 있음', tone: healthy === live.freshness.length ? 'ok' : 'warn' },
    { label: '코드 규모', value: `${fmt(Math.round(totalLoc / 1000))}k줄`, sub: `프런트 화면 ${tabCount}개 탭` },
  ];
  const filteredLineage = st.lineage.filter((e) => !q || `${e.label} ${e.table} ${e.source}`.toLowerCase().includes(q.toLowerCase()));
  const filteredJobs = st.scheduler.jobs.filter((j) => !q || `${j.name} ${j.fn} ${j.desc}`.toLowerCase().includes(q.toLowerCase()));
  const tabs = [['overview', '개요'], ['data', '데이터'], ['process', '프로세스'], ['code', '코드 · API'], ['changes', '변경 이력']];

  return (
    <div className="sm">
      <div className="sm-top">
        <span className={`sm-live${err ? ' off' : ''}`}><i />{err ? `연결 끊김 — ${err}` : '자동 갱신 중'}</span>
        <span className="sm-muted">소스 지문 <code>{data.fingerprint}</code> · 마지막 코드 수정 {ago(lastEdit, now)}</span>
        {flash && <span className="sm-flash">{flash}</span>}
        <span className="sm-spacer" />
        <button type="button" className="adm-btn" onClick={load}><RefreshCw size={14} /> 지금 갱신</button>
      </div>

      <div className="sm-kpis">
        {kpis.map((k) => (
          <div key={k.label} className={`sm-kpi ${k.tone || ''}`}><small>{k.label}</small><b>{k.value}</b><span>{k.sub}</span></div>
        ))}
      </div>

      <div className="sm-tabs" role="tablist">
        {tabs.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'on' : ''} onClick={() => { setTab(k); setQ(''); }}>{l}</button>)}
      </div>

      {tab === 'overview' && (
        <>
          <section className="adm-panel">
            <h2>데이터가 흐르는 길</h2>
            <p className="sm-muted sm-lead">외부 데이터원에서 수집기가 가져와 검증·저장한 뒤, 엔진이 가공하고 API가 화면에 내보냅니다. 숫자는 코드에서 자동 집계됩니다.</p>
            <div className="sm-flow">
              {st.pipeline.map((p, i) => (
                <React.Fragment key={p.stage}>
                  <div className="sm-stage"><b>{p.stage}</b><strong>{p.count != null ? fmt(p.count) : (p.stage === '화면' ? tabCount : '–')}<small> {p.unit || (p.stage === '화면' ? '개 탭' : '')}</small></strong><span>{p.note}</span></div>
                  {i < st.pipeline.length - 1 && <ArrowRight size={16} className="sm-arrow" />}
                </React.Fragment>
              ))}
            </div>
          </section>
          <section className="adm-panel">
            <h2>서비스 상태</h2>
            <div className="adm-status">
              {live.services.map((s) => (
                <div key={s.key} className="adm-stat">
                  <span className={`hub-status ${s.up ? 'up' : 'down'}`}><i />{s.up ? '정상' : '중지'}</span>
                  <b>{s.name}</b><small>{s.port ? `:${s.port}` : s.detail || ''}{s.key === 'backend' && live.uptime_s ? ` · 가동 ${Math.floor(live.uptime_s / 3600)}시간` : ''}</small>
                </div>
              ))}
            </div>
          </section>
          <section className="adm-panel">
            <h2>데이터 신선도 <span className="sm-muted">({healthy}/{live.freshness.length} 정상)</span></h2>
            <div className="sm-chips">
              {live.freshness.map((f) => <span key={f.key} className={`sm-chip ${STATUS[f.status]?.[1] || 'muted'}`} title={`${f.as_of || ''} ${f.issues.join(' ')}`}>{f.label}{f.lag ? ` · ${f.lag}일 지연` : ''}</span>)}
            </div>
          </section>
        </>
      )}

      {tab === 'data' && (
        <section className="adm-panel">
          <div className="sm-row"><h2>어떤 데이터가 모이는가</h2><input className="sm-search" placeholder="데이터셋·테이블·출처 검색" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <p className="sm-muted sm-lead">계보는 코드 텍스트 스캔으로 구합니다: 쓰는 파일 = <code>INSERT/UPDATE</code>가 있는 수집·배치 파일, 읽는 API = 테이블명이 나오는 라우트, 화면 = 그 API를 호출하는 프런트 파일.</p>
          <div className="sm-table-wrap">
            <table className="sm-table">
              <thead><tr><th>데이터셋</th><th>출처 · 주기</th><th>테이블 (행수)</th><th>수집 · 쓰는 파일</th><th>읽는 API</th><th>화면</th><th>상태</th></tr></thead>
              <tbody>
                {filteredLineage.map((e) => {
                  const f = freshBy[e.key];
                  return (
                    <tr key={e.key}>
                      <td><b>{e.label}</b></td>
                      <td>{e.source}<small>{e.schedule}</small></td>
                      <td><code>{e.table}</code><small>{fmt(e.rows)}행</small></td>
                      <td>{e.jobs.length > 0 && <span className="sm-job">{e.jobs.join(', ')}</span>}{e.writers.slice(0, 3).map((w) => <code key={w} className="sm-file">{w.split('/').pop()}</code>)}{e.writer_count > 3 && <small>외 {e.writer_count - 3}개</small>}{!e.jobs.length && !e.writer_count && <small>–</small>}</td>
                      <td>{e.apis.slice(0, 3).map((a) => <code key={a} className="sm-file">{a}</code>)}{e.api_count > 3 && <small>외 {e.api_count - 3}개</small>}{!e.api_count && <small>–</small>}</td>
                      <td>{e.screens.slice(0, 3).join(', ') || '–'}{e.screen_count > 3 && <small>외 {e.screen_count - 3}개</small>}</td>
                      <td>{f ? <><Badge s={f.status} /><small>{f.as_of ? String(f.as_of).slice(0, 10) : ''}</small></> : <span className="sm-muted">{e.contract ? '확인 중' : '계약 없음'}</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {tab === 'process' && (
        <>
          <section className="adm-panel">
            <h2>최근 수집 실행 <span className="sm-muted">(잡별 마지막 실행 · 실패 {live.run_summary?.failed ?? 0} · 실행 중 {live.run_summary?.running ?? 0})</span></h2>
            <div className="sm-table-wrap"><table className="sm-table">
              <thead><tr><th>잡</th><th>상태</th><th>시작</th><th>소요</th><th>오류</th></tr></thead>
              <tbody>{live.runs.map((r) => (
                <tr key={r.job}><td><b>{r.job}</b></td><td><span className={`sm-badge ${r.status === 'success' ? 'ok' : r.status === 'running' ? 'warn' : 'bad'}`}>{r.status}</span></td>
                  <td>{r.started_at?.replace('T', ' ').slice(5, 16)}</td><td>{r.duration != null ? `${Math.round(r.duration)}초` : '–'}</td><td className="sm-err">{r.error}</td></tr>
              ))}</tbody>
            </table></div>
          </section>
          <section className="adm-panel">
            <div className="sm-row"><h2>스케줄러 잡 {st.scheduler.active}개 활성 <span className="sm-muted">/ 전체 {st.scheduler.total}</span></h2><input className="sm-search" placeholder="잡 이름·설명 검색" value={q} onChange={(e) => setQ(e.target.value)} /></div>
            <div className="sm-table-wrap sm-tall"><table className="sm-table">
              <thead><tr><th>잡</th><th>함수</th><th>설명</th><th>상태</th></tr></thead>
              <tbody>{filteredJobs.map((j) => (
                <tr key={j.name}><td><b>{j.name}</b></td><td><code>{j.fn}</code></td><td>{j.desc || <span className="sm-muted">–</span>}</td><td><span className={`sm-badge ${j.disabled ? 'muted' : 'ok'}`}>{j.disabled ? '비활성' : '활성'}</span></td></tr>
              ))}</tbody>
            </table></div>
          </section>
        </>
      )}

      {tab === 'code' && (
        <>
          <div className="sm-cols">
            <section className="adm-panel"><h2>코드 규모 (줄)</h2><Bars rows={st.code.areas} valueKey="lines" labelKey="area" />
              <p className="sm-muted sm-foot">{st.code.areas.map((a) => `${a.area} ${fmt(a.files)}파일`).join(' · ')}</p></section>
            <section className="adm-panel"><h2>API 그룹 상위 (엔드포인트 수)</h2><Bars rows={st.endpoints.groups} valueKey="count" labelKey="prefix" /></section>
          </div>
          <div className="sm-cols">
            <section className="adm-panel"><h2>가장 큰 파일</h2><Bars rows={st.code.largest} valueKey="lines" labelKey="path" />
              <p className="sm-muted sm-foot">큰 파일은 읽는 비용(토큰)이 크다 — 수정 전 <code>docs/SYSTEM_MAP.md</code>와 grep으로 범위를 좁힐 것.</p></section>
            <section className="adm-panel"><h2>화면 구성</h2>
              {Object.values(MODULES).map((m) => <p key={m.key} className="sm-mod"><b>{m.title}</b> <span className="sm-muted">/{m.key}</span> — {m.sections.reduce((a, s) => a + s.tabs.length, 0)}개 탭 · {m.sections.map((s) => s.label).join(' · ')}</p>)}
            </section>
          </div>
        </>
      )}

      {tab === 'changes' && (
        <div className="sm-cols">
          <section className="adm-panel"><h2>최근 수정한 파일</h2>
            <ul className="sm-list">{st.recent.map((f) => <li key={f.path}><code>{f.path}</code><span className="sm-muted">{ago(f.mtime, now)}</span></li>)}</ul></section>
          <section className="adm-panel"><h2>git <span className="sm-muted">{st.git.branch} · 미커밋 {st.git.dirty}개</span></h2>
            <ul className="sm-list">{st.git.commits.map((c) => <li key={c.hash}><span><code>{c.hash}</code> {c.subject}</span><span className="sm-muted">{c.when}</span></li>)}</ul></section>
        </div>
      )}
    </div>
  );
}

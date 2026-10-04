import React, { useCallback, useEffect, useState } from 'react';
import { RefreshCw, Trash2, Plus, Play } from 'lucide-react';

/**
 * 관리자 › 텔레그램 채널 관리 (2026-10-04)
 *  · 채널 추가 / 현황 / 삭제
 *  · 채널별 수집 항목 체크: PDF(문서) · 채팅 본문 · 사진
 *  · 본문은 LLM 해석 없이 원문 그대로 DB(telegram_channel_posts)에 저장
 */
const FLAGS = [
  ['collect_pdf', 'PDF·문서'],
  ['collect_text', '채팅 본문'],
  ['collect_photo', '사진'],
];
const fmt = (n) => Number(n || 0).toLocaleString('ko-KR');
const cell = { padding: '8px 10px', borderBottom: '1px solid var(--line, #e5e7eb)', fontSize: 13, verticalAlign: 'middle' };
const input = { padding: '8px 10px', border: '1px solid var(--line-strong, #cbd5e1)', borderRadius: 8, fontSize: 13 };

export default function AdminTelegram() {
  const [channels, setChannels] = useState([]);
  const [msg, setMsg] = useState('');
  const [form, setForm] = useState({ channel_id: '', channel_name: '', collect_pdf: true, collect_text: true, collect_photo: false });
  const [days, setDays] = useState('');
  const [posts, setPosts] = useState({ total: 0, items: [] });
  const [filter, setFilter] = useState({ channel_id: '', q: '' });

  const call = useCallback(async (url, opt) => {
    const r = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opt });
    if (!r.ok) {
      let d = ''; try { d = (await r.json()).detail; } catch { /* noop */ }
      throw new Error(d || `요청 실패 (${r.status})`);
    }
    return r.json();
  }, []);

  const load = useCallback(() => call('/api/telegram/channels').then(setChannels).catch((e) => setMsg(e.message)), [call]);
  const loadPosts = useCallback(() => {
    const p = new URLSearchParams({ limit: '30' });
    if (filter.channel_id) p.set('channel_id', filter.channel_id);
    if (filter.q) p.set('q', filter.q);
    return call(`/api/telegram/posts?${p}`).then(setPosts).catch((e) => setMsg(e.message));
  }, [call, filter]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadPosts(); }, [loadPosts]);

  const act = async (fn, ok) => {
    try { await fn(); if (ok) setMsg(ok); await load(); } catch (e) { setMsg(e.message); }
  };
  const enc = encodeURIComponent;

  const add = (e) => {
    e.preventDefault();
    act(() => call('/api/telegram/channels', { method: 'POST', body: JSON.stringify(form) }), '채널을 추가했습니다.')
      .then(() => setForm({ ...form, channel_id: '', channel_name: '' }));
  };
  const toggle = (c, key) => act(() => call(`/api/telegram/channels/${enc(c.channel_id)}`, { method: 'PATCH', body: JSON.stringify({ [key]: !c[key] }) }));
  const remove = (c) => {
    if (!window.confirm(`'${c.channel_name || c.channel_id}' 채널을 목록에서 삭제할까요?\n이미 수집된 PDF·본문·사진 데이터는 보존됩니다.`)) return;
    act(() => call(`/api/telegram/channels/${enc(c.channel_id)}`, { method: 'DELETE' }), '삭제했습니다.');
  };
  const collect = (channel_id) => act(() => call('/api/telegram/collect', {
    method: 'POST', body: JSON.stringify({ channel_id, days: days ? Number(days) : undefined, limit: days ? 5000 : undefined }),
  }), `수집을 시작했습니다(${channel_id || '전체'}${days ? `, 최근 ${days}일` : ''}). 잠시 후 새로고침하세요.`);

  return (
    <div className="adm">
      <section className="adm-panel">
        <h2>채널 추가</h2>
        <form onSubmit={add} style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
          <input style={{ ...input, minWidth: 220 }} placeholder="@채널명 또는 채널 ID (필수)" value={form.channel_id}
            onChange={(e) => setForm({ ...form, channel_id: e.target.value })} required />
          <input style={{ ...input, minWidth: 180 }} placeholder="표시 이름 (선택)" value={form.channel_name}
            onChange={(e) => setForm({ ...form, channel_name: e.target.value })} />
          {FLAGS.map(([k, label]) => (
            <label key={k} style={{ fontSize: 13, display: 'flex', gap: 4, alignItems: 'center' }}>
              <input type="checkbox" checked={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.checked })} /> {label}
            </label>
          ))}
          <button type="submit" className="adm-btn"><Plus size={15} /> 추가</button>
        </form>
        <p className="adm-note" style={{ marginTop: 10, marginBottom: 0 }}>
          비공개 채널은 텔레그램 계정이 가입돼 있어야 합니다. 본문은 LLM 해석 없이 원문 그대로 저장됩니다.
        </p>
      </section>

      <section className="adm-panel">
        <h2 style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>채널 현황 ({channels.length})</span>
          <span style={{ display: 'flex', gap: 8, alignItems: 'center', fontWeight: 400 }}>
            <input style={{ ...input, width: 110 }} type="number" min="1" placeholder="과거 N일" value={days} onChange={(e) => setDays(e.target.value)} title="비우면 최근 500건, 입력하면 N일치(최대 5000건) 수집" />
            <button type="button" className="adm-btn" onClick={() => collect('')}><Play size={14} /> 전체 수집</button>
            <button type="button" className="adm-btn" style={{ borderColor: '#3b82f6', color: '#1d4ed8' }} onClick={() => act(() => call('/api/telegram/rag/index', { method: 'POST', body: JSON.stringify({ limit: 5000 }) }), 'RAG 벡터 인덱싱을 백그라운드에서 시작했습니다.')}>⚡ RAG 전체 임베딩</button>
            <button type="button" className="adm-btn" onClick={load}><RefreshCw size={14} /></button>
          </span>
        </h2>
        {msg && <p className="adm-note">{msg}</p>}
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>{['채널', ...FLAGS.map((f) => f[1]), '수집 누적', '마지막 수집', ''].map((h) => (
                <th key={h} style={{ ...cell, textAlign: 'left', color: 'var(--text-muted, #64748b)', fontWeight: 600 }}>{h}</th>))}</tr>
            </thead>
            <tbody>
              {channels.map((c) => (
                <tr key={c.id} style={{ opacity: c.is_active ? 1 : 0.5 }}>
                  <td style={cell}><b>{c.channel_name || c.channel_id}</b><br /><small style={{ color: '#94a3b8' }}>{c.channel_id}{c.is_active ? '' : ' · 비활성'}</small></td>
                  {FLAGS.map(([k]) => (
                    <td key={k} style={cell}><input type="checkbox" checked={!!c[k]} onChange={() => toggle(c, k)} /></td>
                  ))}
                  <td style={cell}>PDF {fmt(c.pdf_count)} · 본문 {fmt(c.post_count)} · 사진 {fmt(c.photo_count)}</td>
                  <td style={cell}>{c.last_sync ? c.last_sync.slice(0, 16).replace('T', ' ') : '–'}</td>
                  <td style={{ ...cell, whiteSpace: 'nowrap' }}>
                    {!c.is_active && <button type="button" className="adm-btn" onClick={() => act(() => call(`/api/telegram/channels/${enc(c.channel_id)}`, { method: 'PATCH', body: JSON.stringify({ is_active: true }) }))}>활성화</button>}
                    <button type="button" className="adm-btn" onClick={() => collect(c.channel_id)}><Play size={13} /> 수집</button>{' '}
                    <button type="button" className="adm-btn" onClick={() => remove(c)}><Trash2 size={13} /> 삭제</button>
                  </td>
                </tr>
              ))}
              {channels.length === 0 && <tr><td style={cell} colSpan={7}>등록된 채널이 없습니다.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section className="adm-panel">
        <h2>수집된 채팅 본문 ({fmt(posts.total)}건)</h2>
        <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
          <select style={input} value={filter.channel_id} onChange={(e) => setFilter({ ...filter, channel_id: e.target.value })}>
            <option value="">전체 채널</option>
            {channels.map((c) => <option key={c.id} value={c.channel_id}>{c.channel_name || c.channel_id}</option>)}
          </select>
          <input style={{ ...input, minWidth: 220 }} placeholder="본문 검색" value={filter.q} onChange={(e) => setFilter({ ...filter, q: e.target.value })} />
        </div>
        {posts.items.map((p) => (
          <div key={`${p.channel_id}-${p.message_id}`} style={{ padding: '10px 0', borderBottom: '1px solid var(--line, #e5e7eb)' }}>
            <small style={{ color: '#94a3b8' }}>{p.msg_date} · {p.channel_id}{p.has_saved_photo ? ' · 📷' : ''}{p.has_document ? ` · 📄 ${p.file_name || ''}` : ''}</small>
            <div style={{ whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.55, marginTop: 4 }}>{p.text || '(본문 없음)'}</div>
          </div>
        ))}
        {posts.items.length === 0 && <p className="adm-note">아직 수집된 본문이 없습니다. 채널 현황에서 ‘수집’을 눌러 시작하세요.</p>}
      </section>
    </div>
  );
}

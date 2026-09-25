/**
 * PriceChart — Lightweight Charts 기반 가격 차트 (HANDOFF §7-6, 2026-09-25)
 *
 * 캔들 + MA5/20/60 + 거래량(별도 pane) + 자본행위 마커 + 매수/매도 마커 + 가격선(진입가·손절선), 확대·이동·십자선.
 * App.jsx(24K줄)에 직접 넣지 않고 React.lazy로 불러 쓴다. 다른 차트(막대·파이·추이)는 recharts 유지.
 *
 * 표시 가격은 호출자가 넘긴 data 그대로다(execution_raw vs 조정 기준은 호출자가 정한다). 분할일에 갭이 보이면 데이터/기준 문제이지
 * 차트 문제가 아니다. 색: 한국식 양봉 빨강(#ef4444)·음봉 파랑(#3b82f6). TradingView 출처 표기(attribution 로고)는 기본값을 유지한다.
 *
 * props
 *   data:        [{date:'YYYY-MM-DD', open, high, low, close, volume}]  (필수, 오름차순)
 *   actions:     [{date, event_type|type|label}]   자본행위 마커(노란 ◆)
 *   trades:      [{date, side:'buy'|'sell', label?}] 매수▲/매도▼ 마커
 *   priceLines:  [{price, title, color?}]           가격선(진입가·손절선 등)
 *   height:      px (기본 340)
 */
import React from 'react';
import {
  createChart, CandlestickSeries, LineSeries, HistogramSeries, createSeriesMarkers, CrosshairMode,
} from 'lightweight-charts';

const UP = '#ef4444';
const DOWN = '#3b82f6';

const sma = (rows, n) => {
  const out = [];
  let sum = 0;
  for (let i = 0; i < rows.length; i++) {
    sum += rows[i].close || 0;
    if (i >= n) sum -= rows[i - n].close || 0;
    if (i >= n - 1) out.push({ time: rows[i].date, value: sum / n });
  }
  return out;
};

export default function PriceChart({ data = [], actions = [], trades = [], priceLines = [], height = 340 }) {
  const ref = React.useRef(null);

  React.useEffect(() => {
    if (!ref.current || !data.length) return undefined;
    const chart = createChart(ref.current, {
      autoSize: true,                                   // 컨테이너 폭 추종(모바일 포함)
      height,
      layout: { background: { color: 'transparent' }, textColor: 'rgba(148,163,184,0.9)', fontSize: 11 },
      grid: { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.05)' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)', scaleMargins: { top: 0.06, bottom: 0.26 } },
      timeScale: { borderColor: 'rgba(255,255,255,0.08)', timeVisible: false },
      localization: { priceFormatter: (p) => Math.round(p).toLocaleString('ko-KR') },
      handleScroll: { pressedMouseMove: true, horzTouchDrag: true },   // 터치 스크롤
      handleScale: { mouseWheel: true, pinch: true },
    });

    const rows = data.filter((d) => d.date && d.close != null);
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: UP, borderUpColor: UP, wickUpColor: UP, downColor: DOWN, borderDownColor: DOWN, wickDownColor: DOWN,
    });
    candles.setData(rows.map((d) => ({
      time: d.date, open: d.open || d.close, high: d.high || d.close, low: d.low || d.close, close: d.close,
    })));

    [[5, '#facc15'], [20, '#f97316'], [60, '#a78bfa']].forEach(([n, color]) => {
      const s = chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      s.setData(sma(rows, n));
    });

    const vol = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'vol', priceLineVisible: false, lastValueVisible: false });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
    vol.setData(rows.map((d) => ({ time: d.date, value: d.volume || 0, color: (d.close || 0) >= (d.open || d.close || 0) ? 'rgba(239,68,68,0.45)' : 'rgba(59,130,246,0.45)' })));

    const inRange = (date) => date >= rows[0].date && date <= rows[rows.length - 1].date;
    const snapDate = (date) => (rows.find((r) => r.date >= date) || {}).date;   // 휴장일이면 다음 거래일에 붙인다
    const markers = [];
    actions.forEach((a) => {
      const t = a.date && inRange(a.date) ? snapDate(a.date) : null;
      if (t) markers.push({ time: t, position: 'aboveBar', color: '#facc15', shape: 'circle', text: a.label || a.event_type || a.type || '자본행위' });
    });
    trades.forEach((tr) => {
      const t = tr.date && inRange(tr.date) ? snapDate(tr.date) : null;
      if (!t) return;
      markers.push(tr.side === 'sell'
        ? { time: t, position: 'aboveBar', color: DOWN, shape: 'arrowDown', text: tr.label || '매도' }
        : { time: t, position: 'belowBar', color: UP, shape: 'arrowUp', text: tr.label || '매수' });
    });
    markers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
    if (markers.length) createSeriesMarkers(candles, markers);

    priceLines.forEach((pl) => {
      if (pl && pl.price > 0) candles.createPriceLine({ price: pl.price, color: pl.color || '#facc15', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: pl.title || '' });
    });

    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [data, actions, trades, priceLines, height]);

  if (!data.length) return null;
  return <div ref={ref} style={{ width: '100%', height }} data-testid="price-chart" />;
}

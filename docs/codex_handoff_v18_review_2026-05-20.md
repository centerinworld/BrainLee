# V18 로직 검토/개선 핸드오프 (for Claude)

작성일: 2026-05-20 (KST)
작성자: Codex
목적: Claude가 V18 로직을 재검증하고 V18.1(개선안) 반영 여부를 판단할 수 있도록 근거/수치/재현방법 전달

---

## 1) 검토 범위

- 대상 코드: `/Applications/stock_dashboard/scripts/backtest_v16_final.py`
  - 파일 내 `E. V18` 섹션 구현 검토
  - DD/교체/switch/ticket 및 6th window 가중치 구조 확인
- 재현 실행:
  - `python3 /Applications/stock_dashboard/scripts/backtest_v16_final.py`
- 추가 스윕 실행:
  - 총 2,592 케이스
  - 파라미터 축:
    - `v_anchor` 비중
    - `v_largetrend` 비중
    - `ext_ticket_pct`
    - `ext_max_ticket`
    - `switch_th`
    - `dd_cut`

---

## 2) V18 기준값 재현 결과 (스크린샷과 일치)

기준 V18(코드 기본값):
- 누적수익률: `+487.28%`
- MDD: `-28.21%`
- 6구간(2025-06~2025-12): `+65.11%`

구간별 (V18):
- ① 2020-03~2021-11: `+94.01%`
- ② 2021-12~2022-10: `+17.62%`
- ③ 2022-11~2023-10: `+23.33%`
- ④ 2023-11~2024-12: `+8.66%`
- ⑤ 2024-06~2025-05: `+5.59%`
- ⑥ 2025-06~2025-12: `+65.11%`

참고: 출력 표기상 `V18` 결과를 일부 문구에서 `V17`로 출력하는 혼선이 존재 (성능엔 영향 없음).

---

## 3) 추가 스윕 결과 요약

스윕 산출물:
- `/Applications/stock_dashboard/scratch/v18_sweep_20260520.csv`

최고 성능 후보 (cost=15bp 기준):
- 누적수익률: `+601.16%`
- MDD: `-26.34%`
- 6구간: `+67.77%`

파라미터:
- 6th window weights:
  - `v_anchor=0.60`
  - `v_largetrend=0.10`
  - 나머지(`v11/v_trend/v8/v10`)는 기존 비율로 잔여분 정규화
- `ext_ticket_pct=0.30`
- `ext_max_ticket=60_000_000`
- `switch_th=0.12`
- `dd_cut=-0.10`

비용 민감도 비교 (기준 V18 vs 개선안):
- 15bp: `+487.28% -> +601.16%`
- 25bp: `+426.80% -> +507.61%`
- 35bp: `+371.95% -> +412.00%`

---

## 4) 해석 (확정 아님, Claude 재검증 권장)

관찰:
1. V18 성과의 핵심은 6구간 집중(반도체 대형주 포착)이며, 전체 성능도 개선안에서 동반 상승.
2. `dd_cut=-0.10`이 본 테스트 세트에서는 손실 제어 + 누적수익 동시 개선 방향으로 작동.
3. `switch_th`를 0.10으로 더 낮춘 변형은 비용이 높아질수록 교체 횟수 급증 리스크가 있으므로(스위치 대폭 증가) 운영상 보수적 검토 필요.

---

## 5) Claude에게 제안하는 다음 작업

### A. V18.1 후보 반영 후 재검증
후보 설정:
- `v_anchor=0.60`, `v_largetrend=0.10`
- `ext_ticket_pct=0.30`, `ext_max_ticket=60M`
- `switch_th=0.12`, `dd_cut=-0.10`

검증 항목:
- 누적수익, MDD, 6구간 수익 유지 여부
- 매수/교체/스탑아웃 횟수
- 기간별 수익률 왜곡 여부(특정 구간 편향 과도성)

### B. 과적합/강건성 체크
권장:
- 거래비용 15/25/35bp 시나리오 재현
- 6th window에서 v_anchor 비중 0.55~0.65 좁은 범위 민감도 재점검
- `ext_ticket_pct` 0.25 vs 0.30 실운용 가능성(체결·슬리피지 가정) 비교

### C. 리포트 품질 수정
- `backtest_v16_final.py` 출력 문구 중 `V17` 표기를 `V18`로 정합 수정

---

## 6) 재현 커맨드

기준 V18 재현:
```bash
python3 /Applications/stock_dashboard/scripts/backtest_v16_final.py
```

스윕 결과 확인:
```bash
python3 - <<'PY'
import pandas as pd
p='/Applications/stock_dashboard/scratch/v18_sweep_20260520.csv'
df=pd.read_csv(p)
print(df.head(20))
PY
```

---

## 7) 결론

- 현재 V18은 이미 우수한 성능이나, 동일 프레임에서 더 나은 조합이 확인됨.
- 우선순위는 `V18.1 후보`를 반영해 Claude가 재검증하고, 과적합 우려를 비용/민감도 테스트로 추가 확인하는 방향이 타당해 보임.


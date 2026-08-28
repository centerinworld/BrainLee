# Tenbagger Signal Research Update

Generated: 2026-06-18

## Bottom Line

- Strongest repeatable buy signal: market-regime-gated price/supply squeeze.

- Best confirmed factors: 3M relative strength, 20D institutional+foreign flow, turnover, 52-week-high proximity.

- Export YoY is useful as a quality confirmation, but it narrows the opportunity set. Short/lending cover is a risk filter, not a return booster in this test.

- Tight per-stock take-profit/stop rules reduced the tenbagger edge. Market regime and position count are better primary controls.

## Strategy Comparison

```csv
strategy,total_return_pct,mdd_pct,test_return_pct,test_mdd_pct,test_hit_pct,invested_months
price_supply_squeeze_top5,638.08,-31.97,391.38,-9.04,64.7,31
squeeze_shortcover_top5,348.42,-22.12,117.57,-22.12,52.9,31
squeeze_export_top5,464.96,-22.74,254.37,-15.29,47.1,31
all_signal_confirmed_top5,147.23,-25.56,222.26,-17.6,58.8,28
drawdown_reversal_event_top8,-34.76,-62.84,37.3,-19.18,35.3,33
KOSPI_^KS11,147.54,-32.69,224.17,-19.08,70.6,60
```

## 3x Signal Lift

```csv
signal,base_3x_rate_pct,top_decile_3x_rate_pct,top_decile_rows
ret_1m,2.27,3.39,11802
ret_3m,2.25,3.82,11714
supply20,2.27,3.92,7765
short_cover_1m,2.15,2.31,11218
export_yoy,4.48,5.06,1541
```

## Latest Model Candidates

```csv
rule,signal_month,stock_code,stock_name,market,close,ret_1m,ret_3m,ret_6m,near_high52,supply20,short_cover_1m,export_yoy,score
price_supply_squeeze_top5,2026-05,307950,현대오토에버,KOSPI,931000.0,1.1111111111111112,0.8900755364069519,3.8779321706765417,1.0,118039.0,-0.3730947063083292,,0.9895486898952036
price_supply_squeeze_top5,2026-05,64400,LG씨엔에스,KOSPI,113800.0,0.7010463378176384,0.620787886462064,0.9911741980108626,1.0,128684.0,-0.2801091559181108,,0.9841374569210172
price_supply_squeeze_top5,2026-05,34730,SK,KOSPI,676000.0,0.6407766990291262,0.6528117359413204,1.5654648956356736,0.9941176470588236,282574.0,-0.184906397059583,,0.981823783732828
price_supply_squeeze_top5,2026-05,336260,두산퓨얼셀,KOSPI,91400.0,0.7992125984251968,1.3989501312335957,1.85625,0.9004926108374385,110261.5771,0.0007468337990756,,0.9813629433403022
price_supply_squeeze_top5,2026-05,353200,대덕전자,KOSPI,190900.0,0.7545955882352942,2.0157977883096367,2.714007782101167,1.0,16929.88750000001,-0.536354443684461,0.1347744839326041,0.9793057055626858
squeeze_export_top5,2026-05,80220,제주반도체,KOSDAQ,97800.0,0.6746575342465753,1.2482758620689656,4.147368421052631,0.8225399495374264,26590.822400000012,-0.9402169129519744,2.1010056698994775,0.9639294535795844
squeeze_export_top5,2026-05,6400,삼성SDI,KOSPI,688000.0,0.0834645669291338,0.5889145496535797,1.2781456953642385,0.9662921348314608,469586.0,-0.0508423601339338,0.4699206783902758,0.949399490374325
squeeze_export_top5,2026-05,64290,인텍플러스,KOSDAQ,42100.0,0.3515248796147672,1.6918158567774937,2.447993447993448,0.9905882352941175,55117.36235,-0.5038748992174051,0.2699555267135811,0.920375699336632
squeeze_export_top5,2026-05,195870,해성디에스,KOSPI,89100.0,0.1082089552238805,0.3603053435114502,0.7188283579249664,0.8701171875,4443.0,-0.0298401786709767,2.032859409986272,0.9203479352411872
squeeze_export_top5,2026-05,95610,테스,KOSDAQ,119100.0,0.3457627118644069,0.703862660944206,1.857301099855128,0.9232558139534884,78218.0,-0.3142104962860252,0.260335504375895,0.9133618898631204
squeeze_shortcover_top5,2026-05,18880,한온시스템,KOSPI,5630.0,0.3215962441314555,0.2428256070640175,0.579242636746143,0.9825479930191972,114875.40368,0.1979617062623488,-0.0794728665025908,0.9600381680008307
squeeze_shortcover_top5,2026-05,7810,코리아써키트,KOSPI,119300.0,0.2148676171079429,1.0393162393162392,2.9667742650895668,1.0,55937.0,0.1260206411714004,0.1347744839326041,0.951451576999971
squeeze_shortcover_top5,2026-05,36710,심텍홀딩스,KOSDAQ,5410.0,0.4484605087014726,1.0338345864661656,0.6582134748103663,0.8811074918566775,9254.0,0.2600804056541915,,0.930771614625877
squeeze_shortcover_top5,2026-05,189330,씨이랩,KOSDAQ,10730.0,0.5416666666666667,1.3530701754385963,1.1897959183673468,1.0,3882.01231,0.1549314346959671,,0.9270778146239648
squeeze_shortcover_top5,2026-05,242040,나무기술,KOSDAQ,8780.0,0.6289424860853432,0.8464773922187172,5.36231884057971,1.0,8397.31659,0.044756439429366,,0.9238173600490156
```

## Practical Program Rule Candidate

1. Run the screen after month-end close.

2. If KOSPI is below its 10-month moving average, hold cash.

3. Candidate filter: 20-day average turnover >= KRW 5B, close >= KRW 1,000, 1M return > 5%, 3M return > 20%, 6M return > 20%, close >= 80% of 52-week high.

4. Rank score: 30% 1M RS + 20% 3M RS + 20% 20D institutional/foreign flow + 20% turnover + 10% 52-week-high proximity.

5. Buy top 5 for risk-balanced operation, or top 3 only when accepting high concentration risk.

6. Sell on next rebalance if no longer selected, or after 45 trading days. Avoid fixed 25-35% take-profit rules for tenbagger hunting; they reduced edge in testing.


## Caveats

- This is research, not investment advice. Survivorship bias and corporate-action errors can remain.

- Kiwoom credit and foreign holding data are too recent for full-period tests; use them only as current-risk overlays until enough history accumulates.

- `short_sell_daily` balance fields are null; short/lending work now uses `short_rank_daily.lnb_bal`.

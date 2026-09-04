# ETF universe correction: alphanumeric KRX short codes

This document supersedes earlier statements that 865 ETFs represented the complete Korean ETF universe.

## Root cause

The KIS master parser accepted only `[0-9]{6}` ETF short codes. KRX now also lists ETFs with six-character alphanumeric codes such as `0194M0`. These products were valid ETF (`group=EF`) records with valid ISINs but were silently excluded.

## Corrected result

- Numeric ETF codes: 865
- Alphanumeric ETF codes: 303
- Complete domestic ETF universe on 2026-09-01: 1,168
- Corrected KRX snapshots: 1,168
- Successful exact KRX PDFs: 1,167
- Empty KRX PDF: `489010` only
- Total KRX component rows: 76,059
- KIS CU/scale rows: 1,168 of 1,168

The prior 865-row universe is not eligible for source cutover evidence. The five-day parity counter was reset before any cutover.

## Samsung Electronics reconciliation

- ETF Check K-only: 241 ETFs, 465,454 hundred-million KRW
- Direct KRX/KIS: 241 ETFs, 464,959 hundred-million KRW
- ETF-count difference: 0
- Aggregate amount difference: approximately 0.11%
- Largest ETF amount: KODEX 200, 83,731 versus 83,639 hundred-million KRW

## Cutover state

- Mode: `legacy_validation`
- Required consecutive passing trading days: 5
- Current passing days after correction: 1
- Direct mode is activated only by the parity gate.
- Two consecutive post-cutover failures automatically select `legacy_fallback`.

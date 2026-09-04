# KRX Marketplace authentication correction

- `.env` contains non-empty `KRX_DATA_ID` and `KRX_DATA_PASS` values.
- KRX accepted the submitted credentials.
- The initial server response was `CD010` (password change required), not an
  account, password, IP, or API failure.
- A one-time password-change postponement was approved and processed on
  2026-08-30. Scheduled jobs do not postpone future password changes.
- A subsequent login returned `CD001` (success).
- The authenticated browser context did not contain the historical
  `mdc.client_session` cookie, but KRX served `MDCSTAT05001` through JSESSIONID.
- KODEX 200 returned 202 component rows in the live verification.

`full_pdf_collector_v2.py` therefore judges login by the KRX server response,
not by one cookie name. It retries intermittent login-page failures and retains
the original collector for rollback.

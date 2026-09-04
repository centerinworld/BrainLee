"""KRX full-PDF collector with current Marketplace authentication handling.

This keeps the original collector available for rollback while correcting two
authentication assumptions: CD001 is a successful login, and KRX PDF requests
can be authenticated by JSESSIONID without an mdc.client_session cookie.
"""
from __future__ import annotations

import logging
from typing import Any

import full_pdf_collector as collector


LOG = logging.getLogger("etf_full_pdf")


def login_code_is_success(code: Any) -> bool:
    return str(code or "") in {"", "CD001"}


class CurrentKRXSession(collector.KRXSession):
    def _load_login_form(self, page):
        for attempt in range(5):
            response = page.goto(collector.LOGIN_URL, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1000)
            body = page.locator("body").inner_text()
            if response and response.status >= 400:
                LOG.warning("KRX login page HTTP %s (attempt %s/5)", response.status, attempt + 1)
            elif "서비스 제공 불가능" in body or "일시적 접근 불안정" in body:
                LOG.warning("KRX login page temporarily unavailable (attempt %s/5)", attempt + 1)
            candidates = [page] + [frame for frame in page.frames if frame != page.main_frame]
            frame = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.locator("input[name='mbrId']").count()
                    and candidate.locator("input[name='pw']").count()
                ),
                None,
            )
            if frame is not None:
                return frame
            page.wait_for_timeout(1500)
        raise collector.KRXUnavailable("KRX login form was not available after 5 attempts")

    def _submit_login(self, page, frame) -> dict[str, Any]:
        id_field = frame.locator("input[name='mbrId']")
        password_field = frame.locator("input[name='pw']")
        id_field.fill("")
        password_field.fill("")
        id_field.press_sequentially(self.username, delay=15)
        password_field.press_sequentially(self.password, delay=25)
        with page.expect_response(
            lambda response: "MDCCOMS001D1.cmd" in response.url,
            timeout=15000,
        ) as response_info:
            if frame.locator("a.jsLoginBtn").count():
                frame.click("a.jsLoginBtn")
            else:
                frame.locator("button[type='submit'], input[type='submit']").first.click()
        return response_info.value.json()

    def _login(self) -> None:
        page = self.context.new_page()
        frame = self._load_login_form(page)
        payload = self._submit_login(page, frame)
        code = str(payload.get("_error_code") or "")
        if code == "CD010":
            raise collector.KRXUnavailable(
                "CD010: KRX password change is required; automatic postponement is disabled"
            )
        if not login_code_is_success(code):
            message = str(payload.get("_error_message") or "KRX login failed")
            raise collector.KRXUnavailable(f"{code}: {message}")

        # KRX currently returns CD001 for a successful login and authenticates
        # requests with JSESSIONID. Requiring the old mdc.client_session cookie
        # incorrectly rejects a valid session.


collector.KRXSession = CurrentKRXSession


if __name__ == "__main__":
    collector.main()

"""KRX full-PDF collector with duplicate-session confirmation support."""
from __future__ import annotations

import full_pdf_collector as collector
import full_pdf_collector_v2 as v2


class CurrentKRXSession(v2.CurrentKRXSession):
    @staticmethod
    def _click_duplicate_confirmation(page) -> None:
        for frame in [page] + list(page.frames):
            for selector in (
                ".ui-dialog:visible button:has-text('확인')",
                ".ui-dialog:visible a:has-text('확인')",
                ".pop_opened:visible button:has-text('확인')",
                ".pop_opened:visible a:has-text('확인')",
                "button:visible:has-text('확인')",
                "a:visible:has-text('확인')",
            ):
                target = frame.locator(selector)
                if target.count():
                    target.first.click()
                    return
        raise collector.KRXUnavailable("KRX duplicate-login confirmation button was not found")

    def _login(self) -> None:
        page = self.context.new_page()
        frame = self._load_login_form(page)
        payload = self._submit_login(page, frame)
        code = str(payload.get("_error_code") or "")

        if code == "CD010":
            raise collector.KRXUnavailable(
                "CD010: KRX password change is required; automatic postponement is disabled"
            )
        if code == "CD011":
            with page.expect_response(
                lambda response: "MDCCOMS001D1.cmd" in response.url,
                timeout=15000,
            ) as response_info:
                self._click_duplicate_confirmation(page)
            payload = response_info.value.json()
            code = str(payload.get("_error_code") or "")

        if not v2.login_code_is_success(code):
            message = str(payload.get("_error_message") or "KRX login failed")
            raise collector.KRXUnavailable(f"{code}: {message}")


collector.KRXSession = CurrentKRXSession


if __name__ == "__main__":
    collector.main()

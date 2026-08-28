import FinanceDataReader as fdr
import pandas as pd
import os
import difflib
import unicodedata
from datetime import datetime

KOR_TO_ENG = {
    'ᄀ': 'r', 'ᄁ': 'R', 'ᄂ': 's', 'ᄃ': 'e', 'ᄄ': 'E', 'ᄅ': 'f', 'ᄆ': 'a', 'ᄇ': 'q', 'ᄈ': 'Q', 'ᄉ': 't', 'ᄊ': 'T', 'ᄋ': 'd', 'ᄌ': 'w', 'ᄍ': 'W', 'ᄎ': 'c', 'ᄏ': 'z', 'ᄐ': 'x', 'ᄑ': 'v', 'ᄒ': 'g',
    'ᅡ': 'k', 'ᅢ': 'o', 'ᅣ': 'i', 'ᅤ': 'O', 'ᅥ': 'j', 'ᅦ': 'p', 'ᅧ': 'u', 'ᅨ': 'P', 'ᅩ': 'h', 'ᅪ': 'hk', 'ᅫ': 'ho', 'ᅬ': 'hl', 'ᅭ': 'y', 'ᅮ': 'n', 'ᅯ': 'nj', 'ᅰ': 'np', 'ᅱ': 'nl', 'ᅲ': 'b', 'ᅳ': 'm', 'ᅴ': 'ml', 'ᅵ': 'l',
    'ᆨ': 'r', 'ᆩ': 'R', 'ᆪ': 'rt', 'ᆫ': 's', 'ᆬ': 'sw', 'ᆭ': 'sg', 'ᆮ': 'e', 'ᆯ': 'f', 'ᆰ': 'fr', 'ᆱ': 'fa', 'ᆲ': 'fq', 'ᆳ': 'ft', 'ᆴ': 'fx', 'ᆵ': 'fv', 'ᆶ': 'fg', 'ᆷ': 'a', 'ᆸ': 'q', 'ᆹ': 'qt', 'ᆺ': 't', 'ᆻ': 'T', 'ᆼ': 'd', 'ᆽ': 'w', 'ᆾ': 'c', 'ᆿ': 'z', 'ᇀ': 'x', 'ᇁ': 'v', 'ᇂ': 'g'
}

def kor_to_eng(text: str) -> str:
    if not isinstance(text, str):
        return ""
    nfd = unicodedata.normalize("NFD", text)
    return "".join(KOR_TO_ENG.get(c, c.lower()) for c in nfd)


class TickerMapper:
    _instance = None
    _cache_file = "ticker_cache.csv"
    _df = None
    _last_update = None
    _load_failed = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TickerMapper, cls).__new__(cls)
        return cls._instance

    def _load_tickers(self):
        now = datetime.now()
        if os.path.exists(self._cache_file):
            mtime = datetime.fromtimestamp(os.path.getmtime(self._cache_file))
            if mtime.date() == now.date():
                self._df = pd.read_csv(self._cache_file, dtype={"Code": str})
                self._load_failed = False
                return

        # DB에서 먼저 로드 시도 (KRX API 대신)
        try:
            import sqlite3
            db_paths = [
                "/Applications/stock_dashboard/stock.db",
                "stock.db",
            ]
            for db_path in db_paths:
                if os.path.exists(db_path):
                    conn = sqlite3.connect(db_path)
                    df_db = pd.read_sql(
                        "SELECT stock_code as Code, stock_name as Name FROM listed_company_info",
                        conn, dtype={"Code": str}
                    )
                    conn.close()
                    if len(df_db) > 100:
                        self._df = df_db
                        self._df.to_csv(self._cache_file, index=False)
                        self._load_failed = False
                        print(f"[TickerMapper] DB에서 {len(df_db)}개 종목 로드 완료")
                        return
        except Exception as e:
            print(f"[TickerMapper] DB 로드 실패: {e}")

        # DB 실패 시 fdr fallback
        print("KRX 종목 리스트를 새로 가져오는 중...")
        try:
            df_krx = fdr.StockListing("KRX")
            self._df = df_krx[["Code", "Name"]]
            self._df.to_csv(self._cache_file, index=False)
            self._last_update = now
            self._load_failed = False
            print(f"[TickerMapper] KRX에서 {len(self._df)}개 종목 로드 완료")
        except Exception as e:
            print(f"[경고] 종목 리스트 신규 로드 실패: {e}")
            if os.path.exists(self._cache_file):
                print("[경고] 구버전 캐시 파일을 사용합니다.")
                self._df = pd.read_csv(self._cache_file, dtype={"Code": str})
                self._load_failed = False
            else:
                print("[오류] 종목 리스트 로드 완전 실패.")
                self._load_failed = True

    def _ensure_loaded(self) -> bool:
        if self._df is None:
            self._load_tickers()
        return self._df is not None

    def get_code(self, name: str) -> str:
        if not self._ensure_loaded():
            return None

        # 1. 정확히 일치
        res = self._df[self._df["Name"] == name]
        if not res.empty:
            return str(res.iloc[0]["Code"])

        # 2. 부분 일치 (짧은 이름 우선)
        res = self._df[self._df["Name"].str.contains(name, na=False)]
        if not res.empty:
            sorted_res = res.assign(name_len=res["Name"].str.len()).sort_values("name_len")
            return str(sorted_res.iloc[0]["Code"])

        # 3. 유사도 기반 (cutoff 높여서 오매칭 방지)
        all_names = self._df["Name"].astype(str).tolist()
        matches = difflib.get_close_matches(name, all_names, n=5, cutoff=0.5)
        if matches:
            for m in matches:
                if name[:-1] in m:
                    res = self._df[self._df["Name"] == m]
                    return str(res.iloc[0]["Code"])
            best_match = matches[0]
            res = self._df[self._df["Name"] == best_match]
            if not res.empty:
                return str(res.iloc[0]["Code"])

        return None

    def get_name(self, code: str) -> str:
        if not self._ensure_loaded():
            return "Unknown"
        res = self._df[self._df["Code"] == code]
        if not res.empty:
            return str(res.iloc[0]["Name"])
        return code

    def search(self, query: str, limit: int = 10) -> list:
        """프론트엔드 자동완성을 위한 검색 - 정확도 우선 정렬"""
        if not self._ensure_loaded() or not query:
            return []

        query = query.strip()

        # 6자리 숫자 코드 직접 검색
        if query.isdigit() and len(query) == 6:
            res = self._df[self._df["Code"] == query]
            if not res.empty:
                return [{"name": str(res.iloc[0]["Name"]), "code": query}]

        # 숫자로 시작하는 코드 검색 (앞자리 매칭)
        if query.isdigit():
            res = self._df[self._df["Code"].str.startswith(query, na=False)]
            return [{"name": str(r["Name"]), "code": str(r["Code"])}
                    for _, r in res.head(limit).iterrows()]

        clean_q = query.replace(" ", "").upper()

        # 동의어 처리 (양방향)
        synonyms = {
            "에스케이": "SK", "엘지": "LG", "케이티": "KT",
            "에이치디": "HD", "씨제이": "CJ", "엔에이치": "NH",
            "케이비": "KB", "디비": "DB", "케이씨씨": "KCC",
            "엘에스": "LS", "지에스": "GS", "씨에스": "CS",
            "현대": "현대", "삼성": "삼성", "하나": "하나",
        }
        alternate_q = clean_q
        for kor, eng in synonyms.items():
            if kor in clean_q:
                alternate_q = clean_q.replace(kor, eng)
                break
            elif eng in clean_q and eng not in ["현대","삼성","하나"]:
                alternate_q = clean_q.replace(eng, kor)
                break

        df_search = self._df.copy()
        df_search["CleanName"] = df_search["Name"].str.replace(" ", "").str.upper()

        # 1순위: 정확히 일치
        mask_exact = df_search["CleanName"] == clean_q

        # 2순위: 앞글자 일치
        mask_starts = (
            df_search["CleanName"].str.startswith(clean_q, na=False) |
            df_search["CleanName"].str.startswith(alternate_q, na=False)
        )

        # 3순위: 포함
        mask_contains = (
            df_search["CleanName"].str.contains(clean_q, na=False) |
            df_search["CleanName"].str.contains(alternate_q, na=False)
        )

        # 영문 타이핑 지원
        if clean_q.isalpha() and clean_q.isascii():
            q_eng = clean_q.lower()
            df_search["EngName"] = df_search["Name"].apply(kor_to_eng)
            mask_contains = mask_contains | df_search["EngName"].str.contains(q_eng, na=False)
            mask_starts = mask_starts | df_search["EngName"].str.startswith(q_eng, na=False)

        # 정확도 순으로 합치기
        res_exact    = df_search[mask_exact]
        res_starts   = df_search[mask_starts & ~mask_exact]
        res_contains = df_search[mask_contains & ~mask_starts & ~mask_exact]
        combined = pd.concat([res_exact, res_starts, res_contains]).drop_duplicates(subset=["Code"])

        results = []
        for _, row in combined.head(limit).iterrows():
            results.append({"name": str(row["Name"]), "code": str(row["Code"])})
        return results


ticker_mapper = TickerMapper()

if __name__ == "__main__":
    print(f"KB증권 -> {ticker_mapper.search('KB증권')}")
    print(f"하나증권 -> {ticker_mapper.search('하나증권')}")
    print(f"sk하이닉스 -> {ticker_mapper.search('sk하이닉스')}")
    print(f"005930 -> {ticker_mapper.search('005930')}")

"""
2026-09-08 신규: 사업 설명 텍스트(애널리스트 리포트, DART 사업보고서 원문 등)를
DeepSeek으로 hs_codes 카테고리와 의미 매칭해 hs_code_company_map 후보를 생성한다.

배경: segment_revenue 세그먼트명을 단순 키워드 부분문자열로 매칭했더니 한국어
동음이의어(KCC의 "실리콘" 사업부문이 반도체 실리콘 웨이퍼로 오매칭 등)로 오탐률
약 50%가 나왔음(CLAUDE.md 4차 수정 (b) 참조). 문맥을 읽을 수 있는 LLM 매칭으로
이 문제를 줄이는 게 목표 — 단, LLM도 틀릴 수 있으므로 결과는 스테이징 테이블에만
쌓고, hs_code_company_map으로의 승격은 사람이 확인 후 별도 커밋 단계에서 수행한다
(이번 세션 전체에서 지켜온 원칙 — 자동매칭 결과를 검증 없이 바로 반영하지 않음).

비용 절감을 위해 OpenAI 대신 DeepSeek(config.get_ai_client(), AI_PROVIDER=deepseek
기본값)을 사용한다.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RUNTIME_ROOT))

import config  # noqa: E402

HS_DB_PATH = str(RUNTIME_ROOT / "hs_trade_lab" / "data" / "hs_trade_lab.db")

_MIN_CONFIDENCE = 0.65  # 이 미만은 후보에서 아예 버림(스테이징에도 안 담음)


def load_hs_taxonomy(hs_conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return hs_conn.execute(
        "SELECT hs_code, description FROM hs_codes WHERE description IS NOT NULL ORDER BY hs_code"
    ).fetchall()


def build_prompt(company_name: str, source_label: str, text: str, taxonomy: list[tuple[str, str]]) -> str:
    tax_lines = "\n".join(f"{hs}: {desc}" for hs, desc in taxonomy)
    return f"""당신은 한국 기업의 사업 내용을 관세청 HS(수출입) 품목분류와 매칭하는 애널리스트입니다.

아래는 현재 우리가 무역데이터를 추적 중인 HS 품목 카테고리 목록입니다(이 목록에 있는
코드만 사용하세요. 목록에 없는 카테고리는 절대 만들어내지 마세요):
---
{tax_lines}
---

다음은 "{company_name}"에 대한 {source_label} 텍스트입니다:
\"\"\"
{text[:4000]}
\"\"\"

주의: 이 텍스트는 십중팔구 정말로 "{company_name}"에 대한 것입니다(경쟁사·업황 비교가
같이 언급되는 것은 정상입니다 — 그것만으로 배제하지 마세요). 텍스트 전체가 처음부터
끝까지 명백히 다른 하나의 회사만 다루고 있어서 "{company_name}"과 무관하다고 확신되는
경우에만 예외적으로 빈 배열을 반환하세요.

위 목록의 카테고리 중 "{company_name}"이 실제로 해당 품목을 상당한 규모로
생산·수출하는지 판단하세요. 다음 사항을 유의하세요:
- 회사 이름에 우연히 포함된 문자열(예: "실리콘" vs 실리콘 웨이퍼)에 속지 마세요.
- 목록에 정확히 일치하는 품목이 없으면 억지로 끼워맞추지 마세요(예: 방탄복을
  "전차·장갑차 부품"으로 매칭하는 식). 하지만 실제로 해당 사업을 하고 목록에도
  합리적으로 맞는 품목이 있다면 적극적으로 매칭하세요 — 너무 소극적일 필요는
  없습니다.
- 확신이 없으면 포함하지 마세요(빈 배열도 정상적인 결과입니다).
- 같은 회사가 여러 카테고리에 매칭될 수 있습니다.

매칭되는 카테고리를 JSON으로 반환하세요:
{{"matches": [{{"hs_code": "정확히 위 목록에 있는 코드", "confidence": 0.0~1.0, "reason": "왜 매칭되는지 한 문장"}}]}}
"""


def call_llm(client, model: str, prompt: str) -> dict:
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=600,
    )
    return json.loads(response.choices[0].message.content or "{}")


def match_text_to_hs(
    client, model: str, taxonomy: list[tuple[str, str]],
    company_name: str, source_label: str, text: str,
) -> list[dict]:
    """반환: [{"hs_code":..., "confidence":..., "reason":...}, ...] (신뢰도 필터링 완료)"""
    if not text or not text.strip():
        return []
    valid_codes = {hs for hs, _ in taxonomy}
    prompt = build_prompt(company_name, source_label, text, taxonomy)
    try:
        payload = call_llm(client, model, prompt)
    except Exception as exc:
        return [{"_error": str(exc)}]
    matches = payload.get("matches") or []
    out = []
    for m in matches:
        hs = str(m.get("hs_code") or "").strip()
        conf = m.get("confidence")
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            continue
        if hs not in valid_codes:
            continue  # 목록에 없는 코드를 지어낸 경우 — 환각 방지 필터
        if conf < _MIN_CONFIDENCE:
            continue
        out.append({"hs_code": hs, "confidence": conf, "reason": m.get("reason", "")})
    return out


def ensure_staging_table(hs_conn: sqlite3.Connection) -> None:
    hs_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_hs_mapping_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            hs_code TEXT NOT NULL,
            confidence REAL NOT NULL,
            reason TEXT,
            source TEXT NOT NULL,
            source_ref TEXT,
            already_mapped INTEGER NOT NULL DEFAULT 0,
            reviewed_status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(stock_code, hs_code, source)
        )
        """
    )
    hs_conn.commit()


# 2026-09-08 발견: 리포트 발행 증권사의 stock_code가 report_files에 잘못 라벨링된
# 사례(상상인증권 파일이 실제로는 삼성SDI 리포트 — LLM이 본문은 정확히 읽었지만
# report_files의 stock_code 라벨을 그대로 따라감)를 실측 확인. 물리적 상품을 수출할
# 수 없는 섹터는 최종 승격 전 걸러내는 안전망.
_IMPLAUSIBLE_SECTOR_KEYWORDS = (
    "증권", "은행", "보험", "카드", "캐피탈", "저축은행", "자산운용", "신탁",
    "지주", "부동산", "리츠", "홀딩스",  # 지주/홀딩스는 자체적으로 제품을 만들지 않음
)


# 2026-09-08 실측 발견: confidence>=0.8 고신뢰도 표본(25건 무작위 검토)에서도
# LLM이 스스로 "정확히 일치하는 코드가 없다"고 인정하면서 억지로 끼워맞춘 사례가
# 있었음(씨엠티엑스: "목록에 실리콘 파츠 관련 코드가 없어 가장 유사한 실리콘카바이드로
# 매칭함"이라고 명시하고도 confidence 0.9 부여 — 프롬프트의 "억지로 끼워맞추지
# 말라" 지시가 완벽히 지켜지지 않음). reason 텍스트에 이런 회피성 표현이 있으면
# confidence와 무관하게 최종 승격 전 별도 검토로 돌린다.
_HEDGE_PHRASES = (
    "유사한 용도", "가장 유사한", "코드가 없어", "일치하는 코드가 없", "직접적인 코드가 없",
    "유통", "포함될 수 있습니다", "정확히 일치하지 않", "간접적으로",
    # 2026-09-08 추가: 200+400개사 배치 실측에서 LLM이 reason에 "매칭하지 않음"이라고
    # 스스로 명시하고도 그 항목을 matches 배열에 포함시켜버린 자기모순 사례 발견
    # (서울반도체→LED, 오리온→라면류 등) — 명시적 부정 표현이 있으면 무조건 배제.
    "매칭하지 않", "매칭은 아님", "매칭은 아니", "적합하지 않",
)


def has_hedge_language(reason: str) -> bool:
    reason = reason or ""
    return any(p in reason for p in _HEDGE_PHRASES)


_SUBSIDIARY_PARTICLES = "는은이가의도를을에과와만로으"


def mentions_subsidiary(reason: str, company_name: str) -> bool:
    """
    2026-09-08 실측 발견: 지주/홀딩스라는 이름이 없는 그룹 대표 종목(SK, GS, 한화,
    효성, SK스퀘어, SK디스커버리, 에코프로 등)도 사업보고서/리포트 원문이 그룹
    전체를 서술하다 보니, LLM이 "SK하이닉스가", "한화에어로스페이스는",
    "GS칼텍스는", "효성티앤씨의" 처럼 자회사 이름을 근거로 든 채 매칭을 모회사
    종목코드에 붙이는 사례가 다수 확인됨(sector_large만으로는 안 걸러짐). reason에
    회사명 뒤에 조사가 아닌 추가 글자가 붙어(예: "SK"+"하이닉스") 다른 법인명이
    등장하거나, "자회사"/"계열사"라는 단어가 직접 나오면 자회사 귀속 의심으로
    본다.
    """
    reason = reason or ""
    if "자회사" in reason or "계열사" in reason or "계열회사" in reason:
        return True
    if not company_name:
        return False
    m = re.search(re.escape(company_name) + r"([가-힣A-Za-z]{2,10})", reason)
    if m:
        suffix = m.group(1)
        if not (len(suffix) <= 2 and all(c in _SUBSIDIARY_PARTICLES for c in suffix)):
            return True
    return False


def is_sector_plausible(stock_code: str, main_conn: sqlite3.Connection) -> bool:
    """
    2026-09-08 실측 보정: sector_large/sector_mid만으로는 부족했음 — 실제 DART
    파일럿 배치에서 롯데지주/SK스퀘어/AK홀딩스/한솔홀딩스/일진홀딩스/녹십자홀딩스/
    하이트진로홀딩스/한화/SK 등 지주·그룹계열사가 자회사(SK하이닉스, 롯데 계열
    식품·화학사 등)의 사업을 자기 것처럼 다중 매칭당하는 패턴이 대량 확인됐는데,
    이 회사들의 sector_large/sector_mid는 "산업재/자본재" "필수소비재" 등으로
    분류돼 있어 섹터 텍스트만으로는 안 걸러짐 — 반면 종목명 자체에는 "지주"/
    "홀딩스"가 항상 들어있으므로 종목명도 함께 검사한다.
    """
    row = main_conn.execute(
        "SELECT stock_name, sector_large, sector_mid FROM stock_universe WHERE stock_code=?", (stock_code,)
    ).fetchone()
    if not row:
        return True  # 정보 없으면 배제하지 않음(과잉차단 방지)
    text = " ".join(str(x) for x in row if x)
    return not any(kw in text for kw in _IMPLAUSIBLE_SECTOR_KEYWORDS)


def save_candidates(
    hs_conn: sqlite3.Connection, stock_code: str, stock_name: str,
    source: str, source_ref: str, matches: list[dict], already_mapped: bool,
) -> int:
    n = 0
    for m in matches:
        if "_error" in m:
            continue
        hs_conn.execute(
            """
            INSERT INTO llm_hs_mapping_candidates
                (stock_code, stock_name, hs_code, confidence, reason, source, source_ref, already_mapped)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(stock_code, hs_code, source) DO UPDATE SET
                confidence=excluded.confidence, reason=excluded.reason,
                source_ref=excluded.source_ref, already_mapped=excluded.already_mapped
            """,
            (stock_code, stock_name, m["hs_code"], m["confidence"], m["reason"],
             source, source_ref, int(already_mapped)),
        )
        n += 1
    hs_conn.commit()
    return n

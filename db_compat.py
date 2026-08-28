from __future__ import annotations

import re
import logging
import os
import sqlite3
from collections.abc import Iterator, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from config import DATABASE_URL, IS_POSTGRES
from database import engine
from db_utils import connect_stock_db


logger = logging.getLogger(__name__)
_ORIGINAL_SQLITE_CONNECT = sqlite3.connect
_SQLITE_ROUTER_INSTALLED = False


class CompatRow:
    """Row supporting both sqlite.Row-style numeric and named access."""

    def __init__(self, columns: Sequence[str], values: Sequence[Any]) -> None:
        self._columns = tuple(columns)
        self._values = tuple(self._normalize(value) for value in values)
        self._by_name = dict(zip(self._columns, self._values))

    @staticmethod
    def _normalize(value: Any) -> Any:
        # SQLite callers expect INTEGER/REAL as int/float, while PostgreSQL
        # NUMERIC is returned as Decimal.
        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else float(value)
        return value

    def __getitem__(self, key: str | int | slice) -> Any:
        # 2026-08-12: sqlite3.Row는 슬라이스 접근(r[1:])을 지원하는데 이 클래스엔 없었음 —
        # backtest.py _run_generic_backtest의 `r[1:]` 호출이 "unhashable type: slice"로
        # 크래시(v2 등 여러 엔진이 PostgreSQL 라우팅 하에서 매번 실패하고 있었음). 원본
        # 튜플을 그대로 슬라이스해 sqlite3.Row와 동일하게 동작시킴.
        if isinstance(key, (int, slice)):
            return self._values[key]
        return self._by_name[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._columns)

    def keys(self) -> tuple[str, ...]:
        return self._columns


_DATE_NOW_OFFSET_RE = re.compile(
    r"date\(\s*'now'\s*,\s*'-(\d+)\s+days?'\s*\)", re.IGNORECASE
)
_DATE_PARAM_OFFSET_RE = re.compile(
    r"date\(\s*\?\s*,\s*'-(\d+)\s+days?'\s*\)", re.IGNORECASE
)
_DATE_COLUMN_OFFSET_RE = re.compile(
    r"date\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*,\s*'-(\d+)\s+days?'\s*\)",
    re.IGNORECASE,
)
_STRFTIME_NOW_RE = re.compile(
    r"strftime\(\s*'%Y%m%d'\s*,\s*date\(\s*'now'\s*,\s*'-(\d+)\s+days?'\s*\)\s*\)",
    re.IGNORECASE,
)
_YMD_DATE_COMPARISON_RE = re.compile(
    r"((?:[A-Za-z_][A-Za-z0-9_]*\.)?(?:rcept_dt|bas_dt))\s*>=\s*date\(\s*'now'\s*,\s*'-(\d+)\s+days?'\s*\)",
    re.IGNORECASE,
)
_PRAGMA_TABLE_INFO_RE = re.compile(
    r"^\s*PRAGMA\s+table_info\s*\(\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?\s*\)\s*;?\s*$",
    re.IGNORECASE,
)
_PRAGMA_NOOP_RE = re.compile(
    r"^\s*PRAGMA\s+(?:busy_timeout|foreign_keys|journal_mode|synchronous|temp_store|"
    r"cache_size|mmap_size|wal_autocheckpoint|optimize|automatic_index)(?:\s*=.*)?;?\s*$",
    re.IGNORECASE,
)


def _split_top_level_commas(s: str) -> list[str]:
    """Split on commas that are not nested inside parens or string literals."""
    parts: list[str] = []
    depth = 0
    in_quote: str | None = None
    start = 0
    for k, c in enumerate(s):
        if in_quote:
            if c == in_quote:
                in_quote = None
        elif c in ("'", '"'):
            in_quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(s[start:k])
            start = k + 1
    parts.append(s[start:])
    return parts


def _translate_round_precision(sql: str) -> str:
    """SQLite's ROUND(expr, n) accepts any numeric-ish expr, but PostgreSQL's
    2-arg ROUND(numeric, int) overload rejects double precision (e.g. the
    common `x/1e8` unit-conversion pattern used throughout this codebase),
    raising UndefinedFunction. Cast the first argument to ::numeric.

    Written as a bracket-walker (not a flat regex) because real call sites
    nest parens, e.g. ROUND(ABS(cf.capex)/1e8, 1). Single-arg ROUND(expr) and
    calls whose precision isn't a literal integer are left untouched (both
    are already valid on both engines / not safely rewritable).
    """
    out: list[str] = []
    lower = sql.lower()
    i = 0
    n = len(sql)
    while True:
        idx = lower.find("round(", i)
        if idx == -1:
            out.append(sql[i:])
            break
        if idx > 0 and (sql[idx - 1].isalnum() or sql[idx - 1] == "_"):
            # part of a longer identifier (unlikely, but be safe)
            out.append(sql[i:idx + 6])
            i = idx + 6
            continue
        out.append(sql[i:idx])
        open_paren = idx + 5
        depth = 0
        j = open_paren
        in_quote: str | None = None
        while j < n:
            c = sql[j]
            if in_quote:
                if c == in_quote:
                    in_quote = None
            elif c in ("'", '"'):
                in_quote = c
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j >= n:
            out.append(sql[idx:])
            i = n
            continue
        inner = sql[open_paren + 1:j]
        parts = _split_top_level_commas(inner)
        if len(parts) == 2 and re.match(r"^\s*-?\d+\s*$", parts[1]):
            expr, precision = parts[0].strip(), parts[1].strip()
            out.append(f"ROUND(({expr})::numeric, {precision})")
        else:
            out.append(sql[idx:j + 1])
        i = j + 1
    return "".join(out)


def _replace_qmark_placeholders(sql: str) -> str:
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(sql):
        char = sql[i]
        if quote:
            out.append(char)
            if char == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    out.append(sql[i + 1])
                    i += 1
                else:
                    quote = None
        elif char in ("'", '"'):
            quote = char
            out.append(char)
        elif char == "?":
            out.append("%s")
        else:
            out.append(char)
        i += 1
    return "".join(out)


def translate_sqlite_sql(sql: str) -> str:
    """Translate the SQLite subset used by live stock-dashboard queries."""
    insert_or_ignore = bool(
        re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", sql, flags=re.IGNORECASE)
    )
    translated = _YMD_DATE_COMPARISON_RE.sub(
        lambda m: f"{m.group(1)} >= TO_CHAR(CURRENT_DATE - INTERVAL '{m.group(2)} days', 'YYYYMMDD')",
        sql,
    )
    translated = _STRFTIME_NOW_RE.sub(
        lambda m: f"TO_CHAR(CURRENT_DATE - INTERVAL '{m.group(1)} days', 'YYYYMMDD')",
        translated,
    )
    translated = re.sub(
        r"strftime\(\s*'%w'\s*,\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\)",
        r"EXTRACT(DOW FROM \1::date)::int::text",
        translated,
        flags=re.IGNORECASE,
    )
    translated = _DATE_NOW_OFFSET_RE.sub(
        lambda m: f"TO_CHAR(CURRENT_DATE - INTERVAL '{m.group(1)} days', 'YYYY-MM-DD')", translated
    )
    translated = re.sub(
        r"date\(\s*'now'\s*,\s*'([+-])(\d+)\s+(days?|months?|years?)'\s*\)",
        lambda m: (
            "TO_CHAR(CURRENT_DATE "
            f"{'+' if m.group(1) == '+' else '-'} INTERVAL '{m.group(2)} {m.group(3)}', "
            "'YYYY-MM-DD')"
        ),
        translated,
        flags=re.IGNORECASE,
    )
    translated = _DATE_PARAM_OFFSET_RE.sub(
        lambda m: f"TO_CHAR(?::date - INTERVAL '{m.group(1)} days', 'YYYY-MM-DD')", translated
    )
    translated = _DATE_COLUMN_OFFSET_RE.sub(
        lambda m: f"({m.group(1)}::date - INTERVAL '{m.group(2)} days')::date",
        translated,
    )
    translated = re.sub(
        r"date\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\)",
        r"\1::date",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"date\(\s*'now'\s*\)",
        "TO_CHAR(CURRENT_DATE, 'YYYY-MM-DD')",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"datetime\(\s*'now'\s*,\s*'localtime'\s*\)",
        "CURRENT_TIMESTAMP",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"datetime\(\s*'now'\s*,\s*'-(\d+)\s+(days?|seconds?)'\s*(?:,\s*'localtime'\s*)?\)",
        lambda m: f"(CURRENT_TIMESTAMP - INTERVAL '{m.group(1)} {m.group(2)}')",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"datetime\(\s*'now'\s*\)",
        "CURRENT_TIMESTAMP",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"datetime\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\)",
        r"\1::timestamp",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
        translated,
        flags=re.IGNORECASE,
    )
    # The legacy database mixed INTEGER and BOOLEAN representations for
    # is_annual.  A bare `IS FALSE` only works for BOOLEAN columns, while the
    # point-in-time backtests also join numeric disclosure tables.  Compare a
    # normalized text value so the same SQLite-era query is valid for both.
    translated = re.sub(
        r"\b((?:[A-Za-z_][A-Za-z0-9_]*\.)?is_annual)\s*=\s*0\b",
        r"COALESCE(\1::text, '0') IN ('0', 'false', 'f')",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"\b((?:[A-Za-z_][A-Za-z0-9_]*\.)?is_annual)\s*=\s*1\b",
        r"COALESCE(\1::text, '0') IN ('1', 'true', 't')",
        translated,
        flags=re.IGNORECASE,
    )
    # 2026-08-12: SQLite printf('%d-05-15', f.year) 같은 as-of 공시일 계산 패턴이
    # backtest.py 14곳에 존재 — PostgreSQL엔 printf()가 없어 UndefinedFunction으로
    # 전부 실패하고 있었음(재무데이터 as-of 로직이라 여러 전략의 핵심 경로).
    # printf('%d<literal suffix>', <expr>) -> (<expr>::text || '<literal suffix>')
    translated = re.sub(
        r"printf\(\s*'%d([^']*)'\s*,\s*([^()]+?)\s*\)",
        r"((\2)::text || '\1')",
        translated,
        flags=re.IGNORECASE,
    )
    # 2026-08-14: SQLite ROUND(x/1e8, 1) 류(억원 환산에 전 코드베이스에서 광범위 사용)가
    # PostgreSQL의 ROUND(double precision, int) 미지원으로 UndefinedFunction 500을 유발
    # (routes/dart_excel.py 등). expr를 ::numeric으로 캐스팅.
    translated = _translate_round_precision(translated)
    # 2026-08-14: SQLite julianday(A)-julianday(B)(두 날짜 사이 일수차, corporate
    # action엔진/미국주식 팩터동기화 등에서 사용)는 PostgreSQL에 julianday 함수 자체가
    # 없어 UndefinedFunction — (A::date - B::date)로 대체(정수 일수차, 동일 의미).
    # 인자에 substr(...) 같은 1단계 중첩 괄호까지 허용(build_corporate_action_
    # adjustment_engine.py의 julianday(substr(...)||...||substr(...)) 패턴 대응).
    _JULIANDAY_ARG = r"(?:[^()]|\([^()]*\))+"
    translated = re.sub(
        rf"julianday\(\s*({_JULIANDAY_ARG}?)\s*\)\s*-\s*julianday\(\s*({_JULIANDAY_ARG}?)\s*\)",
        r"((\1)::date - (\2)::date)",
        translated,
        flags=re.IGNORECASE,
    )
    # 2026-08-14: SQLite instr(haystack, needle)(문자열 포함 위치, 0=미발견)는
    # PostgreSQL엔 없는 함수 — 동일 인자순서/동일 반환값(1-indexed, 0=미발견)인
    # STRPOS(haystack, needle)로 치환(hs_trade_lab/scripts/rebuild_analysis2_cache.py
    # 등 정기실행 스크립트에서 사용).
    translated = re.sub(r"\binstr\(", "STRPOS(", translated, flags=re.IGNORECASE)
    # 2026-08-14: SQLite GROUP_CONCAT()는 PostgreSQL에 없는 함수(STRING_AGG로 대체
    # 필요) — main.py/routes/detailed_analysis.py/routes/cafe_signals.py 등에서
    # UndefinedFunction으로 매번 500을 내고 있었음(개별종목 상세 data-quality 등).
    # 관측된 3가지 형태만 처리(전부 괄호 중첩 없는 단순 인자):
    #   GROUP_CONCAT(DISTINCT col ORDER BY col2) -> STRING_AGG(DISTINCT col::text, ',' ORDER BY col2)
    translated = re.sub(
        r"GROUP_CONCAT\(\s*DISTINCT\s+([A-Za-z_][A-Za-z0-9_.]*)\s+ORDER\s+BY\s+([A-Za-z_][A-Za-z0-9_.]*)\s*\)",
        lambda m: (
            f"STRING_AGG(DISTINCT {m.group(1)}::text, ','"
            f" ORDER BY {m.group(2)}::text)"
            if m.group(1) == m.group(2) else
            f"STRING_AGG(DISTINCT {m.group(1)}::text, ',' ORDER BY {m.group(2)})"
        ),
        translated,
        flags=re.IGNORECASE,
    )
    #   GROUP_CONCAT(expr, 'sep') -> STRING_AGG(expr, 'sep')  (expr는 괄호 미포함)
    translated = re.sub(
        r"GROUP_CONCAT\(\s*([^()]+?)\s*,\s*('(?:[^'\\]|\\.)*')\s*\)",
        r"STRING_AGG(\1, \2)",
        translated,
        flags=re.IGNORECASE,
    )
    #   GROUP_CONCAT(col) -> STRING_AGG(col::text, ',')  (단순 식별자, 구분자 생략형)
    translated = re.sub(
        r"GROUP_CONCAT\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\)",
        r"STRING_AGG(\1::text, ',')",
        translated,
        flags=re.IGNORECASE,
    )
    # 2026-08-14: GROUP_CONCAT(DISTINCT col) — ORDER BY 없는 형태(위 첫 패턴은
    # ORDER BY 필수라 미매치, qa_dart_report_item_mapping.py/hs_trade_lab 다수
    # 사용처에서 "function group_concat(numeric) does not exist"로 실패 중이었음).
    translated = re.sub(
        r"GROUP_CONCAT\(\s*DISTINCT\s+([A-Za-z_][A-Za-z0-9_.]*)\s*\)",
        r"STRING_AGG(DISTINCT \1::text, ',')",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"([A-Za-z_][A-Za-z0-9_.]*)\s+GLOB\s+'\[0-9\]\[0-9\]\[0-9\]\[0-9\]\[0-9\]\[0-9\]'",
        r"\1 ~ '^[0-9]{6}$'",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"([A-Za-z_][A-Za-z0-9_.]*)\s+GLOB\s+'\[0-9\]\*'",
        r"\1 ~ '^[0-9]'",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"SELECT\s+name\s+FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*'table'\s+AND\s+name\s*=\s*'([^']+)'",
        r"SELECT table_name AS name FROM information_schema.tables WHERE table_schema='public' AND table_name='\1'",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"SELECT\s+1\s+FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*'table'\s+AND\s+name\s*=\s*\?",
        "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=?",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"\bFROM\s+sqlite_master\b",
        "FROM ("
        "SELECT table_name AS name, 'table' AS type FROM information_schema.tables "
        "WHERE table_schema='public' UNION ALL "
        "SELECT table_name AS name, 'view' AS type FROM information_schema.views "
        "WHERE table_schema='public' UNION ALL "
        "SELECT indexname AS name, 'index' AS type FROM pg_indexes "
        "WHERE schemaname='public'"
        ") AS sqlite_master",
        translated,
        flags=re.IGNORECASE,
    )
    if insert_or_ignore:
        translated = re.sub(
            r"\bINSERT\s+OR\s+IGNORE\s+INTO\b",
            "INSERT INTO",
            translated,
            flags=re.IGNORECASE,
        )
        stripped = translated.rstrip()
        suffix = ";" if stripped.endswith(";") else ""
        translated = stripped.removesuffix(";") + " ON CONFLICT DO NOTHING" + suffix
    if re.match(r"^\s*(?:INSERT|UPDATE|DELETE)\b", translated, flags=re.IGNORECASE):
        translated = re.sub(
            r"\bCURRENT_TIMESTAMP\b(?!\s*::)",
            "CURRENT_TIMESTAMP::text",
            translated,
            flags=re.IGNORECASE,
        )
    return _replace_qmark_placeholders(translated)


class PostgresCompatCursor:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor
        self._lastrowid: int | None = None

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    @property
    def lastrowid(self) -> int | None:
        # 2026-08-11 추가: sqlite3.Cursor.lastrowid를 흉내내는 호환 속성. Postgres는
        # 이 개념이 없어 INSERT 시 "id" 컬럼을 RETURNING으로 직접 받아와야 함(execute()에서
        # 처리) — collectors/fnguide_financial_collector.py save_snapshot()이 이 속성에
        # 의존하는데 미구현이라 AttributeError로 매일 조용히 실패하고 있던 것을 발견해 추가
        # (financial_source_snapshot 등 "id" 정수 PK를 쓰는 테이블에서만 유효, 없으면 None).
        return self._lastrowid

    def execute(self, sql: str, params: Sequence[Any] | None = None):
        self._lastrowid = None
        values = tuple(params or ())
        if re.search(r"\bINSERT\s+OR\s+REPLACE\s+INTO\b", sql, flags=re.IGNORECASE):
            return self._execute_insert_or_replace(sql, values)
        table_info = _PRAGMA_TABLE_INFO_RE.match(sql)
        if table_info:
            translated = (
                "SELECT ordinal_position - 1 AS cid, column_name AS name, "
                "data_type AS type, CASE WHEN is_nullable='NO' THEN 1 ELSE 0 END AS notnull, "
                "column_default AS dflt_value, 0 AS pk FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position"
            )
            params = (table_info.group(1),)
        elif _PRAGMA_NOOP_RE.match(sql):
            translated = "SELECT NULL AS pragma_result WHERE FALSE"
            params = ()
        else:
            translated = translate_sqlite_sql(sql)
        values = tuple(params or ())
        # 2026-08-11: 순수 INSERT INTO(INSERT OR REPLACE 아닌 일반형)에 "RETURNING id"를
        # 붙여 lastrowid를 흉내낸다. 대상 테이블에 정수 "id" PK가 없으면 Postgres가
        # "column id does not exist"로 실패하므로, 그 경우에만 RETURNING 없이 재시도해
        # 기존 동작(에러 없이 진행, lastrowid=None)을 그대로 보존한다.
        wants_lastrowid = bool(
            re.match(r"\s*INSERT\s+INTO\b", translated, flags=re.IGNORECASE)
            and not re.search(r"\bRETURNING\b", translated, flags=re.IGNORECASE)
        )
        if wants_lastrowid:
            candidate = translated.rstrip().removesuffix(";") + " RETURNING id"
            # 2026-08-12 수정: 실패 시 connection.rollback()으로 트랜잭션 전체를
            # 되돌리면, 같은 커넥션에서 이 INSERT 이전에 실행되고 아직 commit()되지
            # 않은 다른 문장(예: register_run_set()의 backtest_run_sets INSERT 후
            # 이어지는 backtest_run_set_members 루프)까지 통째로 사라진다 —
            # id 컬럼이 없는 테이블에 반복 INSERT할 때마다 이전 변경사항이 조용히
            # 유실되는 버그(register_run_set의 6회 루프 중 마지막 1건만 남던 사례로
            # 발견). SAVEPOINT로 이 probe만 격리해 트랜잭션 전체 롤백을 방지한다.
            try:
                self._cursor.execute("SAVEPOINT lastrowid_probe")
                if values:
                    bound = re.sub(r"%(?!s)", "%%", candidate)
                    self._cursor.execute(bound, values)
                else:
                    self._cursor.execute(candidate)
                row = self._cursor.fetchone()
                if row is not None:
                    self._lastrowid = row[0] if not hasattr(row, "keys") else row["id"]
                self._cursor.execute("RELEASE SAVEPOINT lastrowid_probe")
                return self
            except Exception as exc:
                try:
                    self._cursor.execute("ROLLBACK TO SAVEPOINT lastrowid_probe")
                    self._cursor.execute("RELEASE SAVEPOINT lastrowid_probe")
                except Exception:
                    self._cursor.connection.rollback()
                if "does not exist" not in str(exc):
                    logger.warning(
                        "PostgreSQL compatibility query failed: %s | %s",
                        exc, " ".join(sql.split())[:240],
                    )
                    raise
                # "id" 컬럼이 없는 테이블 — RETURNING 없이 원래 문장으로 재시도.
        try:
            if values:
                translated = re.sub(r"%(?!s)", "%%", translated)
                self._cursor.execute(translated, values)
            else:
                self._cursor.execute(translated)
        except Exception as exc:
            self._cursor.connection.rollback()
            logger.warning("PostgreSQL compatibility query failed: %s | %s", exc, " ".join(sql.split())[:240])
            raise
        return self

    def _execute_insert_or_replace(self, statement: str, values: Sequence[Any]):
        match = re.search(
            r"\bINSERT\s+OR\s+REPLACE\s+INTO\s+([\"A-Za-z_][\"A-Za-z0-9_]*)"
            r"(?:\s*\((.*?)\))?\s+(VALUES|SELECT)\b",
            statement,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            raise ValueError("unsupported INSERT OR REPLACE shape")
        table = match.group(1).strip('"')
        if match.group(2):
            columns = [part.strip().strip('"') for part in match.group(2).split(",")]
        else:
            with self._cursor.connection.cursor() as metadata:
                metadata.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
                    (table,),
                )
                columns = [row[0] for row in metadata.fetchall()]
        with self._cursor.connection.cursor() as metadata:
            metadata.execute(
                "SELECT i.indisprimary, array_agg(a.attname ORDER BY k.ord) "
                "FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid "
                "JOIN pg_namespace n ON n.oid=t.relnamespace "
                "CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,ord) "
                "JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum "
                "WHERE n.nspname='public' AND t.relname=%s AND i.indisunique "
                "GROUP BY i.indexrelid,i.indisprimary "
                "ORDER BY i.indisprimary DESC, cardinality(array_agg(a.attname)) ASC",
                (table,),
            )
            candidates = [list(row[1]) for row in metadata.fetchall()]
        conflict = next(
            (candidate for candidate in candidates if all(key in columns for key in candidate)),
            [],
        )
        translated = re.sub(
            r"\bINSERT\s+OR\s+REPLACE\s+INTO\b",
            "INSERT INTO",
            statement,
            count=1,
            flags=re.IGNORECASE,
        ).rstrip().removesuffix(";")
        if conflict:
            updates = [column for column in columns if column not in conflict]
            quoted_conflict = ", ".join(f'"{key}"' for key in conflict)
            if updates:
                assignments = ", ".join(
                    f'"{column}"=EXCLUDED."{column}"' for column in updates
                )
                translated += f" ON CONFLICT ({quoted_conflict}) DO UPDATE SET {assignments}"
            else:
                translated += f" ON CONFLICT ({quoted_conflict}) DO NOTHING"
        else:
            translated += " ON CONFLICT DO NOTHING"
        translated = translate_sqlite_sql(translated)
        try:
            self._cursor.execute(translated, tuple(values))
        except Exception:
            self._cursor.connection.rollback()
            raise
        return self

    def executemany(self, sql: str, params: Sequence[Sequence[Any]]):
        if re.search(r"\bINSERT\s+OR\s+REPLACE\s+INTO\b", sql, flags=re.IGNORECASE):
            for values in params:
                self.execute(sql, values)
            return self
        try:
            self._cursor.executemany(translate_sqlite_sql(sql), params)
        except Exception:
            self._cursor.connection.rollback()
            raise
        return self

    def _wrap(self, row: Sequence[Any] | None):
        if row is None:
            return None
        columns = [col.name if hasattr(col, "name") else col[0] for col in self._cursor.description]
        return CompatRow(columns, row)

    def fetchone(self):
        return self._wrap(self._cursor.fetchone())

    def fetchall(self):
        rows = self._cursor.fetchall()
        if not rows:
            return []
        columns = [col.name if hasattr(col, "name") else col[0] for col in self._cursor.description]
        return [CompatRow(columns, row) for row in rows]

    def close(self) -> None:
        self._cursor.close()

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row


class PostgresCompatConnection:
    def __init__(self) -> None:
        self._connection = engine.raw_connection()
        self._row_factory = None

    @property
    def row_factory(self):
        return self._row_factory

    @row_factory.setter
    def row_factory(self, value) -> None:
        # CompatRow supports both tuple-style and named access, so callers can
        # retain their sqlite3.Row assignments without changing result shape.
        self._row_factory = value

    def cursor(self) -> PostgresCompatCursor:
        return PostgresCompatCursor(self._connection.cursor())

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> PostgresCompatCursor:
        return self.cursor().execute(sql, params)

    def executemany(self, sql: str, params: Sequence[Sequence[Any]]) -> PostgresCompatCursor:
        return self.cursor().executemany(sql, params)

    def executescript(self, script: str) -> None:
        pending = ""
        for line in script.splitlines(keepends=True):
            pending += line
            if not sqlite3.complete_statement(pending):
                continue
            statement = pending.strip()
            pending = ""
            if not statement:
                continue
            # The cutover already migrated the complete schema and indexes.
            # Legacy startup scripts repeatedly issue SQLite DDL, including
            # reserved identifiers and trigger bodies that are not PostgreSQL
            # syntax. Skip only schema DDL; preserve any data statements.
            if re.match(
                r"^(?:CREATE\s+(?:TABLE|INDEX|UNIQUE\s+INDEX|TRIGGER)|ALTER\s+TABLE)\b",
                statement,
                flags=re.IGNORECASE,
            ):
                continue
            self.execute(statement)
        if pending.strip():
            self.execute(pending)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()


def connect_primary_db(*, timeout: float = 30.0, row_factory=None, readonly: bool = False, wal: bool = False):
    """Connect to the configured primary DB while preserving legacy call semantics."""
    if IS_POSTGRES:
        return PostgresCompatConnection()
    return connect_stock_db(timeout=timeout, row_factory=row_factory, readonly=readonly, wal=wal)


def primary_database_label() -> str:
    return "postgresql" if IS_POSTGRES else DATABASE_URL.split(":", 1)[0]


def _is_primary_sqlite_path(database: Any, *, uri: bool = False) -> bool:
    if not isinstance(database, (str, os.PathLike)):
        return False
    value = os.fspath(database)
    if value == ":memory:":
        return False
    if value.startswith("file:"):
        value = value[5:].split("?", 1)[0]
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    else:
        path = path.resolve()
    return path == Path(__file__).resolve().parent.joinpath("stock.db").resolve()


def install_sqlite_primary_router() -> None:
    """Route process-wide direct opens of the main stock.db to PostgreSQL.

    Only the canonical stock.db path is intercepted. Independent SQLite stores
    keep using the original sqlite3 driver.
    """
    global _SQLITE_ROUTER_INSTALLED
    if _SQLITE_ROUTER_INSTALLED or not IS_POSTGRES:
        return

    def routed_connect(database, *args, **kwargs):
        if _is_primary_sqlite_path(database, uri=bool(kwargs.get("uri"))):
            return PostgresCompatConnection()
        return _ORIGINAL_SQLITE_CONNECT(database, *args, **kwargs)

    sqlite3.connect = routed_connect
    _SQLITE_ROUTER_INSTALLED = True

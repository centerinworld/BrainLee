"""AGI 원문 시드 수집·조회 서비스 (AGI-02 첫 수직 슬라이스).

범위
----
* 시드는 **기존 활성 텔레그램 채널**(`telegram_channels` / `telegram_channel_posts`)이다.
  두 레거시 테이블은 **읽기 전용**이며 이 서비스는 절대 쓰지 않는다(계약 AGI-01 §3 동결 규칙).
* 개인 대화는 **기본 제외**한다(D02 초기안). 텔레그램 공개 채널/슈퍼그룹 id 는 `-100…`
  로 시작하고, 개인 대화/일반 그룹 id 는 그렇지 않다 — 이 규칙으로 `scope` 를 나눈다.
* 첨부 **바이트** 보관·OCR·기업 식별·관심도 집계는 이 슬라이스의 비범위다(AGI-03/04/05).
  따라서 revision 해시는 본문만 대상으로 하고, 선언된 미디어 경로는 `media_refs` 에
  남긴다(해시 미포함 — 바이트 다이제스트가 없으므로 계약 §5-1 을 적용할 수 없다).

무부작용 조회
-------------
`documents_envelope()` 는 SELECT 만 한다(계약 §9-2·T11). 생성·재생성은 별도 인증 작업 API 의
몫이며 여기에 없다.
"""

from __future__ import annotations

import re
import socket
from datetime import datetime
from typing import Any

from agi import revision
from agi.repository import (
    INTERNAL_VISIBILITY,
    PUBLIC_SCOPES,
    PUBLIC_VISIBILITY,
    VISIBILITIES,
    ResearchRepository,
    document_visibility_clauses,
    make_document_id,
    resolve_store_original_body,
    should_create_revision_for_policy,
)
from agi.timeutil import now_utc, parse_legacy_kst, to_kst_text, to_utc

# 텔레그램 공개 채널/슈퍼그룹의 entity id 접두(`-100…`)와 공개 username 접두.
SUPERGROUP_PREFIX = "-100"
USERNAME_PREFIX = "@"

TELEGRAM_SOURCE_KIND = "telegram_channel"
TELEGRAM_SOURCE_OWNER = "owner"

# 텔레그램 공개 username 형식(영문자 시작, 영숫자/밑줄 4~31자).
_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
# 개인 대화(user id)는 양의 정수다. 채널/슈퍼그룹은 `-100…` 로 음수다.
_POSITIVE_INT_RE = re.compile(r"^[1-9][0-9]{0,15}$")

DEFAULT_PAGE_LIMIT = 200


def classify_telegram_scope(channel_id: str | None, entity_hint: str | None = None) -> str:
    """소스가 공개 채널인지 개인 대화인지 **증거 기준**으로 분류한다.

    실측(2026-10-11, `telegram_channels` 15행): `channel_id` 는 텔레그램 숫자 id 가 아니라
    **설정 키(채널 표시명 또는 username)** 이고, 실제 엔티티는 `entity_hint` 에 들어 있다
    (`-100…` = 채널/슈퍼그룹, `@Valuefs1` = 공개 username, NULL = 8행).

    분류(개인 대화 기본 제외의 근거):
      * `entity_hint` 가 `-100…` 또는 `@username` → `public_channel` (증명됨)
      * `channel_id` 가 공개 username 형식 → `public_channel`
        (텔레그램 개인 대화에는 공개 username 이 없다)
      * `entity_hint`/`channel_id` 가 양의 정수 → `private_chat` (사용자 id = 개인 대화)
      * 그 외 → `other` (공개임을 증명할 수 없다 → 기본 **미등록**, 사람 판정 대상)
    """
    hint = "" if entity_hint is None else str(entity_hint).strip()
    key = "" if channel_id is None else str(channel_id).strip()

    for candidate in (hint, key):
        if not candidate:
            continue
        if candidate.startswith(SUPERGROUP_PREFIX):
            return "public_channel"
        if candidate.startswith(USERNAME_PREFIX) and _USERNAME_RE.match(candidate[1:]):
            return "public_channel"
    for candidate in (hint, key):
        if candidate and _POSITIVE_INT_RE.match(candidate):
            return "private_chat"
    for candidate in (hint, key):
        if candidate and _USERNAME_RE.match(candidate):
            return "public_channel"
    return "other"


# 원문 텍스트를 그대로 보관한 단일 출처 상태 — 승격 불가 등급(계약 §8 G4).
DEFAULT_REVISION_QUALITY = "single_source"

# ⚠️ 이 슬라이스의 revision_hash 범위는 **본문 전용**이다. 첨부 바이트를 보관하지 않으므로
#    (계약 §5-1 의 `attachment_digest = sha256(바이트 원문)` 을 만들 수 없다) 미디어 경로는
#    `media_refs` 선언 참조로만 남고 해시에 들어가지 않는다 → **미디어만 바뀐 글은 새 revision 이
#    되지 않는다.** 첨부 바이트 해시는 AGI-04 범위다(Planner 확정 2026-10-11).
HASH_SCOPE = "body_only"
HASH_SCOPE_NOTE = (
    "revision_hash 는 본문 전용(첨부 0건)이다. 첨부 바이트를 아직 보관하지 않아 "
    "attachment_digest(sha256(바이트 원문))를 만들 수 없으므로, 텔레그램 글의 미디어 경로는 "
    "media_refs 에 선언 참조로만 남고 해시에 포함되지 않는다. 따라서 미디어만 수정된 글은 "
    "새 revision 으로 기록되지 않는다 — 첨부 바이트 해시 경로는 AGI-04 범위다."
)

PUBLIC_NOTE = (
    "공개 경로는 is_private=false 이고, 소스 scope 가 allowlist(public_channel/blog/report)에 "
    "있고, publish_summary != 'denied' 인 근거만 반환한다(계약 §9-4). scope 가 allowlist 밖인 "
    "소스(private_chat=개인 대화, other=공개 미증명 미분류)는 is_private=false + "
    "publish_summary='allowed' 조합이어도 공개되지 않는다(fail-closed). 등록 소스의 "
    "publish_summary 기본값은 'denied' 이므로, 소스별 공개 권한을 올리기 전에는 0건이 정상이다. "
    "비공개·미승인 원문은 관리자 인증 경로(/api/agi/admin/documents)에서만 조회한다."
)


def _split_canonical(value: str | None) -> list[str]:
    if not value:
        return []
    return [entry for entry in str(value).split(revision.ENTRY_JOIN) if entry]


def _media_refs(*values: str | None) -> str:
    refs = sorted({str(value).strip() for value in values if value and str(value).strip()})
    return "\n".join(refs)


class ResearchIngestionService:
    """텔레그램 채널 → AGI 원문 보관 테이블 시드 수집기."""

    def __init__(self, *, schema: str | None = None) -> None:
        self.schema = schema

    # ------------------------------------------------------------------ 유틸
    def _repo(self, conn) -> ResearchRepository:
        return ResearchRepository(conn, schema=self.schema)

    # ------------------------------------------------------------------ 소스 시드
    def seed_sources_from_telegram(self, conn, *, include_private: bool = False) -> dict:
        """기존 활성 텔레그램 채널을 `research_sources` 로 등록한다.

        레거시 테이블은 SELECT 만 한다. `scope` 가 `public_channel` 인 소스만 등록한다
        (개인 대화·미분류는 기본 제외 — fail-closed).
        """
        repo = self._repo(conn)
        channels = conn.execute(
            """
SELECT channel_id, channel_name, entity_hint
  FROM telegram_channels
 WHERE channel_id IS NOT NULL
   AND COALESCE(is_active, 0) = 1
 ORDER BY channel_id
"""
        ).fetchall()

        registered: list[dict] = []
        excluded_private: list[dict] = []
        excluded_unclassified: list[dict] = []
        for channel in channels:
            channel_id = str(channel["channel_id"])
            entity_hint = channel["entity_hint"]
            scope = classify_telegram_scope(channel_id, entity_hint)
            item = {
                "channel_id": channel_id,
                "channel_name": channel["channel_name"],
                "entity_hint": entity_hint,
                "scope": scope,
            }
            if scope == "other" or (scope == "private_chat" and not include_private):
                bucket = excluded_private if scope == "private_chat" else excluded_unclassified
                bucket.append(item)
                continue
            source_id = repo.ensure_source(
                source_kind=TELEGRAM_SOURCE_KIND,
                source_owner=TELEGRAM_SOURCE_OWNER,
                source_native_id=channel_id,
                display_name=channel["channel_name"],
                scope=scope,
                coverage="denominator_unknown",
            )
            item["source_id"] = source_id
            registered.append(item)
        return {
            "registered": registered,
            "excluded_private": excluded_private,
            "excluded_unclassified": excluded_unclassified,
            "registered_count": len(registered),
            "excluded_private_count": len(excluded_private),
            "excluded_unclassified_count": len(excluded_unclassified),
            "total_active": len(channels),
        }

    # ------------------------------------------------------------------ 수집
    def ingest_source(
        self,
        conn,
        source_id: str,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        run_kind: str = "seed",
        duplicate_of_run_id: int | None = None,
        host: str | None = None,
    ) -> dict:
        """한 소스의 미처리 글을 오래된 순으로 수집한다(중단재개 가능).

        커서는 처리한 `message_id` 다. 같은 커서로 다시 부르면 **엔티티 추가 0** 이고
        실행 원장만 1행 늘어난다(계약 §5-2).
        """
        repo = self._repo(conn)
        source = repo.get_source(source_id)
        if source is None:
            raise KeyError(f"unknown source_id: {source_id}")
        channel_id = source["source_native_id"]
        is_private = source["scope"] == "private_chat"

        after = int(cursor) if cursor not in (None, "") else None
        sql = """
SELECT message_id, msg_date, text, photo_path, file_name, collected_at
  FROM telegram_channel_posts
 WHERE channel_id = %s
"""
        params: list[Any] = [channel_id]
        if after is not None:
            sql += "   AND message_id > %s\n"
            params.append(after)
        sql += " ORDER BY message_id ASC"
        if limit is not None:
            sql += " LIMIT %s"
            params.append(int(limit))

        rows = conn.execute(sql, tuple(params)).fetchall()

        run_id = repo.begin_run(
            source_id=source_id,
            run_kind=run_kind,
            cursor_start=cursor,
            host=host or socket.gethostname(),
            run_params=f"limit={limit};legacy_tables_read_only=true",
            duplicate_of_run_id=duplicate_of_run_id,
        )

        stored = 0
        duplicates = 0
        policy_skipped = 0
        last_message_id = after
        try:
            for row in rows:
                outcome = self._store_post(
                    repo, source_id, channel_id, is_private, row, run_id, source["store_original"]
                )
                if outcome == "stored":
                    stored += 1
                elif outcome == "policy_skipped":
                    policy_skipped += 1
                else:
                    duplicates += 1
                last_message_id = int(row["message_id"])
        except Exception as exc:
            # 행 단위 실패를 숨기지 않는다 — 원장에 failed 로 남기고 재전파한다.
            # 커서는 성공 시에만 전진하므로 재개는 같은 커서에서 안전하게 다시 시작한다.
            repo.finish_run(
                run_id,
                status="failed",
                cursor_end=None if last_message_id is None else str(last_message_id),
                listed_count=len(rows),
                stored_count=stored,
                skipped_duplicate=duplicates,
                skipped_no_store_policy=policy_skipped,
                skipped_error=1,
                error=str(exc),
            )
            conn.commit()
            raise

        cursor_end = None if last_message_id is None else str(last_message_id)
        repo.finish_run(
            run_id,
            status="success",
            cursor_end=cursor_end,
            listed_count=len(rows),
            stored_count=stored,
            skipped_duplicate=duplicates,
            skipped_no_store_policy=policy_skipped,
        )
        if cursor_end is not None:
            repo.increment_source_progress(
                source_id,
                listed=len(rows),
                stored=stored,
                skipped_duplicate=duplicates,
                skipped_no_store_policy=policy_skipped,
                page_cursor=cursor_end,
                last_collected_at=now_utc(),
                last_success_at=now_utc(),
            )
        return {
            "source_id": source_id,
            "run_id": run_id,
            "run_kind": run_kind,
            "listed_count": len(rows),
            "stored_count": stored,
            "skipped_duplicate": duplicates,
            "skipped_no_store_policy": policy_skipped,
            "skipped_error": 0,
            "cursor_start": cursor,
            "cursor_end": cursor_end,
            "exhausted": limit is None or len(rows) < int(limit),
        }

    def ingest_specific_messages(
        self,
        conn,
        source_id: str,
        message_ids,
        *,
        run_kind: str = "verify",
        host: str | None = None,
        duplicate_of_run_id: int | None = None,
    ) -> dict:
        """**지정한 message_id만** 수집한다 — 채널 전체 백필이 아니다(2026-10-13 사용자 지시).

        `run_kind` 기본값은 `'verify'`다(`backfill`/`seed` 는 순차 커서 수집에 쓰는 이름이라
        부정확하다 — 특정 글을 다시 확인한다는 의미로 기존 enum 값을 그대로 쓴다).
        순차 커서 개념이 없으므로 `source.page_cursor`/`last_success_at` 은 건드리지 않는다 —
        이후 `backfill` 이 이어받을 지점을 이 메서드가 어긋나게 하면 안 된다.
        """
        repo = self._repo(conn)
        source = repo.get_source(source_id)
        if source is None:
            raise KeyError(f"unknown source_id: {source_id}")
        channel_id = source["source_native_id"]
        is_private = source["scope"] == "private_chat"
        ids = sorted({int(m) for m in message_ids})
        if not ids:
            raise ValueError("ingest_specific_messages requires at least one message_id")

        placeholders = ",".join("%s" for _ in ids)
        rows = conn.execute(
            f"SELECT message_id, msg_date, text, photo_path, file_name, collected_at "
            f"FROM telegram_channel_posts WHERE channel_id = %s AND message_id IN ({placeholders}) "
            f"ORDER BY message_id ASC",
            tuple([channel_id, *ids]),
        ).fetchall()
        found_ids = {int(row["message_id"]) for row in rows}
        missing_ids = [i for i in ids if i not in found_ids]

        run_id = repo.begin_run(
            source_id=source_id,
            run_kind=run_kind,
            host=host or socket.gethostname(),
            run_params=f"selective_message_ids={len(ids)};legacy_tables_read_only=true",
            duplicate_of_run_id=duplicate_of_run_id,
        )
        stored = 0
        duplicates = 0
        policy_skipped = 0
        try:
            for row in rows:
                outcome = self._store_post(
                    repo, source_id, channel_id, is_private, row, run_id, source["store_original"]
                )
                if outcome == "stored":
                    stored += 1
                elif outcome == "policy_skipped":
                    policy_skipped += 1
                else:
                    duplicates += 1
        except Exception as exc:  # 행 단위 실패를 숨기지 않는다
            repo.finish_run(
                run_id,
                status="failed",
                listed_count=len(rows),
                stored_count=stored,
                skipped_duplicate=duplicates,
                skipped_no_store_policy=policy_skipped,
                skipped_error=1,
                error=str(exc),
            )
            conn.commit()
            raise

        repo.finish_run(
            run_id,
            status="success",
            listed_count=len(rows),
            stored_count=stored,
            skipped_duplicate=duplicates,
            skipped_no_store_policy=policy_skipped,
        )
        repo.increment_source_progress(
            source_id,
            listed=len(rows),
            stored=stored,
            skipped_duplicate=duplicates,
            skipped_no_store_policy=policy_skipped,
            last_collected_at=now_utc(),
        )
        return {
            "source_id": source_id,
            "run_id": run_id,
            "run_kind": run_kind,
            "requested_count": len(ids),
            "found_count": len(rows),
            "missing_ids": missing_ids,
            "listed_count": len(rows),
            "stored_count": stored,
            "skipped_duplicate": duplicates,
            "skipped_no_store_policy": policy_skipped,
        }

    def _store_post(
        self, repo, source_id: str, channel_id: str, is_private: bool, row, run_id: int, store_original: str
    ) -> str:
        """글 1건 저장. 반환값은 `"stored"`/`"duplicate"`/`"policy_skipped"` 중 하나다.

        `store_original`(계약 §7 권한 3분리)은 소스별로 한 번 읽은 값을 그대로 받는다
        (2026-10-13 외부 검토 R1 — 이전엔 이 검사 없이 본문을 항상 그대로 저장했다).

        2026-10-14 외부 검토 반영: 이전엔 bool 하나로만 돌려줘서 "정책상 안 읽었다"와
        "읽어보니 콘텐츠가 똑같았다"가 호출부에서 똑같이 `skipped_duplicate` 로 집계됐다
        (실제 중복률이 부풀려짐). 세 상태를 명시적으로 구분해 호출부가 각각 다른 카운터에
        쌓을 수 있게 한다(`skipped_no_store_policy` migration 0004).
        """
        external_id = str(row["message_id"])
        document_id = make_document_id(source_id, external_id)

        published_at = parse_legacy_kst(row["msg_date"])
        published_at_source = "source_meta"
        first_seen_at = parse_legacy_kst(row["collected_at"]) or now_utc()
        if published_at is None:
            # 계약 §4: 발표시각 미확보 → 수집시각을 **하한으로 대체**하고 그 사실을 표기한다.
            published_at = first_seen_at
            published_at_source = "collected_at_lower_bound"

        repo.store_document(
            source_id=source_id,
            external_id=external_id,
            canonical_uri=f"tg://channel/{channel_id}/{external_id}",
            published_at=published_at,
            published_at_source=published_at_source,
            first_seen_at=first_seen_at,
            is_private=is_private,
            media_refs=_media_refs(row["photo_path"], row["file_name"]),
            first_run_id=run_id,
        )

        if not should_create_revision_for_policy(store_original):
            # R1: store_original='no' — 변경 이력·본문 모두 남기지 않는다(문서 존재 사실만).
            return "policy_skipped"

        body = row["text"] or ""
        revision_hash, text_digest, entries = revision.compute_revision_hash(body)
        previous = repo.current_revision(document_id)
        previous_state = (
            (previous["text_digest"], _split_canonical(previous["attachments_canonical"]))
            if previous is not None
            else None
        )
        change_kind = revision.classify_change(previous_state, (text_digest, entries))
        stored_body = resolve_store_original_body(store_original, body)

        _, created = repo.store_revision(
            document_id=document_id,
            revision_hash=revision_hash,
            text_digest=text_digest,
            change_kind=change_kind,
            collected_at=first_seen_at,
            body_text=stored_body,
            body_uri=(f"tg://channel/{channel_id}/{external_id}" if stored_body is not None else None),
            attachments_canonical=revision.canonical_attachment_string(entries),
            attachment_count=len(entries),
            # 원문 텍스트를 그대로 보관한 단일 출처 상태 — 승격 불가 등급(계약 §8 G4).
            quality=DEFAULT_REVISION_QUALITY,
            pit_eligible=published_at is not None,
            gate_failures=None,  # 해시 범위가 본문 전용임은 응답의 revision_hash_scope 가 알린다
            run_id=run_id,
        )
        return "stored" if created else "duplicate"

    def backfill(
        self,
        conn,
        source_id: str,
        *,
        batch_size: int = DEFAULT_PAGE_LIMIT,
        max_batches: int = 1,
    ) -> dict:
        """커서를 이어 받아 반복 수집한다(중단재개). 각 배치가 새 run 행이다."""
        repo = self._repo(conn)
        source = repo.get_source(source_id)
        if source is None:
            raise KeyError(f"unknown source_id: {source_id}")
        cursor = source.get("page_cursor")
        batches: list[dict] = []
        for _ in range(max(1, int(max_batches))):
            result = self.ingest_source(
                conn, source_id, limit=batch_size, cursor=cursor, run_kind="backfill"
            )
            batches.append(result)
            cursor = result["cursor_end"]
            if result["exhausted"] or cursor is None:
                break
        return {
            "source_id": source_id,
            "batches": batches,
            "listed_count": sum(batch["listed_count"] for batch in batches),
            "stored_count": sum(batch["stored_count"] for batch in batches),
            "skipped_duplicate": sum(batch["skipped_duplicate"] for batch in batches),
            "skipped_no_store_policy": sum(batch["skipped_no_store_policy"] for batch in batches),
            "final_cursor": cursor,
        }

    # ------------------------------------------------------------------ 조회
    def documents_envelope(
        self,
        conn,
        *,
        source_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        visibility: str = PUBLIC_VISIBILITY,
    ) -> dict:
        """`/api/agi/documents` 응답 봉투 — SELECT 만 한다(무부작용).

        `visibility` 는 **서버 내부에서만** 정해진다(외부 쿼리 파라미터로 받지 않는다).
        공개 경로는 개인 대화·`publish_summary='denied'` 근거를 SQL 에서 배제한다.
        """
        if visibility not in VISIBILITIES:
            raise ValueError(f"invalid visibility: {visibility!r}")
        repo = self._repo(conn)
        if not self._tables_present(conn):
            envelope = {
                "applied": False,
                "visibility": visibility,
                "items": [],
                "as_of": None,
                "snapshot_generation_time": now_utc().isoformat(),
                "source_watermarks": [],
                "quality_summary": {},
                "unconfirmed_fields": [],
                "coverage_summary": {},
                "revision_hash_scope": HASH_SCOPE,
                "revision_hash_scope_note": HASH_SCOPE_NOTE,
                "note": (
                    "AGI 신설 테이블이 아직 적용되지 않았다 "
                    "(migration 0001_research_core 미적용 — live apply 는 사람 승인 단위)."
                ),
            }
            if visibility == PUBLIC_VISIBILITY:
                envelope["public_scopes"] = list(PUBLIC_SCOPES)
            return envelope

        rows = repo.list_documents(
            source_id=source_id, limit=limit, offset=offset, visibility=visibility
        )
        items = [self._serialize_document(row) for row in rows]
        watermarks = repo.source_watermarks(visibility=visibility)

        quality_summary: dict[str, int] = {}
        coverage_summary: dict[str, int] = {}
        for item in items:
            quality = item.get("revision_quality") or "unknown"
            quality_summary[quality] = quality_summary.get(quality, 0) + 1
        for mark in watermarks:
            coverage = mark.get("coverage") or "denominator_unknown"
            coverage_summary[coverage] = coverage_summary.get(coverage, 0) + 1

        unconfirmed = sorted(
            {
                f"published_at_source:{item['published_at_source']}"
                for item in items
                if item.get("published_at_source") != "source_meta"
            }
            | {
                f"revision_quality:{item['revision_quality']}"
                for item in items
                if item.get("revision_quality") not in (None, "verified", "cross_checked")
            }
            | {
                f"coverage:{mark['coverage']}"
                for mark in watermarks
                if mark.get("coverage") != "known"
            }
        )

        as_of = max(
            (item["published_at"] for item in items if item.get("published_at")),
            default=None,
        )
        envelope = {
            "applied": True,
            "visibility": visibility,
            "items": items,
            "count": len(items),
            "limit": max(1, min(int(limit), 500)),
            "offset": max(0, int(offset)),
            "as_of": as_of,
            "snapshot_generation_time": now_utc().isoformat(),
            "source_watermarks": [self._serialize_watermark(mark) for mark in watermarks],
            "quality_summary": quality_summary,
            "unconfirmed_fields": unconfirmed,
            "coverage_summary": coverage_summary,
            "revision_hash_scope": HASH_SCOPE,
            "revision_hash_scope_note": HASH_SCOPE_NOTE,
        }
        if visibility == PUBLIC_VISIBILITY:
            envelope["note"] = PUBLIC_NOTE
            # 노출 경계를 응답에서 직접 확인할 수 있게 allowlist 원문을 싣는다(계약 §9-4).
            envelope["public_scopes"] = list(PUBLIC_SCOPES)
            # AGI-03: 조건 **전체**를 싣는다 — 로드된 서버가 어떤 필터를 쓰는지 HTTP 만으로
            # 판정할 수 있어야 한다(데이터 0행이면 조건의 존재를 다른 방법으로 확인할 수 없다).
            envelope["public_document_conditions"] = document_visibility_clauses(PUBLIC_VISIBILITY)
        return envelope

    def document_envelope(
        self, conn, document_id: str, *, visibility: str = PUBLIC_VISIBILITY
    ) -> dict | None:
        if visibility not in VISIBILITIES:
            raise ValueError(f"invalid visibility: {visibility!r}")
        repo = self._repo(conn)
        if not self._tables_present(conn):
            return None
        row = repo.get_document(document_id, visibility=visibility)
        if row is None:
            return None
        revisions = repo.list_revisions(document_id)
        return {
            "item": self._serialize_document(row),
            "revisions": [self._serialize_revision(rev) for rev in revisions],
            "visibility": visibility,
            "revision_hash_scope": HASH_SCOPE,
            "revision_hash_scope_note": HASH_SCOPE_NOTE,
            "snapshot_generation_time": now_utc().isoformat(),
        }

    def _tables_present(self, conn) -> bool:
        schema_name = self.schema or "public"
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
            (schema_name, "research_documents"),
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------ 직렬화
    @staticmethod
    def _iso(value) -> str | None:
        parsed = to_utc(value) if isinstance(value, datetime) else None
        return parsed.isoformat() if parsed is not None else None

    def _serialize_document(self, row) -> dict:
        return {
            "document_id": row["document_id"],
            "source_id": row["source_id"],
            "source_kind": row["source_kind"],
            "source_native_id": row["source_native_id"],
            "source_display_name": row["display_name"],
            "external_id": row["external_id"],
            "canonical_uri": row["canonical_uri"],
            "title": row["title"],
            "language": row["language"],
            "published_at": self._iso(row["published_at"]),
            "published_at_kst": to_kst_text(row["published_at"]),
            "published_at_source": row["published_at_source"],
            "first_seen_at": self._iso(row["first_seen_at"]),
            "stored_at": self._iso(row["stored_at"]),
            "access_state": row["access_state"],
            "is_private": bool(row["is_private"]),
            "media_refs": row["media_refs"],
            "current_revision_no": row["current_revision_no"],
            "current_revision_hash": row["current_revision_hash"],
            "revision_quality": row["revision_quality"],
            "pit_eligible": row["pit_eligible"],
            "change_kind": row["change_kind"],
            "attachment_count": row["attachment_count"],
            "revision_collected_at": self._iso(row["revision_collected_at"]),
            "store_original": row["store_original"],
            "external_model_send": row["external_model_send"],
            "publish_summary": row["publish_summary"],
            # 노출 allowlist 판정에 쓰인 소스 등급을 응답에 싣는다(공개 응답은 항상
            # PUBLIC_SCOPES 안의 값만 갖는다 — 소비자가 응답만으로 경계를 확인할 수 있다).
            "source_scope": row["source_scope"],
            "coverage": row["coverage"],
        }

    def _serialize_revision(self, row) -> dict:
        return {
            "revision_no": row["revision_no"],
            "revision_hash": row["revision_hash"],
            "text_digest": row["text_digest"],
            "change_kind": row["change_kind"],
            "quality": row["quality"],
            "pit_eligible": row["pit_eligible"],
            "attachment_count": row["attachment_count"],
            "collected_at": self._iso(row["collected_at"]),
            "body_uri": row["body_uri"],
        }

    def _serialize_watermark(self, mark: dict) -> dict:
        return {
            **mark,
            "watermark": self._iso(mark.get("watermark")),
        }

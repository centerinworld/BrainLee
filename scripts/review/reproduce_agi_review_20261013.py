"""2026-10-13 AGI review repro (UPDATED after the R1~R4 fixes): synthetic data in a disposable
PostgreSQL schema only. No public-table writes, network collection, service restart or external
sending. Prints the same four checks as the original review script; each should now show the
fixed behaviour (see docs/AGI_REVIEW_20261013.md and the regression tests added in
tests/test_agi_blog_adapter.py::ExternalReview20261013Tests).
Run from runtime: PYTHONPATH=. ./venv/bin/python scripts/review/reproduce_agi_review_20261013.py
"""
from tests.agi_test_support import isolated_schema
from agi.repository import ResearchRepository, make_document_id
from agi.adapters.blog import BlogAdapter, ListingEntry, ListingPage, FetchResult, PostDetail
from agi.adapters.naver_blog_listing import parse_post_list_html
from agi.timeutil import now_utc

def page(ids):
    return ListingPage(entries=tuple(ListingEntry(external_id=str(i), url=f'https://example.org/{i}', published_at='2026-10-10 08:00:00') for i in ids))

def fetch(body, private=False):
    return lambda _: FetchResult(http_status=200, detail=PostDetail(body_text=body, is_private=private))

with isolated_schema() as (conn, schema):
    repo=ResearchRepository(conn, schema=schema)
    a=BlogAdapter(schema=schema)
    sid=a.ensure_source(conn, source_owner='review', blog_key='synthetic')
    # R1 — default store_original stays 'metadata_only'; body must not be persisted now.
    a.ingest_page(conn,sid,page(['1']),fetch('A'))
    original=conn.execute(f'SELECT store_original FROM "{schema}".research_sources WHERE source_id=%s',(sid,)).fetchone()['store_original']
    bodies=conn.execute(f'SELECT count(*) AS n FROM "{schema}".research_document_revisions WHERE body_text IS NOT NULL').fetchone()['n']
    print('R1 store_original=',original,'stored_body_rows=',bodies,'-> fixed' if bodies==0 else '-> STILL BROKEN')

    # R2 — A->B->A must leave the current pointer on A. (store_original raised to 'yes' here
    # ONLY so body_text is observable for this check — R1's default 'metadata_only' would
    # otherwise leave body_text=None for every revision and this check would be meaningless.
    # A fresh external_id ('2') avoids colliding with the 'A' hash R1 already stored above.)
    conn.execute(f'UPDATE "{schema}".research_sources SET store_original=%s WHERE source_id=%s',('yes',sid))
    a.ingest_page(conn,sid,page(['2']),fetch('A'))
    revision_a = repo.current_revision(make_document_id(sid, '2'))
    a.ingest_page(conn,sid,page(['2']),fetch('B'))
    a.ingest_page(conn,sid,page(['2']),fetch('A'))
    cur=repo.current_revision(make_document_id(sid,'2'))
    print('R2 A->B->A current_body=',cur['body_text'],'expected=A',
          '-> fixed' if cur['body_text']=='A' and cur['revision_hash']==revision_a['revision_hash'] else '-> STILL BROKEN')

    # R3 — a detail-level private flag must restrict even a previously-public, publish-approved doc.
    conn.execute(f'UPDATE "{schema}".research_sources SET publish_summary=%s WHERE source_id=%s',('allowed',sid))
    a.ingest_page(conn,sid,page(['1']),fetch('private-body',private=True))
    row=repo.get_document(make_document_id(sid,'1'), visibility='public')
    print('R3 detail.is_private=True public_visible=', row is not None, 'is_private=', row['is_private'] if row else None,
          '-> fixed' if row is None else '-> STILL BROKEN')

    # R4 — a lone last-page call (page_index=2, no prior page in this call) must not sweep.
    a.ingest_page(conn,sid,page(['111111111111']),fetch('older-page'))
    final_page=parse_post_list_html('<a href="/PostView.naver?logNo=222222222222">last page post</a><script>isLastPage=true</script>',blog_id='synthetic', page_index=2)
    result=a.ingest_page(conn,sid,final_page,fetch('last-page'))
    older=repo.get_document(make_document_id(sid,'111111111111'),visibility='internal')
    print('R4 last_page complete=',final_page.complete,'page_index=',final_page.page_index,
          'sweep_performed=',result['tombstone_sweep_performed'],'previous_page_post_state=',older['access_state'],
          '-> fixed' if older['access_state']=='ok' else '-> STILL BROKEN')

    # --- 2026-10-13 2차 외부 검토 추가 재현 ---
    # P1a — an entry-limit-truncated middle page must NOT be accepted as "said there's more".
    sid2 = a.ensure_source(conn, source_owner='review', blog_key='synthetic-2')
    a.ingest_page(conn, sid2, page(['1', '2', '3']), fetch('x'))
    page_1 = ListingPage(entries=(ListingEntry(external_id='1', url='https://example.org/1'),),
                          truncated=True, limitation='entry_limit_reached', page_index=1)
    page_2 = ListingPage(entries=(ListingEntry(external_id='3', url='https://example.org/3'),),
                          complete=True, page_index=2)
    full_result = a.ingest_full_listing(conn, sid2, [page_1, page_2], fetch('x'))
    doc2 = repo.get_document(make_document_id(sid2, '2'), visibility='internal')
    print('P1a entry-limit middle page: sweep_performed=', full_result['tombstone_sweep_performed'],
          'post_2_state=', doc2['access_state'],
          '-> fixed' if not full_result['tombstone_sweep_performed'] and doc2['access_state'] == 'ok' else '-> STILL BROKEN')

    # P1b — store_original='no' must refuse ANY direct store_revision call, even body_text=None.
    sid3 = a.ensure_source(conn, source_owner='review', blog_key='synthetic-3')
    conn.execute(f'UPDATE "{schema}".research_sources SET store_original=%s WHERE source_id=%s', ('no', sid3))
    doc3_id, _ = repo.store_document(source_id=sid3, external_id='1', canonical_uri=None, title=None,
                                     language=None, published_at=None, published_at_source='unknown',
                                     first_seen_at=now_utc())
    bypass_raised = False
    try:
        repo.store_revision(document_id=doc3_id, revision_hash='h', text_digest='t', change_kind='new',
                            collected_at=now_utc(), body_text=None)
    except ValueError:
        bypass_raised = True
    print('P1b store_original=no direct bypass refused=', bypass_raised, '-> fixed' if bypass_raised else '-> STILL BROKEN')

    # P2 — reverting A->B->A must leave an observation trail on revision 1, not just a pointer flip.
    sid4 = a.ensure_source(conn, source_owner='review', blog_key='synthetic-4')
    conn.execute(f'UPDATE "{schema}".research_sources SET store_original=%s WHERE source_id=%s', ('yes', sid4))
    r1 = a.ingest_page(conn, sid4, page(['1']), fetch('A'))
    a.ingest_page(conn, sid4, page(['1']), fetch('B'))
    r3 = a.ingest_page(conn, sid4, page(['1']), fetch('A'))
    rev = repo.current_revision(make_document_id(sid4, '1'))
    print('P2 last_observed_run_id=', rev.get('last_observed_run_id'), 'expected=', r3['run_id'],
          '-> fixed' if rev.get('last_observed_run_id') == r3['run_id'] else '-> STILL BROKEN')

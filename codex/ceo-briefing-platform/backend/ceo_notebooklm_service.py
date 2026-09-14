"""Source-backed CEO insight bundles prepared for NotebookLM infographic creation."""
from __future__ import annotations
from datetime import datetime
import hashlib, json, sqlite3
from pathlib import Path
import re
from urllib.parse import urlparse

DB=Path(__file__).resolve().parents[1]/"data/ceo_briefing.db"
OUT=Path(__file__).resolve().parent/"data/ceo_notebooklm"
REPORTS=Path("/Volumes/Realtek_NVME/stock_dashboard/reports")
# 2026-09-14 소유자 지적("이름만 거창하지 내용은 부실") 보완: 이전엔 단어가 전부 한국어라
# 실제 글로벌 소스(BBC/ECB/Fed/WSJ/OpenAI 등, services/global_intelligence_ingest.py)가
# 들어와도 하나도 못 걸러 올렸다(진짜 글로벌 기사인데도 이 THEMES 키워드에 안 걸려 세
# 섹션 어디에도 안 들어가고 버려졌음). 영어 키워드를 추가해 실제 수집되는 언어를 반영한다.
THEMES={
 "economy":{"title":"세계 경제와 자본 흐름","icon":"🌍","words":(
  "금리","환율","경제","증시","유가","무역","관세","연준","채권","물가","인플레이션",
  "rate","inflation","tariff","gdp","recession","bond","fed","ecb","central bank","market","economy","trade",
 )},
 "world":{"title":"지정학·산업의 주요 변화","icon":"🧭","words":(
  "국방","방산","전쟁","안보","정부","정책","수주","공급망","중국","미국",
  "war","security","geopolit","sanction","supply chain","china","ukraine","tariff",
 )},
 "ai":{"title":"AI·기술 발전","icon":"🧠","words":(
  "AI","LLM","GPU","인공지능","생성형","반도체","로봇","데이터센터",
  "artificial intelligence","openai","anthropic","deepmind","chatgpt","model","semiconductor","data center",
 )},
}
GLOBAL_AUTHORITIES=("imf.org","worldbank.org","oecd.org","bis.org","federalreserve.gov","ecb.europa.eu","openai.com","deepmind.google","anthropic.com","bbc.co.uk","wsj.com")

def _contains(text,word):
 return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])",text,re.I)) if word.isascii() else word in text

def _theme(title, detail=""):
 title_scores={k:sum(2 for w in v["words"] if _contains(title,w)) for k,v in THEMES.items()}
 scores={k:title_scores[k]+sum(1 for w in v["words"] if _contains(detail,w)) for k,v in THEMES.items()}
 winner=max(scores,key=scores.get)
 # The headline must identify the topic; summary-only keyword collisions are rejected.
 return winner if title_scores[winner]>=2 else None

def collect(limit=90,global_limit=60):
 if not DB.exists():return []
 conn=sqlite3.connect(DB);conn.row_factory=sqlite3.Row
 # 2026-09-14 소유자 지적 보완: 상위 90건을 feed_items 전체(대부분 KAI 국내 뉴스 - 실시간
 # 업데이트가 훨씬 잦음)에서 최신순으로만 뽑으면, 갱신 빈도가 낮은 global_intelligence
 # 소스(DeepMind/OpenAI 블로그 등)는 최신순 경쟁에서 밀려 상위 90위 안에 아예 못 든다
 # (실측: economy는 WSJ가 자주 갱신돼 섞였지만 world/ai는 국내 기사로 100% 채워짐).
 # global_intelligence는 별도로 확보해 국내 실시간 뉴스량과 경쟁하지 않게 한다.
 base_query="SELECT id,title,summary,link,source,article_publisher,article_category,coalesce(article_published_at,published_at) AS published_at FROM feed_items WHERE link!=''"
 rows=conn.execute(f"{base_query} ORDER BY coalesce(article_published_at,published_at) DESC LIMIT ?",(limit,)).fetchall()
 global_rows=conn.execute(f"{base_query} AND feed_type='global_intelligence' ORDER BY coalesce(article_published_at,published_at) DESC LIMIT ?",(global_limit,)).fetchall()
 conn.close()
 seen_ids={r["id"] for r in rows}
 rows=list(rows)+[r for r in global_rows if r["id"] not in seen_ids]
 seen=set();items=[]
 for r in rows:
  parsed=urlparse(r["link"])
  if parsed.scheme not in {"http","https"}:continue
  domain=parsed.netloc.replace("www.","");key=(domain,r["title"][:80])
  if key in seen:continue
  seen.add(key);theme=_theme(r["title"],f"{r['summary']} {r['article_category']}")
  if not theme:continue
  items.append({"id":r["id"],"theme":theme,"title":r["title"],"summary":r["summary"][:700],"url":r["link"],"publisher":r["article_publisher"] or domain or r["source"],"published_at":r["published_at"],"favicon":f"https://www.google.com/s2/favicons?domain={domain}&sz=64","is_global":r["article_category"]=="global_intelligence"})
 return items

def collect_reports():
 result={k:[] for k in THEMES}
 if not REPORTS.exists():return result
 for p in sorted(REPORTS.glob("*.pdf"),key=lambda x:x.stat().st_mtime,reverse=True)[:300]:
  title=p.stem.replace("_"," ");theme=_theme(title)
  if not theme:continue
  if len(result[theme])<4:result[theme].append({"title":p.stem.replace("_"," "),"path":str(p),"modified_at":datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(timespec="seconds")})
 return result

def dashboard():
 items=collect();reports=collect_reports();groups=[]
 for key,meta in THEMES.items():
  # 2026-09-14 보완: 같은 키워드로 국내 기사와 글로벌 기사가 둘 다 걸릴 때, 목록에 먼저
  # 나온다는 이유만으로 국내 기사가 6칸을 다 채우고 글로벌 기사가 밀려나지 않도록
  # global_intelligence 출처를 우선 배치한다(안정 정렬이라 그 안의 최신순은 유지됨).
  matched=[x for x in items if x["theme"]==key]
  matched.sort(key=lambda x:not x["is_global"])
  selected=matched[:6]
  groups.append({"key":key,**meta,"items":selected,"reports":reports[key],"source_count":len(selected)+len(reports[key])})
 latest=None;OUT.mkdir(parents=True,exist_ok=True);files=sorted(OUT.glob("sourcebook_*.md"),reverse=True)
 if files:
  p=files[0];latest={"path":str(p),"created_at":datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(timespec="seconds"),"hash":hashlib.sha256(p.read_bytes()).hexdigest(),"status":"SOURCEBOOK_READY"}
 selected_items=[x for g in groups for x in g["items"]];publishers={x["publisher"] for x in selected_items}
 authority_count=sum(1 for x in selected_items if any(urlparse(x["url"]).netloc.endswith(d) for d in GLOBAL_AUTHORITIES))
 news_count=sum(len(g["items"]) for g in groups);report_count=sum(len(g["reports"]) for g in groups)
 complete=all(len(g["items"])>=3 for g in groups) and len(publishers)>=5 and report_count>=3 and authority_count>=2
 coverage={"status":"BALANCED" if complete else "LIMITED_SOURCE_MIX","news_count":news_count,"report_count":report_count,"publisher_count":len(publishers),"global_authority_count":authority_count,"message":"3개 주제의 뉴스·보고서 구성이 확보되었습니다." if complete else "현재 수집원이 방산·국내 기사에 치우쳐 있습니다. 세계 경제기관과 AI 연구기관 원문을 확충한 뒤 최종 브리핑으로 사용하세요."}
 return {"status":"success","generated_at":datetime.now().astimezone().isoformat(timespec="seconds"),"groups":groups,"total_sources":sum(g["source_count"] for g in groups),"coverage":coverage,"latest_bundle":latest,"notebooklm":{"status":"READY_FOR_NOTEBOOKLM" if latest else "SOURCEBOOK_NOT_BUILT","generation":"NotebookLM에서 인포그래픽 생성","automatic_publish":False}}

def prepare_sourcebook():
 data=dashboard();stamp=datetime.now().strftime("%Y%m%d_%H%M%S");lines=["# CEO Global Intelligence — NotebookLM Sourcebook",f"생성: {data['generated_at']}",f"자료 구성 상태: {data['coverage']['status']} — {data['coverage']['message']}","","편집 원칙: 아래 원문 링크를 우선하고, 사실·분석·불확실성을 구분한다. 출처 없는 수치나 전망을 만들지 않는다. NotebookLM 인포그래픽은 핵심 변화, 경영 영향, 관찰 지표, 반대 근거를 한 화면에 표현한다."]
 for g in data["groups"]:
  lines += ["",f"## {g['icon']} {g['title']}"]
  for i,x in enumerate(g["items"],1):lines += [f"\n### {i}. {x['title']}",f"- 발행처: {x['publisher']}",f"- 시각: {x['published_at'] or '미확인'}",f"- 원문: {x['url']}",f"- 수집 요약: {x['summary'] or '요약 없음'}"]
  if g.get("reports"):
   lines.append("\n### 함께 업로드할 전문 보고서 PDF")
   for x in g["reports"]:lines.append(f"- {x['title']}\n  로컬 파일: {x['path']}")
 content="\n".join(lines);OUT.mkdir(parents=True,exist_ok=True);path=OUT/f"sourcebook_{stamp}.md";path.write_text(content)
 return {"status":"success","bundle":{"path":str(path),"hash":hashlib.sha256(content.encode()).hexdigest(),"source_count":data["total_sources"],"content":content},"next_step":"NotebookLM에 이 소스북을 추가하고 Studio의 인포그래픽 생성을 실행하세요."}

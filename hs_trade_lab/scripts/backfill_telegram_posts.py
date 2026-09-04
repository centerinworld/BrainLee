from __future__ import annotations

import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin
from urllib.request import urlopen


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "hs_trade_lab.db"
ROOT_STOCK_DB = ROOT_DIR.parent / "stock.db"
CHANNEL_URL = "https://t.me/s/BeOn_BeClear"
LOOKBACK_DAYS = 370


COMPANY_SPLIT_RE = re.compile(r"[\/,·ㆍ]| 및 | 와 | 과 ")
HTML_TAG_RE = re.compile(r"<[^>]+>")
LINEBREAK_RE = re.compile(r"<br\s*/?>", re.I)


KEYWORD_SECTOR_MAP = [
    ("반도체", "semiconductors"),
    ("메모리", "semiconductors"),
    ("웨이퍼", "semiconductors"),
    ("포토레지스트", "semiconductors"),
    ("언더필", "semiconductors"),
    ("MR-MUF", "semiconductors"),
    ("이온주입", "semiconductors"),
    ("광전지", "batteries"),
    ("배터리", "batteries"),
    ("양극재", "batteries"),
    ("분리막", "batteries"),
    ("리튬", "batteries"),
    ("전구체", "batteries"),
    ("스텐트", "biotech"),
    ("의료", "biotech"),
    ("백신", "biotech"),
    ("지혈", "biotech"),
    ("체성분", "biotech"),
    ("현미경", "biotech"),
    ("인공호흡기", "biotech"),
    ("원료의약품", "biotech"),
    ("바이오 원료", "biotech"),
    ("화장품", "consumer"),
    ("메이크업", "consumer"),
    ("피부세척", "consumer"),
    ("라이신", "consumer"),
    ("건기식", "consumer"),
    ("마스크팩", "consumer"),
    ("미용기기", "consumer"),
    ("라면", "consumer"),
    ("담배", "consumer"),
    ("소주", "consumer"),
    ("아이스크림", "consumer"),
    ("트랙터", "shipbuilding"),
    ("풍력", "shipbuilding"),
    ("건설기계", "shipbuilding"),
    ("엔진", "shipbuilding"),
    ("열교환기", "shipbuilding"),
    ("무인기", "shipbuilding"),
    ("드론", "shipbuilding"),
    ("자동차", "autos"),
    ("타이어", "autos"),
    ("LED", "autos"),
    ("와이어링", "autos"),
    ("액추에이터", "autos"),
    ("로봇 청소기", "consumer"),
    ("CCTV", "consumer"),
    ("정유", "energy_materials"),
    ("석유", "energy_materials"),
    ("천연가스", "energy_materials"),
    ("에폭시", "energy_materials"),
    ("탄소섬유", "energy_materials"),
    ("BPA", "energy_materials"),
    ("동박", "batteries"),
    ("전해액", "batteries"),
    ("Cap Assembly", "batteries"),
    ("변환기", "energy_materials"),
    ("배전반", "energy_materials"),
    ("자동제어반", "energy_materials"),
    ("밸브", "energy_materials"),
    ("피팅", "energy_materials"),
]

PRIORITY_SECTORS = {
    "batteries",
    "shipbuilding",
    "autos",
}


COMPANY_ALIAS_MAP = {
    "메티바이오메드": "메타바이오메드",
    "제이엔케이히터": "제이엔케이글로벌",
    "OCI": "OCI홀딩스",
    "SK하이닉스(나믹스": "SK하이닉스",
    "삼성전자(나가세": "삼성전자",
    "금호석유": "금호석유화학",
    "코오롱인더스트리": "코오롱인더",
    "코오롱플라스틱": "코오롱ENP",
    "빙그레 등": "빙그레",
    "현대로템 등": "현대로템",
    "백광산업 등": "백광산업",
    "우양 등": "우양",
    "샘표식품 등": "샘표식품",
    "레이 등": "레이",
    "한국타이어": "한국타이어앤테크놀로지",
    "넥센타이어 등": "넥센타이어",
    "엘앤에프 등": "엘앤에프",
    "한화에어로스페이스 등": "한화에어로스페이스",
    "LG에너지솔루션 등": "LG에너지솔루션",
    "코스맥스 등": "코스맥스",
    "씨유메디칼 등": "씨유메디칼",
    "켐트로닉스 등": "켐트로닉스",
    "비씨엔씨 등": "비씨엔씨",
    "세방전지 등": "세방전지",
    "LS": "LS ELECTRIC",
    "ELECTRIC": "LS ELECTRIC",
    "녹십자": "GC녹십자",
    "와이씨": "와이아이케이",
    "유니셈 등": "유니셈",
    "GST 등": "GST",
    "GST": "GST",
    "유니셈": "유니셈",
    "대보마그네틱": "대보마그네틱",
    "강원에너지": "강원에너지",
    "브이엠": "브이엠",
    "제우스": "제우스",
    "엘티씨": "엘티씨",
    "싸이맥스": "싸이맥스",
    "로체시스템즈": "로체시스템즈",
    "라온테크": "라온테크",
    "인텍플러스": "인텍플러스",
    "펨트론": "펨트론",
    "고영": "고영",
    "LG디스플레이": "LG디스플레이",
    "삼성디스플레이": "삼성디스플레이",
    "삼성디플레이": "삼성디스플레이",
    "온세미": "온세미",
    "이엔에프테크놀로지": "이엔에프테크놀로지",
    "셀바이오휴먼텍": "셀바이오휴먼텍",
    "바우와우코리아": "오에스피",
    "LS전선": "LS전선",
    "에스테아이": "에스티아이",
    "SEMES": "세메스",
    "지앤비에스 에코": "지앤비에스에코",
}

TITLE_ALIAS_MAP = {
    "바우와우코리아 (오에스피 자회사)": ["개사료"],
    "달바글로벌 : 기초화장품": ["기초화장용 제품류"],
    "LS전선 : 광섬유 케이블": ["광섬유 광케이블 (전국)"],
    "머큐리 케이블(머큐리) : 광섬유": ["광섬유 광케이블 (전국)"],
    "펨트론": ["3차원 검사장비, 모듈"],
    "인텍플러스 : 3차원 검사장비": ["3차원 검사장비, 모듈"],
    "고영 : 3차원 검사장비": ["3차원 검사장비, 모듈"],
    "파크시스템스 : 산업용자동화원자현미경": ["산업용자동화원자현미경"],
    "제룡전기 : 변압기": ["중대형 변압기"],
    "산일전기 : 전압별 변압기": ["중대형 변압기"],
    "LS ELECTRIC : 변환기": ["소형 변압기"],
    "일진전기 : 변환기": ["소형 변압기"],
    "씨큐브 : 진주광택안료": ["진주광택안료"],
    "상신이디피 : 2차전지 원형 각형 등 팩 모듈(Cap assy포함)": ["Cap Assembly"],
    "와이엠텍 / LS이모빌리티솔루션(LS ELECTRIC 자회사) : EV Relays (계전기, 전기제어용, 전압 1,000V 이하)": ["EV Relay 1,000V 이하 (전국)"],
    "와이엠텍 : EV Relays, LS이모빌리티솔루션(LS ELECTRIC 자회사) (계전기, 전기제어용, 전압 1,000V 초과)": ["EV Relay 1,000V 초과 (전국)"],
    "해성디에스 : Package Substrate + 리드프레임": ["리드프레임"],
    "두산 : CCL": ["CCL 동박적층판 (전국_대만)"],
    "엘티씨 : 유기혼합용제와 시너(thinner)": ["유기혼합용제와 시너"],
    "엘티씨 : PR박리액 (반도체 제조용)": ["PR박리액"],
    "에스티아이 : CCSS": ["CCSS"],
    "에스테아이 : CCSS": ["CCSS"],
    "영구자석": ["희토류 영구자석"],
    "수산화리튬 : 미래나노텍 / 강원에너지 / 하이드로리튬 / POSCO홀딩스": ["이차전지 소재 (수산화리튬)"],
    "영구자석 (전국)": ["희토류 영구자석"],
    "임플란트 : (덴티움 + 오스템임플란트 + 디오 + 덴티스 + 메가젠임플란트)": ["임플란트"],
    "에코프로비엠, LG화학 : NCA (니켈 코발트 알루미늄 산화물의 리튬염)": ["NCA 니켈 코발트 알루미늄 산화물의 리튬염 (전국)"],
    "에코프로비엠 : NCA (니켈 코발트 알루미늄 산화물의 리튬염)": ["NCA 니켈 코발트 알루미늄 산화물의 리튬염 (전국)"],
    "덕산네오룩스 : 유기발광다이오드 OLED 제조용": ["OLED 패널"],
    "LG화학 : 유기발광다이오드 OLED 제조용": ["OLED 패널"],
    "LG디스플레이 : 유기발광다이오드 OLED 제조용": ["OLED 패널"],
    "하이즈항공 : 비행기의 부분품 (부산 강서구)": ["비행기의 부분품"],
    "하이즈항공 : 비행기의 부분품 (경남 진주시)": ["비행기의 부분품"],
    "하이즈항공 : 비행기의 부분품 (부산 강서구 + 경남 진주시)": ["비행기의 부분품"],
    "아이디피 : 카드프린터 (서울 구로구)": ["카드프린터 / 모바일프린터"],
    "아이디피 : 카드프린터 + 소모품 (서울 구로구)": ["카드프린터 / 모바일프린터"],
    "빅솔론 : 모바일프린터 (경기 성남시)": ["카드프린터 / 모바일프린터"],
    "양극활물질": ["NCM계 양극재"],
    "관련주 : 휴젤 / 메디톡스 / 대웅제약 / 휴메딕스 / 바이오플러스 / 파마리서치 등": ["보툴리눔 톡신"],
    "텔레비전 카메라 CCTV": ["산업/의료 영상 카메라"],
    "팅크웨어 : 블랙박스 (경기 성남시)": ["블랙박스"],
    "팅크웨어 : 블랙박스 (경기 성남시_글로벌)": ["블랙박스"],
    "삼양식품 : 기타 인스탄트 면류 (강원 원주시)": ["기타 인스탄트 면류"],
    "삼양식품 : 기타 인스탄트 면류 (경남 밀양시)": ["기타 인스탄트 면류"],
    "삼양식품 : 기타 인스탄트 면류 (서울 성북구)": ["기타 인스탄트 면류"],
    "삼양식품 : 기타 인스탄트 면류 (전북 익산시)": ["기타 인스탄트 면류"],
    "오에스피 : 고양이사료 (충남 논산시)": ["고양이사료"],
    "오에스피 : 고양이사료 (충남 논산시_베트남+홍콩+대만)": ["고양이사료"],
    "롯데웰푸드 : 비스킷, 쿠키, 크래커 (서울 영등포구)": ["비스킷, 쿠키, 크래커"],
    "롯데웰푸드 : 비스킷, 쿠키, 크래커 (서울 영등포구_글로벌)": ["비스킷, 쿠키, 크래커"],
    "동방메디컬 : 기타 관모양 금속 바늘, 봉합용 바늘 (내과용·외과용·치과용·수의과용) (충남 보령시)": ["기타 관모양 금속 바늘, 봉합용 바늘"],
    "동방메디컬 : 기타 캐눌러 (내과용·외과용·치과용·수의과용) (충남 보령시)": ["기타 캐눌러"],
    "코메론 : 줄자 STEEL LONG TAPE 등 (부산 사하구)": ["줄자 STEEL LONG TAPE"],
    "아이디피 : 소모품 (서울 구로구)": ["카드프린터 / 모바일프린터"],
    "프로티아 : 진단장비 (서울 강서구)": ["면역진단기기"],
    "프로티아 : 전체 진단시약 (서울 강서구)": ["기타 진단용 시약, 진단키트"],
    "파마리서치 : 기초화장용 제품류 (강원 강릉시)": ["기초화장용 제품류"],
    "블랭크마스크 (평판디스플레이용 + 반도체 제조용) (전국)": ["블랭크 마스크"],
    "블랭크마스크 (평판디스플레이용 + 반도체 제조용) (전국_글로벌)": ["블랭크 마스크"],
    "블랭크마스크 (평판디스플레이용) (전국)": ["블랭크 마스크"],
    "블랭크마스크 (평판디스플레이용) (전국_중국+대만)": ["블랭크 마스크"],
    "블랭크마스크 (반도체 제조용) (전국)": ["블랭크 마스크"],
    "블랭크마스크 (반도체 제조용) (전국_중국+대만)": ["블랭크 마스크"],
    "플러그 소켓(동축케이블·인쇄회로용의 것) 리노소켓 (전국)": ["IC 테스트 소켓"],
    "TC BONDER (전국)": ["반도체 디바이스/전자집적회로 조립·검사용 기기"],
    "TC BONDER (전국_글로벌)": ["반도체 디바이스/전자집적회로 조립·검사용 기기"],
    "급속열처리장비(Rapid Thermal Processing, RTP) (전국)": ["반도체 보울 또는 웨이퍼 제조용 기기"],
    "알루미늄 파우치 필름 (전국)": ["알루미늄 파우치 필름 (전국)"],
    "알루미늄 파우치 필름 (전국_글로벌)": ["알루미늄 파우치 필름 (전국_글로벌)"],
    "알루미늄 파우치 필름 (전국_폴란드)": ["알루미늄 파우치 필름 (전국_폴란드)"],
    "광섬유 광케이블 (전국)": ["광섬유 광케이블 (전국)"],
    "광섬유 광케이블 (전국_글로벌)": ["광섬유 광케이블 (전국_글로벌)"],
    "아이디스 : 텔레비전 카메라 CCTV (대전 유성구) (미국 한정)": ["텔레비전 카메라 CCTV (대전 유성구) (미국 한정)"],
    "비엠티 : 피팅 (경남 양산) -> (부산 기장군 이전)": ["피팅 (전국)"],
    "비엠티 : 밸브 (경남 양산시 -> 부산 기장군 이전)": ["밸브 (전국)"],
    "비엠티 : 피팅+밸브 (부산 기장군)": ["피팅 (전국)", "밸브 (전국)"],
    "롯데정밀화학 : ECH (울산 남구)": ["ECH (울산 남구)"],
    "12인치 레이저마커 / 레이저 그루빙 (전국)": ["12인치 레이저마커 / 레이저 그루빙 (전국)"],
    "한국타이어 : 승용차용 + 버스·화물차용 (경기 성남시 + 대전 유성구)": ["고무타이어 및 타이어튜브(래디얼 구조의 것)"],
    "넥센타이어 : 고무제 공기타이어 (신품, 승용차용.스테이션왜건.경주자동차용) + (신품, 버스용·화물차용) (경남 양산시)": ["고무타이어 및 타이어튜브(래디얼 구조의 것)"],
    "금호타이어 : 승용차용 + 버스·화물차용 (광주 광산구)": ["고무타이어 및 타이어튜브(래디얼 구조의 것)"],
    "오스템임플란트 : 임플란트 (서울 금천구)": ["임플란트"],
    "오스템임플란트 : 임플란트 (서울 강서구)": ["임플란트"],
    "오스템임플란트 : 임플란트 (서울 강서구_중국+러시아+튀르키예+인도+네덜란드)": ["임플란트"],
    "메가젠임플란트 : 임플란트 (대구 달성군)": ["임플란트"],
    "메가젠임플란트 : 임플란트 (대구 달성군_네덜란드+미국+중국+러시아+튀르키예+우크라이나)": ["임플란트"],
    "디오 : 임플란트 (부산 해운대구)": ["임플란트"],
    "디오 : 임플란트 (부산 해운대구_중국+아랍에미리트+튀르키예+포루투갈)": ["임플란트"],
    "덴티스 : 임플란트 (대구 달서구)": ["임플란트"],
    "덴티스 : 임플란트 (대구 달서구_중국+벨기에+이란)": ["임플란트"],
    "덴티움 : 임플란트 (경기도 수원시)": ["임플란트"],
    "덴티움 : 임플란트 (경기도 수원시_중국+러시아+루마니아+카자흐스탄+인도)": ["임플란트"],
    "한텍 : 기타 열교환기 (가정용 제외) (울산 울주군)": ["기타 열교환기 (가정용 제외)"],
    "한텍 : 기타 열교환기 (가정용 제외) (울산 울주군_글로벌)": ["기타 열교환기 (가정용 제외)"],
    "제이시스메디칼 : 미용목적 의료기기 부분품 (서울 금천구)": ["미용목적 의료기기 부분품(기타 내과, 외과, 치과, 수의과, 안과용 기기)"],
    "제이시스메디칼 : 피부과용 기기 (레이저 작동식) + 피부과용 기기 (레이저 작동식 제외) + 미용목적 의료기기 부분품 (서울 금천구)": ["피부과용 기기 (레이저 작동식)", "피부과용 기기(레이저 작동식 제외)", "미용목적 의료기기 부분품(기타 내과, 외과, 치과, 수의과, 안과용 기기)"],
    "비올 : 피부과용 기기 (레이저 작동식 제외) (경기 성남시)": ["피부과용 기기(레이저 작동식 제외)"],
    "이루다 : 미용목적 의료기기 부분품 (경기 안양시)": ["미용목적 의료기기 부분품(기타 내과, 외과, 치과, 수의과, 안과용 기기)"],
    "이루다 : 피부과용 기기 (레이저 작동식) + 피부과용 기기 (레이저 작동식 제외) + 미용목적 의료기기 부분품 (경기 안양시)": ["피부과용 기기 (레이저 작동식)", "피부과용 기기(레이저 작동식 제외)", "미용목적 의료기기 부분품(기타 내과, 외과, 치과, 수의과, 안과용 기기)"],
    "루트로닉 : 미용목적 의료기기 부분품 (경기 고양시)": ["미용목적 의료기기 부분품(기타 내과, 외과, 치과, 수의과, 안과용 기기)"],
    "루트로닉 : 피부과용 기기 (레이저 작동식) + 피부과용 기기 (레이저 작동식 제외) + 미용목적 의료기기 부분품 (경기 고양시)": ["피부과용 기기 (레이저 작동식)", "피부과용 기기(레이저 작동식 제외)", "미용목적 의료기기 부분품(기타 내과, 외과, 치과, 수의과, 안과용 기기)"],
    "루트로닉 : 피부과용 기기 (레이저 작동식 제외) (경기 고양시)": ["피부과용 기기(레이저 작동식 제외)"],
    "시스템반도체 (전국)": ["비메모리 반도체 (시스템 반도체)"],
    "플래시 메모리(전국)": ["메모리 반도체"],
    "CMOS 이미지센서(CIS) (전국)": ["비메모리 반도체 (시스템 반도체)"],
    "인쇄회로 (테이프형, 리드프레임 기능을 하는 회로가 형성된 것) (전국)": ["안쇄 회로"],
    "인쇄회로 (테이프형, 리드프레임 기능을 하는 회로가 형성된 것) (전국_글로벌)": ["안쇄 회로"],
    "기타 인쇄회로 (전국)": ["기티 인쇄회로 섹터"],
    "기타 인쇄회로 (전국_글로벌)": ["기티 인쇄회로 섹터"],
    "EV Relay 1,000V 이하 (전국)": ["EV Relay 1,000V 이하 (전국)"],
    "EV Relay 1,000V 초과 (전국)": ["EV Relay 1,000V 초과 (전국)"],
    "과산화수소 (반도체 제조용) (전국)": ["과산화수소 (반도체 제조용)"],
    "스크러버 (전국)": ["스크러버"],
    "스크러버 (전국_글로벌)": ["스크러버"],
    "기타 레이저마커 (전국)": ["기타 레이저마커"],
    "기타 레이저마커 (전국_글로벌)": ["기타 레이저마커"],
    "반도체 웨이퍼, 소자의 측정, 검사용 장비 (전국)": ["반도체 웨이퍼, 소자의 측정, 검사용 장비"],
    "반도체 웨이퍼, 소자의 측정, 검사용 장비 (전국_글로벌)": ["반도체 웨이퍼, 소자의 측정, 검사용 장비"],
    "평판디스플레이 모니터용 (전국)": ["평판디스플레이 모니터용"],
    "평판디스플레이 텔레비전용 (전국)": ["평판디스플레이 텔레비전용"],
    "평판디스플레이 모듈 (구동장치.제어회로 없는 유기발광다이오드(OLED)의 것) (전국)": ["평판디스플레이 모듈"],
    "OLED 패널 / OLED 모니터 (전국)": ["OLED 패널"],
    "OLED 패널 (전국)": ["OLED 패널"],
    "OLED TV (전국)": ["OLED TV"],
    "탈철기 (전국)": ["탈철기"],
    "복합부품 집적회로(MCOs) (프로세서, 컨트롤러) (전국)": ["비메모리 반도체 (시스템 반도체)"],
    "MCOs(eMMC/UFS/SiP 등) (전국)": ["메모리 반도체", "비메모리 반도체 (시스템 반도체)"],
    "MCP (복합구조칩 집적회로) (전국_대만+홍콩+중국)": ["메모리 반도체"],
    "NCM (전국)": ["이차전지 양극재"],
    "니켈코발트망간 산화물 전구체 (NCMO)": ["NCA 니켈 코발트 알루미늄 산화물의 리튬염 (전국)"],
    "니켈코발트 망간 수산화물 전구체 (NCMH)": ["니켈코발트 망간 수산화물 전구체 (NCMH)"],
    "수산화리튬 (전국): 미래나노텍 / 강원에너지 / 하이드로리튬 / POSCO홀딩스": ["이차전지 소재 (수산화리튬)"],
    "ESS (전국)": ["이차전지 셀"],
    "반도체 웨이퍼용 증착장비 (전국)": ["반도체 전공정 장비"],
    "건식식각장비 (전국)": ["반도체 전공정 장비"],
    "반도체 웨이퍼 습식 식각 / 세척 장비 (전국)": ["반도체 전공정 장비"],
    "반도체 제조용 장비 (전국)": ["반도체 전공정 장비", "반도체 후공정 장비 / OSAT"],
    "3차원 검사장비, 모듈 (전국)": ["3차원 검사장비, 모듈"],
    "FPD 및 반도체 이송장치 (전국)": ["FPD 및 반도체 이송장치"],
    "연마제 (CMP공정에 쓰이는 소재) (전국)": ["연마제 (CMP공정에 쓰이는 소재)"],
    "MLCC (전국)": ["MLCC"],
    "실리콘카바이드 (전국)": ["실리콘카바이드"],
    "인터페이스보드 프로브 카드 (전국)": ["인터페이스보드 프로브 카드"],
    "러버 소켓 (전국)": ["러버 소켓"],
}


@dataclass
class TelegramPost:
    message_id: str
    posted_at: str
    url: str
    text: str
    title: str
    companies: list[str]
    company_count: int
    mapping_scope: str
    sector_key: str


def create_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_post_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL UNIQUE,
            channel_handle TEXT NOT NULL,
            posted_at TEXT NOT NULL DEFAULT '',
            post_url TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            raw_text TEXT NOT NULL DEFAULT '',
            companies_json TEXT NOT NULL DEFAULT '[]',
            company_count INTEGER NOT NULL DEFAULT 0,
            mapping_scope TEXT NOT NULL DEFAULT 'sector',
            sector_key TEXT NOT NULL DEFAULT '',
            mapping_status TEXT NOT NULL DEFAULT 'unmapped',
            matched_hs_codes_json TEXT NOT NULL DEFAULT '[]',
            matched_companies_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def normalize_space(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def parse_text(html: str) -> str:
    html = LINEBREAK_RE.sub("\n", html)
    text = HTML_TAG_RE.sub(" ", html)
    lines = [normalize_space(unescape(line)) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def extract_title(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    for line in lines:
        if "관련종목" not in line:
            return line[:240]
    return lines[0][:240]


def extract_companies(text: str) -> list[str]:
    match = re.search(r"관련종목\s*:\s*(.+?)(?:\n|$)", text)
    if match:
        raw = match.group(1)
    else:
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        if ":" in first_line:
            title_prefix, title_suffix = [part.strip() for part in first_line.split(":", 1)]
            if (
                any(token in title_prefix for token in ["전국", "글로벌", "수입", "수출"])
                and title_suffix
            ):
                raw = title_suffix
            else:
                raw = title_prefix
        else:
            title_prefix = first_line
            if not title_prefix or len(title_prefix) > 30:
                return []
            raw = title_prefix
    raw = re.split(r"\b20\d{2}년\b|잠정치|수출데이터|수입데이터|입니다\.", raw, maxsplit=1)[0]
    parts = [normalize_space(part) for part in COMPANY_SPLIT_RE.split(raw)]
    if len(parts) == 1:
        space_split = [token.strip() for token in normalize_space(raw).split(" ") if token.strip()]
        if len(space_split) >= 2:
            parts = space_split
    companies: list[str] = []
    for part in parts:
        cleaned = re.sub(r"\([^)]*$", "", part).strip(" .()[]")
        cleaned = re.sub(r"\(.*$", "", cleaned).strip(" .()[]")
        if not cleaned:
            continue
        if cleaned in {"등", "외"}:
            continue
        companies.append(COMPANY_ALIAS_MAP.get(cleaned, cleaned))
    return list(dict.fromkeys(companies))


def infer_sector_key(title: str, text: str) -> str:
    haystack = f"{title} {text}"
    for keyword, sector_key in KEYWORD_SECTOR_MAP:
        if keyword in haystack:
            return sector_key
    return "unknown"


def simplify_label(text: str) -> str:
    simplified = text
    simplified = re.sub(r"\([^)]*\)", "", simplified)
    for token in [
        " 수입",
        " 수출",
        " (전국)",
        " (전국_글로벌)",
        " (전국_미국)",
        " (중국)",
        " (글로벌)",
    ]:
        simplified = simplified.replace(token, "")
    simplified = simplified.replace("래디알", "래디얼")
    return normalize_space(simplified)


def core_product_label(text: str) -> str:
    """회사 prefix/지역 scope를 제거한 핵심 품목 라벨."""
    if not text:
        return ""
    base = text.split(":", 1)[1].strip() if ":" in text else text
    base = re.sub(
        r"\s*\((전국[^)]*|글로벌[^)]*|중국[^)]*|미국[^)]*|베트남[^)]*|일본[^)]*|대만[^)]*|홍콩[^)]*|핀란드[^)]*|모로코[^)]*|인도네시아[^)]*|[가-힣]+ [가-힣]+(?:시|군|구))\)",
        "",
        base,
    )
    return simplify_label(base)


def parse_message_id(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def parse_posted_at(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def fetch_posts(limit_pages: int = 120, lookback_days: int = LOOKBACK_DAYS) -> list[TelegramPost]:
    posts: list[TelegramPost] = []
    seen_urls: set[str] = set()
    url = CHANNEL_URL
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    for page_num in range(limit_pages):
        html = urlopen(url, timeout=20).read().decode("utf-8", "ignore")
        chunks = html.split("tgme_widget_message_wrap")[1:]
        oldest_on_page: datetime | None = None
        page_collected = 0
        for chunk in chunks:
            href_match = re.search(r'tgme_widget_message_date[^>]*href="([^"]+)"', chunk)
            time_match = re.search(r'<time datetime="([^"]+)"', chunk)
            text_match = re.search(
                r'<div class="tgme_widget_message_text js-message_text" dir="auto">(.*?)</div>',
                chunk,
                re.S,
            )
            if not href_match:
                continue
            post_url = href_match.group(1)
            if post_url in seen_urls:
                continue
            seen_urls.add(post_url)
            posted_at = time_match.group(1) if time_match else ""
            posted_dt = parse_posted_at(posted_at)
            if posted_dt is not None:
                oldest_on_page = posted_dt if oldest_on_page is None else min(oldest_on_page, posted_dt)
                if posted_dt < cutoff:
                    continue
            text = parse_text(text_match.group(1)) if text_match else ""
            title = extract_title(text)
            companies = extract_companies(text)
            company_count = len(companies)
            mapping_scope = "company" if 0 < company_count <= 2 else "sector"
            sector_key = infer_sector_key(title, text)
            posts.append(
                TelegramPost(
                    message_id=parse_message_id(post_url),
                    posted_at=posted_at,
                    url=post_url,
                    text=text,
                    title=title,
                    companies=companies,
                    company_count=company_count,
                    mapping_scope=mapping_scope,
                    sector_key=sector_key,
                )
            )
            page_collected += 1

        # 페이지 진행 로그 (stderr → daily_refresh 요약에 영향 없음)
        oldest_str = oldest_on_page.strftime("%Y-%m-%d") if oldest_on_page else "unknown"
        print(
            f"[backfill] page {page_num + 1}: {len(chunks)} chunks, {page_collected} posts collected,"
            f" oldest={oldest_str}, total_so_far={len(posts)}",
            file=sys.stderr,
            flush=True,
        )

        prev_match = re.search(r'<link rel="prev" href="([^"]+)"', html)
        if not prev_match:
            print("[backfill] no prev link — reached end of channel", file=sys.stderr, flush=True)
            break
        if oldest_on_page is not None and oldest_on_page < cutoff:
            print(f"[backfill] oldest {oldest_str} < cutoff {cutoff.strftime('%Y-%m-%d')} — stopping", file=sys.stderr, flush=True)
            break
        url = urljoin("https://t.me", prev_match.group(1))
    return posts


def load_existing_maps(conn: sqlite3.Connection) -> tuple[set[str], set[str], set[str]]:
    hs_display_names = {
        row[0]
        for row in conn.execute("SELECT DISTINCT display_name FROM hs_sector_map WHERE display_name <> ''")
    }
    company_sector_names = {
        row[0]
        for row in conn.execute("SELECT DISTINCT sector_name FROM hs_code_company_map WHERE sector_name <> ''")
    }
    company_names = {
        row[0]
        for row in conn.execute("SELECT DISTINCT stock_name FROM hs_code_company_map WHERE stock_name <> ''")
    }
    # listed universe fallback: company-scope posts can include names not yet seen in hs_code_company_map
    try:
        sconn = sqlite3.connect(f"file:{ROOT_STOCK_DB}?mode=ro", uri=True)
        sconn.row_factory = sqlite3.Row
        for table in ("stock_universe", "stock_meta", "stock_price_daily"):
            try:
                for row in sconn.execute(
                    f"SELECT DISTINCT stock_name, COALESCE(market,'') AS market FROM {table} WHERE stock_name <> ''"
                ):
                    market = (row["market"] or "").upper()
                    if market in {"KOSPI", "KOSDAQ", "유가증권", "코스닥"}:
                        company_names.add(row["stock_name"])
            except sqlite3.Error:
                continue
        sconn.close()
    except sqlite3.Error:
        pass
    company_names.update(COMPANY_ALIAS_MAP.values())
    return hs_display_names, company_sector_names, company_names


def find_matches(
    post: TelegramPost,
    sector_names: set[str],
    company_sector_names: set[str],
    known_company_names: set[str],
) -> tuple[str, list[str], list[str]]:
    normalized_companies = [COMPANY_ALIAS_MAP.get(name, name) for name in post.companies]
    normalized_companies = [normalize_space(name) for name in normalized_companies if name]
    simplified_text = simplify_label(post.text)
    simplified_title = simplify_label(post.title)
    core_title = core_product_label(post.title)
    compact_text = simplified_text.replace(" ", "")
    compact_title = simplified_title.replace(" ", "")
    compact_core_title = core_title.replace(" ", "")
    alias_names: set[str] = set()
    for alias_title, mapped_names in TITLE_ALIAS_MAP.items():
        simplified_alias = simplify_label(alias_title)
        compact_alias = simplified_alias.replace(" ", "")
        if (
            alias_title in post.title
            or alias_title in post.text
            or simplified_alias in simplified_title
            or simplified_alias in simplified_text
            or simplified_alias in core_title
            or compact_alias in compact_title
            or compact_alias in compact_text
            or compact_alias in compact_core_title
        ):
            alias_names.update(mapped_names)

    def _candidate_variants(name: str) -> tuple[str, str]:
        simplified = simplify_label(name)
        compact = simplified.replace(" ", "")
        return simplified, compact

    title_matches = sorted(
        {
            name
            for name in sector_names | company_sector_names | alias_names
            if name
            and (
                name in alias_names
                or any(_candidate_variants(name))
            )
            and (
                name in alias_names
                or
                name in post.title
                or name in post.text
                or post.title in name
                or (_candidate_variants(name)[0] and _candidate_variants(name)[0] in simplified_text)
                or (_candidate_variants(name)[0] and _candidate_variants(name)[0] in simplified_title)
                or (_candidate_variants(name)[0] and _candidate_variants(name)[0] in core_title)
                or (_candidate_variants(name)[1] and _candidate_variants(name)[1] in compact_text)
                or (_candidate_variants(name)[1] and _candidate_variants(name)[1] in compact_title)
                or (_candidate_variants(name)[1] and _candidate_variants(name)[1] in compact_core_title)
            )
        }
    )
    known_company_names_norm = {normalize_space(name) for name in known_company_names}
    company_matches = sorted({name for name in normalized_companies if name in known_company_names_norm})
    mapping_status = "mapped" if title_matches else "unmapped"
    if post.mapping_scope == "company" and normalized_companies:
        # If title/HS mapping evidence is explicit, keep mapped even when company dictionary lags.
        mapping_status = "mapped" if title_matches and (company_matches or post.company_count <= 2) else "unmapped"
    return mapping_status, title_matches, company_matches


def priority_score(post_or_row: TelegramPost | sqlite3.Row) -> int:
    company_count = getattr(post_or_row, "company_count", None)
    if company_count is None:
        company_count = post_or_row["company_count"]
    mapping_scope = getattr(post_or_row, "mapping_scope", None)
    if mapping_scope is None:
        mapping_scope = post_or_row["mapping_scope"]
    sector_key = getattr(post_or_row, "sector_key", None)
    if sector_key is None:
        sector_key = post_or_row["sector_key"]

    score = 0
    if mapping_scope == "company" and 0 < company_count <= 2:
        score += 100
    if mapping_scope == "sector" and sector_key in PRIORITY_SECTORS:
        score += 80
    if sector_key in PRIORITY_SECTORS:
        score += 20
    if sector_key == "unknown":
        score -= 30
    return score


def upsert_posts(conn: sqlite3.Connection, posts: Iterable[TelegramPost]) -> dict[str, int]:
    sector_names, company_sector_names, known_company_names = load_existing_maps(conn)
    summary = {"stored": 0, "unmapped": 0, "mapped": 0}
    for post in posts:
        mapping_status, hs_matches, company_matches = find_matches(
            post, sector_names, company_sector_names, known_company_names
        )
        conn.execute(
            """
            INSERT INTO telegram_post_cache
            (
                message_id, channel_handle, posted_at, post_url, title, raw_text,
                companies_json, company_count, mapping_scope, sector_key,
                mapping_status, matched_hs_codes_json, matched_companies_json,
                updated_at
            )
            VALUES (?, 'BeOn_BeClear', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(message_id) DO UPDATE SET
                posted_at=excluded.posted_at,
                post_url=excluded.post_url,
                title=excluded.title,
                raw_text=excluded.raw_text,
                companies_json=excluded.companies_json,
                company_count=excluded.company_count,
                mapping_scope=excluded.mapping_scope,
                sector_key=excluded.sector_key,
                mapping_status=excluded.mapping_status,
                matched_hs_codes_json=excluded.matched_hs_codes_json,
                matched_companies_json=excluded.matched_companies_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                post.message_id,
                post.posted_at,
                post.url,
                post.title,
                post.text,
                json.dumps(post.companies, ensure_ascii=False),
                post.company_count,
                post.mapping_scope,
                post.sector_key,
                mapping_status,
                json.dumps(hs_matches, ensure_ascii=False),
                json.dumps(company_matches, ensure_ascii=False),
            ),
        )
        summary["stored"] += 1
        summary[mapping_status] += 1
    return summary


def fetch_unmapped_preview(conn: sqlite3.Connection, limit: int = 15) -> list[dict[str, object]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT title, post_url, company_count, mapping_scope, sector_key, companies_json, posted_at
        FROM telegram_post_cache
        WHERE mapping_status = 'unmapped'
        """,
    ).fetchall()
    ranked_rows = sorted(
        rows,
        key=lambda row: (
            priority_score(row),
            row["posted_at"],
        ),
        reverse=True,
    )[:limit]
    return [
        {
            "title": row["title"],
            "post_url": row["post_url"],
            "company_count": row["company_count"],
            "mapping_scope": row["mapping_scope"],
            "sector_key": row["sector_key"],
            "priority_score": priority_score(row),
            "companies": json.loads(row["companies_json"] or "[]"),
        }
        for row in ranked_rows
    ]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    create_table(conn)
    posts = fetch_posts()
    summary = upsert_posts(conn, posts)
    conn.commit()
    preview = fetch_unmapped_preview(conn)
    conn.close()
    print(
        json.dumps(
            {
                "channel": "@BeOn_BeClear",
                "fetched_posts": len(posts),
                "summary": summary,
                "unmapped_preview": preview,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

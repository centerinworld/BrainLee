from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "hs_trade_lab.db"
ROOT_STOCK_DB = ROOT_DIR.parent / "stock.db"
EXPORT_DIR = ROOT_DIR / "market_radar_exports"

FLOW_RE = re.compile(r"^\s*(수출|수입)\s*\(([^)]*)\)")
COMPANY_SPLIT_RE = re.compile(r"[\/,·ㆍ]| 및 | 와 | 과 ")
# Filters domain-specific labels that should not match general posts
GENERIC_SHIP_ENGINE_RE = re.compile(r"선박추진용|디젤.세미디젤|압축점화식")
GENERIC_AIRCRAFT_RE = re.compile(r"비행기의 부분품|헬리콥터의 부분품")

COMPANY_ALIASES = {
    "Sk하이닉스": "SK하이닉스",
    "SK하이닉스(나믹스": "SK하이닉스",
    "삼성전자(나가세": "삼성전자",
    "OCI": "OCI홀딩스",
    "HD현대": "HD현대중공업",
    "JYP Ent": "JYP Ent.",
    "와이아이케이": "와이씨",
    "와이씨 (와이아이케이)": "와이씨",
    "금호석유": "금호석유화학",
    "코오롱인더스트리": "코오롱인더",
    "코오롱플라스틱": "코오롱ENP",
    "에스테아이": "에스티아이",
    "SEMES": "세메스",
    "지앤비에스 에코": "지앤비에스에코",
    "바우와우코리아": "오에스피",
    "삼성디플레이": "삼성디스플레이",
    "GC녹십자": "녹십자",
    "롯데칠성음료": "롯데칠성",
    "현대에너지솔루션": "HD현대에너지솔루션",
}

TITLE_PREFIX_COMPANY_ALLOWLIST = {
    "HD현대중공업",
    "HD현대인프라코어",
    "제주반도체",
}

BAD_COMPANY_TOKENS = {
    "",
    "등",
    "외",
    "비상장",
    "자회사",
    "+",
    "관련종목",
    "월별",
    "수출",
    "수입",
    "데이터",
    "월별 수출 데이터",
    "월별 수입 데이터",
    "월별수출데이터",
    "월별수입데이터",
    "전국_글로벌",
    "필러",
    "전차와",
    "전차",
    "그",
    "밖의",
    "부분품",
    "기초화장품",
    "장갑차량",
    "양극활물질",
    "코발트산",
    "리튬",
    "주석산염",
    "티탄산염",
    "안티몬산염",
    "철산염",
    "아철산염",
    "바나듐산염",
    "소주",
    "임플란트",
    "연방",
}

BAD_COMPANY_PATTERNS = (
    re.compile(r"^전국(?:_|$)"),
    re.compile(r"^[가-힣]+(?:시|군|구)(?:_|$)"),
)

BAD_HS_LABELS = {
    "",
    "기타",
    "수입",
    "수출",
    "수입 수입",
    "수출 수출",
    "수입데이터",
    "수출데이터",
}

HS_ALIASES = {
    # 메모리 반도체
    "메모리반도체": [("854232", "전자집적회로: 메모리"), ("8523511000", "SSD")],
    "메모리 반도체": [("854232", "전자집적회로: 메모리"), ("8523511000", "SSD")],
    "디램": [("854232", "전자집적회로: 메모리")],
    "디램모듈": [("854232", "전자집적회로: 메모리")],
    "MCP": [("854232", "전자집적회로: 메모리")],
    "MCP (복합구조칩 집적회로)": [("854232", "전자집적회로: 메모리")],
    "복합구조칩 집적회로": [("854232", "전자집적회로: 메모리")],
    "전자집적회로": [("854232", "전자집적회로: 메모리")],
    "프로세서.컨트롤러": [("854231", "전자집적회로: 프로세서와 컨트롤러")],
    "플래시 메모리": [("8542321030", "NAND")],
    "시스템반도체": [("854231", "전자집적회로: 프로세서와 컨트롤러")],
    "CMOS 이미지센서(CIS)": [("854231", "전자집적회로: 프로세서와 컨트롤러")],
    "복합부품 집적회로(MCOs) (프로세서, 컨트롤러)": [("854231", "전자집적회로: 프로세서와 컨트롤러")],
    "MCOs(eMMC/UFS/SiP 등)": [("8542323000", "복합칩·HBM")],
    # 디스플레이
    "MLCC": [("8532240000", "세라믹 유전체의 것(다층)")],
    "OLED TV": [("8528725000", "유기발광다이오드(오엘이디) 방식")],
    "OLED 패널": [("8524911000", "유기발광다이오드 표시 모듈")],
    "유기발광다이오드 OLED 제조용": [("8524911000", "유기발광다이오드 표시 모듈")],
    "평판디스플레이 텔레비전용": [("8528725000", "유기발광다이오드(오엘이디) 방식")],
    "평판디스플레이 모니터용": [("8528521000", "평판디스플레이 모니터")],
    "평판디스플레이 모듈": [("8524911000", "유기발광다이오드 표시 모듈"), ("8524912000", "액정표시 모듈")],
    # 반도체 제조 장비
    "스크러버": [("8421219020", "반도체 제조용 여과기나 청정기")],
    "스크러버 액체용•기체용 여과 및 청정기": [("8421219020", "반도체 제조용 여과기나 청정기")],
    "인터페이스보드 프로브 카드": [("8534009000", "그 밖의 인쇄회로")],
    "러버 소켓": [("8536909090", "그 밖의 전기회로 접속용 기기")],
    "실리콘러버소켓": [("8536909090", "그 밖의 전기회로 접속용 기기")],
    "플러그 소켓(동축케이블·인쇄회로용의 것) 리노소켓": [("8536909090", "그 밖의 전기회로 접속용 기기")],
    "CMP": [("8464202000", "반도체 웨이퍼 연마기")],
    "CMP (웨이퍼 표면 연마장치)": [("8464202000", "반도체 웨이퍼 연마기")],
    "연마제": [("3405901000", "반도체 웨이퍼 연마용 조제품")],
    "연마제 (CMP공정에 쓰이는 소재)": [("3405901000", "반도체 웨이퍼 연마용 조제품")],
    "반도체 제조용 장비": [("848620", "반도체 웨이퍼 제조용 기기")],
    "반도체 전공정 장비": [("848620", "반도체 웨이퍼 제조용 기기")],
    "반도체 웨이퍼용 증착장비": [("848620", "반도체 웨이퍼 제조용 기기")],
    "반도체 웨이퍼 습식 식각 / 세척 장비": [("848620", "반도체 웨이퍼 제조용 기기")],
    "건식식각장비": [("848620", "반도체 웨이퍼 제조용 기기")],
    "CCSS": [("848620", "반도체 웨이퍼 제조용 기기")],
    "PR박리액": [("848620", "반도체 웨이퍼 제조용 기기")],
    "유기혼합용제와 시너": [("3814000000", "유기혼합용제와 시너")],
    "고압 수소 열처리 장비": [("848620", "반도체 웨이퍼 제조용 기기")],
    "급속열처리장비": [("848620", "반도체 웨이퍼 제조용 기기")],
    "급속열처리장비(Rapid Thermal Processing, RTP)": [("848620", "반도체 웨이퍼 제조용 기기")],
    "반도체 웨이퍼, 소자의 측정, 검사용 장비": [("9031809070", "반도체 패턴결함 검사장비")],
    "3차원 검사장비, 모듈": [("9031809091", "반도체 검사장비")],
    "3차원 검사장비": [("9031809091", "반도체 검사장비")],
    "산업용자동화원자현미경": [("9012101000", "현미경(광학현미경은 제외한다)")],
    "핸들러": [("9031809091", "반도체 검사장비")],
    "TC BONDER": [("848640", "반도체 디바이스/전자집적회로 조립·검사용 기기")],
    "산업용원자현미경": [("9012101090", "산업용 원자현미경(AFM)")],
    "반도체 웨이퍼 패턴결함 검사장비 및 반도체오버레이계측기": [
        ("9031491000", "광학식 표면 테스터"),
        ("9031494010", "반도체 제조공정용 광학 계측기"),
    ],
    "블랭크마스크": [("7006002000", "반도체·평판디스플레이 블랭크마스크용 유리")],
    # 트랙터
    "소형 트랙터": [("8701912000", "농업용 트랙터(18kW이하)"), ("8701101000", "차축이 하나인 트랙터")],
    "중대형 트랙터": [("8701922000", "농업용 트랙터(18kW~37kW)"), ("8701932000", "농업용 트랙터(37kW~75kW)")],
    "농업용 트랙터": [("8701912000", "농업용 트랙터(18kW이하)"), ("8701922000", "농업용 트랙터(18kW~37kW)")],
    # 진단·의료기기
    "면역진단기기": [("9027509000", "기타 물리·화학 분석기기"), ("3822191000", "면역물품")],
    "면역진단카트리지": [("3822191000", "면역물품")],
    "임플란트": [("9021290000", "치과용 임플란트 등")],
    "엑스선 단층 촬영기": [("9022120000", "엑스선 단층촬영기")],
    "체성분분석기": [("9018198000", "기타 전기식 진단기기")],
    # 변압기
    "초고압 변압기": [("8504231000", "초고압 변압기(10,000~100,000kVA)"), ("8504239000", "초고압 변압기(100,000kVA초과)")],
    "소형 변압기": [("8504319000", "그 밖의 변압기(용량 1kVA 초과 16kVA 이하)")],
    "중대형 변압기": [("8504320000", "변압기(용량 16kVA 초과 500kVA 이하)"), ("8504330000", "변압기(용량 500kVA 초과)")],
    "변환기": [("8504409090", "그 밖의 정지형 변환기")],
    # 광섬유 케이블
    "광섬유 케이블": [("9001100000", "광섬유ㆍ광섬유 다발과 광섬유 케이블")],
    "광섬유 광케이블": [("9001100000", "광섬유ㆍ광섬유 다발과 광섬유 케이블")],
    # 소재·화학
    "실리콘카바이드": [("2849201000", "탄화규소")],
    "실리콘 카바이드": [("2849201000", "탄화규소")],
    "탄화규소": [("2849201000", "탄화규소")],
    "SiC": [("2849201000", "탄화규소")],
    "CNT도전재": [("3824999090", "기타 화학공업 조제품")],
    "NCM": [("2825902050", "니켈 코발트 망간 수산화물")],
    "NCA": [("2825902090", "그 밖의 리튬축전지용 화합물(양극활물질 등)")],
    "진주광택안료": [("3206499000", "그 밖의 무기 안료와 조제품")],
    "수산화리튬": [("2825209000", "그 밖의 리튬의 산화물과 수산화물")],
    "영구자석": [("8505110000", "금속제 영구자석")],
    "리드프레임": [("8536909090", "그 밖의 전기회로 접속용 기기")],
    "Package Substrate": [("8534009000", "그 밖의 인쇄회로")],
    "CCL": [("7410210000", "동박적층판용 동박")],
    "Cap Assembly": [("8507909000", "축전지의 부분품")],
    "피스톤식 엔진 시동용 연산 축전지": [("8507100000", "피스톤식 엔진 시동용 연산축전지")],
    "시동용 연산축전지": [("8507100000", "피스톤식 엔진 시동용 연산축전지")],
    "ESS": [("8507600000", "리튬이온 축전지")],
    "과산화수소": [("2847000000", "과산화수소")],
    "솔더볼": [("8311900000", "기타 납땜·용접·용착 재료")],
    # 전기·전력
    "EV Relay": [("8536410000", "릴레이(1,000V이하)")],
    "EV 릴레이": [("8536410000", "릴레이(1,000V이하)")],
    "EV Relay 1,000V 이하": [("8536410000", "릴레이(1,000V이하)")],
    "EV Relay 1,000V 초과": [("8536491000", "릴레이(1,000V초과)")],
    "AFCI PCB ASSEMBLY": [("8534009000", "그 밖의 인쇄회로")],
    # 레이저 장비
    "레이저마커": [("8456119000", "기타 레이저 가공기")],
    "12인치 레이저마커 / 레이저 그루빙": [("8456119000", "기타 레이저 가공기")],
    "기타 레이저마커": [("8456119000", "기타 레이저 가공기")],
    "레이저 그루빙": [("8456119000", "기타 레이저 가공기")],
    "레이저 스텔스다이싱 장비": [("8456119000", "기타 레이저 가공기")],
    # 이송·물류 장비
    "FPD 및 반도체 이송장치": [("8428909000", "기타 리프트·컨베이어")],
    # 탈철기 (자기분리기)
    "탈철기": [("8505200000", "전자석")],
    "전자석탈철기": [("8505200000", "전자석")],
    # 필러 (의료·미용)
    "필러": [("3001900000", "인체 의료용 조직·세포 등")],
    "리쥬란": [("3001900000", "인체 의료용 조직·세포 등")],
    "FPCB": [("8534009000", "그 밖의 인쇄회로")],
    "부직포": [("5603129000", "그 밖의 부직포")],
    "NCF": [("3921909090", "그 밖의 플라스틱 시트/필름")],
    "유도무기": [("9306900000", "탄약과 탄약 부분품")],
    "로켓 발사기": [("9301200000", "로켓 발사기 등 군수 장비")],
    "레이더": [("8526100000", "레이더 기기")],
    "전차와 그 밖의 장갑차량": [
        ("8710001000", "전차"),
        ("8710002000", "그 밖의 장갑차량"),
    ],
    "K-2 TANKS 및 기타 수송장비 부품": [
        ("8710001000", "전차"),
        ("8710002000", "그 밖의 장갑차량"),
    ],
    # Be On 최근 수출입 카드 표기 보강
    "펄프": [("4703292000", "표백 활엽수 화학목재펄프"), ("4703212000", "표백 침엽수 화학목재펄프")],
    "인쇄용지": [("4810191000", "인쇄용ㆍ필기용 종이와 판지"), ("4810131000", "인쇄용ㆍ필기용 종이와 판지")],
    "특수지": [("4811901000", "그 밖의 종이와 판지")],
    "산업용지": [("4805919090", "그 밖의 종이와 판지"), ("4805929000", "그 밖의 종이와 판지")],
    "골심지": [("4805110000", "반화학 플루팅지"), ("4805190000", "그 밖의 플루팅지")],
    "태양광 셀": [("8541430000", "광전지(모듈에 조립되었거나 패널로 구성된 것으로 한정한다)")],
    "태양광 모듈": [("8541430000", "광전지(모듈에 조립되었거나 패널로 구성된 것으로 한정한다)")],
    "태양광 모듈/패널": [("8541430000", "광전지(모듈에 조립되었거나 패널로 구성된 것으로 한정한다)")],
    "전해질": [("3824999090", "전해액")],
    "반도체 조립용 인캡슐레이션(몰딩 장비)": [("8486402031", "반도체조립용 인캡슐레이션 기기")],
    "반도체 조립용 인캡슐레이션(몰딩장비)": [("8486402031", "반도체조립용 인캡슐레이션 기기")],
    "카드프린터": [("8443321090", "기타 프린터")],
    "카드프린터 + 소모품": [("8443321090", "기타 프린터"), ("8443991000", "프린터의 부분품")],
    "소모품": [("8443991000", "프린터의 부분품")],
    "선이나 케이블의 접속용 구성품": [("8536909010", "선이나 케이블의 접속용 구성품")],
    "부분품 (기타 내과용.외과용.치과용.수의과용.안과용 기기)": [("9018909000", "기타 의료기기 부분품·부속품")],
    "광섬유": [("9001100000", "광섬유ㆍ광섬유 다발과 광섬유 케이블")],
    "전압별 변압기": [("8504320000", "변압기(용량 16kVA 초과 500kVA 이하)"), ("8504330000", "변압기(용량 500kVA 초과)")],
    "2차전지 원형 각형 등 팩 모듈(Cap assy포함)": [("8507609000", "2차전지 원형·각형 등 Cap Assembly, 모듈")],
    "메커니컬셔블.엑스커베이터": [("842952", "360도 회전 상부구조의 굴착기")],
    "메커니컬셔블 엑스커베이터": [("842952", "360도 회전 상부구조의 굴착기")],
    "엑스커베이터": [("842952", "360도 회전 상부구조의 굴착기")],
    "건설기계": [("842952", "360도 회전 상부구조의 굴착기")],
    "불도저.앵글도저": [("8429110000", "무한궤도식 불도저와 앵글도저")],
    "불도저 앵글도저": [("8429110000", "무한궤도식 불도저와 앵글도저")],
    "중소구경 철강제 관": [("7306300000", "그 밖의 철강제 관(용접, 원형, 철이나 비합금강)")],
    "대구경 철강제 관": [
        ("7305110000", "석유ㆍ가스 수송용 라인파이프(세로방향 아크용접, 외경 406.4mm 초과)"),
        ("7305120000", "석유ㆍ가스 수송용 라인파이프(그 밖의 용접, 외경 406.4mm 초과)"),
        ("7305190000", "그 밖의 라인파이프(외경 406.4mm 초과)"),
    ],
    "철강제 관": [("7306300000", "그 밖의 철강제 관(용접, 원형, 철이나 비합금강)")],
}

# Product normalization rules to reduce repetitive missing_hs labels.
PRODUCT_CANON_RULES: list[tuple[str, str]] = [
    ("12인치 레이저마커", "12인치 레이저마커 / 레이저 그루빙"),
    ("레이저 그루빙", "12인치 레이저마커 / 레이저 그루빙"),
    ("리드프레임", "리드프레임"),
    ("전자집적회로", "전자집적회로"),
    ("FPCB", "FPCB"),
    ("부직포", "부직포"),
    ("NCF", "NCF"),
    ("Non Conductive Film", "NCF"),
    ("유기발광다이오드 OLED 제조용", "유기발광다이오드 OLED 제조용"),
    ("유도무기", "유도무기"),
    ("플래시 메모리", "플래시 메모리"),
    ("시스템반도체", "시스템반도체"),
    ("CMOS 이미지센서", "CMOS 이미지센서(CIS)"),
    ("복합부품 집적회로", "복합부품 집적회로(MCOs) (프로세서, 컨트롤러)"),
    ("MCOs", "MCOs(eMMC/UFS/SiP 등)"),
    ("TC BONDER", "TC BONDER"),
    ("산업용원자현미경", "산업용원자현미경"),
    ("패턴결함 검사장비", "반도체 웨이퍼 패턴결함 검사장비 및 반도체오버레이계측기"),
    ("블랭크마스크", "블랭크마스크"),
    ("임플란트", "임플란트"),
    ("피스톤식 엔진 시동용 연산 축전지", "피스톤식 엔진 시동용 연산 축전지"),
    ("ESS", "ESS"),
    ("전차", "전차와 그 밖의 장갑차량"),
    ("K-2 TANKS", "K-2 TANKS 및 기타 수송장비 부품"),
    ("실리콘 카바이드", "실리콘 카바이드"),
    ("CNT도전재", "CNT도전재"),
    ("엑스선 단층 촬영기", "엑스선 단층 촬영기"),
    ("체성분분석기", "체성분분석기"),
    ("반도체 웨이퍼용 증착 장비", "반도체 웨이퍼용 증착장비"),
    ("로켓 발사기", "로켓 발사기"),
    ("레이더", "레이더"),
    ("펄프", "펄프"),
    ("인쇄용지", "인쇄용지"),
    ("특수지", "특수지"),
    ("산업용지", "산업용지"),
    ("골심지", "골심지"),
    ("태양광 셀", "태양광 셀"),
    ("태양광 모듈/패널", "태양광 모듈/패널"),
    ("태양광 모듈", "태양광 모듈"),
    ("전해질", "전해질"),
    ("반도체 조립용 인캡슐레이션(몰딩 장비)", "반도체 조립용 인캡슐레이션(몰딩 장비)"),
    ("반도체 조립용 인캡슐레이션(몰딩장비)", "반도체 조립용 인캡슐레이션(몰딩장비)"),
    ("카드프린터 + 소모품", "카드프린터 + 소모품"),
    ("카드프린터", "카드프린터"),
    ("선이나 케이블의 접속용 구성품", "선이나 케이블의 접속용 구성품"),
    ("부분품 (기타 내과용.외과용.치과용.수의과용.안과용 기기)", "부분품 (기타 내과용.외과용.치과용.수의과용.안과용 기기)"),
    ("광섬유", "광섬유"),
    ("전압 1,000V 이하", "EV Relay 1,000V 이하"),
    ("전압 1,000V 초과", "EV Relay 1,000V 초과"),
    ("EV Relays", "EV Relay"),
    ("전압별 변압기", "전압별 변압기"),
    ("Cap assy", "2차전지 원형 각형 등 팩 모듈(Cap assy포함)"),
    ("Cap Assembly", "2차전지 원형 각형 등 팩 모듈(Cap assy포함)"),
    ("메커니컬셔블", "메커니컬셔블.엑스커베이터"),
    ("엑스커베이터", "메커니컬셔블.엑스커베이터"),
    ("불도저", "불도저.앵글도저"),
    ("앵글도저", "불도저.앵글도저"),
    ("중소구경 철강제 관", "중소구경 철강제 관"),
    ("대구경 철강제 관", "대구경 철강제 관"),
]


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def ensure_schema(conn: sqlite3.Connection) -> None:
    if not has_column(conn, "telegram_post_cache", "trade_flow_type"):
        conn.execute("ALTER TABLE telegram_post_cache ADD COLUMN trade_flow_type TEXT NOT NULL DEFAULT ''")
    if not has_column(conn, "telegram_post_cache", "product_title"):
        conn.execute("ALTER TABLE telegram_post_cache ADD COLUMN product_title TEXT NOT NULL DEFAULT ''")
    if not has_column(conn, "hs_code_company_map", "flow_type"):
        conn.execute("ALTER TABLE hs_code_company_map ADD COLUMN flow_type TEXT NOT NULL DEFAULT 'export'")
        conn.execute(
            """
            UPDATE hs_code_company_map
            SET flow_type = CASE WHEN sector_name LIKE '%수입%' OR note LIKE '%수입%' THEN 'import' ELSE 'export' END
            """
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_company_hs_flow_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_message_id TEXT NOT NULL,
            posted_at TEXT NOT NULL DEFAULT '',
            post_url TEXT NOT NULL DEFAULT '',
            flow_type TEXT NOT NULL,
            flow_scope TEXT NOT NULL DEFAULT '',
            product_title TEXT NOT NULL DEFAULT '',
            hs_code TEXT NOT NULL,
            hs_name TEXT NOT NULL DEFAULT '',
            stock_code TEXT NOT NULL,
            stock_name TEXT NOT NULL DEFAULT '',
            market TEXT NOT NULL DEFAULT '',
            mapping_status TEXT NOT NULL DEFAULT 'exact',
            confidence REAL NOT NULL DEFAULT 0.95,
            source_note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(post_message_id, flow_type, hs_code, stock_code)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_telegram_flow_map_stock ON telegram_company_hs_flow_map(stock_code, flow_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_telegram_flow_map_hs ON telegram_company_hs_flow_map(hs_code, flow_type)")


def normalize_company(name: str) -> str:
    value = re.sub(r"\([^)]*\)", "", name)
    value = re.sub(r"\([^)]*$", "", value).strip(" .()[]")
    value = re.sub(r"\s*등$", "", value).strip()
    return COMPANY_ALIASES.get(value, value)


def is_bad_company_token(name: str) -> bool:
    if name in BAD_COMPANY_TOKENS:
        return True
    return any(pattern.search(name) for pattern in BAD_COMPANY_PATTERNS)


def load_stock_lookup() -> dict[str, dict[str, str]]:
    conn = sqlite3.connect(f"file:{ROOT_STOCK_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    lookup: dict[str, dict[str, str]] = {}
    listed_markets = {"유가증권", "코스닥", "KOSPI", "KOSDAQ"}
    for table in ("stock_universe", "stock_meta", "stock_price_daily"):
        try:
            rows = conn.execute(
                f"""
                SELECT stock_code, stock_name, COALESCE(market, '') AS market
                FROM {table}
                WHERE stock_name <> ''
                """
            ).fetchall()
        except sqlite3.Error:
            continue
        for row in rows:
            market = row["market"] or ""
            if market not in listed_markets:
                continue
            lookup.setdefault(
                row["stock_name"],
                {"stock_code": row["stock_code"], "stock_name": row["stock_name"], "market": market},
            )
    conn.close()
    hs_conn = sqlite3.connect(DB_PATH)
    hs_conn.row_factory = sqlite3.Row
    try:
        for row in hs_conn.execute(
            """
            SELECT DISTINCT stock_code, stock_name
            FROM hs_code_company_map
            WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
              AND stock_name <> ''
            """
        ):
            lookup.setdefault(
                row["stock_name"],
                {"stock_code": row["stock_code"], "stock_name": row["stock_name"], "market": "KRX"},
            )
    finally:
        hs_conn.close()
    return lookup


def add_hs(lookup: dict[str, list[dict[str, str]]], label: str, hs_code: str, hs_name: str) -> None:
    label = re.sub(r"\s+", " ", (label or "").strip())
    if not label:
        return
    entry = {"hs_code": hs_code, "hs_name": hs_name}
    values = lookup.setdefault(label, [])
    if entry not in values:
        values.append(entry)


def load_hs_lookup(conn: sqlite3.Connection) -> dict[str, list[dict[str, str]]]:
    """Build product-label → HS-code lookup from hs_sector_map and HS_ALIASES only.

    hs_code_company_map is intentionally excluded: its sector_names are derived from
    past rebuild outputs, creating a circular dependency that causes exponential bloat
    across successive runs.
    """
    lookup: dict[str, list[dict[str, str]]] = {}
    for row in conn.execute("SELECT hs_code, hs_name, display_name FROM hs_sector_map"):
        add_hs(lookup, row["display_name"], row["hs_code"], row["hs_name"])
        add_hs(lookup, row["hs_name"], row["hs_code"], row["hs_name"])
        simplified = re.sub(r"\s+(수입|수출)$", "", row["display_name"] or "").strip()
        add_hs(lookup, simplified, row["hs_code"], row["hs_name"])
    for label, rows in HS_ALIASES.items():
        for hs_code, hs_name in rows:
            add_hs(lookup, label, hs_code, hs_name)
    return lookup


def json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [str(item).strip() for item in data if str(item).strip()]
    return []


def parse_flow_and_product(raw_text: str, title: str) -> tuple[str, str, str]:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines:
        return "", "", title.strip()
    match = FLOW_RE.match(lines[0])
    if match:
        flow_type = "export" if match.group(1) == "수출" else "import"
        flow_scope = match.group(2).strip()
        candidate_lines = lines[1:]
    elif "수출데이터" in raw_text or "수출 데이터" in raw_text:
        flow_type = "export"
        flow_scope = ""
        candidate_lines = lines
    elif "수입데이터" in raw_text or "수입 데이터" in raw_text:
        flow_type = "import"
        flow_scope = ""
        candidate_lines = lines
    else:
        return "", "", title.strip()
    product = ""
    for line in candidate_lines:
        if "관련종목" in line:
            product = re.split(r"\s+관련종목\b", line, maxsplit=1)[0].strip()
            break
        if re.search(r"20\d{2}년", line):
            break
        if line.startswith("|"):
            continue
        product = line.strip()
        break

    if product and product == (title or "").strip() and len(lines) > 1:
        next_line = lines[1].strip()
        if next_line and "관련종목" not in next_line and not re.search(r"20\d{2}년", next_line):
            product = next_line

    if ":" in product:
        prefix, suffix = product.split(":", 1)
        normalized_prefix = normalize_company(prefix.strip().lstrip("🔸🔹▪• "))
        if (
            any(marker in prefix for marker in ("/", "+", " 등"))
            or normalized_prefix in TITLE_PREFIX_COMPANY_ALLOWLIST
        ) and not suffix.strip().startswith("월별"):
            product = suffix.strip()
    product = re.split(r"\s+관련종목\b|\s+관련주\b", product, maxsplit=1)[0].strip()

    # Fallback: 제목이 "회사명 : 품목 (...)" 형식이면 품목 부분 우선 사용
    if not product:
        t = (title or "").strip()
        if ":" in t:
            product = t.split(":", 1)[1].strip()
        else:
            product = t

    # 지역/국가 scope 추출 (패턴 범위 확대)
    scope_match = re.search(
        r"\((전국[^)]*|글로벌[^)]*|[가-힣]+ [가-힣]+(?:시|군|구)|중국[^)]*|미국[^)]*|베트남[^)]*|일본[^)]*|대만[^)]*|홍콩[^)]*|핀란드[^)]*|모로코[^)]*|인도네시아[^)]*)\)",
        product,
    )
    if scope_match and not flow_scope:
        flow_scope = scope_match.group(1).strip()
    return flow_type, flow_scope, product or title.strip()


def parse_companies(row: sqlite3.Row) -> list[str]:
    companies = json_list(row["matched_companies_json"]) or json_list(row["companies_json"])
    if companies:
        parsed = list(dict.fromkeys(normalize_company(item) for item in companies if not is_bad_company_token(normalize_company(item))))
        if parsed:
            return parsed
    raw_text = row["raw_text"] or ""
    match = re.search(r"관련(?:종목|주)\s*:\s*(.+?)(?:\n|$)", raw_text)
    if not match and ":" in (row["title"] or ""):
        title_prefix = (row["title"] or "").split(":", 1)[0]
        if any(marker in title_prefix for marker in ("/", "+", " 등")):
            match = re.match(r"(.+?)\s*:\s*.+$", row["title"] or "")
    if not match:
        title = (row["title"] or "").strip()
        if ":" in title:
            title_prefix = title.split(":", 1)[0].strip().lstrip("🔸🔹▪• ")
            company = normalize_company(title_prefix)
            if company in TITLE_PREFIX_COMPANY_ALLOWLIST and not is_bad_company_token(company):
                return [company]
        return []
    raw = re.split(r"\b20\d{2}년\b|잠정치|확정치", match.group(1), maxsplit=1)[0]
    values = []
    for item in COMPANY_SPLIT_RE.split(raw):
        company = normalize_company(item)
        if not is_bad_company_token(company):
            values.append(company)
    return list(dict.fromkeys(values))


MAX_HS_LABELS_PER_POST = 12  # summary posts have 30-80 labels; cap to avoid noise


def _label_allowed(label: str, title: str) -> bool:
    """Filter domain-specific labels that don't belong in general product posts."""
    if GENERIC_SHIP_ENGINE_RE.search(label):
        return any(tok in title for tok in ["선박", "엔진", "HD현대", "STX", "조선", "현대중공업"])
    if GENERIC_AIRCRAFT_RE.search(label):
        return any(tok in title for tok in ["비행기", "헬리콥터", "항공", "한화에어로", "KAI", "아스트"])
    return True


def _hs_allowed_for_context(hs_code: str, label: str, product: str, title: str) -> bool:
    context = " ".join([label or "", product or "", title or ""])
    if hs_code == "9021290000" and any(tok in context for tok in ["이온주입", "도핑", "반도체"]):
        return False
    if hs_code == "8536410000" and "1,000V 초과" in context:
        return False
    if hs_code == "8536491000" and "1,000V 이하" in context:
        return False
    return True


def hs_entries_for_post(row: sqlite3.Row, product: str, hs_lookup: dict[str, list[dict[str, str]]]) -> list[dict[str, str]]:
    labels = json_list(row["matched_hs_codes_json"])
    # Skip summary/compilation posts with an explosion of labels
    if len(labels) > MAX_HS_LABELS_PER_POST:
        labels = []
    title = row["title"] or ""
    core_title = title.split(":", 1)[1].strip() if ":" in title else title
    candidates: list[dict[str, str]] = []
    product_core = re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", "", product or "")).strip()
    product_core = re.sub(r"\s+(수입|수출)$", "", product_core).strip()

    def _label_relevant(label: str) -> bool:
        simplified = re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", "", label or "")).strip()
        simplified = re.sub(r"\s+(수입|수출)$", "", simplified).strip()
        if not product_core:
            return True
        if simplified.startswith("("):
            return False
        return simplified == product_core or simplified in product_core or product_core in simplified

    def _canon(label: str) -> list[str]:
        out = []
        for needle, canon in PRODUCT_CANON_RULES:
            if needle in label:
                out.append(canon)
        return out
    for label in [*labels, product, core_title]:
        if label in BAD_HS_LABELS:
            continue
        if label != product and label != core_title and not _label_relevant(label):
            continue
        if not _label_allowed(label, title):
            continue
        candidates.extend(item for item in hs_lookup.get(label, []) if _hs_allowed_for_context(item["hs_code"], label, product, title))
        # Remove all parenthetical content and normalize whitespace
        simplified = re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", "", label)).strip()
        if simplified != label:
            candidates.extend(item for item in hs_lookup.get(simplified, []) if _hs_allowed_for_context(item["hs_code"], simplified, product, title))
        # Strip 수출/수입 suffix (common in matched_hs_codes_json labels)
        no_flow = re.sub(r"\s+(수입|수출)$", "", simplified).strip()
        if no_flow != simplified:
            candidates.extend(item for item in hs_lookup.get(no_flow, []) if _hs_allowed_for_context(item["hs_code"], no_flow, product, title))
        # Remove only geographic scope parentheses
        no_scope = re.sub(
            r"\s*\((전국[^)]*|글로벌[^)]*|중국[^)]*|미국[^)]*|베트남[^)]*|일본[^)]*|대만[^)]*|홍콩[^)]*|핀란드[^)]*|모로코[^)]*|인도네시아[^)]*|[가-힣]+ [가-힣]+(?:시|군|구))\)",
            "",
            label,
        ).strip()
        if no_scope != label:
            candidates.extend(item for item in hs_lookup.get(no_scope, []) if _hs_allowed_for_context(item["hs_code"], no_scope, product, title))
        # canonical keyword-based expansions
        for canon in _canon(label):
            candidates.extend(item for item in hs_lookup.get(canon, []) if _hs_allowed_for_context(item["hs_code"], canon, product, title))
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for item in candidates:
        if item["hs_code"] in seen:
            continue
        seen.add(item["hs_code"])
        unique.append(item)
    return unique


def upsert_company_map(
    conn: sqlite3.Connection,
    *,
    flow_type: str,
    flow_scope: str,
    product: str,
    hs: dict[str, str],
    stock: dict[str, str],
    row: sqlite3.Row,
) -> str:
    sector_name = f"{product} {'수입' if flow_type == 'import' else '수출'}".strip()
    note = (
        f"텔레그램 @BeOn_BeClear 검증 메시지 기반 exact 매핑: {row['message_id']} "
        f"{row['post_url']} / {flow_type}({flow_scope})"
    )
    existing = conn.execute(
        "SELECT id, mapping_status, confidence, flow_type FROM hs_code_company_map WHERE hs_code=? AND stock_code=?",
        (hs["hs_code"], stock["stock_code"]),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE hs_code_company_map
            SET hs_name=?,
                stock_name=?,
                sector_name=?,
                flow_type=?,
                mapping_status='exact',
                confidence=MAX(COALESCE(confidence, 0), 0.95),
                note=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (hs["hs_name"], stock["stock_name"], sector_name, flow_type, note, existing["id"]),
        )
        return "updated"
    conn.execute(
        """
        INSERT INTO hs_code_company_map (
            hs_code, hs_name, stock_code, stock_name, sector_name,
            match_type, mapping_status, confidence, note, flow_type
        )
        VALUES (?, ?, ?, ?, ?, 'company', 'exact', 0.95, ?, ?)
        """,
        (
            hs["hs_code"],
            hs["hs_name"],
            stock["stock_code"],
            stock["stock_name"],
            sector_name,
            note,
            flow_type,
        ),
    )
    return "inserted"


def rebuild() -> dict[str, object]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    stock_lookup = load_stock_lookup()
    hs_lookup = load_hs_lookup(conn)
    conn.execute("DELETE FROM telegram_company_hs_flow_map")
    rows = conn.execute(
        """
        SELECT *
        FROM telegram_post_cache
        WHERE raw_text LIKE '수출%'
           OR raw_text LIKE '수입%'
           OR title LIKE '수출%'
           OR title LIKE '수입%'
           OR raw_text LIKE '%수출데이터%'
           OR raw_text LIKE '%수입데이터%'
           OR raw_text LIKE '%수출 데이터%'
           OR raw_text LIKE '%수입 데이터%'
        ORDER BY posted_at DESC
        """
    ).fetchall()
    summary: dict[str, object] = {
        "flow_posts_scanned": len(rows),
        "flow_posts_with_hs_and_company": 0,
        "evidence_rows": 0,
        "inserted": 0,
        "updated": 0,
        "missing_hs": {},
        "missing_company": {},
        "flow_counts": {"export": 0, "import": 0},
    }
    for row in rows:
        flow_type, flow_scope, product = parse_flow_and_product(row["raw_text"], row["title"])
        if not flow_type:
            continue
        summary["flow_counts"][flow_type] = int(summary["flow_counts"].get(flow_type, 0)) + 1
        conn.execute(
            """
            UPDATE telegram_post_cache
            SET trade_flow_type=?, product_title=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (flow_type, product, row["id"]),
        )
        hs_entries = hs_entries_for_post(row, product, hs_lookup)
        companies = parse_companies(row)
        stocks = []
        for company in companies:
            stock = stock_lookup.get(company)
            if stock:
                stocks.append(stock)
            else:
                missing = summary["missing_company"]
                missing[company] = missing.get(company, 0) + 1
        if not hs_entries:
            missing_hs = summary["missing_hs"]
            missing_hs[product] = missing_hs.get(product, 0) + 1
        if not hs_entries or not stocks:
            continue
        summary["flow_posts_with_hs_and_company"] = int(summary["flow_posts_with_hs_and_company"]) + 1

        # 매핑 성공 시 캐시 상태도 즉시 동기화
        conn.execute(
            """
            UPDATE telegram_post_cache
            SET mapping_status='mapped',
                matched_companies_json=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (json.dumps([s["stock_name"] for s in stocks], ensure_ascii=False), row["id"]),
        )
        for hs in hs_entries:
            for stock in stocks:
                result = upsert_company_map(
                    conn,
                    flow_type=flow_type,
                    flow_scope=flow_scope,
                    product=product,
                    hs=hs,
                    stock=stock,
                    row=row,
                )
                summary[result] = int(summary[result]) + 1
                conn.execute(
                    """
                    INSERT INTO telegram_company_hs_flow_map (
                        post_message_id, posted_at, post_url, flow_type, flow_scope, product_title,
                        hs_code, hs_name, stock_code, stock_name, market, mapping_status, confidence, source_note
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'exact', 0.95, ?)
                    ON CONFLICT(post_message_id, flow_type, hs_code, stock_code) DO UPDATE SET
                        posted_at=excluded.posted_at,
                        post_url=excluded.post_url,
                        flow_scope=excluded.flow_scope,
                        product_title=excluded.product_title,
                        hs_name=excluded.hs_name,
                        stock_name=excluded.stock_name,
                        market=excluded.market,
                        mapping_status='exact',
                        confidence=0.95,
                        source_note=excluded.source_note,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        row["message_id"],
                        row["posted_at"],
                        row["post_url"],
                        flow_type,
                        flow_scope,
                        product,
                        hs["hs_code"],
                        hs["hs_name"],
                        stock["stock_code"],
                        stock["stock_name"],
                        stock["market"],
                        f"{row['title']} / {row['post_url']}",
                    ),
                )
                summary["evidence_rows"] = int(summary["evidence_rows"]) + 1
    for key in ("missing_hs", "missing_company"):
        values = summary[key]
        summary[key] = dict(sorted(values.items(), key=lambda item: item[1], reverse=True)[:40])
    # Earlier broad-label bugs could leave exact company mappings behind even after
    # the per-post flow table was rebuilt correctly. Keep only exact rows supported
    # by at least one current Telegram flow evidence row.
    stale_exact_rows = conn.execute(
        """
        SELECT COUNT(*)
        FROM hs_code_company_map m
        WHERE m.mapping_status='exact'
          AND m.note LIKE '텔레그램 @BeOn_BeClear 검증 메시지 기반 exact 매핑:%'
          AND NOT EXISTS (
              SELECT 1 FROM telegram_company_hs_flow_map f
              WHERE f.hs_code=m.hs_code AND f.stock_code=m.stock_code
          )
        """
    ).fetchone()[0]
    conn.execute(
        """
        DELETE FROM hs_code_company_map
        WHERE mapping_status='exact'
          AND note LIKE '텔레그램 @BeOn_BeClear 검증 메시지 기반 exact 매핑:%'
          AND NOT EXISTS (
              SELECT 1 FROM telegram_company_hs_flow_map f
              WHERE f.hs_code=hs_code_company_map.hs_code
                AND f.stock_code=hs_code_company_map.stock_code
          )
        """
    )
    summary["stale_exact_rows_removed"] = int(stale_exact_rows or 0)
    conn.commit()
    export_audit(conn)
    conn.close()
    return summary


def export_audit(conn: sqlite3.Connection) -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / "telegram_company_hs_flow_map.csv"
    rows = conn.execute(
        """
        SELECT posted_at, post_message_id, flow_type, flow_scope, product_title,
               stock_code, stock_name, market, hs_code, hs_name, mapping_status,
               confidence, post_url, source_note
        FROM telegram_company_hs_flow_map
        ORDER BY posted_at DESC, flow_type, product_title, stock_name, hs_code
        """
    ).fetchall()
    headers = [desc[0] for desc in conn.execute(
        """
        SELECT posted_at, post_message_id, flow_type, flow_scope, product_title,
               stock_code, stock_name, market, hs_code, hs_name, mapping_status,
               confidence, post_url, source_note
        FROM telegram_company_hs_flow_map
        LIMIT 0
        """
    ).description]
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(headers)
        writer.writerows(rows)


def main() -> None:
    print(json.dumps(rebuild(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

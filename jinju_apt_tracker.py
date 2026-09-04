import requests
import xml.etree.ElementTree as ET
import datetime
from env_utils import require_env

API_KEY = require_env("PUBLIC_DATA_API_KEY")
ENDPOINT = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"

def fetch_apartment_data():
    # 진주시 법정동 코드: 48170
    lawd_cd = "48170"
    
    # 현재 연월 구하기 (YYYYMM)
    now = datetime.datetime.now()
    deal_ymd = now.strftime("%Y%m")
    
    params = {
        "serviceKey": requests.utils.unquote(API_KEY),
        "LAWD_CD": lawd_cd,
        "DEAL_YMD": deal_ymd,
        "pageNo": "1",
        "numOfRows": "1000"
    }

    try:
        response = requests.get(ENDPOINT, params=params)
        response.raise_for_status()
        return response.text
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None

def parse_xml_to_html(xml_data):
    if not xml_data:
        return "<h1>데이터를 불러오지 못했습니다.</h1>"
    
    try:
        root = ET.fromstring(xml_data)
        
        # resultCode 확인
        header = root.find(".//header")
        if header is not None:
            result_code = header.find("resultCode")
            result_msg = header.find("resultMsg")
            
            if result_code is not None and result_code.text is not None:
                code_text = result_code.text.strip()
                if code_text not in ("00", "0", "200") and result_msg is not None and result_msg.text != "OK":
                    msg_text = result_msg.text if result_msg is not None else "알 수 없는 에러"
                    return f"<h1>API 에러 (코드: {code_text}): {msg_text}</h1><br><textarea style='width:100%; height:300px;'>{xml_data}</textarea>"

        items = root.findall(".//item")
        
        # HTML 뼈대 및 CSS 스타일
        html = """
        <!DOCTYPE html>
        <html lang="ko">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>진주시 아파트 실거래가</title>
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Pretendard:wght@300;400;500;600;700&display=swap');
                
                :root {
                    --primary: #4f46e5;
                    --primary-hover: #4338ca;
                    --bg-color: #f1f5f9;
                    --card-bg: #ffffff;
                    --text-main: #0f172a;
                    --text-light: #64748b;
                    --border: #e2e8f0;
                }
                
                body {
                    font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, Roboto, sans-serif;
                    background-color: var(--bg-color);
                    color: var(--text-main);
                    margin: 0;
                    padding: 40px 20px;
                }
                
                .container {
                    max-width: 1200px;
                    margin: 0 auto;
                }
                
                header {
                    text-align: center;
                    margin-bottom: 40px;
                }
                
                h1 {
                    color: var(--primary);
                    margin-bottom: 10px;
                    font-weight: 700;
                    font-size: 2.5rem;
                    letter-spacing: -0.05em;
                }
                
                .subtitle {
                    color: var(--text-light);
                    font-size: 1.1rem;
                }
                
                /* 필터 영역 스타일 */
                .filter-container {
                    background: var(--card-bg);
                    padding: 24px;
                    border-radius: 16px;
                    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
                    margin-bottom: 30px;
                    display: flex;
                    gap: 20px;
                    align-items: center;
                    border: 1px solid var(--border);
                }
                
                .filter-group {
                    display: flex;
                    flex-direction: column;
                    flex: 1;
                    gap: 8px;
                }
                
                .filter-label {
                    font-size: 0.9rem;
                    font-weight: 600;
                    color: var(--text-main);
                }
                
                select {
                    width: 100%;
                    padding: 12px 16px;
                    border-radius: 8px;
                    border: 1px solid var(--border);
                    font-size: 1rem;
                    font-family: inherit;
                    background-color: #f8fafc;
                    color: var(--text-main);
                    cursor: pointer;
                    transition: border-color 0.2s;
                    appearance: none;
                    background-image: url("data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3e%3cpolyline points='6 9 12 15 18 9'%3e%3c/polyline%3e%3c/svg%3e");
                    background-repeat: no-repeat;
                    background-position: right 1rem center;
                    background-size: 1em;
                }
                
                select:focus {
                    outline: none;
                    border-color: var(--primary);
                    box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.1);
                }
                
                .grid {
                    display: grid;
                    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
                    gap: 24px;
                }
                
                .card {
                    background-color: var(--card-bg);
                    border-radius: 16px;
                    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
                    padding: 28px;
                    transition: all 0.2s ease;
                    border: 1px solid var(--border);
                    display: block; /* 기본적으로 표시 */
                }
                
                .card:hover {
                    transform: translateY(-4px);
                    box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
                    border-color: #cbd5e1;
                }
                
                .apt-name {
                    font-size: 1.25rem;
                    font-weight: 700;
                    margin-bottom: 16px;
                    display: flex;
                    align-items: center;
                    gap: 8px;
                }
                
                .dong-badge {
                    background-color: #e0e7ff;
                    color: var(--primary);
                    padding: 4px 10px;
                    border-radius: 20px;
                    font-size: 0.8rem;
                    font-weight: 600;
                }
                
                .price {
                    font-size: 1.8rem;
                    font-weight: 700;
                    color: #ef4444;
                    margin-bottom: 20px;
                    padding-bottom: 16px;
                    border-bottom: 1px dashed var(--border);
                }
                
                .info-row {
                    display: flex;
                    justify-content: space-between;
                    margin-bottom: 12px;
                    font-size: 0.95rem;
                }
                
                .info-row:last-child {
                    margin-bottom: 0;
                }
                
                .label {
                    color: var(--text-light);
                }
                
                .value {
                    font-weight: 600;
                }
                
                #no-results {
                    display: none;
                    text-align: center;
                    padding: 40px;
                    font-size: 1.2rem;
                    color: var(--text-light);
                    grid-column: 1 / -1;
                    background: var(--card-bg);
                    border-radius: 16px;
                    border: 1px dashed var(--border);
                }
            </style>
        </head>
        <body>
            <div class="container">
                <header>
                    <h1>진주시 아파트 실거래가</h1>
                    <div class="subtitle">마지막 업데이트: """ + datetime.datetime.now().strftime("%Y년 %m월 %d일 %H시 %M분") + """</div>
                </header>
                
                <div class="filter-container">
                    <div class="filter-group">
                        <span class="filter-label">🏢 아파트명</span>
                        <select id="aptFilter">
                            <option value="all">모든 아파트 보기</option>
                        </select>
                    </div>
                    <div class="filter-group">
                        <span class="filter-label">📐 평수 (전용면적)</span>
                        <select id="areaFilter">
                            <option value="all">모든 평수 보기</option>
                        </select>
                    </div>
                </div>

                <div class="grid" id="cardGrid">
        """
        
        if not items:
            html += "<p style='text-align:center; grid-column: 1 / -1; font-size: 1.2rem; color: var(--text-light);'>이번 달 거래 내역이 아직 없습니다.</p>"
            
        for item in items:
            apt_name = item.findtext("aptNm", default="알 수 없음").strip()
            dong = item.findtext("umdNm", default="").strip()
            price = item.findtext("dealAmount", default="0").strip()
            area = item.findtext("excluUseAr", default="0").strip()
            floor = item.findtext("floor", default="0").strip()
            year = item.findtext("dealYear", default="").strip()
            month = item.findtext("dealMonth", default="").strip()
            day = item.findtext("dealDay", default="").strip()
            build_year = item.findtext("buildYear", default="").strip()
            
            # 평수 계산 (1평 = 3.3058제곱미터)
            try:
                area_float = float(area)
                pyung = round(area_float / 3.3058)
                pyung_str = f"{pyung}평"
            except:
                pyung = 0
                pyung_str = f"{area}㎡"
                
            date_str = f"{year}.{month.zfill(2)}.{day.zfill(2)}"
            
            html += f"""
                    <div class="card" data-apt="{apt_name}" data-area="{pyung}">
                        <div class="apt-name"><span class="dong-badge">{dong}</span> {apt_name}</div>
                        <div class="price">{price}만원</div>
                        <div class="info-row">
                            <span class="label">📐 면적</span>
                            <span class="value">{pyung_str} ({area}㎡)</span>
                        </div>
                        <div class="info-row">
                            <span class="label">🏢 층수</span>
                            <span class="value">{floor}층</span>
                        </div>
                        <div class="info-row">
                            <span class="label">🏗️ 건축년도</span>
                            <span class="value">{build_year}년</span>
                        </div>
                        <div class="info-row">
                            <span class="label">📅 거래일자</span>
                            <span class="value">{date_str}</span>
                        </div>
                    </div>
            """
            
        html += """
                    <div id="no-results">조건에 맞는 거래 내역이 없습니다. 😢</div>
                </div>
            </div>

            <script>
                document.addEventListener('DOMContentLoaded', () => {
                    const cards = document.querySelectorAll('.card');
                    if(cards.length === 0) return;
                    
                    const apts = new Set();
                    const areas = new Set();
                    
                    // 데이터 수집
                    cards.forEach(card => {
                        if(card.dataset.apt) apts.add(card.dataset.apt);
                        if(card.dataset.area) areas.add(parseInt(card.dataset.area));
                    });

                    const aptFilter = document.getElementById('aptFilter');
                    const areaFilter = document.getElementById('areaFilter');
                    const noResults = document.getElementById('no-results');

                    // 아파트명 드롭다운 채우기 (가나다순 정렬)
                    Array.from(apts).sort().forEach(apt => {
                        const option = document.createElement('option');
                        option.value = apt;
                        option.textContent = apt;
                        aptFilter.appendChild(option);
                    });

                    // 평수 드롭다운 채우기 (오름차순 정렬)
                    Array.from(areas).sort((a,b)=>a-b).forEach(area => {
                        const option = document.createElement('option');
                        option.value = area;
                        option.textContent = area + '평대';
                        areaFilter.appendChild(option);
                    });

                    // 필터링 기능
                    function applyFilters() {
                        const selectedApt = aptFilter.value;
                        const selectedArea = areaFilter.value;
                        let visibleCount = 0;
                        
                        cards.forEach(card => {
                            const matchApt = selectedApt === 'all' || card.dataset.apt === selectedApt;
                            const matchArea = selectedArea === 'all' || card.dataset.area === selectedArea;
                            
                            if(matchApt && matchArea) {
                                card.style.display = 'block';
                                visibleCount++;
                            } else {
                                card.style.display = 'none';
                            }
                        });
                        
                        if(visibleCount === 0) {
                            noResults.style.display = 'block';
                        } else {
                            noResults.style.display = 'none';
                        }
                    }

                    aptFilter.addEventListener('change', applyFilters);
                    areaFilter.addEventListener('change', applyFilters);
                });
            </script>
        </body>
        </html>
        """
        return html
    except Exception as e:
        print(f"Error parsing XML: {e}")
        return "<h1>데이터 파싱 오류가 발생했습니다.</h1>"

def main():
    print("데이터를 가져오는 중...")
    xml_data = fetch_apartment_data()
    
    if not xml_data:
        print("API 호출에 실패하여 기본 템플릿으로 HTML을 생성합니다.")
        xml_data = "" 
        
    print("데이터 파싱 및 HTML 생성 중...")
    html_content = parse_xml_to_html(xml_data)
    
    output_file = "/Volumes/Realtek_NVME/stock_dashboard/runtime/jinju_apt.html"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"✅ HTML 파일이 성공적으로 생성되었습니다: {output_file}")

if __name__ == "__main__":
    main()

from sqlalchemy.orm import Session
import models, schemas
from datetime import datetime
from fastapi import HTTPException

# 규칙 7: 모든 분석 결과 및 주석은 한글로 작성

class AIAnalyzer:
    """
    재무 데이터와 주가 추세를 결합하여 AI 분석 리포트를 생성하는 클래스
    """

    def __init__(self, db: Session):
        self.db = db

    def generate_stock_report(self, stock_code: str):
        """
        특정 종목의 재무 및 차트 데이터를 분석하여 한글 리포트를 생성합니다.
        항상 models.AIAnalysisReport ORM 객체를 반환하거나, 데이터 부족 시 HTTPException을 발생시킵니다.
        """
        financial = self.db.query(models.FinancialData).filter(
            models.FinancialData.stock_code == stock_code
        ).order_by(models.FinancialData.year.desc(), models.FinancialData.quarter.desc()).first()

        prices = self.db.query(models.PriceHistory).filter(
            models.PriceHistory.stock_code == stock_code
        ).order_by(models.PriceHistory.date.desc()).limit(7).all()

        # [연동 버그 수정 ⑤] 기존에 str을 반환하면 호출측(main.py)에서 .id, .content 접근 시
        # AttributeError가 발생함. 데이터 부족은 404로 명확히 처리.
        if not financial or not prices:
            raise HTTPException(
                status_code=404,
                detail=f"[{stock_code}] 분석을 위한 데이터가 충분하지 않습니다. 데이터 수집 후 재시도하세요."
            )

        recent_close = prices[0].close
        start_close = prices[-1].close
        price_change = ((recent_close - start_close) / start_close) * 100 if start_close else 0.0

        revenue_str = f"{financial.revenue / 1e8:,.0f}억원" if financial.revenue else "N/A"
        profit_str = f"{financial.operating_profit / 1e8:,.0f}억원" if financial.operating_profit else "N/A"

        trend = "상승" if price_change > 0 else "하락"
        analysis_content = f"""[종목 분석 리포트: {stock_code}]
- 최근 재무: 매출 {revenue_str}, 영업이익 {profit_str} (전분기 대비 분석 필요)
- 주가 추세: 최근 7일간 {price_change:+.2f}% {trend} 중 (현재가: {recent_close:,.0f}원)
- 종합 의견:
  재무 건전성이 유지되는 가운데 주가가 {trend}세를 보이고 있습니다.
  기술적으로는 거래량 변화를 주시하며 분기별 성장 지속 여부를 확인할 필요가 있습니다."""

        new_report = models.AIAnalysisReport(
            stock_code=stock_code,
            report_date=datetime.now(),
            content=analysis_content.strip()
        )
        self.db.add(new_report)
        self.db.commit()
        self.db.refresh(new_report)

        return new_report  # 항상 ORM 객체 반환

    def get_latest_report(self, stock_code: str):
        """
        최근 생성된 리포트를 조회합니다. 없으면 None 반환.
        """
        return self.db.query(models.AIAnalysisReport).filter(
            models.AIAnalysisReport.stock_code == stock_code
        ).order_by(models.AIAnalysisReport.report_date.desc()).first()

    def general_query(self, query: str):
        """
        자연어 질문에 대해 데이터를 기반으로 답변합니다.
        """
        from ticker_utils import ticker_mapper

        words = query.split()
        target_stock = None
        for word in words:
            code = ticker_mapper.get_code(word)
            if code:
                target_stock = {"name": word, "code": code}
                break

        if not target_stock:
            return "질문하신 내용에서 종목명을 찾을 수 없습니다. (예: 삼성전자 영업이익 어때?)"

        financial = self.db.query(models.FinancialData).filter(
            models.FinancialData.stock_code == target_stock["code"]
        ).order_by(models.FinancialData.year.desc(), models.FinancialData.quarter.desc()).first()

        if not financial:
            return f"'{target_stock['name']}'에 대한 최신 재무 데이터가 아직 수집되지 않았습니다."

        if "영업이익" in query or "돈" in query:
            return f"{target_stock['name']}의 최신 영업이익은 약 {financial.operating_profit / 1e8:,.0f}억원입니다."
        elif "매출" in query:
            return f"{target_stock['name']}의 최신 매출액은 약 {financial.revenue / 1e8:,.0f}억원입니다."
        else:
            report = self.get_latest_report(target_stock["code"])
            if report:
                return f"[{target_stock['name']} 분석 요약] {report.content[:100]}..."
            else:
                return f"'{target_stock['name']}'에 대해 구체적으로 무엇이 궁금하신가요? (예: 매출, 영업이익 등)"

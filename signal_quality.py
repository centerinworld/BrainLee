"""Explainable quality score for actionable stock signals."""
from __future__ import annotations
import math

def score_signal(*, expected_return_pct, downside_pct, positive_rate_pct, sample_count,
                 regime_suitability=0, data_quality=1, lookahead_violations=0,
                 dilution_risk=False, trading_restriction=False):
    sample_conf=min(1,math.log1p(max(0,sample_count))/math.log(501))
    edge=max(-1,min(1,expected_return_pct/30))
    downside=max(0,min(1,abs(min(0,downside_pct))/40))
    hit=max(-1,min(1,(positive_rate_pct-50)/30))
    regime=max(-1,min(1,regime_suitability))
    confidence=100*sample_conf*max(0,min(1,data_quality))
    raw=50+25*edge+12*hit+8*regime-20*downside
    penalties=[]
    if lookahead_violations: raw-=40;penalties.append('lookahead')
    if dilution_risk: raw-=18;penalties.append('dilution')
    if trading_restriction: raw-=50;penalties.append('trading_restriction')
    quality=max(0,min(100,raw*(.55+.45*sample_conf)*max(.4,data_quality)))
    action='buy_candidate' if quality>=72 and confidence>=55 else 'watch' if quality>=52 else 'avoid'
    return {'quality_score':round(quality,1),'confidence_score':round(confidence,1),'action':action,
            'expected_return_pct':expected_return_pct,'downside_pct':downside_pct,
            'positive_rate_pct':positive_rate_pct,'sample_count':sample_count,
            'regime_suitability':regime,'data_quality':data_quality,'penalties':penalties}

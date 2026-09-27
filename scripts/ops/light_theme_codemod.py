#!/usr/bin/env python3
"""light_theme_codemod.py — 프런트 인라인 다크 팔레트를 라이트 팔레트로 일괄 변환 (2026-09-27, 사이트 전면 개편).

기존 화면은 다크 배경 전제의 파스텔(Tailwind 300~400 단계) 색을 인라인 스타일에 직접 쓴다(예: color:'#f87171', background:'rgba(255,255,255,0.05)').
흰 배경에서는 대비가 무너지므로 (1) 밝은 오버레이 → 잉크 오버레이, (2) 파스텔 강조색 → 600~700 단계, (3) 어두운 표면 → 흰색/연회색,
(4) 밝은 글자색 → 진한 글자색으로 바꾼다. 컨텍스트가 필요한 규칙(배경 hex, 글자색 hex)은 속성 이름을 보고 적용한다.

  python3 scripts/ops/light_theme_codemod.py [--dry-run] [파일...]     (기본: frontend/src 의 App.jsx·views·기타 jsx, hub/ 제외)

변환은 한 방향(다크→라이트)이며 멱등이 아니다 — 파일 첫 줄의 마커 주석(pass1/pass2)으로 이미 적용된 단계는 건너뛴다(git diff로 검토·되돌리기).
pass2 = 실측(대비 검사)으로 드러난 잔여분: 삼항식 속 밝은 회색 글자, 희미한 잉크 알파 글자, 남은 파스텔 글자색, 앰버 글자 대비.
pass7 = 남은 어두운 배경 hex(휘도<0.03, 예 #080c14)를 배경 컨텍스트에서 연한 페이지색으로.
pass6 = 글자 진하게(사용자 요청 '회색이라 안 보임'): 회색 계열 글자 hex 를 한 단계씩 어둡게, 잉크 알파 글자 하한 0.88.
pass5 = 구분선 가시성(사용자 요청): 인라인 border 의 잉크 알파 하한 0.2 (기존 0.04~0.1은 흰 배경에서 안 보임).
pass4 = 3차 실측 잔여(삼항식 속 어두운 배경 hex, 파스텔 rgba 글자, 남은 라임·시안 계열, 강조 rgba 알파 하한).
pass3 = 2차 실측: 임의의 near-black 반투명 배경(rgba(10,10,22,.97) 등) → 흰색, 삼항식 속 희미한 잉크·슬레이트 글자 알파 하한, 강조 글자색 한 단계 더 진하게(틴트 배경 위 대비).
"""
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
MARK = "/* light-theme-codemod-2026-09-27 */"
MARK2 = "/* light-theme-codemod-pass2-2026-09-27 */"
MARK3 = "/* light-theme-codemod-pass3-2026-09-27 */"
MARK4 = "/* light-theme-codemod-pass4-2026-09-27 */"
MARK5 = "/* light-theme-codemod-pass5-2026-09-27 */"
MARK6 = "/* light-theme-codemod-pass6-2026-09-27 */"
MARK7 = "/* light-theme-codemod-pass7-2026-09-27 */"

# ── rgba 기준색 매핑 (파스텔 → 600 단계). 어두운 표면은 별도 규칙 ─────────────────────────────
RGBA_ACCENT = {
    "45,212,191": "37,99,235",    # teal-400  → primary blue (legacy --accent-mint 를 primary 로 통일)
    "239,68,68": "220,38,38", "248,113,113": "220,38,38",
    "251,191,36": "217,119,6", "245,158,11": "217,119,6",
    "34,197,94": "22,163,74", "74,222,128": "22,163,74",
    "148,163,184": "100,116,139",
    "99,102,241": "79,70,229",
    "59,130,246": "37,99,235", "96,165,250": "37,99,235",
    "167,139,250": "124,58,237", "139,92,246": "124,58,237",
    "52,211,153": "5,150,105", "16,185,129": "5,150,105", "110,231,183": "5,150,105",
    "56,189,248": "2,132,199",
    "250,204,21": "202,138,4",
    "249,115,22": "234,88,12", "251,146,60": "234,88,12",
    "20,184,166": "13,148,136",
}
RGBA_DARK_SURFACE = {   # 어두운 패널/드롭다운 배경 → 흰색 계열 (알파 유지)
    "15,15,25": "255,255,255", "15,23,42": "255,255,255", "17,24,39": "255,255,255",
    "11,15,26": "255,255,255", "9,12,22": "255,255,255", "30,41,59": "248,250,252",
    "8,12,24": "255,255,255", "10,15,29": "255,255,255", "18,26,47": "255,255,255",
}

# ── hex 강조색 매핑 (전역 — 문자열 어디에 있든 동일 의미의 강조색) ───────────────────────────────
HEX_ACCENT = {
    "#94a3b8": "#64748b", "#64748b": "#475569",
    "#f87171": "#dc2626", "#ef4444": "#dc2626", "#fca5a5": "#b91c1c", "#fecaca": "#b91c1c", "#fb7185": "#e11d48", "#fda4af": "#be123c",
    "#fbbf24": "#d97706", "#f59e0b": "#d97706", "#facc15": "#ca8a04", "#fcd34d": "#b45309", "#fde68a": "#a16207",
    "#34d399": "#059669", "#22c55e": "#16a34a", "#4ade80": "#16a34a", "#10b981": "#059669", "#6ee7b7": "#059669", "#86efac": "#15803d", "#a7f3d0": "#047857",
    "#60a5fa": "#2563eb", "#3b82f6": "#2563eb", "#93c5fd": "#3b82f6", "#bfdbfe": "#3b82f6", "#7dd3fc": "#0284c7", "#38bdf8": "#0284c7",
    "#2dd4bf": "#2563eb", "#5eead4": "#0d9488", "#67e8f9": "#0891b2", "#22d3ee": "#0891b2",
    "#a78bfa": "#7c3aed", "#c4b5fd": "#6d28d9", "#a5b4fc": "#4f46e5", "#818cf8": "#4f46e5", "#c084fc": "#9333ea", "#a855f7": "#9333ea", "#6366f1": "#4f46e5", "#c7d2fe": "#6366f1",
    "#f97316": "#ea580c", "#fb923c": "#ea580c", "#fdba74": "#c2410c",
    "#f472b6": "#db2777",
}
DARK_HEX_BG = {"#0f172a", "#1e293b", "#111827", "#1a1a2e", "#0d1320", "#172033", "#1f2937", "#0b0f1a", "#0a0e1a", "#0f1220", "#131a2b", "#000", "#000000"}
SOFT_DARK_HEX = {"#1e293b", "#1f2937"}    # 카드형 → 연회색, 나머지 → 흰색
LIGHT_TEXT = {"#e2e8f0": "#1e293b", "#f1f5f9": "#0f172a", "#f8fafc": "#0f172a", "#cbd5e1": "#475569", "#e5e7eb": "#374151", "#d1d5db": "#4b5563", "#f9fafb": "#0f172a"}
WHITES = {"#fff", "#ffffff", "white"}


def convert_file(text: str) -> str:
    # 보호 토큰: 어두운 표면에서 만든 흰색은 잉크로 되돌리지 않는다
    def dark_to_protected(m):
        base = re.sub(r"\s+", "", m.group(1))
        return f"rgba(@@{RGBA_DARK_SURFACE[base].replace(',', '_')}@@,{m.group(2)}" if base in RGBA_DARK_SURFACE else m.group(0)
    text = re.sub(r"rgba\(\s*(\d+\s*,\s*\d+\s*,\s*\d+)\s*,(\s*)", dark_to_protected, text)

    def accent(m):
        base = re.sub(r"\s+", "", m.group(1))
        return f"rgba({RGBA_ACCENT[base]},{m.group(2)}" if base in RGBA_ACCENT else m.group(0)
    text = re.sub(r"rgba\(\s*(\d+\s*,\s*\d+\s*,\s*\d+)\s*,(\s*)", accent, text)

    # 흰색 오버레이 → 잉크
    text = re.sub(r"rgba\(\s*255\s*,\s*255\s*,\s*255\s*,", "rgba(15,23,42,", text)
    # 보호 토큰 복원
    text = re.sub(r"rgba\(@@(\d+)_(\d+)_(\d+)@@,", r"rgba(\1,\2,\3,", text)

    # 검은 그림자는 흰 배경에서 과하게 무거우므로 알파를 낮춘다 (box-shadow 형태만)
    text = re.sub(r"(\d+px\s+-?\d+px\s+\d+px\s+(?:-?\d+px\s+)?)rgba\(\s*0\s*,\s*0\s*,\s*0\s*,\s*([0-9.]+)\s*\)", lambda m: f"{m.group(1)}rgba(0,0,0,{round(float(m.group(2)) * 0.35, 3):g})", text)

    # 컨텍스트 규칙 A: 배경으로 쓰인 어두운 hex → 흰색/연회색
    def bg_dark(m):
        hx = m.group(2).lower()
        if hx in DARK_HEX_BG and hx not in ("#000", "#000000"):
            return f"{m.group(1)}{'#f8fafc' if hx in SOFT_DARK_HEX else '#ffffff'}"
        return m.group(0)
    text = re.sub(r"(\b(?:background|backgroundColor|background-color)\s*[:=]\s*\{?\s*['\"`])(#[0-9a-fA-F]{3,6})\b", bg_dark, text)
    # 어두운 테두리(solid #1e293b 등) → 연한 선
    text = re.sub(r"(solid\s+)(#1e293b|#334155|#2b3240|#3a4457|#1f2937|#1a1a2e)\b", r"\1#e2e8f0", text, flags=re.I)

    # 컨텍스트 규칙 B: 글자색으로 쓰인 밝은 hex → 진한 글자색. #fff는 같은 줄에 채워진 강조색 배경이 있으면 유지.
    def text_color(m):
        hx = m.group(2).lower()
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        line = text[line_start: line_end if line_end != -1 else len(text)]
        if hx in WHITES:
            solid_bg = re.search(r"background(?:Color)?\s*[:=]\s*\{?\s*['\"`]?(?:#[0-9a-fA-F]{3,6}|linear-gradient|var\(--accent)", line)
            if solid_bg and not re.search(r"background(?:Color)?\s*[:=]\s*\{?\s*['\"`]?#(?:0f172a|1e293b|111827|f8fafc|ffffff|fff)\b", line, re.I):
                return m.group(0)
            return f"{m.group(1)}var(--text-primary)"
        if hx in LIGHT_TEXT:
            return f"{m.group(1)}{LIGHT_TEXT[hx]}"
        return m.group(0)
    text = re.sub(r"(\b(?:color|fill|stroke)\s*[:=]\s*\{?\s*['\"`])(#[0-9a-fA-F]{3,6}|white)(?=['\"`])", text_color, text)

    # 전역: 강조 hex
    def hexrep(m):
        return HEX_ACCENT.get(m.group(0).lower(), m.group(0))
    text = re.sub(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", hexrep, text)
    return text


# ── pass2: 대비 자동 검사(브라우저)에서 드러난 잔여분 ─────────────────────────────────────────────
PASTEL_TEXT = {"#d1fae5": "#047857", "#dcfce7": "#15803d", "#ddd6fe": "#6d28d9", "#e9d5ff": "#7e22ce", "#d8b4fe": "#7e22ce", "#f9a8d4": "#be185d",
               "#99f6e4": "#0f766e", "#cffafe": "#0e7490", "#e0f2fe": "#0369a1", "#dbeafe": "#1d4ed8", "#ffe4e6": "#be123c", "#fef3c7": "#92400e",
               "#fde047": "#a16207", "#f3f4f6": "#374151"}
LIGHT_NEUTRAL = {"#e2e8f0": "#1e293b", "#f1f5f9": "#0f172a", "#cbd5e1": "#475569", "#e5e7eb": "#374151"}
SURFACE_WORDS = re.compile(r"background|border|solid|shadow|outline|Shadow|gradient", re.I)


def convert_pass2(text: str) -> str:
    # 남은 파스텔 hex 글자색 (color: 컨텍스트)
    text = re.sub(r"(\bcolor\s*:\s*['\"`])(#[0-9a-fA-F]{6})(?=['\"`])", lambda m: m.group(1) + PASTEL_TEXT.get(m.group(2).lower(), m.group(2)), text)

    # 삼항식·상수 속 밝은 회색: 같은 줄에서 hex 바로 앞 60자에 배경·테두리 단서가 없으면 글자/선 색으로 보고 진하게
    def neutral(m):
        before = m.string[max(m.start() - 60, m.string.rfind("\n", 0, m.start()) + 1): m.start()]
        return m.group(0) if SURFACE_WORDS.search(before) else LIGHT_NEUTRAL[m.group(0).lower()]
    text = re.sub(r"#(?:e2e8f0|f1f5f9|cbd5e1|e5e7eb)\b", neutral, text, flags=re.I)

    # 글자색으로 쓰인 앰버는 흰 배경·연한 앰버 틴트 위에서 대비 3:1 미만 → 700 단계
    text = re.sub(r"(\bcolor\s*:\s*['\"`])#d97706(?=['\"`])", r"\1#b45309", text)
    text = re.sub(r"(['\"`])#d97706(['\"`])(\s*:\s*['\"`]#[0-9a-fA-F]{6}['\"`])", r"\1#b45309\2\3", text)  # 삼항식의 참 분기
    text = re.sub(r"(\?\s*['\"`])#d97706(['\"`])", r"\1#b45309\2", text)

    # 희미한 잉크 알파 글자(다크 시절 rgba(255,255,255,0.25~0.5) 글자)는 흰 배경에서 읽히지 않는다 → 알파 하한 0.6
    def faint(m):
        a = float(m.group(2))
        return f"{m.group(1)}rgba(15,23,42,{max(a, 0.6):g})" if a < 0.6 else m.group(0)
    text = re.sub(r"(\bcolor\s*:\s*['\"`])rgba\(15,23,42,([0-9.]+)\)", faint, text)
    return text


# ── pass3: 2차 대비 검사 잔여분 ──────────────────────────────────────────────────────────────
DARKEN = {"#d97706": "#b45309", "#16a34a": "#15803d", "#059669": "#047857", "#3b82f6": "#2563eb", "#ca8a04": "#a16207",
          "#9ca3af": "#6b7280", "#a7b0bf": "#6b7280", "#c7ceda": "#6b7280"}


def convert_pass3(text: str) -> str:
    # 배경으로 쓰인 임의의 near-black 반투명색(알파 ≥ .5) → 흰색. 잉크색(15,23,42)·순수 검정은 제외
    def dark_bg(m):
        r, g, b = int(m.group(2)), int(m.group(3)), int(m.group(4))
        if max(r, g, b) <= 45 and (r, g, b) not in ((15, 23, 42), (0, 0, 0)):
            return f"{m.group(1)}rgba(255,255,255,{m.group(5)})"
        return m.group(0)
    text = re.sub(r"(\bbackground(?:Color)?\s*:\s*[^,;{}]*?)rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(0?\.[5-9]\d*|1)\s*\)", dark_bg, text)

    # 글자색 표현식(삼항 포함) 속 희미한 잉크·슬레이트 알파 → 하한
    def raise_alpha(m):
        a = float(m.group(3))
        floor = 0.6 if m.group(2).startswith("15,23,42") else 0.85
        return f"{m.group(1)}rgba({m.group(2)},{max(a, floor):g})" if a < floor else m.group(0)
    text = re.sub(r"(\bcolor\s*:\s*[^,{}\n]*?['\"`])rgba\((15,23,42|100,116,139),([0-9.]+)\)", raise_alpha, text)

    # 강조 글자색 한 단계 더 진하게 (연한 틴트 배경 위에서도 4:1 안팎)
    text = re.sub(r"#[0-9a-fA-F]{6}\b", lambda m: DARKEN.get(m.group(0).lower(), m.group(0)), text)
    return text


# ── pass4: 3차 대비 검사 잔여분 ──────────────────────────────────────────────────────────────
DARK_BG_TERNARY = {"#1f2937", "#374151", "#334155", "#111827", "#0f172a", "#1e293b", "#1a1a2e", "#0d1320", "#172033"}
EXTRA_MAP = {"#a3e635": "#65a30d", "#84cc16": "#4d7c0f", "#0ea5e9": "#0284c7", "#06b6d4": "#0891b2", "#e879f9": "#a21caf",
             "#ff6b6b": "#dc2626", "#ff4d4f": "#dc2626", "#ea580c": "#c2410c"}
ACCENT_RGB = {"220,38,38", "5,150,105", "4,120,87", "217,119,6", "180,83,9", "37,99,235", "124,58,237", "22,163,74", "234,88,12", "2,132,199", "13,148,136", "79,70,229"}


def convert_pass4(text: str) -> str:
    # 삼항식 속 어두운 배경 hex: 같은 줄에서 가장 가까운 속성이 background 이면 연한 배경으로
    def ternary_bg(m):
        line_start = m.string.rfind("\n", 0, m.start()) + 1
        before = m.string[max(line_start, m.start() - 90): m.start()]
        bi, ci = before.lower().rfind("background"), max(before.rfind("color:"), before.rfind("Color:"))
        return "#f1f5f9" if m.group(0).lower() in DARK_BG_TERNARY and bi > ci and bi >= 0 else m.group(0)
    text = re.sub(r"#[0-9a-fA-F]{6}\b", ternary_bg, text)

    # 파스텔 rgba 글자(밝은 파랑·앰버 등)는 진한 색으로
    def pastel_text(m):
        r, g, b, a = int(m.group(2)), int(m.group(3)), int(m.group(4)), m.group(5)
        if min(r, g, b) < 140 and not (r > 200 and g > 150 and b < 150):
            return m.group(0)
        if b > r:
            return f"{m.group(1)}rgba(29,78,216,{max(float(a), 0.9):g})"
        if r > g >= b:
            return f"{m.group(1)}rgba(180,83,9,{max(float(a), 0.9):g})"
        return f"{m.group(1)}rgba(15,23,42,0.75)"
    text = re.sub(r"(\bcolor\s*:\s*[^,{}\n]*?['\"`])rgba\((\d+),(\d+),(\d+),([0-9.]+)\)", pastel_text, text)

    # 슬레이트·강조 rgba 글자 알파 하한
    def floor(m):
        base = m.group(2)
        a = float(m.group(3))
        if base == "100,116,139":
            return f"{m.group(1)}#475569"
        return f"{m.group(1)}rgba({base},{max(a, 0.9):g})" if base in ACCENT_RGB and a < 0.9 else m.group(0)
    text = re.sub(r"(\bcolor\s*:\s*[^,{}\n]*?['\"`])rgba\((\d+,\d+,\d+),([0-9.]+)\)", floor, text)

    return re.sub(r"#[0-9a-fA-F]{6}\b", lambda m: EXTRA_MAP.get(m.group(0).lower(), m.group(0)), text)


# ── pass5: 구분선 가시성 ─────────────────────────────────────────────────────────────────────
def convert_pass5(text: str) -> str:
    def border(m):
        a = float(m.group(2))
        return f"{m.group(1)}rgba(15,23,42,{max(a, 0.2):g})" if a < 0.2 else m.group(0)
    return re.sub(r"(\bborder\w*\s*:\s*[^,{}\n]*?)rgba\(15,23,42,([0-9.]+)\)", border, text)


# ── pass6: 글자 진하게 ───────────────────────────────────────────────────────────────────────
DARKER_TEXT = {"#64748b": "#334155", "#475569": "#1e293b", "#6b7280": "#374151", "#94a3b8": "#475569", "#9ca3af": "#4b5563", "#a1a1aa": "#52525b"}


def convert_pass6(text: str) -> str:
    text = re.sub(r"#[0-9a-fA-F]{6}\b", lambda m: DARKER_TEXT.get(m.group(0).lower(), m.group(0)), text)
    def ink(m):
        a = float(m.group(2))
        return f"{m.group(1)}rgba(15,23,42,{max(a, 0.88):g})" if a < 0.88 else m.group(0)
    text = re.sub(r"(\bcolor\s*:\s*[^,{}\n]*?['\"`])rgba\(15,23,42,([0-9.]+)\)", ink, text)
    return re.sub(r"(\bcolor\s*:\s*[^,{}\n]*?['\"`])rgba\(100,116,139,[0-9.]+\)", r"\1#334155", text)


# ── pass7: 남은 어두운 배경 ──────────────────────────────────────────────────────────────────
def _lum(h: str) -> float:
    h = h.lstrip("#")
    h = "".join(c * 2 for c in h) if len(h) == 3 else h
    r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def convert_pass7(text: str) -> str:
    def dark_bg(m):
        hx = m.group(2)
        return f"{m.group(1)}#f4f6fb" if hx.lower() not in ("#000", "#000000") and _lum(hx) < 0.03 else m.group(0)
    # background: '#080c14'  /  background: cond ? '#0a0a14' : ...   (같은 줄에서 color 보다 background 가 더 가까운 경우)
    text = re.sub(r"(\bbackground(?:Color)?\s*[:=]\s*\{?\s*['\"`])(#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3})(?=['\"`])", dark_bg, text)
    def ternary(m):
        line_start = m.string.rfind("\n", 0, m.start()) + 1
        before = m.string[max(line_start, m.start() - 90): m.start()]
        bi, ci = before.lower().rfind("background"), max(before.rfind("color:"), before.rfind("Color:"))
        return "#f4f6fb" if bi > ci >= -1 and bi >= 0 and _lum(m.group(0)) < 0.03 and m.group(0).lower() not in ("#000000",) else m.group(0)
    return re.sub(r"#[0-9a-fA-F]{6}\b", ternary, text)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    files = [Path(a) for a in args] or sorted({p for p in [*(SRC / "views").glob("*.jsx"), *SRC.glob("*.jsx")] if p.name != "main.jsx"})
    for f in files:
        text = f.read_text()
        out, marks, body = text, "", text
        for mark, fn in ((MARK, convert_file), (MARK2, convert_pass2), (MARK3, convert_pass3), (MARK4, convert_pass4), (MARK5, convert_pass5), (MARK6, convert_pass6), (MARK7, convert_pass7)):
            body = body.replace(mark + "\n", "", 1)
            out = out.replace(mark + "\n", "", 1)
            if mark not in text:
                out = fn(out)
            marks += mark + "\n"
        changed = sum(1 for a, b in zip(body.splitlines(), out.splitlines()) if a != b)
        print(f"{f.name}: 변경된 줄 {changed}")
        if not dry and (marks + out) != text:
            f.write_text(marks + out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

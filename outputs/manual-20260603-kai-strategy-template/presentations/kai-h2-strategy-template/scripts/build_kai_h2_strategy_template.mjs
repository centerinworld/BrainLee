import pptxgen from "file:///Users/brainlee/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/pptxgenjs/dist/pptxgen.cjs.js";

const OUT = "/Applications/stock_dashboard/outputs/manual-20260603-kai-strategy-template/presentations/kai-h2-strategy-template/output/KAI_H2_Strategy_Template.pptx";

const COLORS = {
  navy: "0B2F6B",
  blue: "114E96",
  sky: "2D6FBA",
  pale: "EAF1F8",
  pale2: "F4F7FB",
  line: "C8D4E3",
  text: "17324D",
  muted: "62768C",
  red: "A02233",
  green: "2C6E62",
  amber: "C48917",
  white: "FFFFFF",
};

const pptx = new pptxgen();
pptx.layout = "LAYOUT_WIDE";
pptx.author = "OpenAI Codex";
pptx.company = "KAI";
pptx.subject = "KAI H2 strategy meeting template";
pptx.title = "KAI H2 Strategy Template";
pptx.lang = "ko-KR";
pptx.theme = {
  headFontFace: "Aptos Display",
  bodyFontFace: "Aptos",
  lang: "ko-KR",
};

function addMaster() {
  pptx.defineSlideMaster({
    title: "KAI_H2_MASTER",
    background: { color: COLORS.white },
    margin: [0.35, 0.4, 0.3, 0.4],
    slideNumber: {
      x: 12.48,
      y: 7.05,
      w: 0.35,
      h: 0.2,
      align: "center",
      fontFace: "Aptos",
      fontSize: 9,
      color: COLORS.muted,
      margin: 0,
    },
    objects: [
      { rect: { x: 0, y: 0, w: 4.7, h: 0.6, fill: { color: COLORS.navy }, line: { color: COLORS.navy, transparency: 100 } } },
      { rect: { x: 4.7, y: 0, w: 4.95, h: 0.6, fill: { color: COLORS.blue }, line: { color: COLORS.blue, transparency: 100 } } },
      { rect: { x: 9.55, y: -0.02, w: 0.26, h: 0.72, rotate: 28, fill: { color: COLORS.sky, transparency: 10 }, line: { color: COLORS.sky, transparency: 100 } } },
      { rect: { x: 9.85, y: 0, w: 3.48, h: 0.6, fill: { color: COLORS.white }, line: { color: COLORS.white, transparency: 100 } } },
      { line: { x: 0.2, y: 0.69, w: 12.6, h: 0, line: { color: COLORS.blue, width: 1.2 } } },
      { text: { text: "KAI  |  하반기 전략회의", options: { x: 0.45, y: 0.14, w: 3.1, h: 0.2, fontFace: "Aptos", fontSize: 18, bold: true, color: COLORS.white, margin: 0 } } },
      { text: { text: "한국항공우주산업", options: { x: 0.46, y: 0.34, w: 1.65, h: 0.15, fontFace: "Aptos", fontSize: 8, color: "D8E5F5", margin: 0 } } },
      { text: { text: "MASTER EDIT AREA", options: { x: 10.08, y: 0.12, w: 0.9, h: 0.15, fontFace: "Aptos", fontSize: 7, color: "8FA6C3", margin: 0 } } },
      { text: { text: "KAI", options: { x: 11.42, y: 0.1, w: 1.2, h: 0.22, fontFace: "Arial", fontSize: 22, bold: true, italic: true, align: "right", color: COLORS.navy, margin: 0 } } },
      { text: { text: "H2 Strategy Meeting", options: { x: 10.78, y: 0.34, w: 1.84, h: 0.15, fontFace: "Aptos", fontSize: 8, align: "right", color: COLORS.red, margin: 0 } } },
      { placeholder: { text: "", options: { name: "Title", type: "title", x: 0.55, y: 0.95, w: 8.6, h: 0.45, fontFace: "Aptos Display", fontSize: 26, bold: true, color: COLORS.text, margin: 0 } } },
      { placeholder: { text: "", options: { name: "Body", type: "body", x: 0.55, y: 1.45, w: 11.95, h: 5.25, fontFace: "Aptos", fontSize: 12, color: COLORS.text, margin: 0 } } },
    ],
  });
}

function addSectionLabel(slide, label) {
  slide.addText(label, {
    x: 0.56,
    y: 0.77,
    w: 1.65,
    h: 0.16,
    fontFace: "Aptos",
    fontSize: 8,
    bold: true,
    color: COLORS.sky,
    margin: 0,
  });
}

function addTitle(slide, title, subtitle) {
  slide.addText(title, {
    x: 0.56,
    y: 0.98,
    w: 8.9,
    h: 0.36,
    fontFace: "Aptos Display",
    fontSize: 24,
    bold: true,
    color: COLORS.text,
    margin: 0,
  });
  if (subtitle) {
    slide.addText(subtitle, {
      x: 0.58,
      y: 1.34,
      w: 8.8,
      h: 0.18,
      fontFace: "Aptos",
      fontSize: 9,
      color: COLORS.muted,
      margin: 0,
    });
  }
}

function addPlaceholderBox(slide, { x, y, w, h, title, body, fill = COLORS.white, accent = COLORS.sky }) {
  slide.addShape("roundRect", {
    x, y, w, h,
    rectRadius: 0.08,
    fill: { color: fill },
    line: { color: COLORS.line, width: 1, dash: "dash" },
  });
  slide.addShape("rect", {
    x: x + 0.02, y: y + 0.02, w: 0.08, h: h - 0.04,
    fill: { color: accent },
    line: { color: accent, transparency: 100 },
  });
  slide.addText(title, {
    x: x + 0.18, y: y + 0.12, w: w - 0.32, h: 0.18,
    fontFace: "Aptos",
    fontSize: 11,
    bold: true,
    color: COLORS.text,
    margin: 0,
  });
  slide.addText(body, {
    x: x + 0.18, y: y + 0.34, w: w - 0.28, h: h - 0.44,
    fontFace: "Aptos",
    fontSize: 10,
    color: COLORS.muted,
    margin: 0,
    valign: "top",
  });
}

function addTag(slide, text, x, y, w = 1.1, color = COLORS.navy) {
  slide.addShape("roundRect", {
    x, y, w, h: 0.24,
    rectRadius: 0.06,
    fill: { color },
    line: { color, transparency: 100 },
  });
  slide.addText(text, {
    x, y: y + 0.04, w, h: 0.1,
    fontFace: "Aptos",
    fontSize: 8,
    bold: true,
    color: COLORS.white,
    align: "center",
    margin: 0,
  });
}

function addCover() {
  const slide = pptx.addSlide({ masterName: "KAI_H2_MASTER" });
  addSectionLabel(slide, "COVER TEMPLATE");
  slide.addShape("roundRect", {
    x: 0.58, y: 1.1, w: 8.9, h: 2.05,
    rectRadius: 0.08,
    fill: { color: COLORS.pale2 },
    line: { color: COLORS.line, transparency: 70 },
  });
  slide.addText("하반기 전략회의", {
    x: 0.84, y: 1.48, w: 4.2, h: 0.38,
    fontFace: "Aptos Display",
    fontSize: 28,
    bold: true,
    color: COLORS.text,
    margin: 0,
  });
  slide.addText("[본부/사업명 입력]", {
    x: 0.86, y: 1.96, w: 3.0, h: 0.22,
    fontFace: "Aptos",
    fontSize: 14,
    color: COLORS.sky,
    bold: true,
    margin: 0,
  });
  slide.addText("핵심 방향, 투자 우선순위, 실행계획, 리스크 대응을 한 번에 정리하는 표지 템플릿", {
    x: 0.86, y: 2.28, w: 5.8, h: 0.22,
    fontFace: "Aptos",
    fontSize: 10,
    color: COLORS.muted,
    margin: 0,
  });
  addPlaceholderBox(slide, { x: 9.72, y: 1.08, w: 3.0, h: 2.1, title: "표지 사용 가이드", body: "[발표 제목]\n[발표 조직]\n[일자]\n[발표자]" });
  addPlaceholderBox(slide, { x: 0.58, y: 3.5, w: 3.88, h: 2.55, title: "핵심 아젠다", body: "• [아젠다 1]\n• [아젠다 2]\n• [아젠다 3]\n• [아젠다 4]" });
  addPlaceholderBox(slide, { x: 4.67, y: 3.5, w: 3.88, h: 2.55, title: "회의 목적", body: "• [의사결정 포인트]\n• [공유 목적]\n• [보고 범위]" });
  addPlaceholderBox(slide, { x: 8.76, y: 3.5, w: 3.96, h: 2.55, title: "작성 메모", body: "표지는 조직명/회의명 교체 후,\n하단 3개 블록으로 발표 포인트를 요약합니다." });
  slide.addNotes("커버 템플릿: 제목, 조직명, 회의 목적, 핵심 아젠다를 한 장에서 정리하는 시작 슬라이드.");
}

function addAgenda() {
  const slide = pptx.addSlide({ masterName: "KAI_H2_MASTER" });
  addSectionLabel(slide, "AGENDA TEMPLATE");
  addTitle(slide, "회의 Agenda", "전체 발표 흐름과 각 세션의 목적을 요약하는 기본 목차형 템플릿");
  const items = [
    ["01", "경영환경 및 전제", "시장, 정책, 고객, 내부 제약조건 정리"],
    ["02", "사업별 핵심 전략", "성장축, 방어축, 집중영역 정리"],
    ["03", "재무 및 투자 우선순위", "예산, CAPEX, 수익성 관점 연결"],
    ["04", "실행 로드맵", "분기별 주요 마일스톤과 책임조직"],
    ["05", "주요 리스크 대응", "리스크, 선제조치, 의사결정 필요사항"],
  ];
  items.forEach((item, idx) => {
    const y = 1.78 + idx * 0.9;
    slide.addShape("roundRect", {
      x: 0.68, y, w: 0.62, h: 0.52,
      rectRadius: 0.08,
      fill: { color: idx === 0 ? COLORS.navy : COLORS.blue },
      line: { color: COLORS.white, transparency: 100 },
    });
    slide.addText(item[0], {
      x: 0.68, y: y + 0.13, w: 0.62, h: 0.16,
      fontFace: "Aptos",
      fontSize: 12,
      bold: true,
      color: COLORS.white,
      align: "center",
      margin: 0,
    });
    addPlaceholderBox(slide, {
      x: 1.48, y: y - 0.02, w: 11.0, h: 0.56,
      title: item[1], body: item[2], fill: idx % 2 === 0 ? COLORS.white : COLORS.pale2, accent: idx === 0 ? COLORS.navy : COLORS.sky,
    });
  });
  slide.addNotes("Agenda 템플릿: 회의 목차, 세션 제목, 세션 목적을 계단형으로 보여주는 목차 슬라이드.");
}

function addExecutiveSummary() {
  const slide = pptx.addSlide({ masterName: "KAI_H2_MASTER" });
  addSectionLabel(slide, "EXECUTIVE SUMMARY");
  addTitle(slide, "핵심 메시지 요약", "보고 초반에 결론과 요청사항을 먼저 정리하는 임원 보고형 템플릿");
  const cards = [
    [0.58, "핵심 메시지 1", "[가장 중요한 결론]\n수치, 방향, 의미를 3줄 이내로 정리"],
    [4.4, "핵심 메시지 2", "[사업/투자/운영 측면의 두 번째 결론]\n조직 간 정렬 포인트 포함"],
    [8.22, "핵심 메시지 3", "[즉시 실행 또는 경영진 의사결정 필요사항]\n영향도와 urgency 기재"],
  ];
  cards.forEach(([x, title, body], idx) => addPlaceholderBox(slide, { x, y: 1.8, w: 3.55, h: 2.05, title, body, accent: [COLORS.navy, COLORS.sky, COLORS.red][idx] }));
  addPlaceholderBox(slide, {
    x: 0.58, y: 4.1, w: 7.38, h: 1.84,
    title: "근거 / 수치 / 배경",
    body: "• [시장/고객/정책 변화]\n• [내부 성과/원가/개발 진척]\n• [이슈 발생 배경과 영향도]",
    fill: COLORS.pale2,
  });
  addPlaceholderBox(slide, {
    x: 8.18, y: 4.1, w: 4.56, h: 1.84,
    title: "의사결정 요청",
    body: "[승인 필요안]\n[조정 필요안]\n[추가 검토 필요안]",
    fill: COLORS.white,
    accent: COLORS.red,
  });
  slide.addNotes("Executive summary 템플릿: 3대 메시지와 의사결정 요청을 한 장에 정리.");
}

function addPillars() {
  const slide = pptx.addSlide({ masterName: "KAI_H2_MASTER" });
  addSectionLabel(slide, "STRATEGY PILLARS");
  addTitle(slide, "전략 축 / 과제 체계", "3대 전략 축과 각 축별 핵심 과제를 정리하는 구조화 템플릿");
  const pillars = [
    ["성장 축", COLORS.navy, ["[신규 사업 기회]", "[주력 매출 성장 과제]", "[고객/시장 확대 과제]"]],
    ["수익성 축", COLORS.blue, ["[원가 혁신 과제]", "[포트폴리오 조정]", "[개발 효율화 과제]"]],
    ["실행력 축", COLORS.red, ["[조직/인력 과제]", "[협업체계/거버넌스]", "[리스크 관리 체계]"]],
  ];
  pillars.forEach((pillar, idx) => {
    const x = 0.62 + idx * 4.15;
    slide.addShape("roundRect", {
      x, y: 1.82, w: 3.8, h: 4.72,
      rectRadius: 0.08,
      fill: { color: COLORS.white },
      line: { color: COLORS.line, width: 1 },
    });
    slide.addShape("rect", {
      x, y: 1.82, w: 3.8, h: 0.56,
      fill: { color: pillar[1] },
      line: { color: pillar[1], transparency: 100 },
    });
    slide.addText(pillar[0], {
      x, y: 2.01, w: 3.8, h: 0.14,
      fontFace: "Aptos",
      fontSize: 13,
      bold: true,
      color: COLORS.white,
      align: "center",
      margin: 0,
    });
    pillar[2].forEach((item, itemIdx) => {
      addPlaceholderBox(slide, {
        x: x + 0.18, y: 2.62 + itemIdx * 1.22, w: 3.44, h: 0.92,
        title: `과제 ${itemIdx + 1}`,
        body: `${item}\n[정량 KPI / 일정 / 책임조직]`,
        fill: itemIdx % 2 === 0 ? COLORS.pale2 : COLORS.white,
        accent: pillar[1],
      });
    });
  });
  slide.addNotes("전략 축 템플릿: 성장/수익성/실행력 3개 축으로 전략 과제를 구조화.");
}

function addDashboard() {
  const slide = pptx.addSlide({ masterName: "KAI_H2_MASTER" });
  addSectionLabel(slide, "KPI DASHBOARD");
  addTitle(slide, "핵심 지표 Dashboard", "수치 카드와 차트 영역을 함께 배치하는 임원용 KPI 템플릿");
  const kpis = [
    ["수주", "[지표 값]", COLORS.navy],
    ["매출", "[지표 값]", COLORS.blue],
    ["영업이익", "[지표 값]", COLORS.green],
    ["현안 지표", "[지표 값]", COLORS.red],
  ];
  kpis.forEach((kpi, idx) => {
    const x = 0.58 + idx * 3.07;
    slide.addShape("roundRect", {
      x, y: 1.77, w: 2.82, h: 1.06,
      rectRadius: 0.08,
      fill: { color: idx % 2 === 0 ? COLORS.pale2 : COLORS.white },
      line: { color: COLORS.line, width: 1 },
    });
    slide.addText(kpi[0], {
      x: x + 0.18, y: 1.95, w: 1.2, h: 0.14,
      fontFace: "Aptos",
      fontSize: 9,
      bold: true,
      color: COLORS.muted,
      margin: 0,
    });
    slide.addText(kpi[1], {
      x: x + 0.18, y: 2.15, w: 1.4, h: 0.24,
      fontFace: "Aptos Display",
      fontSize: 20,
      bold: true,
      color: kpi[2],
      margin: 0,
    });
    slide.addText("[전년/계획 대비]", {
      x: x + 1.6, y: 2.17, w: 1.0, h: 0.12,
      fontFace: "Aptos",
      fontSize: 7,
      align: "right",
      color: COLORS.muted,
      margin: 0,
    });
  });
  addPlaceholderBox(slide, { x: 0.58, y: 3.12, w: 5.96, h: 2.92, title: "Chart Area A", body: "[수주/매출/생산 추이 차트 삽입]\n[차트 제목]\n[주석/단위]", fill: COLORS.white, accent: COLORS.navy });
  addPlaceholderBox(slide, { x: 6.72, y: 3.12, w: 3.0, h: 2.92, title: "Chart Area B", body: "[원가/개발/납기 지표 차트]\n[보조 KPI]\n[시사점]", fill: COLORS.white, accent: COLORS.sky });
  addPlaceholderBox(slide, { x: 9.93, y: 3.12, w: 2.8, h: 2.92, title: "Insight Rail", body: "• [핵심 인사이트]\n• [변동 원인]\n• [다음 액션]", fill: COLORS.pale2, accent: COLORS.red });
  slide.addNotes("KPI dashboard 템플릿: 상단 4 KPI 카드 + 하단 차트 2개 + 인사이트 레일.");
}

function addRoadmap() {
  const slide = pptx.addSlide({ masterName: "KAI_H2_MASTER" });
  addSectionLabel(slide, "ROADMAP TEMPLATE");
  addTitle(slide, "하반기 실행 Roadmap", "분기별 단계와 책임조직을 함께 보여주는 일정형 템플릿");
  const phases = [
    ["Phase 1", COLORS.navy, 0.7, 2.1],
    ["Phase 2", COLORS.blue, 3.85, 2.1],
    ["Phase 3", COLORS.sky, 7.0, 2.1],
    ["Phase 4", COLORS.red, 10.15, 2.1],
  ];
  phases.forEach((phase) => {
    slide.addShape("chevron", {
      x: phase[2], y: phase[3], w: 2.6, h: 0.74,
      fill: { color: phase[1] },
      line: { color: phase[1], transparency: 100 },
    });
    slide.addText(phase[0], {
      x: phase[2] + 0.24, y: phase[3] + 0.25, w: 1.8, h: 0.12,
      fontFace: "Aptos",
      fontSize: 10,
      bold: true,
      color: COLORS.white,
      margin: 0,
    });
    addPlaceholderBox(slide, {
      x: phase[2], y: 3.05, w: 2.7, h: 2.08,
      title: "[핵심 마일스톤]",
      body: "• [일정 1]\n• [일정 2]\n• [일정 3]",
      fill: COLORS.white,
      accent: phase[1],
    });
  });
  slide.addShape("rect", {
    x: 0.7, y: 5.52, w: 12.0, h: 0.9,
    fill: { color: COLORS.pale2 },
    line: { color: COLORS.line, width: 1 },
  });
  slide.addText("Owner / 협업조직", {
    x: 0.92, y: 5.79, w: 1.7, h: 0.12,
    fontFace: "Aptos",
    fontSize: 9,
    bold: true,
    color: COLORS.text,
    margin: 0,
  });
  ["전략", "사업", "재무", "개발"].forEach((tag, idx) => addTag(slide, tag, 2.3 + idx * 1.25, 5.7, 1.0, [COLORS.navy, COLORS.blue, COLORS.green, COLORS.red][idx]));
  slide.addText("[분기별 책임조직, 주요 협업 부서, 의사결정 게이트를 이 영역에 정리]", {
    x: 7.5, y: 5.78, w: 4.7, h: 0.12,
    fontFace: "Aptos",
    fontSize: 8,
    color: COLORS.muted,
    align: "right",
    margin: 0,
  });
  slide.addNotes("Roadmap 템플릿: 4단계 chevron + 각 단계별 마일스톤 + 하단 owner 영역.");
}

function addIssueRiskAction() {
  const slide = pptx.addSlide({ masterName: "KAI_H2_MASTER" });
  addSectionLabel(slide, "ISSUE / RISK / ACTION");
  addTitle(slide, "이슈 / 리스크 / 대응 과제", "현안 공유와 선제조치를 구조화하는 위험관리형 템플릿");
  const cols = [
    ["Issue", COLORS.navy, 0.58],
    ["Risk", COLORS.red, 4.37],
    ["Action", COLORS.green, 8.16],
  ];
  cols.forEach((col) => {
    slide.addShape("rect", {
      x: col[2], y: 1.82, w: 3.58, h: 0.46,
      fill: { color: col[1] },
      line: { color: col[1], transparency: 100 },
    });
    slide.addText(col[0], {
      x: col[2], y: 1.98, w: 3.58, h: 0.12,
      fontFace: "Aptos",
      fontSize: 12,
      bold: true,
      color: COLORS.white,
      align: "center",
      margin: 0,
    });
    for (let i = 0; i < 3; i += 1) {
      addPlaceholderBox(slide, {
        x: col[2], y: 2.46 + i * 1.33, w: 3.58, h: 1.08,
        title: `[항목 ${i + 1}]`,
        body: "[내용 입력]\n[영향도 / 시기 / 소유조직]",
        fill: i % 2 === 0 ? COLORS.white : COLORS.pale2,
        accent: col[1],
      });
    }
  });
  slide.addNotes("Issue/Risk/Action 템플릿: 이슈, 리스크, 대응과제를 병렬로 보여주는 현안 슬라이드.");
}

function addMatrix() {
  const slide = pptx.addSlide({ masterName: "KAI_H2_MASTER" });
  addSectionLabel(slide, "PRIORITY MATRIX");
  addTitle(slide, "투자 / 과제 우선순위 Matrix", "영향도와 실행난이도를 동시에 설명하는 우선순위 평가형 템플릿");
  slide.addShape("rect", {
    x: 1.0, y: 2.0, w: 7.6, h: 4.5,
    fill: { color: COLORS.white },
    line: { color: COLORS.line, width: 1.2 },
  });
  slide.addShape("line", { x: 4.8, y: 2.0, w: 0, h: 4.5, line: { color: COLORS.line, width: 1 } });
  slide.addShape("line", { x: 1.0, y: 4.25, w: 7.6, h: 0, line: { color: COLORS.line, width: 1 } });
  slide.addText("Impact", {
    x: 0.28, y: 1.9, w: 0.5, h: 0.18, rotate: 270,
    fontFace: "Aptos",
    fontSize: 10,
    bold: true,
    color: COLORS.text,
    margin: 0,
  });
  slide.addText("Execution Difficulty", {
    x: 3.65, y: 6.6, w: 2.4, h: 0.16,
    fontFace: "Aptos",
    fontSize: 10,
    bold: true,
    color: COLORS.text,
    align: "center",
    margin: 0,
  });
  const quadrants = [
    [1.12, 2.12, "Quick Win", COLORS.green],
    [4.93, 2.12, "Strategic Bet", COLORS.navy],
    [1.12, 4.37, "Selective", COLORS.amber],
    [4.93, 4.37, "Defer / Watch", COLORS.red],
  ];
  quadrants.forEach((q) => {
    slide.addText(q[2], {
      x: q[0], y: q[1], w: 1.2, h: 0.12,
      fontFace: "Aptos",
      fontSize: 10,
      bold: true,
      color: q[3],
      margin: 0,
    });
  });
  ["[과제 A]", "[과제 B]", "[과제 C]", "[과제 D]", "[과제 E]"].forEach((label, idx) => {
    const positions = [
      [2.0, 3.0, COLORS.green],
      [5.9, 2.9, COLORS.navy],
      [3.6, 5.1, COLORS.amber],
      [6.4, 5.3, COLORS.red],
      [4.7, 3.8, COLORS.sky],
    ][idx];
    slide.addShape("roundRect", {
      x: positions[0], y: positions[1], w: 1.05, h: 0.34,
      rectRadius: 0.06,
      fill: { color: positions[2], transparency: 8 },
      line: { color: positions[2], width: 1 },
    });
    slide.addText(label, {
      x: positions[0], y: positions[1] + 0.11, w: 1.05, h: 0.1,
      fontFace: "Aptos",
      fontSize: 8,
      bold: true,
      color: positions[2],
      align: "center",
      margin: 0,
    });
  });
  addPlaceholderBox(slide, {
    x: 8.95, y: 2.0, w: 3.82, h: 4.5,
    title: "판단 기준 / 설명",
    body: "• 가로축: [실행난이도 정의]\n• 세로축: [영향도 정의]\n• 점/라벨: [과제명]\n• 우측에는 판단 기준, 예외사항, 의사결정 메모를 기입",
    fill: COLORS.pale2,
    accent: COLORS.navy,
  });
  slide.addNotes("Priority matrix 템플릿: 영향도와 실행난이도 2x2로 우선순위를 정리.");
}

function addWorkstream() {
  const slide = pptx.addSlide({ masterName: "KAI_H2_MASTER" });
  addSectionLabel(slide, "WORKSTREAM ALIGNMENT");
  addTitle(slide, "워크스트림 / 책임조직 정렬", "조직별 실행 항목과 연계관계를 한 장에서 보여주는 실행정렬형 템플릿");
  const lanes = [
    ["사업전략", COLORS.navy],
    ["개발/생산", COLORS.blue],
    ["재무/투자", COLORS.green],
    ["지원/거버넌스", COLORS.red],
  ];
  lanes.forEach((lane, idx) => {
    const y = 1.92 + idx * 1.08;
    slide.addShape("rect", {
      x: 0.58, y, w: 1.45, h: 0.82,
      fill: { color: lane[1] },
      line: { color: lane[1], transparency: 100 },
    });
    slide.addText(lane[0], {
      x: 0.66, y: y + 0.28, w: 1.28, h: 0.12,
      fontFace: "Aptos",
      fontSize: 10,
      bold: true,
      color: COLORS.white,
      align: "center",
      margin: 0,
    });
    for (let i = 0; i < 3; i += 1) {
      addPlaceholderBox(slide, {
        x: 2.22 + i * 3.38, y, w: 3.06, h: 0.82,
        title: `[실행 항목 ${i + 1}]`,
        body: "[담당자 / 일정 / 산출물]",
        fill: i % 2 === 0 ? COLORS.white : COLORS.pale2,
        accent: lane[1],
      });
    }
  });
  slide.addText("각 행은 조직 또는 워크스트림, 각 박스는 핵심 deliverable로 사용합니다.", {
    x: 0.6, y: 6.45, w: 6.0, h: 0.14,
    fontFace: "Aptos",
    fontSize: 8,
    color: COLORS.muted,
    margin: 0,
  });
  addTag(slide, "Owner", 10.0, 6.35, 0.85, COLORS.navy);
  addTag(slide, "Support", 10.95, 6.35, 0.95, COLORS.blue);
  addTag(slide, "Decision", 12.0, 6.35, 0.95, COLORS.red);
  slide.addNotes("Workstream 템플릿: 조직별 실행항목과 owner/support/decision 구분을 정리.");
}

function addAppendix() {
  const slide = pptx.addSlide({ masterName: "KAI_H2_MASTER" });
  addSectionLabel(slide, "APPENDIX DETAIL");
  addTitle(slide, "상세 검토 / 부록", "본문 이후 세부 수치, 근거, 표, 추가 차트를 넣는 상세형 템플릿");
  addPlaceholderBox(slide, {
    x: 0.58, y: 1.82, w: 8.7, h: 4.92,
    title: "Main Content Area",
    body: "[대형 표 / 상세 차트 / 세부 분석 / 도식 삽입]\n\n본문에서 설명하지 못한 근거와 상세자료를 이 영역에 배치합니다.",
    fill: COLORS.white,
    accent: COLORS.navy,
  });
  addPlaceholderBox(slide, {
    x: 9.48, y: 1.82, w: 3.25, h: 2.28,
    title: "Key Notes",
    body: "• [해석 포인트]\n• [제약사항]\n• [전제조건]",
    fill: COLORS.pale2,
    accent: COLORS.red,
  });
  addPlaceholderBox(slide, {
    x: 9.48, y: 4.32, w: 3.25, h: 2.42,
    title: "Source / Footnote",
    body: "[출처]\n[기준시점]\n[단위]\n[비고]",
    fill: COLORS.white,
    accent: COLORS.sky,
  });
  slide.addNotes("Appendix 템플릿: 표/차트 상세자료와 우측 주석 레일을 함께 제공.");
}

addMaster();
addCover();
addAgenda();
addExecutiveSummary();
addPillars();
addDashboard();
addRoadmap();
addIssueRiskAction();
addMatrix();
addWorkstream();
addAppendix();

await pptx.writeFile({ fileName: OUT });
console.log(OUT);

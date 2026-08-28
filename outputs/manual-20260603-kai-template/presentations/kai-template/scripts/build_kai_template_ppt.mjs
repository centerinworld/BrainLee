import pptxgen from "file:///Users/brainlee/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/pptxgenjs/dist/pptxgen.cjs.js";

const bgPath = "/Applications/stock_dashboard/outputs/manual-20260603-kai-template/presentations/kai-template/assets/kai_template_bg.png";
const outPath = "/Applications/stock_dashboard/outputs/manual-20260603-kai-template/presentations/kai-template/output/KAI_16x9_Template_v2.pptx";

const pptx = new pptxgen();
pptx.layout = "LAYOUT_WIDE";
pptx.author = "OpenAI Codex";
pptx.company = "KAI";
pptx.subject = "KAI template";
pptx.title = "KAI 16:9 Template";

const slide = pptx.addSlide();
slide.background = { color: "FFFFFF" };
slide.addImage({ path: bgPath, x: 0, y: 0, w: 13.333, h: 7.5 });

slide.addText("ONE TEAM", {
  x: 11.33,
  y: 0.12,
  w: 1.52,
  h: 0.28,
  fontFace: "Arial",
  fontSize: 19,
  bold: true,
  italic: true,
  color: "0B1F4A",
  align: "right",
  margin: 0,
  fill: { color: "FFFFFF", transparency: 100 },
  line: { color: "FFFFFF", transparency: 100 },
});

slide.addText("ONE KAI", {
  x: 11.40,
  y: 0.41,
  w: 1.43,
  h: 0.26,
  fontFace: "Arial",
  fontSize: 18,
  bold: true,
  italic: true,
  color: "8D1B2D",
  align: "right",
  margin: 0,
  fill: { color: "FFFFFF", transparency: 100 },
  line: { color: "FFFFFF", transparency: 100 },
});

await pptx.writeFile({ fileName: outPath });
console.log(outPath);

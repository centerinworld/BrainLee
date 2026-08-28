import { HorizontalAlignment } from "@oai/artifact-tool";

const SLIDE = { width: 1280, height: 720 };
const BANNER_HEIGHT = 64;

function addRect(slide, config) {
  const shape = slide.shapes.add({});
  shape.position.set({
    left: config.left,
    top: config.top,
    width: config.width,
    height: config.height,
  });
  if (config.rotation) {
    shape.position.rotation = config.rotation;
  }
  if (config.fill) {
    shape.fill.color = config.fill;
  }
  if (config.line === false) {
    shape.line.visible = false;
  } else if (config.line) {
    shape.line.visible = true;
    shape.line.color = config.line.color;
    shape.line.width = config.line.width ?? 1;
  } else {
    shape.line.visible = false;
  }
  if (typeof config.borderRadius === "number") {
    shape.borderRadius = config.borderRadius;
  }
  return shape;
}

function addText(slide, config) {
  const box = slide.shapes.add({});
  box.position.set({
    left: config.left,
    top: config.top,
    width: config.width,
    height: config.height,
  });
  box.fill.color = config.fill ?? "#FFFFFF";
  box.line.visible = false;
  box.text.set(config.text);
  box.text.fontSize = config.fontSize;
  box.text.color = config.color;
  box.text.bold = config.bold ?? false;
  box.text.italic = config.italic ?? false;
  box.text.typeface = config.typeface ?? "Aptos";
  box.text.alignment = config.alignment ?? HorizontalAlignment.left;
  if (config.verticalAlignment) {
    box.text.verticalAlignment = config.verticalAlignment;
  }
  if (config.insets) {
    box.text.insets = config.insets;
  }
  return box;
}

export async function slide01(presentation) {
  const slide = presentation.slides.add();

  addRect(slide, {
    left: 0,
    top: 0,
    width: SLIDE.width,
    height: SLIDE.height,
    fill: "#FFFFFF",
    line: false,
  });

  addRect(slide, {
    left: 18,
    top: 18,
    width: 930,
    height: BANNER_HEIGHT,
    fill: "#0B1F4A",
    line: false,
    borderRadius: 10,
  });

  addRect(slide, {
    left: 470,
    top: 18,
    width: 500,
    height: BANNER_HEIGHT,
    fill: "#0F3F8A",
    line: false,
  });

  addRect(slide, {
    left: 900,
    top: 7,
    width: 65,
    height: 88,
    fill: "#1F4FA0",
    line: false,
    rotation: 28,
  });

  addRect(slide, {
    left: 922,
    top: 12,
    width: 220,
    height: 74,
    fill: "#FFFFFF",
    line: false,
    rotation: -2,
  });

  addRect(slide, {
    left: 945,
    top: 14,
    width: 12,
    height: 56,
    fill: "#D6D8DD",
    line: false,
    rotation: 32,
  });

  addRect(slide, {
    left: 967,
    top: 14,
    width: 6,
    height: 56,
    fill: "#BFC3CA",
    line: false,
    rotation: 32,
  });

  addRect(slide, {
    left: 980,
    top: 14,
    width: 6,
    height: 56,
    fill: "#BFC3CA",
    line: false,
    rotation: 32,
  });

  addRect(slide, {
    left: 18,
    top: 86,
    width: 1244,
    height: 6,
    fill: "#0D3C82",
    line: false,
  });

  addRect(slide, {
    left: 22,
    top: 104,
    width: 1236,
    height: 596,
    fill: "#FFFFFF",
    line: { color: "#D8DDE6", width: 1.25 },
    borderRadius: 8,
  });

  addRect(slide, {
    left: 1046,
    top: 18,
    width: 10,
    height: 34,
    fill: "#B0B4BA",
    line: false,
    rotation: 62,
  });

  addRect(slide, {
    left: 1064,
    top: 10,
    width: 10,
    height: 48,
    fill: "#979CA4",
    line: false,
    rotation: 58,
  });

  addRect(slide, {
    left: 1082,
    top: 19,
    width: 10,
    height: 28,
    fill: "#B0B4BA",
    line: false,
    rotation: 58,
  });

  addText(slide, {
    left: 1116,
    top: 12,
    width: 138,
    height: 30,
    text: "ONE TEAM",
    fontSize: 20,
    color: "#0B1F4A",
    bold: true,
    italic: true,
    typeface: "Arial",
    alignment: HorizontalAlignment.right,
    verticalAlignment: "middle",
    fill: "#FFFFFF",
  });

  addText(slide, {
    left: 1120,
    top: 37,
    width: 134,
    height: 28,
    text: "ONE KAI",
    fontSize: 19,
    color: "#8E1D2C",
    bold: true,
    italic: true,
    typeface: "Arial",
    alignment: HorizontalAlignment.right,
    verticalAlignment: "middle",
    fill: "#FFFFFF",
  });

  return slide;
}

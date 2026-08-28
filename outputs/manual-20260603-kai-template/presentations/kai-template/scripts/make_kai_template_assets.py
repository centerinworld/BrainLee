from PIL import Image, ImageDraw

SRC = "/Users/brainlee/Downloads/KakaoTalk_Photo_2026-06-03-22-18-07.png"
OUT = "/Applications/stock_dashboard/outputs/manual-20260603-kai-template/presentations/kai-template/assets/kai_template_bg.png"
PREVIEW = "/Applications/stock_dashboard/outputs/manual-20260603-kai-template/presentations/kai-template/preview/final/kai_template_preview_v2.png"
FONT = "/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf"


def main():
    img = Image.open(SRC).convert("RGBA")
    draw = ImageDraw.Draw(img)

    # Clear the table/grid area while preserving the outer frame and blue header strip.
    draw.rounded_rectangle(
        (42, 162, 1334, 740),
        radius=4,
        fill=(255, 255, 255, 255),
    )

    # Clear the slogan area so replacement text stays editable in PPT.
    draw.rectangle((1180, 0, 1376, 118), fill=(255, 255, 255, 255))

    img.save(OUT)

    preview = img.copy()
    preview_draw = ImageDraw.Draw(preview)
    from PIL import ImageFont

    font1 = ImageFont.truetype(FONT, 29)
    font2 = ImageFont.truetype(FONT, 28)

    preview_draw.text((1190, 14), "ONE TEAM", font=font1, fill=(11, 31, 74, 255))
    preview_draw.text((1203, 48), "ONE KAI", font=font2, fill=(141, 27, 45, 255))
    preview.save(PREVIEW)


if __name__ == "__main__":
    main()

from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    scale = 4
    size = 256
    image = Image.new("RGBA", (size * scale, size * scale), "#15171c")
    draw = ImageDraw.Draw(image)

    def box(values: tuple[int, int, int, int], **kwargs: object) -> None:
        draw.rounded_rectangle(tuple(value * scale for value in values), **kwargs)

    box((15, 15, 241, 241), radius=38 * scale, fill="#1d2027", outline="#ff7a1a", width=9 * scale)
    candles = [
        (62, 72, 62, 177, 43, 89, 81, 135, "#ef4444"),
        (128, 48, 128, 201, 109, 104, 147, 166, "#22c55e"),
        (194, 78, 194, 184, 175, 119, 213, 158, "#ef4444"),
    ]
    for x1, y1, x2, y2, left, top, right, bottom, color in candles:
        draw.line((x1 * scale, y1 * scale, x2 * scale, y2 * scale), fill=color, width=7 * scale)
        draw.rectangle((left * scale, top * scale, right * scale, bottom * scale), fill=color)

    image = image.resize((size, size), Image.Resampling.LANCZOS)
    image.save(
        Path(__file__).resolve().parent / "app_icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    main()

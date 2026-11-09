"""앱 아이콘 생성 (홈 화면 · 파비콘). 실행: python tools/make_icon.py

결과: app/static/ 에 icon-32/180/192/512.png
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "app" / "static"
S = 2048  # 2배로 그린 뒤 축소 (안티앨리어싱)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw() -> Image.Image:
    img = Image.new("RGB", (S, S))
    top, bottom = (11, 18, 38), (30, 41, 99)
    px = img.load()
    for y in range(S):
        for x in range(0, S):
            t = min(1.0, (0.75 * y + 0.25 * x) / S)
            px[x, y] = lerp(top, bottom, t)
    d = ImageDraw.Draw(img)

    # 옅은 격자
    for i in range(1, 6):
        y = int(S * (0.18 + i * 0.12))
        d.line([(int(S * 0.12), y), (int(S * 0.88), y)], fill=(52, 64, 120), width=6)

    green, red = (34, 197, 94), (239, 68, 68)
    # 캔들 (x 중심, 시가, 종가, 고가, 저가) — 0~1 비율, y 는 아래가 1
    candles = [
        (0.24, 0.74, 0.62, 0.57, 0.80, green),
        (0.40, 0.60, 0.66, 0.55, 0.71, red),
        (0.56, 0.64, 0.46, 0.40, 0.69, green),
        (0.72, 0.47, 0.30, 0.24, 0.52, green),
    ]
    bw = int(S * 0.095)
    for cx, o, c, hi, lo, col in candles:
        x = int(S * cx)
        d.line([(x, int(S * hi)), (x, int(S * lo))], fill=col, width=int(S * 0.016))
        y0, y1 = sorted((int(S * o), int(S * c)))
        d.rounded_rectangle([x - bw // 2, y0, x + bw // 2, y1], radius=int(S * 0.014), fill=col)

    # 상승 화살표 (레버리지 추세선)
    amber = (251, 191, 36)
    pts = [(0.16, 0.84), (0.36, 0.66), (0.50, 0.71), (0.80, 0.30)]
    pts = [(int(S * x), int(S * y)) for x, y in pts]
    d.line(pts, fill=amber, width=int(S * 0.034), joint="curve")
    for p in pts[:-1]:
        r = int(S * 0.017)
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=amber)
    tip = (int(S * 0.86), int(S * 0.22))
    d.polygon([tip, (int(S * 0.73), int(S * 0.27)), (int(S * 0.82), int(S * 0.36))], fill=amber)

    # 3× 배지
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    try:
        font = ImageFont.truetype(font_path, int(S * 0.15))
    except OSError:
        font = ImageFont.load_default()
    d.text((int(S * 0.15), int(S * 0.13)), "3×", font=font, fill=(255, 255, 255))
    return img


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw()
    for size in (32, 180, 192, 512):
        big.resize((size, size), Image.LANCZOS).save(OUT / f"icon-{size}.png", optimize=True)
    print("saved to", OUT)


if __name__ == "__main__":
    main()

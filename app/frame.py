"""Рендер 800x480 карточки погоды для e-ink рамки Spectra 6 (E6).

Слева — текущая погода крупно (иконка, температура, ветер/влажность/давление),
справа — прогноз на 4 дня колонками. Только чистые палитрные цвета,
чтобы прошивка рамки отображала их 1:1 без дизеринга.
"""
from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from weather import describe

W, H = 800, 480
RENDER_VERSION = 1   # менять при правках макета — форсирует перерисовку рамки

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (255, 0, 0)
YELLOW = (255, 255, 0)
GREEN = (0, 160, 70)
BLUE = (0, 70, 200)

_FONTS = {
    True: [   # bold
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",   # dev на macOS
    ],
    False: [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ],
}
_FONT_CACHE: dict = {}


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key not in _FONT_CACHE:
        for path in _FONTS[bold]:
            try:
                _FONT_CACHE[key] = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
        else:
            _FONT_CACHE[key] = ImageFont.load_default(size)
    return _FONT_CACHE[key]


MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def _fmt_temp(t: float) -> str:
    v = round(t)
    return f"{v:+d}°" if v else "0°"


# ---------------------------------------------------------------- иконки
# Рисуем примитивами PIL: сплошные силуэты хорошо читаются на e-ink.

def _sun(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float):
    for i in range(8):
        from math import cos, pi, sin
        a = i * pi / 4
        d.line([cx + cos(a) * r * 1.25, cy + sin(a) * r * 1.25,
                cx + cos(a) * r * 1.75, cy + sin(a) * r * 1.75],
               fill=YELLOW, width=max(3, int(r * 0.22)))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=YELLOW, outline=BLACK, width=2)


def _cloud(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float, fill=BLACK):
    # силуэт из трёх кругов и прямоугольника
    d.ellipse([cx - s * .5, cy - s * .18, cx - s * .06, cy + s * .26], fill=fill)
    d.ellipse([cx - s * .26, cy - s * .38, cx + s * .22, cy + .1 * s], fill=fill)
    d.ellipse([cx + s * .02, cy - s * .2, cx + s * .5, cy + s * .26], fill=fill)
    d.rectangle([cx - s * .28, cy + s * .02, cx + s * .28, cy + s * .26], fill=fill)


def _drops(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float, n=3):
    for i in range(n):
        x = cx + (i - (n - 1) / 2) * s * 0.3
        y = cy + (s * 0.06 if i % 2 else 0)
        d.ellipse([x - s * .05, y, x + s * .05, y + s * .2], fill=BLUE)


def _flakes(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float, n=3):
    from math import cos, pi, sin
    for i in range(n):
        x = cx + (i - (n - 1) / 2) * s * 0.32
        y = cy + s * 0.12 + (s * 0.05 if i % 2 else 0)
        r = s * 0.09
        for k in range(3):
            a = k * pi / 3
            d.line([x - cos(a) * r, y - sin(a) * r, x + cos(a) * r, y + sin(a) * r],
                   fill=BLUE, width=max(2, int(s * .03)))


def _bolt(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float):
    d.polygon([(cx + s * .06, cy - s * .1), (cx - s * .16, cy + s * .18),
               (cx - s * .02, cy + s * .18), (cx - s * .1, cy + s * .44),
               (cx + s * .16, cy + s * .1), (cx + s * .02, cy + s * .1)],
              fill=YELLOW, outline=BLACK)


def _fog(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float):
    for i in range(3):
        y = cy + s * .18 + i * s * .12
        d.line([cx - s * .4, y, cx + s * .4, y], fill=BLACK, width=max(3, int(s * .05)))


def draw_icon(d: ImageDraw.ImageDraw, code: int | None, cx: float, cy: float, s: float):
    """code — WMO weather code, s — размер иконки (сторона квадрата)."""
    c = code or 0
    if c == 0:
        _sun(d, cx, cy, s * 0.3)
    elif c in (1, 2):
        _sun(d, cx - s * .18, cy - s * .18, s * 0.2)
        _cloud(d, cx + s * .08, cy + s * .1, s * 0.75)
    elif c == 3:
        _cloud(d, cx, cy, s * 0.9)
    elif c in (45, 48):
        _cloud(d, cx, cy - s * .12, s * 0.75)
        _fog(d, cx, cy, s)
    elif c in (51, 53, 55, 56, 57):
        _cloud(d, cx, cy - s * .12, s * 0.8)
        _drops(d, cx, cy + s * .18, s * .8, n=2)
    elif c in (61, 63, 65, 66, 67, 80, 81, 82):
        _cloud(d, cx, cy - s * .12, s * 0.8)
        _drops(d, cx, cy + s * .18, s, n=3)
    elif c in (71, 73, 75, 77, 85, 86):
        _cloud(d, cx, cy - s * .14, s * 0.8)
        _flakes(d, cx, cy + s * .2, s)
    elif c >= 95:
        _cloud(d, cx, cy - s * .14, s * 0.8)
        _bolt(d, cx, cy + s * .1, s * .8)
    else:
        _cloud(d, cx, cy, s * 0.9)


# ---------------------------------------------------------------- макет

def _temp_color(t: float):
    if t >= 25:
        return RED
    if t <= 0:
        return BLUE
    return BLACK


def render_frame(data: dict) -> bytes:
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    cur, daily = data["current"], data["daily"]
    upd = datetime.fromisoformat(data["updated"])

    # ── шапка ──
    d.text((24, 18), data["place"], font=_font(34), fill=BLACK)
    date_s = (f"{WEEKDAYS[upd.weekday()]}, {upd.day} {MONTHS[upd.month - 1]}"
              f" · {upd.strftime('%H:%M')}")
    d.text((W - 24, 26), date_s, font=_font(24, False), fill=BLACK, anchor="ra")
    d.line([24, 66, W - 24, 66], fill=BLACK, width=2)

    # ── слева: сейчас ──
    draw_icon(d, cur["code"], 100, 160, 130)
    t = _fmt_temp(cur["temp"])
    d.text((190, 100), t, font=_font(96), fill=_temp_color(cur["temp"]))
    d.text((36, 250), describe(cur["code"]), font=_font(30, False), fill=BLACK)
    d.text((36, 292), f"ощущается {_fmt_temp(cur['feels'])}",
           font=_font(24, False), fill=BLACK)

    rows = [
        ("ветер", f"{cur['wind_ms']:.0f} м/с {cur['wind_dir']}"),
        ("влажность", f"{cur['humidity']:.0f}%"),
        ("давление", f"{cur['pressure_mmhg']} мм"),
    ]
    for i, (label, val) in enumerate(rows):
        y = 348 + i * 40
        d.text((36, y), label, font=_font(20, False), fill=BLACK)
        d.text((360, y), val, font=_font(24), fill=BLUE, anchor="ra")

    d.line([390, 86, 390, H - 24], fill=BLACK, width=1)

    # ── справа: прогноз на 4 дня ──
    x0, x1 = 400, W - 12
    cw = (x1 - x0) / 4
    today = upd.date()
    for i, day in enumerate(daily[:4]):
        cx = x0 + cw * i + cw / 2
        dt = datetime.fromisoformat(day["date"]).date()
        if dt == today:
            label = "сегодня"
        elif (dt - today).days == 1:
            label = "завтра"
        else:
            label = WEEKDAYS[dt.weekday()]
        d.text((cx, 92), label, font=_font(22), fill=BLACK, anchor="ma")
        draw_icon(d, day["code"], cx, 190, 84)
        d.text((cx, 262), _fmt_temp(day["tmax"]), font=_font(34),
               fill=_temp_color(day["tmax"]), anchor="ma")
        d.text((cx, 308), _fmt_temp(day["tmin"]), font=_font(26), fill=BLUE, anchor="ma")
        if (day["precip_prob"] or 0) >= 20 and (day["precip_mm"] or 0) >= 0.1:
            d.text((cx, 352), f"{day['precip_mm']:.1f} мм",
                   font=_font(20, False), fill=BLUE, anchor="ma")
            d.text((cx, 378), f"{day['precip_prob']:.0f}%",
                   font=_font(18, False), fill=BLUE, anchor="ma")

    # ── низ: восход/закат ──
    sr = datetime.fromisoformat(daily[0]["sunrise"]).strftime("%H:%M")
    ss = datetime.fromisoformat(daily[0]["sunset"]).strftime("%H:%M")
    d.text(((x0 + x1) / 2, H - 44), f"восход {sr} · закат {ss}",
           font=_font(20, False), fill=BLACK, anchor="ma")

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def frame_etag(data: dict) -> str:
    """Хэш содержимого — рамка перерисовывается только при новых данных."""
    key = json.dumps([RENDER_VERSION, data["updated"], data["current"],
                      [d["code"] for d in data["daily"]],
                      [d["tmax"] for d in data["daily"]]], sort_keys=True)
    return '"' + hashlib.sha256(key.encode()).hexdigest()[:20] + '"'

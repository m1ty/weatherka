"""Рендер 800x480 карточки погоды для e-ink рамки Spectra 6 (E6).

Макет: шапка с местом и датой; слева — текущая погода крупно, справа —
прогноз на 4 дня колонками; строка метрик (ветер/влажность/давление/солнце);
внизу — почасовой график температуры на сутки со столбиками осадков.
Только чистые палитрные цвета, чтобы прошивка отображала их 1:1 без дизеринга.
"""
from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime
from math import cos, pi, sin

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from weather import describe

W, H = 800, 480
RENDER_VERSION = 3   # менять при правках макета — форсирует перерисовку рамки

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


def _temp_color(t: float):
    if t >= 25:
        return RED
    if t <= 0:
        return BLUE
    return BLACK


# ---------------------------------------------------------------- иконки
# Стиль: контурные облака (белая заливка, чёрный контур), жёлтое солнце/луна,
# синие осадки. Контур получаем, рисуя чёрный силуэт и белый чуть меньше.

def _sun(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float):
    for i in range(8):
        a = i * pi / 4
        d.line([cx + cos(a) * r * 1.35, cy + sin(a) * r * 1.35,
                cx + cos(a) * r * 1.7, cy + sin(a) * r * 1.7],
               fill=YELLOW, width=max(2, round(r * 0.16)))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=YELLOW, outline=BLACK, width=2)


def _moon(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=YELLOW, outline=BLACK, width=2)
    # серп: «выгрызаем» белым кругом, смещённым к верхнему правому краю
    d.ellipse([cx - r * .3, cy - r * 1.45, cx + r * 1.6, cy + r * .45], fill=WHITE)


def _cloud(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float):
    # контур без артефактов: силуэт-маска и её эрозия (MinFilter),
    # чёрное — вся маска, белое — внутренность
    w = max(2, round(s * 0.055))
    size = int(s) + 6
    m = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(m)
    o = size / 2
    parts = [
        ("e", -s * .5, -s * .18, -s * .04, s * .28),
        ("e", -s * .28, -s * .4, s * .24, s * .12),
        ("e", s * .02, -s * .22, s * .5, s * .28),
        ("r", -s * .3, 0, s * .3, s * .28),
    ]
    for kind, a, b, c, e in parts:
        (md.ellipse if kind == "e" else md.rectangle)(
            [a + o, b + o, c + o, e + o], fill=255)
    inner = m.filter(ImageFilter.MinFilter(2 * w + 1))
    pos = (round(cx - o), round(cy - o))
    d.bitmap(pos, m, fill=BLACK)
    d.bitmap(pos, inner, fill=WHITE)


def _drops(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float, n=3):
    for i in range(n):
        x = cx + (i - (n - 1) / 2) * s * 0.28
        y = cy + (s * 0.05 if i % 2 else 0)
        d.line([x, y, x - s * 0.08, y + s * 0.2],
               fill=BLUE, width=max(2, round(s * 0.05)))


def _flakes(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float, n=3):
    for i in range(n):
        x = cx + (i - (n - 1) / 2) * s * 0.32
        y = cy + s * 0.12 + (s * 0.05 if i % 2 else 0)
        r = s * 0.09
        for k in range(3):
            a = k * pi / 3
            d.line([x - cos(a) * r, y - sin(a) * r, x + cos(a) * r, y + sin(a) * r],
                   fill=BLUE, width=max(2, round(s * .03)))


def _bolt(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float):
    d.polygon([(cx + s * .06, cy - s * .1), (cx - s * .16, cy + s * .18),
               (cx - s * .02, cy + s * .18), (cx - s * .1, cy + s * .44),
               (cx + s * .16, cy + s * .1), (cx + s * .02, cy + s * .1)],
              fill=YELLOW, outline=BLACK)


def _fog(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float):
    for i in range(3):
        y = cy + s * .2 + i * s * .12
        d.line([cx - s * .4, y, cx + s * .4, y], fill=BLACK, width=max(2, round(s * .045)))


def draw_icon(d: ImageDraw.ImageDraw, code: int | None, cx: float, cy: float,
              s: float, is_day: bool = True):
    """code — WMO weather code, s — размер иконки, is_day — солнце или луна."""
    c = code or 0
    lum = _sun if is_day else _moon
    if c == 0:
        lum(d, cx, cy, s * 0.3)
    elif c in (1, 2):
        lum(d, cx - s * .18, cy - s * .2, s * 0.2)
        _cloud(d, cx + s * .06, cy + s * .12, s * 0.75)
    elif c == 3:
        _cloud(d, cx, cy, s * 0.9)
    elif c in (45, 48):
        _cloud(d, cx, cy - s * .14, s * 0.75)
        _fog(d, cx, cy, s)
    elif c in (51, 53, 55, 56, 57):
        _cloud(d, cx, cy - s * .12, s * 0.8)
        _drops(d, cx, cy + s * .2, s * .8, n=2)
    elif c in (61, 63, 65, 66, 67, 80, 81, 82):
        _cloud(d, cx, cy - s * .12, s * 0.8)
        _drops(d, cx, cy + s * .2, s, n=3)
    elif c in (71, 73, 75, 77, 85, 86):
        _cloud(d, cx, cy - s * .14, s * 0.8)
        _flakes(d, cx, cy + s * .2, s)
    elif c >= 95:
        _cloud(d, cx, cy - s * .14, s * 0.8)
        _bolt(d, cx, cy + s * .12, s * .8)
    else:
        _cloud(d, cx, cy, s * 0.9)


# ---------------------------------------------------------------- блоки макета

def _fit_font(d: ImageDraw.ImageDraw, text: str, max_w: float,
              size: int, bold=True, min_size=10) -> ImageFont.FreeTypeFont:
    """Крупнейший шрифт, при котором текст влезает в max_w.
    Страхует от «поехавших» границ на шрифтах разной ширины (Arial/DejaVu)."""
    while size > min_size and d.textlength(text, font=_font(size, bold)) > max_w:
        size -= 2
    return _font(size, bold)


def _metric_row(d: ImageDraw.ImageDraw, cx: float, y: int, parts,
                size=20, max_w=W - 48.0):
    """Строка из чередующихся сегментов (текст, цвет, жирность), по центру.
    Размер шрифта ужимается, пока строка не влезет в max_w."""
    while size > 10:
        fonts = {b: _font(size, b) for b in (True, False)}
        total = sum(d.textlength(t, font=fonts[b]) for t, _c, b in parts)
        if total <= max_w:
            break
        size -= 1
    x = cx - total / 2
    for t, color, b in parts:
        d.text((x, y), t, font=fonts[b], fill=color)
        x += d.textlength(t, font=fonts[b])


def _draw_chart(d: ImageDraw.ImageDraw, hourly: list[dict],
                x0: int, x1: int, y0: int, y1: int):
    """Температурная кривая на сутки + столбики вероятности осадков."""
    if len(hourly) < 2:
        return
    temps = [h["temp"] for h in hourly]
    tmin, tmax = min(temps), max(temps)
    span = max(tmax - tmin, 4.0)          # плоские сутки не растягиваем на всю высоту
    pad = (span - (tmax - tmin)) / 2
    n = len(hourly)

    def xy(i: int, t: float) -> tuple[float, float]:
        return (x0 + (x1 - x0) * i / (n - 1),
                y1 - (y1 - y0) * (t - tmin + pad) / span)

    # столбики вероятности осадков — за кривой
    bar_w = (x1 - x0) / (n - 1) * 0.5
    for i, h in enumerate(hourly):
        if h["prob"] < 10:
            continue
        bx = x0 + (x1 - x0) * i / (n - 1)
        bh = (y1 - y0) * 0.5 * h["prob"] / 100
        d.rectangle([bx - bar_w / 2, y1 - bh, bx + bar_w / 2, y1], fill=BLUE)

    # ось часов: подписи каждые 3 часа, пунктир и день недели на полуночи
    for i, h in enumerate(hourly[:-1]):
        dt = datetime.fromisoformat(h["time"])
        bx = x0 + (x1 - x0) * i / (n - 1)
        if dt.hour == 0:
            for yy in range(y0 - 4, y1, 7):
                d.line([bx, yy, bx, yy + 3], fill=BLACK, width=1)
            d.text((bx + 5, y0 - 6), WEEKDAYS[dt.weekday()],
                   font=_font(16), fill=BLACK)
        if dt.hour % 3 == 0:
            d.text((bx, y1 + 8), f"{dt.hour:02d}",
                   font=_font(15, False), fill=BLACK, anchor="ma")

    d.line([x0, y1, x1, y1], fill=BLACK, width=1)

    # кривая температуры
    pts = [xy(i, t) for i, t in enumerate(temps)]
    d.line(pts, fill=BLACK, width=3, joint="curve")
    d.ellipse([pts[0][0] - 4, pts[0][1] - 4, pts[0][0] + 4, pts[0][1] + 4], fill=BLACK)

    # подписи экстремумов: макс над точкой, мин под точкой; у краёв диапазона
    # переворачиваем, чтобы не налезать на подписи часов и строку метрик
    imax, imin = temps.index(tmax), temps.index(tmin)
    for idx, prefer_above in ((imax, True), (imin, False)):
        x, y = pts[idx]
        x = min(max(x, x0 + 22), x1 - 22)
        above = prefer_above if (y0 + 22 < y < y1 - 26) else y > y1 - 26
        d.text((x, y + (-7 if above else 7)), _fmt_temp(temps[idx]),
               font=_font(18), fill=_temp_color(temps[idx]),
               anchor="mb" if above else "ma")


# ---------------------------------------------------------------- кадр

def render_frame(data: dict) -> bytes:
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    cur, daily = data["current"], data["daily"]
    upd = datetime.fromisoformat(data["updated"])

    # ── шапка ──
    d.text((24, 18), data["place"], font=_font(34), fill=BLACK)
    date_s = f"{WEEKDAYS[upd.weekday()]}, {upd.day} {MONTHS[upd.month - 1]}"
    d.text((W - 24, 26), date_s, font=_font(24, False), fill=BLACK, anchor="ra")
    d.line([24, 66, W - 24, 66], fill=BLACK, width=2)

    # ── слева: сейчас ──
    draw_icon(d, cur["code"], 95, 152, 115, cur["is_day"])
    t_str = _fmt_temp(cur["temp"])
    d.text((175, 96), t_str, font=_fit_font(d, t_str, 372 - 175, 84),
           fill=_temp_color(cur["temp"]))
    d.text((36, 226), describe(cur["code"]), font=_font(28, False), fill=BLACK)
    d.text((36, 264), f"ощущается {_fmt_temp(cur['feels'])}",
           font=_font(22, False), fill=BLACK)

    # ── справа: прогноз на 4 дня ──
    x0, x1 = 392, W - 16
    cw = (x1 - x0) / 4
    d.line([382, 84, 382, 288], fill=BLACK, width=1)
    today = upd.date()
    for i, day in enumerate(daily[:4]):
        cx = x0 + cw * i + cw / 2
        if i:                                     # разделители колонок
            d.line([x0 + cw * i, 96, x0 + cw * i, 280], fill=BLACK, width=1)
        dt = datetime.fromisoformat(day["date"]).date()
        label = ("сегодня" if dt == today else
                 "завтра" if (dt - today).days == 1 else WEEKDAYS[dt.weekday()])
        d.text((cx, 88), label, font=_font(20), fill=BLACK, anchor="ma")
        draw_icon(d, day["code"], cx, 152, 74)
        d.text((cx, 200), _fmt_temp(day["tmax"]), font=_font(30),
               fill=_temp_color(day["tmax"]), anchor="ma")
        d.text((cx, 238), _fmt_temp(day["tmin"]), font=_font(24), fill=BLUE, anchor="ma")
        precip = f"{day['precip_prob'] or 0:.0f}%"
        if (day["precip_mm"] or 0) >= 0.1:
            precip += f" · {day['precip_mm']:.1f}"
        d.text((cx, 270), precip, font=_font(18, False), fill=BLUE, anchor="ma")

    # ── строка метрик ──
    d.line([24, 302, W - 24, 302], fill=BLACK, width=1)
    sr = datetime.fromisoformat(daily[0]["sunrise"]).strftime("%H:%M")
    ss = datetime.fromisoformat(daily[0]["sunset"]).strftime("%H:%M")
    dot = ("  ·  ", BLACK, False)
    _metric_row(d, W / 2, 314, [
        ("ветер ", BLACK, False), (f"{cur['wind_ms']:.0f} м/с {cur['wind_dir']}", BLUE, True),
        dot, ("влажность ", BLACK, False), (f"{cur['humidity']:.0f}%", BLUE, True),
        dot, ("давление ", BLACK, False), (f"{cur['pressure_mmhg']} мм", BLUE, True),
        dot, ("восход ", BLACK, False), (sr, BLACK, True),
        dot, ("закат ", BLACK, False), (ss, BLACK, True),
    ], size=18)

    # ── низ: почасовой график ──
    _draw_chart(d, data.get("hourly") or [], 44, W - 44, 366, 438)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def frame_etag(data: dict) -> str:
    """Хэш содержимого с округлением — рамка перерисовывается только
    при заметных изменениях, а не из-за каждой десятой градуса."""
    cur = data["current"]
    key = json.dumps([
        RENDER_VERSION,
        round(cur["temp"]), round(cur["feels"]), cur["code"], cur["is_day"],
        round(cur["wind_ms"]), round(cur["humidity"] / 5), cur["pressure_mmhg"],
        [(dd["code"], round(dd["tmax"]), round(dd["tmin"]),
          round((dd["precip_prob"] or 0) / 5)) for dd in data["daily"][:4]],
        [round(h["temp"] * 2) / 2 for h in data.get("hourly") or []],
        [round(h["prob"] / 10) for h in data.get("hourly") or []],
    ])
    return '"' + hashlib.sha256(key.encode()).hexdigest()[:20] + '"'

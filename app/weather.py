"""Open-Meteo client: текущая погода + прогноз на 5 дней, без API-ключа."""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

LAT = float(os.environ.get("LAT", "55.55"))
LON = float(os.environ.get("LON", "37.55"))          # Бутово, Москва
TZ = os.environ.get("WEATHER_TZ", "Europe/Moscow")
PLACE = os.environ.get("PLACE", "Бутово")

API = "https://api.open-meteo.com/v1/forecast"
PARAMS = {
    "latitude": LAT,
    "longitude": LON,
    "timezone": TZ,
    "wind_speed_unit": "ms",
    "current": ",".join([
        "temperature_2m", "apparent_temperature", "relative_humidity_2m",
        "weather_code", "wind_speed_10m", "wind_direction_10m",
        "surface_pressure", "is_day",
    ]),
    "daily": ",".join([
        "weather_code", "temperature_2m_max", "temperature_2m_min",
        "precipitation_sum", "precipitation_probability_max",
        "sunrise", "sunset",
    ]),
    "forecast_days": 5,
}

# WMO weather code -> короткое описание по-русски
DESCRIPTIONS = {
    0: "ясно", 1: "преим. ясно", 2: "малооблачно", 3: "пасмурно",
    45: "туман", 48: "изморозь",
    51: "морось", 53: "морось", 55: "сильная морось",
    56: "лед. морось", 57: "лед. морось",
    61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
    66: "ледяной дождь", 67: "ледяной дождь",
    71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежные зёрна",
    80: "небольшой ливень", 81: "ливень", 82: "сильный ливень",
    85: "снегопад", 86: "сильный снегопад",
    95: "гроза", 96: "гроза с градом", 99: "гроза с градом",
}

_DIRS = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"]


def wind_dir(deg: float | None) -> str:
    if deg is None:
        return ""
    return _DIRS[round(deg / 45) % 8]


def describe(code: int | None) -> str:
    return DESCRIPTIONS.get(code or 0, "—")


async def fetch() -> dict:
    """Возвращает нормализованный словарь; бросает исключение при сбое сети."""
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(API, params=PARAMS)
        r.raise_for_status()
        raw = r.json()

    cur, day = raw["current"], raw["daily"]
    daily = []
    for i, date in enumerate(day["time"]):
        daily.append({
            "date": date,
            "code": day["weather_code"][i],
            "tmax": day["temperature_2m_max"][i],
            "tmin": day["temperature_2m_min"][i],
            "precip_mm": day["precipitation_sum"][i],
            "precip_prob": day["precipitation_probability_max"][i],
            "sunrise": day["sunrise"][i],
            "sunset": day["sunset"][i],
        })

    return {
        "place": PLACE,
        "updated": datetime.now(ZoneInfo(TZ)).isoformat(timespec="minutes"),
        "current": {
            "temp": cur["temperature_2m"],
            "feels": cur["apparent_temperature"],
            "humidity": cur["relative_humidity_2m"],
            "code": cur["weather_code"],
            "wind_ms": cur["wind_speed_10m"],
            "wind_dir": wind_dir(cur.get("wind_direction_10m")),
            # станционное давление в мм рт. ст. — привычная величина
            "pressure_mmhg": round(cur["surface_pressure"] * 0.750062),
            "is_day": bool(cur.get("is_day", 1)),
        },
        "daily": daily,
    }

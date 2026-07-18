# Weatherka

Самодельный аналог TRMNL: сервис рендерит прогноз погоды для Бутово (Москва)
в 800×480 PNG под e-ink рамку Spectra 6 (E6) и отдаёт его по URL.
Рамка опрашивает `/api/frame.png`; благодаря ETag/304 перерисовка
происходит только при новых данных — бережёт батарею и циклы E6.

Источник данных — [Open-Meteo](https://open-meteo.com/), без API-ключа.

## Эндпоинты

- `GET /api/frame.png` — кадр для рамки (ETag/304)
- `GET /api/weather` — данные в JSON
- `GET /healthz` — статус
- `GET /` — предпросмотр кадра в браузере

## Настройка (env)

| Переменная | По умолчанию | |
|---|---|---|
| `LAT` / `LON` | `55.55` / `37.55` | координаты (Бутово) |
| `PLACE` | `Бутово` | название на кадре |
| `WEATHER_TZ` | `Europe/Moscow` | часовой пояс |
| `REFRESH_MINUTES` | `20` | период обновления прогноза |

## Установка в LXC (Proxmox)

Создать контейнер (на pve, шаблон Debian 12):

```sh
pct create 131 local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
  --hostname weatherka --memory 512 --cores 1 --rootfs local-lvm:4 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp --unprivileged 1 --start 1
```

Затем из клона этого репозитория на pve:

```sh
./setup.sh 131      # первичная установка: пакеты, venv, systemd
./deploy.sh 131     # последующие обновления кода
```

Кадр будет доступен по `http://<ip-контейнера>:8000/api/frame.png` —
этот URL и указывается в настройках рамки как источник изображения.

## Локальный запуск

```sh
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/uvicorn main:app --app-dir app --port 8000
```

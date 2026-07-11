# WarpWails

Локальный CPU-only TTS для русского текста. Основной пайплайн — **F5-TTS Russian** с референсами голоса Горлума (рус. дубляж, тёмный регистр) по эмоциям, RUAccent для ударений, варп-эффект «мелкого ушлого демона» и полуслучайный генератор вставок (смешки, скрипы, шёпоты). Потоковый вывод сразу в колонки, аудиофайлы не создаются.

## Сервис-демон (модель в памяти)

```bash
WarpWails-F5/bin/python warpwails_service.py   # 127.0.0.1:7500
curl -s -X POST http://127.0.0.1:7500/speak -H 'Content-Type: application/json' \
  -d '{"text": "[сарказм] Ну надо же, хозяин."}'
curl -s http://127.0.0.1:7500/health
```

`/speak` ставит реплику в очередь: сразу играет заглушку (смешок/скрип из банка), первая фраза стартует по готовности, остальные считаются конвейером. `opener: false` — без заглушки. Порт и заглушка — блок `service` в `voice_profile.json`, устройство — `f5.device` (`cpu` → `cuda` после апгрейда).

## Основной запуск (F5 + Горлум + вставки)

```bash
WarpWails-F5/bin/python warpwails_f5.py --text "[сарказм] Ну надо же. Опять смертные идеи."
WarpWails-F5/bin/python warpwails_f5.py --check     # проверка окружения и референсов
WarpWails-F5/bin/python warpwails_f5.py --dry ...   # чистый голос без эффекта и вставок
WarpWails-F5/bin/python warpwails_f5.py --no-sfx ...# без смешков/скрипов
WarpWails-F5/bin/python warpwails_f5.py --seed 7 ...# воспроизводимые вставки
```

Эмоции = референсы в `voice_profile.json` → `f5.refs` (нарезаны из сцен в `refs/final/`): `default`, `сарказм`, `ехидно`, `холодно`, `угроза`, `шепот`, `драма`, `безумие`, `смех`, `ярость`.

Компоненты:
- `warp_effect.py` — WarpImpEffect: эхо уезжает питчем вверх, ring-mod, сатурация (конфиг `warp_imp`).
- `warp_sfx.py` — WarpSfxInserter: вставки в паузы/поверх речи/между фразами + варп-подложка (конфиг `sfx`, банк `sfx/manifest.json`).
- `tools/` — скрипты добычи: `fetch_bbc_sfx.py`, `extract_gollum_sfx.py`, `cut_refs.py`, `build_sfx_bank.py`, `transcribe_refs.py`.

Ниже — легаси-описание XTTS-пайплайна (`warpwails.py`), он остаётся как запасной.

## Что используется

- TTS: `tts_models/multilingual/multi-dataset/xtts_v2`
- Устройство: CPU only (`torch==2.1.2+cpu`)
- Русские ударения: `silero-stress`
- Вывод: raw PCM stream в `aplay`, `pw-play` или `paplay`
- Эффект: локальная CPU-обработка “потустороннее эхо демона варпа”

XTTS-v2 требует принятия лицензии Coqui CPML при первой загрузке модели. Скрипт не принимает лицензию автоматически.

## Установка

```bash
cd /media/shushunya/SHUSHUNYA/shushunya/WarpWails
./setup.sh
source WarpWails-XTTS/bin/activate
```

## Проверка

```bash
python warpwails.py --check
python warpwails.py --emotions
python warpwails.py --text "[ехидно] Старый замок стоял на холме. Замо́к на двери был сломан." --preview
```

## Запуск в колонки

```bash
python warpwails.py --text "[сарказм] Ну конечно. Еще одна смертная идея."
```

Или файл:

```bash
python warpwails.py examples/warp_script.txt
```

Никаких `--out`, WAV, MP3 или временных аудиофайлов нет.

## Эмоции

Формат строки:

```text
[сарказм] Ну конечно. Еще одна смертная идея.
[шепот] Я слышу, как реальность трескается.
[угроза] Сделай шаг ближе.
```

Доступные профили: `сарказм`, `ехидно`, `шепот`, `ярость`, `угроза`, `смех`, `устало`, `драма`, `холодно`, `безумие`.

## Ударения

Автоматические ударения включены. Для омографов лучше указывать ударение вручную через комбинируемый акут:

```text
Старый за́мок стоял на холме. Замо́к на двери был сломан.
```

Отключить автоматическую разметку:

```bash
python warpwails.py --no-auto-stress --text "..."
```

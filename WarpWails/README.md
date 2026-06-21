# WarpWails

WarpWails - заготовка для генерации сверхъестественного TTS: сначала естественный эмоциональный голос ElevenLabs, затем локальная обработка в потустороннее эхо саркастично-ехидного демона варпа.

## Почему ElevenLabs

На 12 июня 2026 лучший выбор под эту задачу - ElevenLabs Eleven v3:

- ElevenLabs описывает TTS как речь с нюансированной интонацией, темпом и эмоциональной осведомленностью.
- Eleven v3 поддерживает audio tags: эмоции и манеру можно задавать прямо перед фразой, например `[sarcastic]`, `[whispers]`, `[laughs]`.
- В официальной документации Eleven v3 назван их самым продвинутым и выразительным speech synthesis model.

OpenAI TTS тоже можно добавить вторым провайдером, но для этой конкретной задачи ElevenLabs удобнее из-за audio tags.

## Окружение

Окружение должно лежать здесь:

```bash
/media/shushunya/SHUSHUNYA/shushunya/WarpWails/WarpWails
```

На этой машине стандартное создание venv уперлось в отсутствие системного `ensurepip`/`python3.12-venv`, но установлен `uv`, поэтому окружение можно довести так:

```bash
cd /media/shushunya/SHUSHUNYA/shushunya/WarpWails
uv venv WarpWails
source WarpWails/bin/activate
```

В проекте нет обязательных Python-зависимостей: генератор использует стандартную библиотеку. Для ElevenLabs нужен только API-ключ.

## Настройка

```bash
cp .env.example .env
```

Заполнить:

```bash
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
```

`ELEVENLABS_VOICE_ID` лучше взять из Voice Library. Для демонической насмешливой подачи лучше выбирать низкий, хриплый, драматичный голос, а не нейтральный дикторский.

## Запуск

```bash
source /media/shushunya/SHUSHUNYA/shushunya/WarpWails/WarpWails/bin/activate
python warpwails.py examples/warp_script.txt --out out/warp.wav
```

Можно передавать текст прямо:

```bash
python warpwails.py --text "[сарказм] Ну конечно. Еще одна смертная идея." --out out/line.wav
```

## Формат эмоций

Эмоция ставится перед фразой:

```text
[сарказм] Ну конечно. Еще одна смертная идея.
[шепот] Я слышу, как реальность трескается.
[ярость] Варп не просит разрешения.
```

Поддерживаемые русские алиасы: `сарказм`, `ехидно`, `шепот`, `ярость`, `угроза`, `смех`, `устало`, `драма`, `холодно`, `безумие`.

Скрипт превращает их в ElevenLabs audio tags, затем добавляет фирменную постобработку:

- темный резонанс;
- двойное эхо;
- низкий “подголосок”;
- легкая сатурация;
- нестабильное варп-мерцание амплитуды.

## Файлы

- `warpwails.py` - основной CLI.
- `voice_profile.json` - профиль модели, голоса и эффекта.
- `examples/warp_script.txt` - пример реплик.
- `.env.example` - переменные окружения.

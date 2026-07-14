# Shushunya kernel

Выбран upstream Linux `6.18.y` LTS. Первая зафиксированная база —
`v6.18.38`; прогнозируемая поддержка ветки — до декабря 2028 года.

Канонические параметры и контрольные суммы находятся в `source.lock.json`.
Никаких `latest`: переход на следующий `6.18.x` делается отдельным изменением
lock-файла и повторным boot-тестом.

```bash
./scripts/fetch-kernel.sh
./scripts/prepare-kernel-work.sh
```

`fetch-kernel.sh` скачивает tarball и подпись с kernel.org, проверяет обе
SHA-256, OpenPGP-подпись и полный fingerprint подписанта, после чего извлекает
read-only эталон в `kernel/source/`.

`prepare-kernel-work.sh` создаёт отдельную рабочую копию и применяет только
патчи, перечисленные в `patches/series`. Оригинал никогда не патчится.

Конфигурация VM строится из `x86_64_defconfig` и фрагментов в
`config/fragments/`. Полный lock-конфиг появится после установки build-зависимостей;
конфиг физического Pop!_OS копировать нельзя.

#!/usr/bin/env python3
"""
Собирает zip-архив для импорта данных Mindustry (v8 / build 158-160).

Архив имеет ровно ту же структуру, что и встроенный экспорт игры
(Настройки -> Данные -> Экспорт), пути считаются от папки данных Mindustry:

    settings.bin          -> <data>/settings.bin
    settings_backup.bin   -> <data>/settings_backup.bin
    server_list.json      -> <data>/server_list.json
    maps/                 -> <data>/maps/
    saves/                -> <data>/saves/     (сюда кладутся все *.msav)

Такой файл подходит как для импорта в самой игре
(Настройки -> Данные -> Импорт -> выбрать zip),
так и для ручной распаковки в папку данных игры.

Запуск:  python3 make-import-zip.py
"""

import os
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "mindustry-saves-import.zip")

# файлы, которые игровой импортёр ожидает увидеть в корне архива
ROOT_FILES = ["settings.bin", "settings_backup.bin", "server_list.json"]

# файлы из корня репозитория, которые являются сохранениями -> кладём в saves/
SAVE_EXT = ".msav"

# steam_autocloud.vdf не включаем: это служебный маркер Steam Cloud,
# привязанный к аккаунту (accountid), Steam создаёт его сам.


def sort_key(name: str):
    """0.msav, 1.msav, ... 23.msav, затем sector-serpulo-*"""
    base = name.split(".")[0]
    try:
        return (0, int(base), name)
    except ValueError:
        return (1, 0, name)


def main():
    saves = sorted(
        [f for f in os.listdir(ROOT) if f.endswith(SAVE_EXT) and os.path.isfile(os.path.join(ROOT, f))],
        key=sort_key,
    )
    maps = sorted(
        [f for f in os.listdir(os.path.join(ROOT, "maps"))] if os.path.isdir(os.path.join(ROOT, "maps")) else []
    )
    maps = [f for f in maps if not f.endswith(".vdf")]

    if not saves:
        raise SystemExit("Не найдено ни одного файла *.msav в " + ROOT)

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        # --- корневые файлы настроек ---
        for name in ROOT_FILES:
            path = os.path.join(ROOT, name)
            if os.path.isfile(path):
                z.write(path, name)

        # --- сохранения ---
        z.writestr("saves/", b"")
        for name in saves:
            z.write(os.path.join(ROOT, name), "saves/" + name)

        # --- кастомные карты ---
        if maps:
            z.writestr("maps/", b"")
            for name in maps:
                z.write(os.path.join(ROOT, "maps", name), "maps/" + name)

    total = sum(i.file_size for i in zipfile.ZipFile(OUT).infolist())
    print("Готово: %s" % OUT)
    print("  сохранений: %d" % len(saves))
    print("  карт: %d" % len(maps))
    print("  размер архива: %.2f МБ (распакованных данных: %.2f МБ)" % (
        os.path.getsize(OUT) / 1048576, total / 1048576))


if __name__ == "__main__":
    main()

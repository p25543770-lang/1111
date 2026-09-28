#!/usr/bin/env python3
"""
Собирает zip-архив для импорта данных Mindustry (v8 / build 158-160).

Структура архива — такая же, как у встроенного экспорта игры
(Настройки -> Данные -> Экспорт): пути считаются от папки данных Mindustry.

Что кладётся в архив:
    settings.bin            -> <data>/settings.bin            (обязателен для импорта)
    settings_backup.bin     -> <data>/settings_backup.bin
    server_list.json        -> <data>/server_list.json
    maps/                   -> <data>/maps/                   (*.msav из папки maps)
    saves/                  -> <data>/saves/                  (все *.msav из корня)
    schematics/ (если есть) -> <data>/schematics/             (*.msch, в т.ч. из корня)
    mods/       (если есть) -> <data>/mods/                   (*.jar, в т.ч. из корня)
    previews/, assetCache/, be_builds/, screenshots/ (если есть) — как есть

Не кладутся: last_log.txt, README, скрипты, steam_autocloud.vdf
(последний — маркер Steam Cloud с чужим accountid, Steam создаёт его сам).

Запуск:  python3 make-import-zip.py [имя-архива.zip]
"""

import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, sys.argv[1] if len(sys.argv) > 1 else "mindustry-saves-import.zip")

# файлы, которые игровой импортёр ожидает увидеть в корне архива
ROOT_FILES = ["settings.bin", "settings_backup.bin", "server_list.json"]

# папки данных, которые переносятся целиком, если они есть в репозитории
OPTIONAL_DIRS = ["schematics", "mods", "previews", "assetCache", "be_builds", "screenshots"]

# файлы, лежащие в корне, но относящиеся к подпапкам данных
LOOSE_RULES = [(".msch", "schematics"), (".jar", "mods"), (".msav.png", "previews")]

# служебный мусор
IGNORE_NAMES = {"steam_autocloud.vdf", ".DS_Store", "Thumbs.db", "desktop.ini"}

SAVE_EXT = ".msav"


def sort_key(name: str):
    """0.msav, 1.msav, ... 23.msav, затем sector-serpulo-*"""
    base = name.split(".")[0]
    try:
        return (0, int(base), name)
    except ValueError:
        return (1, 0, name)


def list_dir(path):
    if not os.path.isdir(path):
        return []
    return sorted(f for f in os.listdir(path)
                  if os.path.isfile(os.path.join(path, f)) and f not in IGNORE_NAMES)


def main():
    out_name = os.path.basename(OUT)
    root_files = [f for f in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, f))]

    # --- сохранения: все *.msav из корня ---
    saves = sorted([f for f in root_files if f.endswith(SAVE_EXT)], key=sort_key)

    # --- кастомные карты ---
    maps = list_dir(os.path.join(ROOT, "maps"))

    # --- необязательные папки ---
    optional = {d: list_dir(os.path.join(ROOT, d)) for d in OPTIONAL_DIRS}

    # --- «россыпь» в корне: .msch -> schematics/, .jar -> mods/, *.msav.png -> previews/ ---
    loose = {}
    for f in root_files:
        low = f.lower()
        for ext, dest in LOOSE_RULES:
            if low.endswith(ext) and not (dest == "mods" and f == out_name):
                loose.setdefault(dest, []).append(f)
                break

    if not saves:
        raise SystemExit("Не найдено ни одного файла *.msav в " + ROOT)

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        # корневые файлы настроек
        for name in ROOT_FILES:
            path = os.path.join(ROOT, name)
            if os.path.isfile(path):
                z.write(path, name)

        # сохранения
        z.writestr("saves/", b"")
        for name in saves:
            z.write(os.path.join(ROOT, name), "saves/" + name)

        # кастомные карты
        if maps:
            z.writestr("maps/", b"")
            for name in maps:
                z.write(os.path.join(ROOT, "maps", name), "maps/" + name)

        # необязательные папки + «россыпь»
        for folder in OPTIONAL_DIRS:
            files = optional[folder] + [f for f in loose.get(folder, []) if f not in optional[folder]]
            if not files:
                continue
            z.writestr(folder + "/", b"")
            for name in sorted(files):
                src = os.path.join(ROOT, folder, name)
                if not os.path.isfile(src):
                    src = os.path.join(ROOT, name)
                z.write(src, f"{folder}/{name}")

    # --- отчёт ---
    zip_names = zipfile.ZipFile(OUT).namelist()
    dirs_in_zip = sorted({n.split("/")[0] for n in zip_names if "/" in n})
    total = sum(i.file_size for i in zipfile.ZipFile(OUT).infolist())

    print("Готово: %s" % OUT)
    print("  сохранений: %d | карт: %d" % (len(saves), len(maps)))
    print("  папки в архиве: %s" % ", ".join(dirs_in_zip))
    print("  размер архива: %.2f МБ (распакованных данных: %.2f МБ)" % (
        os.path.getsize(OUT) / 1048576, total / 1048576))

    missing = [d for d in OPTIONAL_DIRS if d not in dirs_in_zip and d != "screenshots"]
    if missing:
        print("\nНет в архиве (в репозитории таких файлов нет): %s" % ", ".join(missing))
        print("  Чтобы добавить: закинь папку(и) %s в корень репозитория и запусти скрипт снова," % ", ".join(missing))
        print("  либо (проще) собери полный архив у себя на ПК: build-full-zip.bat")

    # сейвы, у которых есть только бэкап — в списке сохранений игры их не видно
    suffix = "-backup" + SAVE_EXT
    primary = {f for f in saves if not f.endswith(suffix)}
    orphans = sorted(f[: -len(suffix)] for f in saves
                     if f.endswith(suffix) and f[: -len(suffix)] not in primary)
    if orphans:
        print("\nВНИМАНИЕ: только бэкап, без основного файла (в игре не отобразится): %s" % ", ".join(orphans))


if __name__ == "__main__":
    main()

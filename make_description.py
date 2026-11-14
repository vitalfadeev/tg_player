#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Читает все *.txt из каталога mp3/,
парсит поля "автор", "аудио", "длина", "ссылка",
преобразует tg:// ссылки в https://t.me/...,
сортирует по имени файла (natural sort)
и формирует video/description.txt с таймкодами (главами) для YouTube.
"""

import re
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs


def natural_key(name: str):
    """Ключ для естественной сортировки имён файлов."""
    return [int(p) if p.isdigit() else p.lower()
            for p in re.split(r"(\d+)", name)]


def parse_txt(filepath: Path) -> dict:
    data = {}
    with filepath.open("r", encoding="utf-8-sig") as f:
        for line in f:
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            data[key.strip()] = value.strip()
    return data


def tg_to_web(link: str) -> str:
    """
    Преобразует tg://resolve?domain=...&thread=...&post=...
    в https://t.me/<domain>/<thread>/<post>
    (или https://t.me/<domain>/<post>, если thread нет).
    Другие ссылки возвращает без изменений.
    """
    link = link.strip()
    if not link.startswith("tg://"):
        return link

    # Парсим вручную, т.к. urlparse может неверно разобрать нестандартную схему tg://
    try:
        # Отделяем query-часть от схемы и пути: tg://resolve?domain=...
        path_part, _, query = link[5:].partition("?")
        if path_part != "resolve":
            return link  # поддерживаем только resolve-ссылки

        params = parse_qs(query)
        domain = params.get("domain", [None])[0]
        post   = params.get("post",   [None])[0]
        thread = params.get("thread", [None])[0]

        if not domain or not post:
            return link

        if thread:
            return f"https://t.me/{domain}/{thread}/{post}"
        return f"https://t.me/{domain}/{post}"
    except Exception:
        return link


def parse_duration(s: str) -> float:
    m = re.search(r"\(([\d.]+)\s*сек\)", s)
    if m:
        return float(m.group(1))
    m = re.search(r"(?:(\d+):)?(\d+):(\d+)", s)
    if m:
        h = int(m.group(1) or 0)
        return h * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return 0.0


def format_time(seconds: float) -> str:
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def strip_mp3(name: str) -> str:
    name = re.sub(r"\.mp3$", "", name, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", name).strip()


def generate_description(src_dir: Path, out_file: Path) -> int:
    """
    Собирает description.txt с таймкодами (главами) из txt-сайдкаров.

    :param src_dir:  каталог с mp3 и одноимёнными NN.txt
    :param out_file: путь к результирующему description.txt
    :return: 0 при успехе, 1 при ошибке
    """
    if not src_dir.is_dir():
        print(f"Каталог '{src_dir}' не найден.", file=sys.stderr)
        return 1

    out_file.parent.mkdir(parents=True, exist_ok=True)

    txt_files = sorted(
        src_dir.glob("*.txt"), key=lambda p: natural_key(p.name)
    )
    print(f"Найдено txt-файлов в {src_dir}/: {len(txt_files)}")

    entries, skipped = [], []
    for path in txt_files:
        if path.name == "description.txt":
            continue
        d = parse_txt(path)

        missing = [k for k in ("автор", "длина", "аудио", "ссылка")
                   if k not in d or not d[k]]
        if missing:
            skipped.append((path.name, missing))
            continue

        raw_link = d["ссылка"].strip()
        web_link = tg_to_web(raw_link)

        entries.append({
            "file": path.name,
            "author": d["автор"].strip(),
            "audio": strip_mp3(d["аудио"]),
            "link": web_link,
            "duration": parse_duration(d["длина"]),
        })

    for name, miss in skipped:
        print(f"  пропущен {name}: нет полей {miss}")

    if not entries:
        print("Не найдено подходящих txt-файлов.", file=sys.stderr)
        return 1

    lines, current = [], 0.0
    for e in entries:
        lines.append(
            f"[{format_time(current)}] {e['author']} - {e['audio']}. {e['link']}"
        )
        current += e["duration"]

    out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\nГотово: {out_file}")
    print(f"Записей: {len(lines)}, общая длительность: {format_time(current)}")
    return 0


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="make_description",
        description="Генерирует description.txt из txt-сайдкаров mp3/.",
    )
    parser.add_argument(
        "--src_dir", default="mp3",
        help="Каталог с mp3 и txt-сайдкарами (по умолчанию mp3)",
    )
    parser.add_argument(
        "--output", default="video/description.txt",
        help="Путь к результирующему description.txt "
             "(по умолчанию mp4/description.txt)",
    )
    args, _ = parser.parse_known_args(argv)
    return generate_description(
        src_dir=Path(args.src_dir),
        out_file=Path(args.output),
    )


if __name__ == "__main__":
    raise SystemExit(main())

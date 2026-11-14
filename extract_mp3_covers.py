#!/usr/bin/env python3
"""Извлечение встроенных обложек из MP3 в отдельные .jpg рядом с файлом.

Логика для каждого .mp3 в каталоге:
  1) если рядом уже есть картинка с тем же стемом (.jpg/.jpeg/.png/.gif)
     — ничего не делаем (обложка уже есть);
  2) если рядом есть одноимённый .mp4 — ничего не делаем
     (видео-компаньон важнее обложки);
  3) иначе — пытаемся извлечь встроенную обложку из метаданных MP3
     и сохранить её как <stem>.jpg рядом с MP3;
  4) если встроенной обложки нет — предупреждение, файл пропускается.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# Приоритетный порядок поиска «уже имеющейся» картинки
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif",
              ".JPG", ".JPEG", ".PNG", ".GIF")
VIDEO_EXTS = (".mp4", ".MP4")

FFMPEG_TIMEOUT = 120


def run(cmd: list[str], *, check: bool = True,
        timeout: int = FFMPEG_TIMEOUT) -> subprocess.CompletedProcess:
    result = subprocess.run(
        cmd,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )
    return result


def has_companion_image(mp3: Path) -> Path | None:
    """Есть ли рядом картинка с тем же стемом (любое из расширений)."""
    for ext in IMAGE_EXTS:
        p = mp3.with_suffix(ext)
        if p.is_file():
            return p
    return None


def has_companion_video(mp3: Path) -> Path | None:
    """Есть ли рядом одноимённый .mp4."""
    for ext in VIDEO_EXTS:
        p = mp3.with_suffix(ext)
        if p.is_file():
            return p
    return None


def extract_embedded_cover(mp3: Path, target: Path) -> bool:
    """Извлекает первый видеопоток (обложку) из MP3 в target (.jpg).

    Возвращает True при успехе.
    """
    if target.exists():
        try:
            target.unlink()
        except OSError:
            pass

    result = run(
        ["ffmpeg", "-y", "-nostdin", "-loglevel", "error",
         "-i", str(mp3),
         "-map", "0:v:0",      # первый видеопоток = обложка
         "-frames:v", "1",     # один кадр
         "-q:v", "2",          # качество JPEG (2 ≈ очень хорошее)
         str(target)],
        check=False,
    )
    if result.returncode != 0:
        # ffmpeg не нашёл обложки (или иная ошибка) — подчистим мусор
        if target.exists():
            try:
                target.unlink()
            except OSError:
                pass
        return False

    return target.exists() and target.stat().st_size > 0


def process_dir(directory: Path) -> int:
    """Обрабатывает все MP3 в каталоге. Возвращает код выхода."""
    if shutil.which("ffmpeg") is None:
        print("❌ ffmpeg не найден в PATH", file=sys.stderr)
        return 1

    if not directory.is_dir():
        print(f"❌ Каталог не найден: {directory}", file=sys.stderr)
        return 1

    mp3_files = sorted(
        (f for f in directory.iterdir()
         if f.is_file() and f.suffix.lower() == ".mp3"),
        key=lambda p: p.name.lower(),
    )
    if not mp3_files:
        print(f"ℹ️  В {directory} нет .mp3 файлов")
        return 0

    print(f"🎵 Найдено MP3: {len(mp3_files)}")
    extracted = skipped_img = skipped_vid = failed = 0

    for i, mp3 in enumerate(mp3_files, start=1):
        prefix = f"[{i}/{len(mp3_files)}] {mp3.name}"

        # 1) уже есть картинка — не трогаем
        img = has_companion_image(mp3)
        if img is not None:
            print(f"⏭  {prefix} — уже есть картинка {img.name}")
            skipped_img += 1
            continue

        # 2) есть одноимённый mp4 — не трогаем
        vid = has_companion_video(mp3)
        if vid is not None:
            print(f"⏭  {prefix} — есть видео {vid.name}, пропуск")
            skipped_vid += 1
            continue

        # 3) пытаемся извлечь встроенную обложку
        target = mp3.with_suffix(".jpg")
        if extract_embedded_cover(mp3, target):
            print(f"✅ {prefix} — обложка извлечена в {target.name}")
            extracted += 1
        else:
            print(f"⚠️  {prefix} — встроенной обложки нет", file=sys.stderr)
            failed += 1

    print(
        f"\n📊 Итог: извлечено {extracted}, "
        f"пропущено (картинка есть) {skipped_img}, "
        f"пропущено (видео есть) {skipped_vid}, "
        f"без обложки {failed}"
    )
    return 0


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="extract_covers",
        description="Извлечение встроенных обложек из MP3 в .jpg рядом с файлом.",
    )
    parser.add_argument(
        "directory", nargs="?", default=".",
        help="Каталог с MP3 (по умолчанию текущий)",
    )
    args, _ = parser.parse_known_args(argv)
    return process_dir(Path(args.directory))


if __name__ == "__main__":
    raise SystemExit(main())
    
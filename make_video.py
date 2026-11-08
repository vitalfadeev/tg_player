#!/usr/bin/env python3
"""Склейка MP3 (с обложками) в один mp4/final.mp4 (360p, H.264 + AAC)."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

MP3_DIR       = Path("mp3")
OUT_DIR       = Path("mp4")
TMP_DIR       = Path("temp")
DEFAULT_COVER = Path("default.jpg")
FINAL         = OUT_DIR / "final.mp4"

WIDTH, HEIGHT   = 640, 360
FPS             = 1
AUDIO_BITRATE   = "192k"
AUDIO_RATE      = 44100
AUDIO_CHANNELS  = 2


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        cmd, check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )
    return result


def extract_cover(audio: Path, cover: Path) -> bool:
    """Пробует вытащить встроенную обложку (ID3 APIC). Возвращает True при успехе."""
    run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-i", str(audio), "-an", "-vcodec", "copy", str(cover)],
        check=False,
    )
    return cover.exists() and cover.stat().st_size > 0


def make_segment(cover: Path, audio: Path, segment: Path) -> None:
    vf = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
    )
    run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-framerate", str(FPS), "-i", str(cover),
        "-i", str(audio),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-tune", "stillimage", "-r", str(FPS),
        "-c:a", "aac", "-b:a", AUDIO_BITRATE,
        "-ar", str(AUDIO_RATE), "-ac", str(AUDIO_CHANNELS),
        "-shortest", str(segment),
    ])


def concat_segments(list_file: Path, output: Path) -> None:
    run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", "-movflags", "+faststart", str(output),
    ])


def main() -> int:
    if shutil.which("ffmpeg") is None:
        print("❌ ffmpeg не найден в PATH", file=sys.stderr)
        return 1
    if not MP3_DIR.is_dir():
        print(f"❌ Каталог {MP3_DIR}/ не найден", file=sys.stderr)
        return 1

    mp3_files = sorted(MP3_DIR.glob("*.mp3"))  # порядок = алфавитный
    if not mp3_files:
        print(f"❌ В {MP3_DIR}/ нет mp3-файлов", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(exist_ok=True)
    TMP_DIR.mkdir(exist_ok=True)

    list_file = TMP_DIR / "list.txt"
    segments: list[Path] = []

    try:
        for i, audio in enumerate(mp3_files, start=1):
            cover = TMP_DIR / f"{audio.stem}.jpg"
            if not extract_cover(audio, cover):
                if DEFAULT_COVER.is_file():
                    print(f"⚠️  Нет обложки для {audio.name}, использую {DEFAULT_COVER}")
                    cover = DEFAULT_COVER
                else:
                    print(
                        f"❌ Нет обложки для {audio.name} и нет {DEFAULT_COVER}",
                        file=sys.stderr,
                    )
                    return 1

            segment = TMP_DIR / f"seg_{i:03d}.mp4"
            print(f"🎬 [{i}/{len(mp3_files)}] {audio.name}")
            make_segment(cover, audio, segment)
            segments.append(segment)

        list_file.write_text(
            "\n".join(f"file '{seg.resolve().as_posix()}'" for seg in segments) + "\n",
            encoding="utf-8",
        )

        print(f"🔗 Склейка {len(segments)} сегментов → {FINAL}")
        concat_segments(list_file, FINAL)

    finally:
        shutil.rmtree(TMP_DIR, ignore_errors=True)

    print(f"✅ Готово: {FINAL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Склейка MP3 + (jpg/png | mp4 | gif) в один mp4/video.mp4 (360p, H.264 + AAC).

Длина каждого сегмента ВСЕГДА равна длине mp3 (приоритет аудио).
Для каждого mp3 ищется компаньон с тем же стемом в порядке приоритета:
  1) картинка (.jpg/.jpeg/.png/.webp),
  2) видео (.mp4),
  3) GIF (.gif).
Если ничего не найдено — используется default.gif.
"""
from __future__ import annotations

import math
import shutil
import subprocess
import sys
from pathlib import Path

MP3_DIR       = Path("mp3")
OUT_DIR       = Path("mp4")
TMP_DIR       = Path("temp")
DEFAULT_COVER = Path("default.gif")
FINAL         = OUT_DIR / "video.mp4"

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp",
              ".JPG", ".JPEG", ".PNG", ".WEBP")
VIDEO_EXTS = (".mp4", ".MP4")
GIF_EXTS   = (".gif", ".GIF")

WIDTH, HEIGHT   = 640, 360
FPS             = 30           # единое значение для concat -c copy
AUDIO_BITRATE   = "192k"
AUDIO_RATE      = 44100
AUDIO_CHANNELS  = 2

# Таймаут на один вызов ffmpeg. 10 минут — с большим запасом для любого
# разумного mp3, но не даст висеть вечно.
FFMPEG_TIMEOUT  = 600


def run(cmd: list[str], *, check: bool = True,
        timeout: int = FFMPEG_TIMEOUT) -> subprocess.CompletedProcess:
    result = subprocess.run(
        cmd,
        check=False,
        stdin=subprocess.DEVNULL,   # ffmpeg не должен читать TTY
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,            # страховка от залипаний
    )
    if check and result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )
    return result


def probe_duration(media: Path) -> float | None:
    """Длительность медиа в секундах (ffprobe) или None.

    Отбрасывает inf/nan — некоторые битые mp3 дают такие значения,
    и тогда `-t inf` превратил бы энкод в бесконечный.
    """
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             str(media)],
            check=False, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=30,
        )
        if result.returncode != 0:
            return None
        val = result.stdout.strip()
        if not val or val == "N/A":
            return None
        d = float(val)
        if not math.isfinite(d) or d <= 0:
            return None
        return d
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return None


def kind_by_ext(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp"}:
        return "image"
    if ext == ".mp4":
        return "video"
    if ext == ".gif":
        return "gif"
    raise ValueError(f"Неподдерживаемый тип компаньона: {ext}")


def find_cover(audio: Path) -> tuple[Path, str] | None:
    for ext in IMAGE_EXTS:
        p = audio.with_suffix(ext)
        if p.is_file():
            return (p, "image")
    for ext in VIDEO_EXTS:
        p = audio.with_suffix(ext)
        if p.is_file():
            return (p, "video")
    for ext in GIF_EXTS:
        p = audio.with_suffix(ext)
        if p.is_file():
            return (p, "gif")
    return None


def make_segment(cover: Path, audio: Path, segment: Path, kind: str) -> None:
    vf = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
    )

    if kind == "image":
        input_args = ["-loop", "1", "-framerate", str(FPS), "-i", str(cover)]
    elif kind in ("video", "gif"):
        input_args = ["-stream_loop", "-1", "-i", str(cover)]
    else:
        raise ValueError(f"Неизвестный kind: {kind}")

    audio_duration = probe_duration(audio)

    cmd = [
        "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
        *input_args,
        "-i", str(audio),
        # Явная раскладка потоков:
        #   вход 0 — компаньон (картинка / mp4 / gif) → только видеоряд
        #   вход 1 — mp3                                → только аудио
        # Без -map ffmpeg при наличии mp4 может взять его аудио вместо mp3.
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-tune", "stillimage", "-r", str(FPS),
        "-c:a", "aac", "-b:a", AUDIO_BITRATE,
        "-ar", str(AUDIO_RATE), "-ac", str(AUDIO_CHANNELS),
    ]

    if audio_duration is not None:
        cmd += ["-t", f"{audio_duration:.3f}"]
        cmd += ["-frames:v", str(int(math.ceil(audio_duration * FPS)) + 1)]
    else:
        print(f"⚠️  Не удалось определить длительность {audio.name}, "
              f"использую -shortest", file=sys.stderr)
        cmd += ["-shortest"]

    cmd.append(str(segment))

    print("   $ " + " ".join(cmd))
    try:
        run(cmd)
    except subprocess.TimeoutExpired:
        sys.stderr.write(
            f"❌ ffmpeg не уложился в {FFMPEG_TIMEOUT}с "
            f"при сборке {segment.name}. Команда:\n  "
            + " ".join(cmd) + "\n"
        )
        raise


def concat_segments(list_file: Path, output: Path) -> None:
    run([
        "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", "-movflags", "+faststart", str(output),
    ])


def main() -> int:
    if shutil.which("ffmpeg") is None:
        print("❌ ffmpeg не найден в PATH", file=sys.stderr)
        return 1
    if shutil.which("ffprobe") is None:
        print("⚠️  ffprobe не найден — обрезка по длине mp3 может быть "
              "менее точной (будет использован -shortest)", file=sys.stderr)

    MP3_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    mp3_files = sorted(
        (f for f in MP3_DIR.iterdir()
         if f.is_file() and f.suffix.lower() == ".mp3"),
        key=lambda p: p.name.lower(),
    )
    if not mp3_files:
        print(f"❌ В {MP3_DIR}/ нет mp3-файлов", file=sys.stderr)
        return 1

    if FINAL.exists():
        try:
            FINAL.unlink()
            print(f"ℹ️  Старый {FINAL} удалён (будет перезаписан)")
        except OSError as e:
            print(f"⚠️  Не удалось удалить старый {FINAL}: {e}", file=sys.stderr)

    list_file = TMP_DIR / "list.txt"
    segments: list[Path] = []

    try:
        for i, audio in enumerate(mp3_files, start=1):
            found = find_cover(audio)
            if found is not None:
                cover, kind = found
                kind_label = {"image": "картинка",
                              "video": "видео",
                              "gif": "gif"}[kind]
            elif DEFAULT_COVER.is_file():
                cover = DEFAULT_COVER
                kind = kind_by_ext(DEFAULT_COVER)
                kind_label = "default"
                print(f"⚠️  Нет картинки/видео/gif для {audio.name}, "
                      f"использую {DEFAULT_COVER}")
            else:
                print(
                    f"❌ Нет картинки/видео/gif для {audio.name} "
                    f"и нет {DEFAULT_COVER}",
                    file=sys.stderr,
                )
                return 1

            segment = TMP_DIR / f"seg_{i:03d}.mp4"
            print(f"🎬 [{i}/{len(mp3_files)}] {audio.name}  "
                  f"({kind_label}: {cover.name})")
            make_segment(cover, audio, segment, kind)
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

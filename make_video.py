#!/usr/bin/env python3
"""Склейка MP3/M4A + (jpg/png/webp | mp4 | gif) в один mp4/video.mp4.

Сначала извлекает встроенные обложки из аудиофайлов (если нет готовых картинок).
Затем собирает видео.
Длина каждого сегмента ВСЕГДА равна длине аудиофайла (приоритет аудио).
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
DEFAULT_COVER = Path("default.mp4")   # заглушка: видеоряд из этого файла, звук — из аудио
FINAL         = OUT_DIR / "video.mp4"

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp",
              ".JPG", ".JPEG", ".PNG", ".WEBP")
VIDEO_EXTS = (".mp4", ".MP4")
GIF_EXTS   = (".gif", ".GIF")

WIDTH, HEIGHT   = 640, 360
FPS_IMAGE = 5    # обложки, default.mp4
FPS_VIDEO = 24   # реальные .mp4 / .gif компаньоны
VIDEO_CRF       = "30"       # YouTube всё равно пережмёт 360p до ~1 Mbps

# ---- АУДИО: максимум качества, совместимый с YouTube ----
AUDIO_BITRATE   = "320k"     # AAC-LC 320 kbps — практический максимум для YouTube
AUDIO_RATE      = 48000      # рекомендация YouTube (и лучше, чем 44.1k при апсемплинге 48k-источников)
AUDIO_CHANNELS  = 2
AUDIO_PROFILE   = "aac_low"  # AAC-LC — обязателен для широкой совместимости

FFMPEG_TIMEOUT  = 600


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


def probe_duration(media: Path) -> float | None:
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
    """Ищет файл-компаньон с тем же стемом: image → mp4 → gif."""
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


# ==================== БЛОК ИЗВЛЕЧЕНИЯ ОБЛОЖЕК ====================

def extract_embedded_cover(audio: Path, target: Path) -> bool:
    """Извлекает первый видеопоток (обложку) из аудиофайла в target (.jpg)."""
    if target.exists():
        try:
            target.unlink()
        except OSError:
            pass

    result = run(
        ["ffmpeg", "-y", "-nostdin", "-loglevel", "error",
         "-i", str(audio),
         "-map", "0:v:0",      # первый видеопоток = обложка
         "-frames:v", "1",     # один кадр
         "-q:v", "2",          # качество JPEG (2 ≈ очень хорошее)
         str(target)],
        check=False,
    )
    if result.returncode != 0:
        if target.exists():
            try:
                target.unlink()
            except OSError:
                pass
        return False

    return target.exists() and target.stat().st_size > 0


def extract_covers_from_dir(directory: Path) -> None:
    """Проходит по всем mp3/m4a и извлекает обложки, если нет компаньонов."""
    audio_files = sorted(
        (f for f in directory.iterdir()
         if f.is_file() and f.suffix.lower() in (".mp3", ".m4a")),
        key=lambda p: p.name.lower(),
    )
    if not audio_files:
        return

    print(f"🎵 Предварительное извлечение обложек: найдено {len(audio_files)} аудиофайлов")
    extracted = skipped_img = skipped_vid = failed = 0

    for i, audio in enumerate(audio_files, start=1):
        prefix = f"[{i}/{len(audio_files)}] {audio.name}"

        # 1) уже есть картинка — не трогаем
        img = None
        for ext in IMAGE_EXTS:
            p = audio.with_suffix(ext)
            if p.is_file():
                img = p
                break
        if img is not None:
            skipped_img += 1
            continue

        # 2) есть одноимённый mp4 — не трогаем
        vid = None
        for ext in VIDEO_EXTS:
            p = audio.with_suffix(ext)
            if p.is_file():
                vid = p
                break
        if vid is not None:
            skipped_vid += 1
            continue

        # 3) пытаемся извлечь встроенную обложку
        target = audio.with_suffix(".jpg")
        if extract_embedded_cover(audio, target):
            print(f"✅ {prefix} — обложка извлечена в {target.name}")
            extracted += 1
        else:
            print(f"⚠️  {prefix} — встроенной обложки нет", file=sys.stderr)
            failed += 1

    print(f"📊 Итог извлечения: извлечено {extracted}, пропущено {skipped_img + skipped_vid}, без обложки {failed}\n")

# =================================================================

def make_segment(cover: Path, audio: Path, segment: Path, kind: str) -> None:
    vf = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
    )

    if kind == "image":
        input_args = ["-loop", "1", "-framerate", str(FPS_IMAGE), "-i", str(cover)]
        video_opts = [
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "stillimage",
            "-crf", VIDEO_CRF,
            "-x264-params",
            "keyint=999999:min-keyint=999999:scenecut=0:ref=1:bframes=0:"
            "subme=0:me=dia:trellis=0:weightp=0:8x8dct=0:aq-mode=0:psy-rd=0",
            "-threads", "1",
        ]
    elif kind in ("video", "gif"):
        input_args = ["-stream_loop", "-1", "-i", str(cover)]
        video_opts = [
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "26",
            "-x264-params", "keyint=250:min-keyint=25",
            "-threads", "1",
        ]
    else:
        raise ValueError(f"Неизвестный kind: {kind}")

    audio_duration = probe_duration(audio)

    cmd = [
        "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
        *input_args,
        "-i", str(audio),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-vf", vf,
        *video_opts,
        "-r", str(FPS_VIDEO),
        "-c:a", "aac",
        "-profile:a", AUDIO_PROFILE,
        "-b:a", AUDIO_BITRATE,
        "-ar", str(AUDIO_RATE),
        "-ac", str(AUDIO_CHANNELS),
        "-aac_coder", "twoloop",     # лучший из встроенных кодеров (не всегда есть libfdk_aac)
    ]

    if audio_duration is not None:
        cmd += ["-t", f"{audio_duration:.3f}"]
        cmd += ["-frames:v", str(int(math.ceil(audio_duration * FPS_VIDEO)) + 1)]
    else:
        cmd += ["-shortest"]

    cmd.append(str(segment))
    print("   $ " + " ".join(cmd))
    run(cmd)


def concat_segments(list_file: Path, output: Path) -> None:
    run([
        "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", "-movflags", "+faststart", str(output),
    ])


def _has_video_stream(path: Path) -> bool:
    """True, если у файла есть хотя бы один видеопоток."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error",
             "-select_streams", "v:0",
             "-show_entries", "stream=codec_type",
             "-of", "default=noprint_wrappers=1:nokey=1",
             str(path)],
            check=False, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=15,
        )
        return r.returncode == 0 and "video" in r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_conversion(
    output: Path,
    default_video: Path,
    mp3_dir: Path = Path("mp3"),
    tmp_dir: Path | None = None,
    on_segment=None,   # callable(idx, total) — после каждого готового сегмента    
) -> int:
    """
    Собирает один mp4 из аудиофайлов (mp3/m4a) каталога `mp3_dir`.
    """
    if shutil.which("ffmpeg") is None:
        print("❌ ffmpeg не найден в PATH", file=sys.stderr)
        return 1
    if shutil.which("ffprobe") is None:
        print("⚠️  ffprobe не найден — обрезка по длине аудио может быть "
              "менее точной (будет использован -shortest)", file=sys.stderr)

    out_dir = output.parent
    if str(out_dir) in ("", "."):
        out_dir = Path(".")
    if tmp_dir is None:
        tmp_dir = out_dir / ".temp"

    if default_video.is_file() and not _has_video_stream(default_video):
        print(f"⚠️  В {default_video} нет видеопотока — fallback не сработает",
              file=sys.stderr)

    mp3_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # --- ШАГ 1: ИЗВЛЕЧЕНИЕ ОБЛОЖЕК ---
    extract_covers_from_dir(mp3_dir)

    # --- ШАГ 2: СБОРКА ВИДЕО ---
    # Ищем и .mp3, и .m4a
    audio_files = sorted(
        (f for f in mp3_dir.iterdir()
         if f.is_file() and f.suffix.lower() in (".mp3", ".m4a")),
        key=lambda p: p.name.lower(),
    )
    if not audio_files:
        print(f"❌ В {mp3_dir}/ нет файлов .mp3 или .m4a", file=sys.stderr)
        return 1

    if output.exists():
        try:
            output.unlink()
            print(f"ℹ️  Старый {output} удалён (будет перезаписан)")
        except OSError as e:
            print(f"⚠️  Не удалось удалить старый {output}: {e}",
                  file=sys.stderr)

    list_file = tmp_dir / "list.txt"
    segments: list[Path] = []

    try:
        for i, audio in enumerate(audio_files, start=1):
            # ---- Цепочка приоритетов для компаньона ----
            found = find_cover(audio)

            if found is not None:
                cover, kind = found
                kind_label = {"image": "картинка",
                              "video": "видео",
                              "gif": "gif"}[kind]
            else:
                # 4) default_video (если нет файлов-компаньонов)
                if default_video.is_file():
                    cover = default_video
                    kind = kind_by_ext(default_video)
                    kind_label = "default"
                    print(f"⚠️  Нет файла-обложки для {audio.name}, использую {default_video}")
                else:
                    print(
                        f"❌ Нет файла-обложки для {audio.name} и нет {default_video}",
                        file=sys.stderr,
                    )
                    return 1

            segment = tmp_dir / f"seg_{i:03d}.mp4"
            print(f"🎬 [{i}/{len(audio_files)}] {audio.name}  "
                  f"({kind_label}: {cover.name})")
            make_segment(cover, audio, segment, kind)
            segments.append(segment)

            if on_segment is not None:
                try:
                    on_segment(i, len(audio_files))
                except Exception as e:
                    print(f"⚠️  on_segment failed: {e}", file=sys.stderr)            

        list_file.write_text(
            "\n".join(f"file '{seg.resolve().as_posix()}'"
                      for seg in segments) + "\n",
            encoding="utf-8",
        )

        print(f"🔗 Склейка {len(segments)} сегментов → {output}")
        concat_segments(list_file, output)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"✅ Готово: {output}")
    return 0


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="make_video",
        description="Склейка MP3/M4A + обложки в один mp4.",
    )
    parser.add_argument(
        "--output", default="mp4/video.mp4",
        help="Путь к результирующему mp4 (по умолчанию mp4/video.mp4)",
    )
    parser.add_argument(
        "--default_video", default="default.mp4",
        help="Видео-заглушка для отсутствующих обложек (по умолчанию default.mp4)",
    )
    parser.add_argument(
        "--mp3_dir", default="mp3",
        help="Каталог с аудиофайлами и одноимёнными компаньонами (по умолчанию mp3)",
    )
    args, _ = parser.parse_known_args(argv)

    return run_conversion(
        output=Path(args.output),
        default_video=Path(args.default_video),
        mp3_dir=Path(args.mp3_dir),
    )


if __name__ == "__main__":
    raise SystemExit(main())

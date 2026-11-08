#!/bin/bash
set -e

mkdir -p mp4 temp
shopt -s nullglob
files=(mp3/*.mp3)          # алфавитный порядок; замените на свой массив, если нужен другой
shopt -u nullglob

if [ ${#files[@]} -eq 0 ]; then
    echo "❌ В mp3/ нет файлов"
    exit 1
fi

LIST=temp/list.txt
: > "$LIST"

i=0
for audio in "${files[@]}"; do
    i=$((i+1))
    base=$(basename "$audio" .mp3)
    cover="temp/${base}.jpg"
    seg=$(printf "temp/seg_%03d.mp4" "$i")

    # Извлекаем встроенную обложку (ID3 APIC), если она есть
    ffmpeg -y -loglevel error -i "$audio" -an -vcodec copy "$cover" 2>/dev/null || true
    if [ ! -f "$cover" ]; then
        echo "⚠️  Нет обложки для $audio, использую default.jpg"
        cover="default.jpg"
    fi

    # Готовим сегмент: статичная обложка 360p + аудио
    ffmpeg -y -loglevel error -loop 1 -framerate 1 -i "$cover" -i "$audio" \
        -vf "scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,format=yuv420p" \
        -c:v libx264 -preset fast -tune stillimage -r 1 \
        -c:a aac -b:a 192k -ar 44100 -ac 2 -shortest \
        "$seg"

    echo "✅ Обработан: $audio"
    echo "file '$(realpath "$seg")'" >> "$LIST"
done

# Склейка всех сегментов без перекодирования
ffmpeg -y -loglevel error -f concat -safe 0 -i "$LIST" \
       -c copy -movflags +faststart mp4/final.mp4

rm -rf temp
echo "🎬 Готово: mp4/final.mp4"

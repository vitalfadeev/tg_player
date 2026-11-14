#!/bin/sh

# Headless: топ-10 за неделю по реакциям 🏆 ❤ ❤️‍🔥 ⚡ 👍 ❤️‍🔥 в ./mp3
./venv/bin/python main.py \
    --url=https://t.me/sunochatmix/26116 \
    --period=pin \
    --pin=https://t.me/sunochatmix/26116/205872 \
    --reactions="🏆" \
    --tracks=1 \
    --download=./mp3-battle \
    --make_video=./mp3-battle/video/video.mp4 \
    --default_video=./default.mp4

# --pin=https://t.me/sunochatmix/26116/222854
 
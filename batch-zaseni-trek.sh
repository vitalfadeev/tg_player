#!/bin/sh

# Headless: топ-10 за неделю по реакциям 🏆 ❤ ❤️‍🔥 ⚡ 👍 ❤️‍🔥 в ./mp3
./venv/bin/python main.py \
    --url=https://t.me/sunochatmix/1526 \
    --period=yesterday \
    --reactions="🏆" \
    --tracks=10 \
    --download=./mp3 \
    --make_video=./mp3/video/video.mp4 \
    --default_video=./default.mp4

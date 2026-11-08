@echo off
setlocal enabledelayedexpansion
if not exist "mp4" mkdir mp4
if not exist "temp" mkdir temp

set "LIST=temp\list.txt"
if exist "%LIST%" del "%LIST%"

set /a i=0
for %%F in (mp3\*.mp3) do (
    set /a i+=1
    set "audio=%%F"
    set "base=%%~nF"
    set "cover=temp\!base!.jpg"
    set "seg=temp\seg_!i!.mp4"

    ffmpeg -y -loglevel error -i "!audio!" -an -vcodec copy "!cover!" 2>nul
    if not exist "!cover!" (
        echo [!] Нет обложки для !audio!, использую default.jpg
        set "cover=default.jpg"
    )

    ffmpeg -y -loglevel error -loop 1 -framerate 1 -i "!cover!" -i "!audio!" ^
        -vf "scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,format=yuv420p" ^
        -c:v libx264 -preset fast -tune stillimage -r 1 ^
        -c:a aac -b:a 192k -ar 44100 -ac 2 -shortest "!seg!"

    echo Обработан: !audio!
    echo file 'temp/seg_!i!.mp4' >> "%LIST%"
)

ffmpeg -y -loglevel error -f concat -safe 0 -i "%LIST%" -c copy -movflags +faststart mp4\final.mp4

rmdir /s /q temp
echo Готово: mp4\final.mp4
pause

# main.py
import argparse
import asyncio
import functools
import re
import sys
from pathlib import Path

import flet as ft

import tg_backend
import config
from backend import TelegramBackend
from ui import TelegramPlayerUI


# -------- Разбор эмодзи-реакций --------
_EMOJI_PATTERN = re.compile(
    r'('
    r'[\U0001F1E6-\U0001F1FF]{2}'
    r'|'
    r'[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF]'
    r'(?:\uFE0F|\uFE0E)?'
    r'(?:[\U0001F3FB-\U0001F3FF])?'
    r'(?:\u200D[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF]'
    r'(?:\uFE0F|\uFE0E)?(?:[\U0001F3FB-\U0001F3FF])?)*'
    r')'
)


def _split_emojis(text: str) -> list[str]:
    return _EMOJI_PATTERN.findall(text)


def parse_reactions(value: str) -> list[str]:
    if not value:
        return []
    value = value.strip()
    if not value:
        return []
    if ',' in value:
        return [r.strip() for r in value.split(',') if r.strip()]
    if ' ' in value:
        return [r for r in value.split() if r]
    return _split_emojis(value)


def parse_tg_url(url: str) -> tuple[str | None, int | None]:
    """
    Разбирает ссылку Telegram и возвращает (forum, topic).
    Лишние сегменты (например, ID сообщения) игнорируются.

    Поддерживаемые формы:
      https://t.me/<username>/<topic>
      https://t.me/<username>
      https://t.me/c/<channel_id>/<topic>
      t.me/<username>/<topic>
      @<username>
    """
    if not url:
        return None, None

    s = url.strip()
    s = re.sub(r'^https?://', '', s, flags=re.IGNORECASE)
    s = s.split('?', 1)[0].split('#', 1)[0]
    s = re.sub(r'^(?:www\.)?(?:t|telegram)\.me/', '', s, flags=re.IGNORECASE)

    parts = [p for p in s.split('/') if p]
    if not parts:
        return None, None

    # Приватный канал: /c/<channel_id>/<topic>
    if parts[0] == 'c' and len(parts) >= 2 and parts[1].isdigit():
        forum = f"-100{parts[1]}"
        topic = None
        if len(parts) >= 3:
            try:
                topic = int(parts[2])
            except ValueError:
                topic = None
        return forum, topic

    # Публичный канал/юзер: /<username>/<topic>
    forum = parts[0].lstrip('@')
    topic = None
    if len(parts) >= 2:
        try:
            topic = int(parts[1])
        except ValueError:
            topic = None
    return forum, topic


def parse_pin_message_id(url: str) -> int | None:
    """
    Извлекает ID сообщения из ссылки `--pin` — берётся ПОСЛЕДНЕЕ число в URL.

    Примеры:
      https://t.me/sunochatmix/26116/222854   → 222854
      https://t.me/c/1234567890/26116/222854  → 222854
      https://t.me/sunochatmix/222854         → 222854

    :return: int или None, если чисел в ссылке нет.
    """
    if not url:
        return None
    s = url.strip().split('?', 1)[0].split('#', 1)[0]
    numbers = re.findall(r'\d+', s)
    if not numbers:
        return None
    return int(numbers[-1])


def resolve_target(args: argparse.Namespace) -> tuple[str | None, int | None]:
    """
    Итоговые (forum, topic) с приоритетом:
      --forum / --topic (CLI) > значения из --url > значения из --pin.

    :return: (forum, topic); любое поле может быть None.
    """
    url_forum, url_topic = parse_tg_url(getattr(args, "url", None) or "")
    pin_forum, pin_topic = parse_tg_url(getattr(args, "pin", None) or "")

    forum = args.forum if args.forum else (url_forum or pin_forum)
    topic = (
        args.topic
        if args.topic is not None
        else (url_topic if url_topic is not None else pin_topic)
    )
    return forum, topic


def resolve_pin_id(args: argparse.Namespace) -> int | None:
    """
    Итоговый pinned_id:
      --pin_id (явно задан) > последнее число из --pin-ссылки.

    :return: int или None.
    """
    if getattr(args, "pin_id", None) is not None:
        return args.pin_id
    pin_url = getattr(args, "pin", None)
    if pin_url:
        return parse_pin_message_id(pin_url)
    return None


# -------- Аргументы --------
def parse_arguments(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="telegram-player",
        description="Telegram Player: CLI-режим (скачивание топ-треков) или UI.",
    )
    parser.add_argument(
        "--proxy",
        choices=["auto", "direct", "mtproto", "none"],
        default="auto",
        help="Режим соединения: "
             "auto (сначала напрямую, при неудаче — MTProxy) | "
             "direct / none (только напрямую) | "
             "mtproto (только MTProxy). По умолчанию: auto.",
    )
    parser.add_argument("--forum", help="Форум: @username, username или числовой ID")
    parser.add_argument("--topic", type=int, help="ID топика (ветки)")
    parser.add_argument(
        "--url", default=None,
        help="Ссылка Telegram: https://t.me/<username>/<topic>. "
             "Из неё берутся --forum и --topic; "
             "--forum/--topic переопределяют соответствующие части URL.",
    )
    parser.add_argument(
        "--period", default="week",
        choices=["all", "year", "month", "week", "yesterday", "day", "pin"],
        help="Период сбора: all|year|month|week|yesterday|day|pin (по умолчанию week)",
    )
    parser.add_argument(
        "--pin_id", type=int, default=None,
        help="ID закреплённого сообщения (для --period=pin). "
             "Переопределяет значение, извлечённое из --pin.",
    )
    parser.add_argument(
        "--pin", default=None,
        help="Ссылка на закреплённое сообщение: "
             "https://t.me/<username>/<topic>/<message_id>. "
             "Из неё берётся последнее число как pinned_id "
             "(нужен --period=pin). Форум и топик также извлекаются "
             "из ссылки, если не заданы через --forum/--topic/--url.",
    )
    parser.add_argument(
        "--download", default="./mp3",
        help="Каталог для скачанных файлов (по умолчанию ./mp3)",
    )
    parser.add_argument(
        "--tracks", type=int, default=10,
        help="Сколько треков скачать — топ по числу реакций (по умолчанию 10)",
    )
    parser.add_argument(
        "--reactions", default="",
        help="Фильтр по реакциям: 🏆❤❤️‍🔥⚡👍 (через запятую/пробел или подряд)",
    )
    parser.add_argument(
        "--make_video", default=None,
        help="Собрать mp4 из mp3 в указанный файл (например, ./mp4/video.mp4). "
             "Если не задан — конвертация не выполняется.",
    )
    parser.add_argument(
        "--default_video", default=None,
        help="Видео-заглушка для отсутствующих обложек (по умолчанию default.mp4)",
    )
    args, _ = parser.parse_known_args(argv)
    return args


# -------- Headless-режим --------
async def run_headless(args: argparse.Namespace) -> int:
    forum, topic = resolve_target(args)
    pin_id = resolve_pin_id(args)

    if not forum or topic is None:
        print("[CLI] Ошибка: нужен --url, --pin, либо --forum и --topic.",
              file=sys.stderr)
        return 2

    if args.period == "pin" and pin_id is None:
        print("[CLI] Ошибка: для --period=pin нужен --pin_id или --pin-ссылка.",
              file=sys.stderr)
        return 2

    # Наружу CLI выставляет короткое "pin", внутри tg_backend режим
    # исторически называется "pinned" — маппим здесь, в одном месте.
    period = "pinned" if args.period == "pin" else args.period
    reactions_list = parse_reactions(args.reactions)
    pinned_id_str = str(pin_id) if pin_id else "0"

    print(
        f"[CLI] forum={forum!r} topic={topic} period={period} "
        f"pinned_id={pinned_id_str} "
        f"tracks={args.tracks} download={args.download!r} "
        f"reactions={reactions_list or '<любые>'}"
    )

    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    client = None
    print(f"[CLI] Подключение к Telegram (режим: {args.proxy}) ...")
    try:
        client, used_mode = await tg_backend.login_and_connect(
            proxy_mode=args.proxy,
            direct_timeout=1.0,
            total_timeout=20.0,
        )
    except Exception as e:
        print(f"[CLI] Не удалось подключиться: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 1
    print(f"[CLI] Подключено через {used_mode}.")

    try:
        # Резолвим forum (username → id, либо оставляем int)
        fi = str(forum).lstrip("@")
        try:
            if fi.lstrip("-").isdigit():
                forum_entity = await client.get_entity(int(fi))
            else:
                forum_entity = await client.get_entity(fi)
        except Exception as e:
            print(f"[CLI] Не удалось разрешить форум {forum!r}: {e}",
                  file=sys.stderr)
            return 1

        forum_id = forum_entity.id
        title = getattr(forum_entity, "title", None) or str(forum_id)
        print(f"[CLI] Форум: {title} (id={forum_id})")

        forum_username = getattr(forum_entity, "username", None)

        raw = tg_backend.fetch_telegram_messages(
            client=client,
            forum_id=forum_id,
            topic_id=topic,
            pinned_id=pinned_id_str,
            start_pinned_id="0",
            time_filter=period,
            reactions=reactions_list or None,
            audio_only=True,
        )
        if asyncio.isfuture(raw) or isinstance(raw, asyncio.Task):
            messages, _ = await raw
        else:
            messages, _ = raw

        if not messages:
            print("[CLI] Сообщений по фильтрам не найдено.")
            return 1

        print(f"[CLI] Найдено: {len(messages)}; беру топ-{args.tracks}.")
        top = messages[: args.tracks]
        top_ids = [m["id"] for m in top]
        for i, m in enumerate(top, 1):
            tg_url = tg_backend._make_tg_link(
                forum_username, forum_id, topic, m["id"]
            )
            print(f"   #{i}: id={m['id']}, reactions={m['total']}, {tg_url}")

        config.OUTPUT_DIR = args.download
        print(f"[CLI] Скачивание в {args.download} ...")
        results = await tg_backend.download_audios_and_images_by_ids(
            client=client,
            forum_id=forum_id,
            topic_id=topic,
            message_ids=top_ids,
        )

        ok = sum(1 for r in results if r.get("audio_path"))
        print(f"[CLI] Готово: {ok}/{len(top)} треков скачано в {args.download}")
        return 0 if ok > 0 else 1

    finally:
        await client.disconnect()


# -------- UI-режим --------
def main(page: ft.Page, args: argparse.Namespace | None = None):
    if args is None:
        args = parse_arguments()

    print(f"[Main] Режим соединения (proxy): {args.proxy}")
    backend = TelegramBackend(client=None)   # клиент появится после подключения
    page.telegram_ready = asyncio.Event()
    app_ui = TelegramPlayerUI(page, backend)
    app_ui.build()

    async def start_telegram():
        print("[Main] Подключение к Telegram...")
        app_ui.connection_loader.visible = True
        page.update()
        try:
            client, used_mode = await tg_backend.login_and_connect(
                proxy_mode=args.proxy,
                direct_timeout=1.0,
            )
            backend.client = client
            print(f"[Main] Подключено через {used_mode}. Авторизация...")

            # ❌ БЫЛО:   async with client:
            # ✅ СТАЛО:  обычный try/finally — start() НЕ вызывается,
            #            значит интерактивный input() не всплывает.
            try:
                if not await client.is_user_authorized():
                    from auth_dialog import AuthDialog
                    print("[Main] Сессия не авторизована — показываю диалог входа.")
                    auth_ok = await AuthDialog(page, client).authenticate()
                    if not auth_ok:
                        print("[Main] Авторизация отменена пользователем.")
                        app_ui.connection_loader.controls.clear()
                        app_ui.connection_loader.controls.append(
                            ft.Text(
                                "Авторизация отменена. Перезапустите приложение.",
                                color=ft.Colors.RED_600,
                                weight=ft.FontWeight.BOLD,
                            )
                        )
                        page.update()
                        return
                    print("[Main] Авторизация успешна.")

                print(f"[Main] Telegram клиент готов (режим: {used_mode}).")
                app_ui.connection_loader.visible = False
                page.update()
                page.telegram_ready.set()
                while True:
                    await asyncio.sleep(3600)
            finally:
                try:
                    await client.disconnect()
                except Exception as ex:
                    print(f"[Main] Ошибка при отключении клиента: {ex}")

        except Exception as e:
            print(f"[Main] Ошибка при подключении к Telegram: {e}")
            app_ui.connection_loader.controls.clear()
            app_ui.connection_loader.controls.append(
                ft.Text(
                    f"Ошибка подключения к Telegram: {e}",
                    color=ft.Colors.RED_600,
                    weight=ft.FontWeight.BOLD,
                )
            )
            page.update()

    page.run_task(start_telegram)


if __name__ == "__main__":
    cli_args = parse_arguments()

    did_something = False

    _target_forum, _target_topic = resolve_target(cli_args)
    _has_target = _target_forum is not None and _target_topic is not None

    if _has_target:
        rc = asyncio.run(run_headless(cli_args))
        if rc != 0:
            sys.exit(rc)
        did_something = True

        import make_description
        # src_dir — это каталог, куда скачаны mp3 (--download, по умолчанию ./mp3).
        # description.txt кладём в <src_dir>/video/.
        src_dir = Path(cli_args.download)
        desc_dir = src_dir / "video"
        desc_file = desc_dir / "description.txt"

        print(f"[Main] Генерация описания: {desc_file}")
        rc_desc = make_description.generate_description(
            src_dir=Path(cli_args.download),
            out_file=desc_file,
        )
        if rc_desc != 0:
            print(f"[Main] ⚠️  Описание не сгенерировано (код {rc_desc}). "
                  f"Продолжаю.", file=sys.stderr)

    if cli_args.make_video:
        import make_video
        default_video = cli_args.default_video or "default.mp4"
        rc = make_video.run_conversion(
            output=Path(cli_args.make_video),
            default_video=Path(default_video),
            mp3_dir=Path(cli_args.download),
        )
        if rc != 0:
            sys.exit(rc)
        did_something = True

    if did_something:
        sys.exit(0)

    print("[Main] Запуск UI.")
    ft.run(functools.partial(main, args=cli_args), assets_dir="assets")

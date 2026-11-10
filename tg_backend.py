import os
import re
import json
import asyncio
import datetime
from datetime import time
from zoneinfo import ZoneInfo
from telethon import TelegramClient, connection
from telethon.tl.types import Channel, PeerUser, PeerChannel, PeerChat
from telethon.tl.functions.messages import GetForumTopicsRequest
from telethon.tl.types import InputMessagesFilterMusic
from telethon.tl.types import InputMessagesFilterPinned
MSK = ZoneInfo("Europe/Moscow")

# Импортируем настройки из созданного файла config.py
import config

_AUDIO_MIME_EXT = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/m4a": ".m4a",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/aac": ".aac",
}

def _has_image(message) -> bool:
    """Фото (message.photo) либо документ с MIME image/*."""
    if getattr(message, "photo", None):
        return True
    file = getattr(message, "file", None)
    if file is not None:
        mime = getattr(file, "mime_type", None)
        if mime and mime.startswith("image/"):
            return True
    return False

def _sanitize_filename(name: str) -> str:
    """Убирает из имени символы, недопустимые в именах файлов на большинстве ФС."""
    if not name:
        return ""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', '_', str(name)).strip()
    cleaned = re.sub(r'_+', '_', cleaned)   # схлопываем повторные подчёркивания
    return cleaned


async def _get_author_name(client, message, cache: dict) -> str:
    """
    Возвращает безопасное для ФС имя автора сообщения.
    Кэширует результат по sender_id / from_id, чтобы не дёргать сеть
    повторно для нескольких сообщений одного автора в одном пакете.
    """
    # Ключ кэша
    key = message.sender_id
    if key is None:
        from_id = getattr(message, "from_id", None)
        if from_id is not None:
            uid = getattr(from_id, "user_id", None)
            if uid is not None:
                key = uid
            else:
                cid = getattr(from_id, "channel_id", None)
                if cid is not None:
                    key = f"ch_{cid}"

    if key is not None and key in cache:
        return cache[key]

    # 1) Пытаемся получить отправителя через сам Message (Telethon ≥ 1.24)
    sender = None
    try:
        if hasattr(message, "get_sender"):
            sender = await message.get_sender()
    except Exception:
        sender = None

    # 2) Fallback — через client.get_entity
    if sender is None and message.sender_id is not None:
        try:
            sender = await client.get_entity(message.sender_id)
        except Exception:
            sender = None

    author_name = None
    if sender is not None:
        # Каналы/группы — title; пользователи — first/last или username
        title = getattr(sender, "title", None)
        if title:
            author_name = title
        else:
            first = getattr(sender, "first_name", None) or ""
            last  = getattr(sender, "last_name",  None) or ""
            full  = f"{first} {last}".strip()
            if full:
                author_name = full
            elif getattr(sender, "username", None):
                author_name = sender.username

    if not author_name:
        author_name = f"user_{message.sender_id}" if message.sender_id is not None else "unknown"

    safe = _sanitize_filename(author_name) or "unknown"
    if key is not None:
        cache[key] = safe
    return safe

def _fmt_duration(sec) -> str:
    """Форматирует длительность в 'MM:SS (SS.mmm сек)'. None → 'n/a'."""
    if sec is None:
        return "n/a"
    total = int(round(sec))
    m, s = divmod(total, 60)
    return f"{m:02d}:{s:02d} ({sec:.3f} сек)"


def _read_audio_meta(path) -> tuple[float | None, list[str]]:
    """
    Читает длительность и WXXX-ссылки из аудиофайла.

    WXXX — «user defined URL link frame» в ID3v2 (обычно mp3); для не-ID3
    форматов (ogg/m4a/flac с vorbis-комментариями) список пустой.

    Порядок определения длительности:
      1) mutagen.File(...).info.length (любой формат: mp3/ogg/m4a/flac/...);
      2) если mutagen недоступен/не смог — ffprobe format.duration.

    :return: (duration_sec: float|None, wxxx_list: list[str])
    """
    duration = None
    wxxx_links: list[str] = []

    # --- 1. mutagen: длительность + WXXX ---
    try:
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3
        audio = MutagenFile(path)
        if audio is not None:
            info = getattr(audio, "info", None)
            if info is not None:
                length = getattr(info, "length", None)
                if (isinstance(length, (int, float))
                        and length > 0
                        and length == length            # отсекаем NaN
                        and length != float("inf")):    # и inf
                    duration = float(length)
            tags = getattr(audio, "tags", None)
            if isinstance(tags, ID3):
                for frame in tags.getall("WXXX"):
                    url = getattr(frame, "url", None)
                    desc = getattr(frame, "desc", "") or ""
                    if url:
                        wxxx_links.append(f"{desc}: {url}" if desc else str(url))
    except ImportError:
        pass   # mutagen не установлен — пойдём через ffprobe
    except Exception as e:
        print(f"[Warning] mutagen не прочитал {path}: {e}")

    # --- 2. Fallback: ffprobe для длительности ---
    if duration is None:
        try:
            import subprocess
            res = subprocess.run(
                ["ffprobe", "-v", "error",
                 "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1",
                 str(path)],
                check=False, capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=30,
            )
            if res.returncode == 0:
                val = res.stdout.strip()
                if val and val != "N/A":
                    d = float(val)
                    if d > 0 and d == d and d != float("inf"):
                        duration = d
        except Exception as e:
            print(f"[Warning] ffprobe не прочитал {path}: {e}")

    return duration, wxxx_links


def _make_tg_link(forum_username, forum_id, topic_id, message_id) -> str:
    """
    Строит tg:// ссылку на сообщение в топике.

    * Публичный канал (есть username) — tg://resolve?domain=…&thread=…&post=…
    * Приватный (нет username) — tg://privatepost?channel=…&post=…
      (у privatepost нет thread; топик теряется, но сообщение открывается).
    """
    if forum_username:
        return (f"tg://resolve?domain={forum_username}"
                f"&thread={topic_id}&post={message_id}")
    raw = str(forum_id)
    channel = raw[4:] if raw.startswith("-100") else raw.lstrip("-")
    return f"tg://privatepost?channel={channel}&post={message_id}"


def login(session_name='session_faketls',proxy_mode='mtproto'):
    """
    Инициализирует и возвращает настроенный клиент TelegramClient.
    Для отправки запросов клиент необходимо активировать через 'async with client'.
    """
    client = None
    if proxy_mode == 'mtproto':
        print("Подключение через FakeTLS MTProxy...")
        client = TelegramClient(
            session_name, 
            config.API_ID, 
            config.API_HASH,
            connection=connection.ConnectionTcpMTProxyRandomizedIntermediate,
            proxy=(config.PROXY_IP, config.PROXY_PORT, config.PROXY_SECRET)
        )
    else:
        print("Подключение напрямую...")
        client = TelegramClient(
            session_name, 
            config.API_ID, 
            config.API_HASH
        )
    return client

async def get_forums(client, base_data_dir="data"):
    """
    Получает список групп-форумов, сохраняет forums.json в base_data_dir 
    и создает подкаталоги с ID форумов. Возвращает список объектов форумов.
    """
    if not os.path.exists(base_data_dir):
        os.makedirs(base_data_dir)

    print("Сбор информации о форумах...")
    forums_list = []
    
    async for dialog in client.iter_dialogs():
        if isinstance(dialog.entity, Channel):
            if dialog.entity.forum:
                forum_id = dialog.id
                
                forum_data = {
                    "id": forum_id,
                    "title": dialog.name,
                    "username": dialog.entity.username if dialog.entity.username else None,
                    # pinned_msg_id
                }
                forums_list.append(forum_data)
                
                # Создаем подкаталог с ID форума внутри каталога data
                forum_dir = os.path.join(base_data_dir, str(forum_id))
                if not os.path.exists(forum_dir):
                    os.makedirs(forum_dir)
    
    output_file = os.path.join(base_data_dir, "forums.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(forums_list, f, ensure_ascii=False, indent=4)
        
    print(f"[Успешно] Данные сохранены в файл: {output_file}")
    print(f"Всего найдено форумов: {len(forums_list)}")
    
    return forums_list

async def get_topics(client, forum_id, base_data_dir="data"):
    """
    Получает список топиков (разделов) для конкретного форума по его ID из Telegram.
    Сохраняет список в файл data/{forum_id}/topics.json и возвращает его.
    """
    forum_dir = os.path.join(base_data_dir, str(forum_id))
    if not os.path.exists(forum_dir):
        os.makedirs(forum_dir)

    print(f"Запрос топиков для форума ID {forum_id}...")
    
    try:
        forum_entity = await client.get_entity(int(forum_id))
        topics_result = await client(GetForumTopicsRequest(
            peer=forum_entity,
            offset_date=None,
            offset_id=0,
            offset_topic=0,
            limit=100
        ))
    except Exception as e:
        print(f"[Ошибка] Не удалось получить топики для форума {forum_id}: {e}")
        return []

    topics_list = []
    for topic in topics_result.topics:
        if hasattr(topic, 'title'):
            topic_data = {
                "id": topic.id,
                "title": topic.title,
                "closed": getattr(topic, 'closed', False),
                "pinned": getattr(topic, 'pinned', False)
            }
            topics_list.append(topic_data)

    output_file = os.path.join(forum_dir, "topics.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(topics_list, f, ensure_ascii=False, indent=4)
        
    print(f"[Успешно] Топики сохранены в файл: {output_file}")
    return topics_list

def get_topics_for_forum(forum_id, base_data_dir="data"):
    """
    Синхронная функция. Читает сохраненный локальный файл data/{forum_id}/topics.json 
    и возвращает список топиков без отправки сетевых запросов.
    """
    file_path = os.path.join(base_data_dir, str(forum_id), "topics.json")
    
    if not os.path.exists(file_path):
        print(f"[Ошибка] Файл не найден: {file_path}. Сначала необходимо вызвать асинхронную get_topics().")
        return []
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            topics_list = json.load(f)
        return topics_list
    except Exception as e:
        print(f"[Ошибка] Не удалось прочитать локальный файл {file_path}: {e}")
        return []

def msg_from_tgmssage(message,reactions):
    msg_reactions = []
    total_filtered_reactions = 0
    if message.reactions and message.reactions.results:
        for r in message.reactions.results:
            # Проверяем наличие атрибута emoticon (он есть только у ReactionEmoji)
            if hasattr(r.reaction, 'emoticon'):
                msg_reactions.append(r.reaction.emoticon)
            elif hasattr(r.reaction, 'document_id'):
                # Для кастомных премиум-эмодзи подставляем заглушку-звездочку
                msg_reactions.append("⭐")

            if reactions:
                if hasattr(r.reaction, 'emoticon'):
                    if r.reaction.emoticon in reactions:
                        total_filtered_reactions += r.count
            else:
                total_filtered_reactions += r.count

    # file name
    has_file = False
    file_name = None
    if message.media and not message.photo:
        has_file = True
        file_name = getattr(message.file, 'name', None)
        if not file_name:
            file_name = f"file_{message.id}_{message.date.strftime('%Y%m%d_%H%M%S')}"

    msg = {
        "id"        : message.id,
        "date"      : message.date.isoformat(),
        "raw_text"  : message.raw_text if message.raw_text else "",
        "text"      : message.text if message.text else "",
        "sender_id" : message.sender_id,
        "reactions" : msg_reactions,
        "total"     : total_filtered_reactions,
        "has_file"  : has_file,
        "file_name" : file_name,
        "audio"     : True if message.audio else False,
    }

    return msg     

async def get_pinned_message(client: TelegramClient, forum_id: int, topic_id: int):
    forum_entity = await client.get_entity(int(forum_id))

    async for message in client.iter_messages(forum_entity, reply_to=int(topic_id), limit=1000):
        if message.pinned:
            return msg_from_tgmssage (message,[])

    return None

async def get_messages_from_last_pin_to_end(client: TelegramClient, forum_id: int, topic_id: int, audio_only: bool, reactions: list):
    """
    Возвращает сообщения в топике `topic_id` форума `forum_id`
    от последнего закреплённого сообщения до самого нового.
    """
    messages = []
    forum_entity = await client.get_entity(int(forum_id))

    async for message in client.iter_messages(forum_entity, reply_to=int(topic_id)):
        if message.pinned:
            break
        messages.append(msg_from_tgmssage(message,reactions))

    return messages

async def get_messages_all(client: TelegramClient, forum_id: int, topic_id: int, audio_only: bool, reactions: list):
    """
    Возвращает сообщения в топике `topic_id` форума `forum_id`
    от последнего закреплённого сообщения до самого нового.
    """
    messages = []
    forum_entity = await client.get_entity(int(forum_id))

    async for message in client.iter_messages(forum_entity, reply_to=int(topic_id)):
        messages.append(msg_from_tgmssage(message,reactions))

    return messages

async def get_messages_between_pins(
    client: TelegramClient,
    forum_id: int,
    topic_id: int,
    old_pinned_id: int,
    new_pinned_id: int,
    reverse: bool = False,
    reactions: list = []
):
    """
    Возвращает сообщения в топике `topic_id` форума `forum_id`,
    находящиеся между двумя закреплёнными сообщениями.

    :param old_pinned_id: ID более старого закреплённого сообщения.
    :param new_pinned_id: ID более нового закреплённого сообщения.
    :param reverse: если True, сообщения будут возвращены от старых к новым.
    """
    forum_entity = await client.get_entity(int(forum_id))

    # ID должны быть упорядочены: min_id < max_id
    min_id = min(old_pinned_id, new_pinned_id)
    max_id = max(old_pinned_id, new_pinned_id)

    messages = []
    async for message in client.iter_messages(
        forum_entity,
        reply_to=int(topic_id),   # фильтр по топику (ветке)
        min_id=min_id,            # исключает сообщения с ID <= min_id
        #max_id=max_id,            # исключает сообщения с ID >= max_id
        reverse=reverse            # порядок: по умолчанию от новых к старым
    ):
        messages.append(msg_from_tgmssage(message,reactions))

    return messages

def get_time_threshold (time_filter):
    start_date = None
    limit = None

    if time_filter == "day":
        # Сообщения за текущий календарный день (с 00:00)
        start_date = datetime.datetime.combine(
            datetime.datetime.now(MSK).date(),
            time.min,
            tzinfo=MSK,
        )    
    elif time_filter == "week":
        # Сообщения за последние 7 дней от текущего момента (с 00:00)
        today_msk = datetime.datetime.now(MSK).date()
        start_date = datetime.datetime.combine(
            today_msk - datetime.timedelta(days=7),
            time.min,
            tzinfo=MSK,
        )
    elif time_filter == "month":
        # Сообщения за последние 30 дней от текущего момента (с 00:00)
        today_msk = datetime.datetime.now(MSK).date()
        start_date = datetime.datetime.combine(
            today_msk.replace(day=1),
            time.min,
            tzinfo=MSK,
        )
    elif time_filter == "year":
        # Сообщения за последние 365 дней от текущего момента
        today_msk = datetime.datetime.now(MSK).date()
        start_date = datetime.datetime.combine(
            today_msk.replace(month=1,day=1),
            time.min,
            tzinfo=MSK,
        )
    elif time_filter == "all":
        limit = False
    else:
        limit = False

    return (start_date,limit)

async def get_messages_for_period(client: TelegramClient, forum_id: int, topic_id: int, time_filter: str, audio_only: bool, reactions: list):
    start_date, limit = get_time_threshold (time_filter)

    messages = []
    forum_entity = await client.get_entity(int(forum_id))

    async for message in client.iter_messages(forum_entity, reply_to=int(topic_id)):
        if message.date < start_date:
            break
        messages.append(msg_from_tgmssage(message,reactions))

    return messages

def fetch_telegram_messages(client, forum_id: str, topic_id: str, pinned_id: str, start_pinned_id:str, time_filter: str, reactions: list, audio_only: bool, limit=50, base_data_dir="data") -> list:
    """
    Синхронная функция для внешней программы. Запускает внутренний асинхронный процесс 
    сбора сообщений, сохраняет результат в JSON и возвращает список.
    """
    # Внутренняя асинхронная логика сбора
    async def _fetch():
        topic_dir = os.path.join(base_data_dir, str(forum_id), str(topic_id))
        if not os.path.exists(topic_dir):
            os.makedirs(topic_dir)

        print(f"Сбор сообщений из топика {topic_id} форума {forum_id}...")
        messages_list = []
        reactions_list = []

        # Вычисляем порог времени на основе переданного фильтра
        days = 0
        local_now = datetime.datetime.now()
        local_today_midnight = datetime.datetime.combine(local_now.date(), datetime.time.min)
        fetch_limit = limit

        print(f"Сбор сообщений из топика {topic_id} форума {forum_id} (лимит {fetch_limit})...")

        if time_filter == "pinned":
            messages = await get_messages_from_last_pin_to_end(client, forum_id, topic_id, audio_only, reactions)
        elif time_filter == "all":
            messages = await get_messages_all(client, forum_id, topic_id, audio_only, reactions)
        else:
            messages = await get_messages_for_period(client, forum_id, topic_id, time_filter, audio_only, reactions)

        for msg in messages:
            # reactions
            if reactions and msg.get("total",0) == 0:
                continue
            # audio
            if audio_only and msg.get("audio",False) == False:
                continue

            messages_list.append(msg)

        try:
            pass

        except Exception as e:
            print(f"[Ошибка] Не удалось получить сообщения по фильтру: {e}")
            return ([],[])

#        output_file = os.path.join(topic_dir, "messages.json")
#        with open(output_file, "w", encoding="utf-8") as f:
#            json.dump(messages_list, f, ensure_ascii=False, indent=4)
#
#        print(f"[Успешно] Отфильтрованные сообщения сохранены в: {output_file}")

        return (
            sorted(messages_list, key=lambda x: x["total"], reverse=True),
            [],   # пока, например, []
        )

    # Безопасный запуск асинхронного кода внутри обычной синхронной функции
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Если цикл уже запущен внешней программой (например, FastAPI)
        return loop.create_task(_fetch())
    else:
        # Если запускается в синхронном скрипте
        return asyncio.run(_fetch())

def get_total_reactions(message, reactions):
    total_filtered_reactions = 0
    from telethon.tl.types import ReactionEmoji
    
    if message.reactions:
        for reaction_count in message.reactions.results:
            # Проверяем, является ли реакция обычным эмодзи и имеет ли она emoticon
            if isinstance(reaction_count.reaction, ReactionEmoji) and hasattr(reaction_count.reaction, 'emoticon'):
                emoji = reaction_count.reaction.emoticon
                
                if emoji in reactions:
                    total_filtered_reactions += reaction_count.count
                    print(f"Найдена реакция {emoji}: {reaction_count.count} шт.")
            else:
                # Сюда попадают ReactionCustomEmoji. Если в выбранных фильтрах UI есть "⭐", 
                # мы можем посчитать и премиум-реакции
                if "⭐" in reactions and hasattr(reaction_count.reaction, 'document_id'):
                    total_filtered_reactions += reaction_count.count
                    print(f"Найдена кастомная реакция: {reaction_count.count} шт.")
                
    print(f"Всего подходящих реакций: {total_filtered_reactions}")
    return total_filtered_reactions

async def fetch_one_message(client, forum_id, topic_id, id):
    """
    Извлекает одно сообщение по его ID из указанного топика форума.
    
    :param client:      активированный TelegramClient
    :param forum_id:    ID форума (канала)
    :param topic_id:    ID топика (ветки) — используется для верификации
    :param id:          ID сообщения
    :return:            объект сообщения Telethon или None
    """
    try:
        forum_entity = await client.get_entity(int(forum_id))
        message = await client.get_messages(forum_entity, ids=int(id))

        # client.get_messages с ids возвращает объект или None
        if message is None:
            print(f"[Warning] Сообщение {id} не найдено в форуме {forum_id}.")
            return None

        # Дополнительная проверка принадлежности к топику (для форумов)
        # у сообщений внутри топика reply_to.reply_to_top_id == topic_id
        if topic_id:
            reply = getattr(message, "reply_to", None)
            top_id = getattr(reply, "reply_to_top_id", None) if reply else None
            if top_id is not None and int(top_id) != int(topic_id):
                print(f"[Warning] Сообщение {id} не принадлежит топику {topic_id}.")
                return None

        return message

    except Exception as e:
        print(f"[Ошибка] Не удалось получить сообщение {id}: {e}")
        return None

async def get_message_by_id(client: TelegramClient, forum_id: int, topic_id: int, message_id: int):
    """
    Возвращает одно сообщение по его ID из указанного топика форума.
    Тонкая обёртка над fetch_one_message (единое именование trio-функций).

    :param client:      активированный TelegramClient
    :param forum_id:    ID форума (канала)
    :param topic_id:    ID топика (ветки)
    :param message_id:  ID сообщения
    :return:            объект сообщения Telethon или None
    """
    return await fetch_one_message(client, forum_id, topic_id, message_id)


async def get_previous_message_from_same_author(
    client: TelegramClient,
    forum_id: int,
    topic_id: int,
    message_id: int,
):
    """
    Возвращает ближайшее более СТАРОЕ сообщение в том же топике
    от того же автора, что и сообщение с message_id.

    Поиск идёт от message_id назад (max_id = message_id, default order = от новых к старым),
    поэтому первое найденное сообщение от того же sender_id — самое близкое по времени.

    :return: объект сообщения Telethon или None (если не найдено / исходное сообщение отсутствует)
    """
    try:
        limit = 1
        original = await fetch_one_message(client, forum_id, topic_id, message_id)
        if original is None:
            return None

        sender_id = original.sender_id
        if sender_id is None:
            return None

        forum_entity = await client.get_entity(int(forum_id))

        async for message in client.iter_messages(
            forum_entity,
            reply_to=int(topic_id),
            max_id=int(message_id),   # только сообщения с ID < message_id (более старые)
        ):
            if message.sender_id == sender_id:
                return message

            limit -= 1
            if limit == 0:
                break

        return None
    except Exception as e:
        print(f"[Ошибка] get_previous_message_from_same_author({message_id}): {e}")
        return None


async def get_next_message_from_same_author(
    client: TelegramClient,
    forum_id: int,
    topic_id: int,
    message_id: int,
):
    """
    Возвращает ближайшее более НОВОЕ сообщение в том же топике
    от того же автора, что и сообщение с message_id.

    Поиск идёт от message_id вперёд (min_id = message_id, reverse=True = от старых к новым),
    поэтому первое найденное сообщение от того же sender_id — самое близкое по времени.

    :return: объект сообщения Telethon или None (если не найдено / исходное сообщение отсутствует)
    """
    try:
        limit = 1
        original = await fetch_one_message(client, forum_id, topic_id, message_id)
        if original is None:
            return None

        sender_id = original.sender_id
        if sender_id is None:
            return None

        forum_entity = await client.get_entity(int(forum_id))

        async for message in client.iter_messages(
            forum_entity,
            reply_to=int(topic_id),
            min_id=int(message_id),   # только сообщения с ID > message_id (более новые)
            reverse=True,             # от старых к новым
        ):
            if message.sender_id == sender_id:
                return message

            limit -= 1
            if limit == 0:
                break

        return None
    except Exception as e:
        print(f"[Ошибка] get_next_message_from_same_author({message_id}): {e}")
        return None


async def download_audios_and_images_by_ids(
    client: TelegramClient,
    forum_id: int,
    topic_id: int,
    message_ids: list,
):
    """
    Для каждого ID из `message_ids`:
      0. (ПЕРЕД ЦИКЛОМ) Удаляет все файлы из config.OUTPUT_DIR.
      1. Скачивает аудио в config.OUTPUT_DIR под именем NN<ext_аудио>.
      2. Ищет соседнее медиа того же автора (картинка → видео/GIF).
      3. Кладёт рядом текстовый файл NN.txt:
           id, автор, ссылка tg://, имя аудио, длина (MM:SS), картинка, видео,
           WXXX-ссылки из ID3v2.
      4. Все файлы перезаписываются.

    :return: [{"message_id", "seq", "audio_path", "image_path",
               "video_path", "txt_path", "duration_sec", "wxxx"}, ...]
    """
    if not os.path.exists(config.OUTPUT_DIR):
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    else:
        # ---- 0. Полная очистка каталога от файлов ----
        removed_files = 0
        failed_files = 0
        try:
            for entry in os.listdir(config.OUTPUT_DIR):
                fp = os.path.join(config.OUTPUT_DIR, entry)
                if os.path.isfile(fp) or os.path.islink(fp):
                    try:
                        os.remove(fp)
                        removed_files += 1
                    except Exception as e:
                        failed_files += 1
                        print(f"[Ошибка] Не удалось удалить {fp}: {e}")
            if removed_files or failed_files:
                print(f"[Инфо] Удалено файлов: {removed_files}"
                      + (f", не удалось: {failed_files}" if failed_files else ""))
            else:
                print("[Инфо] Каталог уже был пуст (файлов нет).")
        except Exception as e:
            print(f"[Ошибка] Не удалось очистить каталог {config.OUTPUT_DIR}: {e}")

    # ---- Username форума один раз (для tg:// ссылок) ----
    forum_username = None
    try:
        _forum_entity = await client.get_entity(int(forum_id))
        forum_username = getattr(_forum_entity, "username", None)
    except Exception as e:
        print(f"[Warning] Не удалось получить данные форума {forum_id}: {e}")

    results = []
    author_cache = {}

    for idx, message_id in enumerate(message_ids, start=1):
        seq = f"{idx:02d}"
        report = {
            "message_id": message_id,
            "seq": seq,
            "audio_path": None,
            "image_path": None,
            "video_path": None,
            "txt_path": None,
            "duration_sec": None,
            "wxxx": [],
        }

        # ---- 1. Исходное сообщение ----
        message = await fetch_one_message(client, forum_id, topic_id, message_id)
        if message is None:
            print(f"[Пропуск] Сообщение {message_id} не получено.")
            results.append(report)
            continue

        if not message.audio:
            print(f"[Пропуск] В сообщении {message_id} нет аудио.")
            results.append(report)
            continue

        author = await _get_author_name(client, message, author_cache)

        # ---- Расширение аудио ----
        raw_audio_name = getattr(message.file, "name", None) if message.file else None
        audio_stem = ""
        audio_ext = ""
        if raw_audio_name:
            stem, ext = os.path.splitext(raw_audio_name)
            audio_stem = stem or raw_audio_name
            audio_ext = ext or ""
        else:
            audio_stem = f"audio_{message.id}_{message.date.strftime('%Y%m%d_%H%M%S')}"

        if not audio_ext:
            mime = (getattr(message.file, "mime_type", None) or "") if message.file else ""
            audio_ext = _AUDIO_MIME_EXT.get(mime, "")
        if not audio_ext:
            audio_ext = ".mp3"

        audio_name = f"{seq}{audio_ext}"
        audio_path = os.path.join(config.OUTPUT_DIR, audio_name)

        if os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except Exception as e:
                print(f"[Ошибка] Не удалось удалить {audio_path}: {e}")
                results.append(report)
                continue

        msg_local_time = message.date.astimezone()
        print(f"Найдено в {msg_local_time.strftime('%H:%M:%S')}. "
              f"Скачивание аудио: {audio_name}...")
        try:
            downloaded = await client.download_media(message, file=audio_path)
            print(f"[Успешно] Аудио сохранено: {downloaded}")
            report["audio_path"] = downloaded
        except Exception as e:
            print(f"[Ошибка] Не удалось скачать аудио {audio_name}: {e}")
            results.append(report)
            continue

        # ---- Длительность + WXXX из скачанного аудио ----
        audio_duration, wxxx_links = _read_audio_meta(audio_path)
        report["duration_sec"] = audio_duration
        report["wxxx"] = wxxx_links
        if audio_duration is not None:
            print(f"   длительность: {_fmt_duration(audio_duration)}")
        if wxxx_links:
            print(f"   WXXX: {len(wxxx_links)} ссылк(и)")

        # ---- 2. Соседняя картинка → если нет, то видео/GIF ----
        media_msg = None
        media_kind = None

        try:
            img_msg = await get_image_message_from_same_author(
                client=client, forum_id=forum_id,
                topic_id=topic_id, message_id=message_id, prefer="any",
            )
        except Exception as e:
            print(f"[Ошибка] Поиск картинки для {message_id}: {e}")
            img_msg = None

        if img_msg is not None:
            media_msg, media_kind = img_msg, "image"
        else:
            try:
                vid_msg = await get_video_message_from_same_author(
                    client=client, forum_id=forum_id,
                    topic_id=topic_id, message_id=message_id, prefer="any",
                )
            except Exception as e:
                print(f"[Ошибка] Поиск видео/GIF для {message_id}: {e}")
                vid_msg = None
            if vid_msg is not None:
                media_msg, media_kind = vid_msg, "video"

        image_filename = None
        video_filename = None

        if media_msg is None:
            print(f"[Инфо] Соседней картинки/видео для сообщения {message_id} не найдено.")
        else:
            if media_kind == "image":
                media_ext = ".jpg"
                if not getattr(media_msg, "photo", None) and getattr(media_msg, "file", None):
                    mime = getattr(media_msg.file, "mime_type", None) or ""
                    if mime.startswith("image/"):
                        media_ext = "." + mime.split("/", 1)[1].split(";")[0].strip()
                    else:
                        src_name = getattr(media_msg.file, "name", None)
                        if src_name and "." in src_name:
                            media_ext = "." + src_name.rsplit(".", 1)[1]
            else:
                media_ext = ""
                if getattr(media_msg, "file", None):
                    mime = getattr(media_msg.file, "mime_type", None) or ""
                    if mime.startswith("video/"):
                        media_ext = "." + mime.split("/", 1)[1].split(";")[0].strip()
                    elif mime == "image/gif":
                        media_ext = ".gif"
                if not media_ext and getattr(media_msg, "file", None):
                    src_name = getattr(media_msg.file, "name", None)
                    if src_name and "." in src_name:
                        media_ext = "." + src_name.rsplit(".", 1)[1]
                if not media_ext:
                    media_ext = ".mp4"

            media_name = f"{seq}{media_ext}"
            media_path = os.path.join(config.OUTPUT_DIR, media_name)

            if os.path.exists(media_path):
                try:
                    os.remove(media_path)
                except Exception as e:
                    print(f"[Ошибка] Не удалось удалить {media_path}: {e}")
                    media_msg = None

            if media_msg is not None:
                kind_label = "картинки" if media_kind == "image" else "видео/GIF"
                media_local_time = media_msg.date.astimezone()
                print(f"Найдено в {media_local_time.strftime('%H:%M:%S')}. "
                      f"Скачивание {kind_label}: {media_name}...")
                try:
                    downloaded_media = await client.download_media(media_msg, file=media_path)
                    if media_kind == "image":
                        print(f"[Успешно] Картинка сохранена: {downloaded_media}")
                        report["image_path"] = downloaded_media
                        image_filename = media_name
                    else:
                        print(f"[Успешно] Видео/GIF сохранено: {downloaded_media}")
                        report["video_path"] = downloaded_media
                        video_filename = media_name
                except Exception as e:
                    print(f"[Ошибка] Не удалось скачать {media_name}: {e}")

        # ---- 3. Текстовый сайдкар NN.txt ----
        tg_link = _make_tg_link(forum_username, forum_id, topic_id, message_id)
        txt_name = f"{seq}.txt"
        txt_path = os.path.join(config.OUTPUT_DIR, txt_name)
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(f"id сообщения: {message_id}\n")
                f.write(f"автор: {author}\n")
                f.write(f"ссылка: {tg_link}\n")
                f.write(f"аудио: {raw_audio_name or (audio_stem + audio_ext)}\n")
                f.write(f"длина: {_fmt_duration(audio_duration)}\n")
                f.write(f"картинка: {image_filename or '-'}\n")
                f.write(f"видео: {video_filename or '-'}\n")
                if wxxx_links:
                    for wxxx in wxxx_links:
                        f.write(f"WXXX: {wxxx}\n")
                else:
                    f.write("WXXX: -\n")
            report["txt_path"] = txt_path
            print(f"[Успешно] Текстовый файл сохранён: {txt_path}")
        except Exception as e:
            print(f"[Ошибка] Не удалось записать {txt_path}: {e}")

        results.append(report)
        await asyncio.sleep(1.5)

    return results

            
async def get_image_message_from_same_author(
    client: TelegramClient,
    forum_id: int,
    topic_id: int,
    message_id: int,
    prefer: str = "any",   # "any" | "previous" | "next"
):
    """
    Проверяет только ДВУХ непосредственных соседей сообщения message_id в топике:
      - сообщение с ID, идущее на 1 выше (более старое),
      - сообщение с ID, идущее на 1 ниже (более новое).
    Возвращает того соседа, который одновременно:
      * от того же автора, что и message_id,
      * содержит картинку (фото или image/*-документ).

    :param prefer: "any"      — вернуть любого подходящего соседа (сначала проверяется верхний);
                   "previous" — смотреть только верхнего соседа (1 вверх);
                   "next"     — смотреть только нижнего соседа (1 вниз).
    :return:       объект сообщения Telethon или None.
    """
    try:
        original = await fetch_one_message(client, forum_id, topic_id, message_id)
        if original is None:
            return None

        sender_id = original.sender_id
        if sender_id is None:
            return None

        forum_entity = await client.get_entity(int(forum_id))
        mid = int(message_id)

        prev_img = None
        next_img = None

        # Сосед на 1 выше (ID < mid), самый ближайший
        if prefer in ("any", "previous"):
            async for message in client.iter_messages(
                forum_entity,
                reply_to=int(topic_id),
                max_id=mid,        # строго старше
                limit=1,           # только один — непосредственный сосед сверху
            ):
                if message.sender_id == sender_id and _has_image(message):
                    prev_img = message

        # Сосед на 1 ниже (ID > mid), самый ближайший
        if prefer in ("any", "next"):
            async for message in client.iter_messages(
                forum_entity,
                reply_to=int(topic_id),
                min_id=mid,        # строго новее
                reverse=True,      # от старых к новым → первый = ближайший сверху от mid
                limit=1,           # только один — непосредственный сосед снизу
            ):
                if message.sender_id == sender_id and _has_image(message):
                    next_img = message

        if prefer == "previous":
            return prev_img
        if prefer == "next":
            return next_img
        return prev_img or next_img

    except Exception as e:
        print(f"[Ошибка] get_image_message_from_same_author({message_id}): {e}")
        return None

def _has_video_or_gif(message) -> bool:
    """Видео (message.video) либо GIF/анимация (message.gif),
       либо документ с MIME video/* или image/gif."""
    if getattr(message, "video", None):
        return True
    if getattr(message, "gif", None):
        return True
    file = getattr(message, "file", None)
    if file is not None:
        mime = getattr(file, "mime_type", None) or ""
        if mime.startswith("video/") or mime == "image/gif":
            return True
    return False


async def get_video_message_from_same_author(
    client: TelegramClient,
    forum_id: int,
    topic_id: int,
    message_id: int,
    prefer: str = "any",   # "any" | "previous" | "next"
):
    """
    Ищет видео/GIF-сообщение СРЕДИ НЕПОСРЕДСТВЕННЫХ СОСЕДЕЙ сообщения message_id
    в том же топике, причём от ТОГО ЖЕ автора.

    Проверяются ровно два соседа (не дальше одного сообщения):
      - сообщение с ID на 1 выше (более старое),
      - сообщение с ID на 1 ниже (более новое).
    Возвращается только тот, который одновременно:
      * от того же sender_id, что и message_id,
      * содержит видео (message.video) или GIF-анимацию (message.gif,
        либо документ с MIME video/* / image/gif).

    :param prefer: "any"      — сначала проверяется верхний сосед, затем нижний;
                   "previous" — смотреть только верхнего соседа;
                   "next"     — смотреть только нижнего соседа.
    :return:       объект сообщения Telethon или None.
    """
    try:
        original = await fetch_one_message(client, forum_id, topic_id, message_id)
        if original is None:
            return None

        sender_id = original.sender_id
        if sender_id is None:
            return None

        forum_entity = await client.get_entity(int(forum_id))
        mid = int(message_id)

        prev_vid = None
        next_vid = None

        # Сосед на 1 выше (ID < mid), самый ближайший
        if prefer in ("any", "previous"):
            async for message in client.iter_messages(
                forum_entity,
                reply_to=int(topic_id),
                max_id=mid,        # строго старше
                limit=1,           # только один — непосредственный сосед сверху
            ):
                if message.sender_id == sender_id and _has_video_or_gif(message):
                    prev_vid = message

        # Сосед на 1 ниже (ID > mid), самый ближайший
        if prefer in ("any", "next"):
            async for message in client.iter_messages(
                forum_entity,
                reply_to=int(topic_id),
                min_id=mid,        # строго новее
                reverse=True,      # от старых к новым → первый = ближайший снизу
                limit=1,           # только один — непосредственный сосед снизу
            ):
                if message.sender_id == sender_id and _has_video_or_gif(message):
                    next_vid = message

        if prefer == "previous":
            return prev_vid
        if prefer == "next":
            return next_vid
        return prev_vid or next_vid

    except Exception as e:
        print(f"[Ошибка] get_video_message_from_same_author({message_id}): {e}")
        return None


async def download_audio_by_id(
    client: TelegramClient,
    forum_id: int,
    topic_id: int,
    message_id: int,
):
    """
    Скачивает аудиофайл из сообщения с заданным ID в каталог config.OUTPUT_DIR.
    Если файл с таким именем уже существует — перезаписывает его поверх.

    :param client:      активированный TelegramClient
    :param forum_id:    ID форума (канала)
    :param topic_id:    ID топика (ветки)
    :param message_id:  ID сообщения
    :return:            путь к сохранённому файлу (str) или None, если скачать не удалось
                        (сообщение не найдено / в нём нет аудио / ошибка скачивания).
    """
    if not os.path.exists(config.OUTPUT_DIR):
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    message = await fetch_one_message(client, forum_id, topic_id, message_id)
    if message is None:
        print(f"[Пропуск] Сообщение {message_id} не получено.")
        return None

    if not message.audio:
        print(f"[Пропуск] В сообщении {message_id} нет аудио.")
        return None

    # Имя файла: из метаданных, иначе синтетическое (как в download_tracks)
    filename = getattr(message.file, 'name', None)
    if not filename:
        ext = ""
        mime = getattr(message.file, 'mime_type', None) or ""
        if "/" in mime:
            ext = "." + mime.split("/", 1)[1].split(";")[0].strip()
        filename = f"audio_{message.id}_{message.date.strftime('%Y%m%d_%H%M%S')}{ext}"

    file_path = os.path.join(config.OUTPUT_DIR, filename)

    # Перезапись поверх: если файл уже есть — удаляем заранее,
    # чтобы Telethon не создал «filename (1).ogg» вместо перезаписи.
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            print(f"[Инфо] Старый файл перезаписывается: {file_path}")
        except Exception as e:
            print(f"[Ошибка] Не удалось удалить старый файл {file_path}: {e}")
            return None

    msg_local_time = message.date.astimezone()
    print(f"Найдено в {msg_local_time.strftime('%H:%M:%S')}. Скачивание аудио: {filename}...")

    try:
        path = await client.download_media(message, file=file_path)
        print(f"[Успешно] Сохранено: {path}")
        return path
    except Exception as e:
        print(f"[Ошибка] Не удалось скачать {filename}: {e}")
        return None


async def download_tracks (client, forum_id, topic_id, messages):
    if not os.path.exists(config.OUTPUT_DIR):
        os.makedirs(config.OUTPUT_DIR)

    # --- Шаг скачивания файлов из конкретного топика ---
    # Передаем topic_id в параметр reply_to для фильтрации по разделу
    for id in messages:
        message = await fetch_one_message (client, forum_id, topic_id, id)

        if message is None:
            print(f"[Пропуск] Сообщение {id} не получено.")
            continue

        if message.media and not message.photo:
            filename = getattr(message.file, 'name', None)
            if not filename:
                filename = f"file_{message.id}_{message.date.strftime('%Y%m%d_%H%M%S')}"
            
            file_path = os.path.join(config.OUTPUT_DIR, filename)
            msg_local_time = message.date.astimezone()
            print(f"Найдено в {msg_local_time.strftime('%H:%M:%S')}. Скачивание: {filename}...")
            
            try:
                await client.download_media(message, file=file_path)
                print(f"[Успешно] Сохранено: {filename}")
                await asyncio.sleep(1.5)
            except Exception as e:
                print(f"[Ошибка] Не удалось скачать {filename}: {e}")


# Блок для проверки автономного запуска
async def main():
    client = login()
    async with client:
        print("Авторизация успешна!")
        forums = await get_forums(client)
        
        if forums:
            # ИСПРАВЛЕНО: берём id из первого элемента списка форумов
            f_id = str(forums[0]["id"])
            topics = await get_topics(client, f_id)
            
            if topics:
                # ИСПРАВЛЕНО: берём id из первого элемента списка топиков
                t_id = str(topics[0]["id"])
                
                # Вызов новой функции (так как она синхронная, вызываем без await)                
                fetch_telegram_messages(
                    client=client,
                    forum_id=f_id,
                    topic_id=t_id,
                    pinned_id="0",
                    start_pinned_id="0",
                    time_filter="all",
                    reactions=None,
                    audio_only=True
                )


if __name__ == '__main__':
    asyncio.run(main())

import os
import json
import asyncio
import datetime
from telethon import TelegramClient, connection
from telethon.tl.types import Channel, PeerUser, PeerChannel, PeerChat
from telethon.tl.functions.messages import GetForumTopicsRequest

# Импортируем настройки из созданного файла config.py
import config

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
                    "username": dialog.entity.username if dialog.entity.username else None
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

def fetch_pinned_messages(client, forum_id: str, topic_id: str, limit=50, base_data_dir="data") -> list:
    """
    Синхронная функция. Получает список закрепленных сообщений в конкретном топике форума.
    Сохраняет результат в файл data/{forum_id}/{topic_id}/pinned_messages.json и возвращает список.
    """
    async def _fetch():
        topic_dir = os.path.join(base_data_dir, str(forum_id), str(topic_id))
        if not os.path.exists(topic_dir):
            os.makedirs(topic_dir)

        print(f"Сбор закрепленных сообщений из топика {topic_id} форума {forum_id}...")
        pinned_list = []

        try:
            forum_entity = await client.get_entity(int(forum_id))
            
            # ИСПРАВЛЕНО: Вместо pinned=True в iter_messages (которого не существует),
            # мы перебираем сообщения в топике и проверяем флаг message.pinned вручную.
            # Для ускорения процесса можно ограничить общий поиск.
            async for message in client.iter_messages(forum_entity, reply_to=int(topic_id), limit=limit):
                
                # Проверяем, является ли сообщение закрепленным
                if not getattr(message, 'pinned', False):
                    continue

                sender_id = None
                if isinstance(message.from_id, PeerUser):
                    sender_id = message.from_id.user_id
                elif isinstance(message.from_id, PeerChannel):
                    sender_id = message.from_id.channel_id
                elif isinstance(message.from_id, PeerChat):
                    sender_id = message.from_id.chat_id

                total_reactions = sum(r.count for r in message.reactions.results) if message.reactions else 0

                msg_data = {
                    "id": message.id,
                    "date": message.date.isoformat(),
                    "text": message.text if message.text else "",
                    "sender_id": sender_id,
                    "reactions": [],
                    "total_reactions": total_reactions
                }
                pinned_list.append(msg_data)
                
        except Exception as e:
            print(f"[Ошибка] Не удалось получить закрепы: {e}")
            return []

        output_file = os.path.join(topic_dir, "pinned_messages.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(pinned_list, f, ensure_ascii=False, indent=4)

        print(f"[Успешно] Закрепленные сообщения сохранены в: {output_file}")
        
        return pinned_list

    # Безопасный запуск асинхронного кода внутри синхронной функции
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        return loop.create_task(_fetch())
    else:
        return asyncio.run(_fetch())

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

        print(f"Сбор сообщений из топика {topic_id} форума {forum_id} (лимит {limit})...")
        messages_list = []

        # Вычисляем порог времени на основе переданного фильтра
        time_threshold = None
        local_now = datetime.datetime.now()
        local_today_midnight = datetime.datetime.combine(local_now.date(), datetime.time.min)

        if time_filter == "pinned":
            # Сообщения после закрепа
            time_threshold = None
        elif time_filter == "day":
            # Сообщения за текущий календарный день (с 00:00)
            time_threshold = local_today_midnight.astimezone(datetime.timezone.utc)
        elif time_filter == "week":
            # Сообщения за последние 7 дней от текущего момента
            start_date = local_now - datetime.timedelta(days=7)
            time_threshold = start_date.astimezone(datetime.timezone.utc)
        elif time_filter == "month":
            # Сообщения за последние 30 дней от текущего момента
            start_date = local_now - datetime.timedelta(days=30)
            time_threshold = start_date.astimezone(datetime.timezone.utc)
        elif time_filter == "year":
            # Сообщения за последние 365 дней от текущего момента
            start_date = local_now - datetime.timedelta(days=365)
            time_threshold = start_date.astimezone(datetime.timezone.utc)
        elif time_filter == "all":
            time_threshold = None

        try:
            forum_entity = await client.get_entity(int(forum_id))
            
            async for message in client.iter_messages(forum_entity, reply_to=int(topic_id), limit=limit):
                # Так как сообщения идут от новых к старым, при выходе за порог прекращаем сбор
                if time_threshold and message.date < time_threshold:
                    break

                # filter pinned
                if time_filter == "pinned" and pinned_id and message.id < int(pinned_id):
                    break
                if time_filter == "pinned" and pinned_id and message.id != int(pinned_id):
                    if getattr(message, 'pinned', False):
                        messages_list.clear()

                # filter audio
                if audio_only:
                    is_audio = False
                    # Проверяем наличие медиа, которое является аудиозаписью или голосовым сообщением
                    if message.media and getattr(message, 'audio', None):
                        is_audio = True
                    # Если в сообщении есть документ, проверяем его mime_type
                    elif message.document and hasattr(message.document, 'mime_type'):
                        if message.document.mime_type.startswith('audio/'):
                            is_audio = True

                    # Если фильтр включен, а аудио не найдено — пропускаем сообщение
                    if not is_audio:
                        continue

                # --- ИСПРАВЛЕНО: Безопасное извлечение эмодзи с поддержкой кастомных реакций ---
                msg_reactions = []
                if message.reactions and message.reactions.results:
                    for r in message.reactions.results:
                        # Проверяем наличие атрибута emoticon (он есть только у ReactionEmoji)
                        if hasattr(r.reaction, 'emoticon'):
                            msg_reactions.append(r.reaction.emoticon)
                        elif hasattr(r.reaction, 'document_id'):
                            # Для кастомных премиум-эмодзи подставляем заглушку-звездочку
                            msg_reactions.append("⭐")

                if reactions and not any(react in msg_reactions for react in reactions):
                    continue

                has_file = False
                file_name = None
                if message.media and not message.photo:
                    has_file = True
                    file_name = getattr(message.file, 'name', None)
                    if not file_name:
                        file_name = f"file_{message.id}_{message.date.strftime('%Y%m%d_%H%M%S')}"

                sender_id = None
                if isinstance(message.from_id, PeerUser):
                    sender_id = message.from_id.user_id
                elif isinstance(message.from_id, PeerChannel):
                    sender_id = message.from_id.channel_id
                elif isinstance(message.from_id, PeerChat):
                    sender_id = message.from_id.chat_id

                if reactions:
                    total = get_total_reactions (message, reactions)
                else:
                    total = sum(r.count for r in message.reactions.results) if message.reactions else 0

                msg_data = {
                    "id": message.id,
                    "date": message.date.isoformat(),
                    "text": message.text if message.text else "",
                    "sender_id": sender_id,
                    "pinned_id": pinned_id,
                    "reactions": msg_reactions,
                    "has_file": has_file,
                    "file_name": file_name,
                    "total": total
                }
                messages_list.append(msg_data)
                
        except Exception as e:
            print(f"[Ошибка] Не удалось получить сообщения по фильтру: {e}")
            return []

        output_file = os.path.join(topic_dir, "messages.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(messages_list, f, ensure_ascii=False, indent=4)

        print(f"[Успешно] Отфильтрованные сообщения сохранены в: {output_file}")

        top_10 = sorted(messages_list, key=lambda x: x["total"], reverse=True)

        return top_10

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

        from telethon import TelegramClient, events

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
                    audio_only=True,
                    reactions=None,
                    limit=50
                )

if __name__ == '__main__':
    asyncio.run(main())

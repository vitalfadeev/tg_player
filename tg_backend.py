import os
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

    async for message in client.iter_messages(forum_entity, reply_to=int(topic_id)):
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

async def get_messages_for_period(client: TelegramClient, forum_id: int, topic_id: int, time_filter: str, audio_only: bool, reactions: list):
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

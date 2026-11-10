import os
import json
import asyncio
from datetime import datetime, timedelta

# Импортируем функции и обертку из вашего нового скрипта tg_backend.py
import tg_backend

class TelegramBackend:
    def __init__(self, client=None):
        """
        Инициализирует бэкенд. 
        Принимает активный или инициализированный объект TelegramClient.
        """
        self.client = client

    def load_json_config(self, file_path: str) -> list:
        """Безопасная загрузка локальных JSON-файлов."""
        if not os.path.exists(file_path):
            print(f"[Backend] Предупреждение: Файл {file_path} не найден.")
            return []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            print(f"[Backend] Ошибка: Файл {file_path} содержит не массив, а {type(data)}.")
            return []
        except Exception as ex:
            print(f"[Backend] Ошибка чтения JSON по пути {file_path}: {ex}")
            return []

    async def get_forums(self, base_data_dir="data") -> list:
        """
        Асинхронно запрашивает актуальный список форумов из Telegram через tg_backend.py.
        """
        if not self.client:
            print("[Backend] Критическая ошибка: TelegramClient не передан в TelegramBackend.")
            return []
        
        print("[Backend] Запрос актуального списка форумов из Telegram...")
        try:
            # Вызываем асинхронную функцию напрямую из сетевого модуля
            return await tg_backend.get_forums(self.client, base_data_dir=base_data_dir)
        except Exception as ex:
            print(f"[Backend] Ошибка при получении списка форумов: {ex}")
            return []

    async def get_topics_for_forum(self, forum_id: str) -> list:
        """
        Асин запросы к tg_backend.py для получения актуальных топиков из Telegram.
        """
        if not forum_id or forum_id == "error":
            return []
        
        if not self.client:
            print("[Backend] Критическая ошибка: TelegramClient не передан в TelegramBackend.")
            return []
        
        print(f"[Backend] Запрос актуальных топиков из Telegram для форума: {forum_id}")
        
        # Вызываем асинхронную функцию получения топиков напрямую из Telegram через tg_backend
        try:
            return await tg_backend.get_topics(self.client, forum_id)
        except Exception as ex:
            print(f"[Backend] Ошибка при получении топиков: {ex}")
            return []

    async def get_pinned_message(self, forum_id, topic_id):
        msg = await tg_backend.get_pinned_message(
            client=self.client,
            forum_id=forum_id,
            topic_id=topic_id
        )
        # msg is a Telethon Message object
        #text = msg.get("text", "")
        #msg["title"] = text if len(text) < 60 else text[:57] + "..."

        return msg

    async def fetch_telegram_messages(self, forum_id: str, topic_id: str, pinned_id: str, start_pinned_id: str, time_filter: str, reactions: list, audio_only: bool) -> list:
        """
        Запускает актуальный сбор сообщений через TelegramClient из tg_backend.py,
        после чего адаптирует полученные данные под требования UI.
        """
        print(f"[Backend] Запрос сообщений. Форум: {forum_id}, Топик: {topic_id}, Закреп: {pinned_id}, Фильтр: {time_filter}, Реакции: {reactions}")
        
        if not forum_id or not topic_id or forum_id == "error" or topic_id == "error":
            print("[Backend] Ошибка: Неверный forum_id или topic_id.")
            return []

        if not self.client:
            print("[Backend] Критическая ошибка: TelegramClient не передан в TelegramBackend.")
            return []

        # 1. Вызываем функцию парсинга сообщений из tg_backend.py.
        res = tg_backend.fetch_telegram_messages(
            client=self.client,
            forum_id=forum_id,
            topic_id=topic_id,
            pinned_id=pinned_id,
            start_pinned_id=start_pinned_id,
            time_filter=time_filter,
            reactions=reactions,
            audio_only=audio_only
        )

        # ИСПРАВЛЕНИЕ: Если вернулась асинхронная задача Task (из-за работы внутри Flet), дожидаемся её
        if asyncio.isfuture(res) or isinstance(res, asyncio.Task):
            raw_messages,pinned_list = await res
        else:
            raw_messages,pinned_list = res

        if not raw_messages:
            return ([],[])

        # 3. Адаптация структуры данных под ожидание UI
        ui_mapped_messages = []
        for msg in raw_messages:
            text_snippet = msg.get("text", "").strip()
            if not text_snippet and msg.get("has_file"):
                text_snippet = f"Файл: {msg.get('file_name')}"
            elif not text_snippet:
                text_snippet = f"Сообщение {msg.get('id')}"
            
            title = text_snippet if len(text_snippet) < 60 else text_snippet[:57] + "..."

            mapped_msg = {
                "total": msg.get("total"),
                "title": title,
                "reactions": msg.get("reactions"),
                "id": msg.get("id"),
                "date": msg.get("date"),
                "text": msg.get("text"),
                "has_file": msg.get("has_file"),
                "file_name": msg.get("file_name"),
                "sender_id": msg.get("sender_id")
            }
            ui_mapped_messages.append(mapped_msg)

        return (ui_mapped_messages,pinned_list)

    async def get_image_message_from_same_author(
        self,
        forum_id: str,
        topic_id: str,
        message_id: int,
        prefer: str = "any",   # "any" | "previous" | "next"
    ):
        """
        Возвращает сообщение с картинкой от того же автора, что и сообщение `message_id`,
        но только среди ДВУХ непосредственных соседей в топике (1 вверх / 1 вниз).

        :param prefer: "any"      — любой подходящий сосед (сначала верхний);
                       "previous" — только 1 вверх;
                       "next"     — только 1 вниз.
        :return:       dict с данными сообщения для UI, либо None.
        """
        if not forum_id or not topic_id or forum_id == "error" or topic_id == "error":
            print("[Backend] Ошибка: Неверный forum_id или topic_id.")
            return None

        if not self.client:
            print("[Backend] Критическая ошибка: TelegramClient не передан в TelegramBackend.")
            return None

        try:
            message = await tg_backend.get_image_message_from_same_author(
                client=self.client,
                forum_id=forum_id,
                topic_id=topic_id,
                message_id=message_id,
                prefer=prefer,
            )
        except Exception as ex:
            print(f"[Backend] Ошибка при поиске соседнего сообщения с картинкой: {ex}")
            return None

        if message is None:
            return None

        # Адаптация под UI — так же, как в fetch_telegram_messages
        text = getattr(message, "text", "") or ""
        text_snippet = text.strip()

        file_name = None
        has_file = False
        if getattr(message, "media", None) and not getattr(message, "photo", None):
            has_file = True
            file_name = getattr(getattr(message, "file", None), "name", None)

        if not text_snippet and has_file:
            text_snippet = f"Файл: {file_name}"
        elif not text_snippet:
            text_snippet = f"Сообщение {message.id}"

        title = text_snippet if len(text_snippet) < 60 else text_snippet[:57] + "..."

        return {
            "id":         message.id,
            "date":       message.date.isoformat() if message.date else None,
            "text":       text,
            "title":      title,
            "sender_id":  message.sender_id,
            "has_file":   has_file,
            "file_name":  file_name,
            "has_image":  True,
            # reaction-полей здесь нет — они не нужны для картинки-соседа,
            # но структуру можно привести к тому же виду, что и mapped_msg,
            # если UI ожидает единый формат:
            "total":      0,
            "reactions":  [],
        }

    async def download_audio_by_id(self, forum_id, topic_id, message_id):
        if not self.client:
            print("[Backend] Критическая ошибка: TelegramClient не передан в TelegramBackend.")
            return None
        try:
            return await tg_backend.download_audio_by_id(
                client=self.client,
                forum_id=forum_id,
                topic_id=topic_id,
                message_id=message_id,
            )
        except Exception as ex:
            print(f"[Backend] Ошибка при скачивании аудио {message_id}: {ex}")
            return None

    async def download_tracks (self, forum_id, topic_id, messages):
        res = await tg_backend.download_tracks(
                    client=self.client,
                    forum_id=forum_id,
                    topic_id=topic_id,
                    messages=messages
                )
        return res

    async def download_audios_and_images_by_ids(self, forum_id, topic_id, message_ids):
        if not self.client:
            print("[Backend] Критическая ошибка: TelegramClient не передан в TelegramBackend.")
            return []
        try:
            return await tg_backend.download_audios_and_images_by_ids(
                client=self.client,
                forum_id=forum_id,
                topic_id=topic_id,
                message_ids=message_ids,
            )
        except Exception as ex:
            print(f"[Backend] Ошибка при пакетной загрузке аудио+картинок: {ex}")
            traceback.print_exc()   # <-- добавить
            raise                   # <-- пробросить в UI, либо вернуть []
            
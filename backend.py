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

    async def download_tracks (self, forum_id, topic_id, messages):
        res = await tg_backend.download_tracks(
                    client=self.client,
                    forum_id=forum_id,
                    topic_id=topic_id,
                    messages=messages
                )
        return res



# main.py
import flet as ft
import asyncio
import tg_backend
from backend import TelegramBackend
from ui import TelegramPlayerUI

def main(page: ft.Page):
    # 1. Инициализируем клиент Telethon из tg_backend
    client = tg_backend.login()
    
    # 2. Инициализируем бизнес-логику (Бэкэнд) и передаем туда клиент
    backend = TelegramBackend(client=client)
    
    # ДОБАВЛЕНО: Создаем событие, которое станет True, когда Telegram подключится
    page.telegram_ready = asyncio.Event()
    
    # 3. Инициализируем графическую оболочку (Фронтенд)
    app_ui = TelegramPlayerUI(page, backend)
    
    # 4. Строим интерфейс
    app_ui.build()

    # 5. Фоновая задача для автоматического подключения и удержания соединения Telegram
    async def start_telegram():
        print("[Main] Подключение к Telegram...")
        
        # Показываем индикатор подключения перед логином
        app_ui.connection_loader.visible = True
        page.update()
        
        try:
            async with client:
                print("[Main] Telegram клиент успешно запущен и авторизован!")
                
                # Прячем индикатор, так как логин успешно выполнен
                app_ui.connection_loader.visible = False
                page.update()
                
                # ДОБАВЛЕНО: Сигнализируем интерфейсу, что клиент готов к работе
                page.telegram_ready.set()
                
                # Удерживаем соединение активным, пока работает приложение
                while True:
                    await asyncio.sleep(3600)
        except Exception as e:
            print(f"[Main] Ошибка при подключении к Telegram: {e}")
            app_ui.connection_loader.controls.clear()
            app_ui.connection_loader.controls.append(
                ft.Text(f"Ошибка подключения к Telegram: {e}", color=ft.Colors.RED_600, weight=ft.FontWeight.BOLD)
            )
            page.update()

    # Запускаем асинхронный контекст клиента в фоновом потоке Flet
    page.run_task(start_telegram)

if __name__ == "__main__":
    ft.run(main)

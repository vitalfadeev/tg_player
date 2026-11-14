import os
import sys
import flet as ft
import traceback
import asyncio
import subprocess
from pathlib import Path

import config

def _human_bytes(n: int) -> str:
    if n is None:
        return "?"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024.0:
            return f"{n:,.1f} {unit}".replace(",", " ")
        n /= 1024.0
    return f"{n:,.1f} TB".replace(",", " ")


class TelegramPlayerUI:
    def __init__(self, page: ft.Page, backend):
        self.page = page
        self.backend = backend
        self.selected_reactions = set()

        self.current_fetch_task = None
        self._pins_dialog = None
        self._download_dialog = None
        self.current_download_task = None
        self._dl_cancelled = False
        self._dl_generation = 0

        self._dl_last_update_ts = 0.0     # для троттлинга
        self._dl_bytes_text = None        # создаётся в open_download_dialog        
        self._dl_close_btn = None
        
        # Защита от Garbage Collector
        self.page.data = self
        
        try:
            self.main_loop = asyncio.get_running_loop()
        except RuntimeError:
            self.main_loop = asyncio.get_event_loop()
            
        print(f"[Python 3.13 Core] UI loop initialized: {self.main_loop}")
        
        self.EMOJI_ICONS = ['🏆','❤','❤️','🔥','⚡','👍','❤️‍🔥',]

    def build(self):
        self.page.title = "TG Player"
        self.page.vertical_alignment = ft.MainAxisAlignment.START
        self.page.padding = 10 

        # 1. Объявляем компоненты
        self.forum_dropdown = ft.Dropdown(
            label="Выберите форум", 
            hint_text="Нажмите Обновить", 
            expand=True,
            dense=True
        )
        self.topic_dropdown = ft.Dropdown(
            label="Выберите топик", 
            hint_text="Сначала выберите форум", 
            expand=True, 
            dense=True
        )
        # ЗАМЕНЕНО: теперь это просто кнопка "Диапазон закрепа" вместо Dropdown
        # ИСПРАВЛЕНО ДЛЯ FLET 1.0.5: Используем ft.controls.Button
        self.pinned_tooltip = ft.Tooltip(
            message="Стилизованная подсказка",
            bgcolor=ft.Colors.BLUE_GREY_700,
            text_style=ft.TextStyle(color=ft.Colors.WHITE),
        )
        self.subject_dropdown = ft.Button(
            content="Закреп",
            tooltip=self.pinned_tooltip,
            on_click=lambda e: self.on_subject_selected_bridge()
        )
        self.pinned_id = None
        
        self.selected_post_text = ft.Text("Ничего не выбрано", color=ft.Colors.BLUE_400, weight=ft.FontWeight.BOLD, size=12)
        self.loader = ft.ProgressRing(width=20, height=20, stroke_width=3, visible=False)
        
        self.connection_loader = ft.Row(
            controls=[
                ft.ProgressRing(width=16, height=16, stroke_width=2),
                ft.Text("Подключение к Telegram...", size=12, color=ft.Colors.ORANGE_700)
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            visible=False 
        )        
        self.posts_list = ft.ListView(expand=True, spacing=2, height=300) 

        # === НОВОЕ: кнопка Download ===
        self.download_btn = ft.Button(
            "В Youtube",
            on_click=lambda e: self.on_download_clicked_bridge(),
        )

        top_row = ft.Row(
            controls=[self.forum_dropdown, self.topic_dropdown],
            alignment=ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=10
        )

        self.filter_radio_group = ft.RadioGroup(
            content=ft.Row(
                controls=[
                    ft.Radio(value="all", label="Все"),
                    ft.Radio(value="year", label="год"),
                    ft.Radio(value="month", label="месяц"),
                    ft.Radio(value="week", label="неделя"),
                    ft.Radio(value="yesterday", label="вчера"),
                    ft.Radio(value="day", label="день"),
                    ft.Radio(value="pin", label="закреп"),
                ],
                alignment=ft.MainAxisAlignment.START,
                spacing=15
            ),
            value="day"
        )

        filter_row = ft.Row(
            controls=[
                ft.Text("", weight=ft.FontWeight.BOLD, size=13), 
                self.filter_radio_group,
                self.subject_dropdown 
            ],
            alignment=ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=10
        )

        reaction_controls = []
        reaction_1 = False
        for emoji_text in self.EMOJI_ICONS:  # Итерируемся по ключам-эмодзи
            # Текстовая подпись с эмодзи прямо в чекбоксе
            cb = ft.Checkbox(
                label=emoji_text,
                value=reaction_1,
                data=emoji_text,
                label_style=ft.TextStyle(size=18),  # крупнее, чтобы эмодзи читалось
            )
            reaction_controls.append(cb)
            reaction_1 = False

        # Инициализируем чекбокс audio с отступом слева для визуального разделения
        self.audio_checkbox = ft.Checkbox(
            label="audio", 
            value=True, 
            on_change=lambda e: self.on_audio_changed_bridge()
        )

        # Исправлено для Flet 1.0.5+: передаем текст позиционным аргументом
        self.to_text_btn = ft.Button(
            "В текст",
            on_click=lambda e: self.on_to_text_clicked_bridge()
        )

        # Конструируем reaction_row: выстраиваем элементы в один ряд, 
        # Добавлен self.audio_checkbox сразу после *reaction_controls
        reaction_row = ft.Row(
            controls=[
                ft.Text("", weight=ft.FontWeight.BOLD, size=13), 
                *reaction_controls,
                self.audio_checkbox,
                ft.Container(expand=True),  # «Пружина», выталкивает кнопки вправо
                self.to_text_btn,
                self.download_btn,          # <-- теперь после «В текст»
            ],
            alignment=ft.MainAxisAlignment.START, 
            vertical_alignment=ft.CrossAxisAlignment.CENTER, 
            spacing=10
        )

        def compact_divider():
            return ft.Divider(height=1, thickness=1, color=ft.Colors.SURFACE_CONTAINER_HIGHEST)

        # 2. Добавляем структуру на страницу
        self.page.add(
            self.connection_loader, 
            top_row, compact_divider(), 
            filter_row, compact_divider(), 
            reaction_row, compact_divider(),
            ft.Row(
                controls=[
                    ft.Row(controls=[ft.Text("Список постов:", weight=ft.FontWeight.BOLD, size=14), self.loader], spacing=10), 
                    self.selected_post_text
                ], 
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN
            ),
            self.posts_list,
        )

        # 3. ИСПРАВЛЕНО ДЛЯ PYTHON 3.13 & FLET 1.0.5: стабильные лямбда-обертки
        self.forum_dropdown.on_select = lambda e: self.on_forum_selected_bridge()
        self.topic_dropdown.on_select = lambda e: self.on_topic_selected_bridge()
        self.subject_dropdown.on_click = lambda e: self.on_subject_selected_bridge()
        self.filter_radio_group.on_change = lambda e: self.on_filter_changed_bridge()

        for checkbox in reaction_controls:
            checkbox.on_change = lambda e: self.on_reaction_changed_bridge(e)

        # Фиксируем состояние на клиенте
        self.page.update()
        
        # Запускаем фоновый Event
        asyncio.run_coroutine_threadsafe(self.wait_for_telegram_and_refresh(), self.main_loop)
    
    # =========================================================================
    # ЧИСТЫЕ МЕТОДЫ БЕЗ СИСТЕМНЫХ АРГУМЕНТОВ ДЛЯ НАДЕЖНОСТИ
    # =========================================================================
    
    def safe_execute(self, coro):
        try:
            # 1. Если есть старая незавершенная задача, отменяем её
            if self.current_fetch_task and not self.current_fetch_task.done():
                print("[UI Bridge] Отмена предыдущей долгой задачи...")
                self.current_fetch_task.cancel()
            
            # 2. Запускаем новую и сохраняем на неё ссылку
            self.current_fetch_task = asyncio.run_coroutine_threadsafe(coro, self.main_loop)
        except Exception as ex:
            print(f"[Bridge Error] Loop execution crash: {ex}")

    def on_to_text_clicked_bridge(self):
        print("[LOG] Клик по кнопке 'В текст'.")
        self.safe_execute(self.to_text())

    def on_forum_selected_bridge(self):
        print("!!! [STABLE BRIDGE] СОБЫТИЕ DROPDOWN ДОШЛО ДО PYTHON !!!")
        print(f"[LOG] Текущее значение forum_dropdown.value: {self.forum_dropdown.value}")
        self.safe_execute(self.on_forum_selected_async())

    def on_subject_selected_bridge(self):
        print("[LOG] Нажата кнопка выбора закрепа — открываем popup с 5 последними.")
        self.safe_execute(self.show_pins_popup())

    def on_filter_changed_bridge(self):
        print(f"[LOG] Радио-фильтр изменен мышкой на: {self.filter_radio_group.value}")
        # Если пользователь кликнул на любой временной фильтр (день, неделя и т.д.),
        # сбрасываем визуальный выбор в выпадающем списке закрепов, чтобы не путать интерфейс
        self.safe_execute(self.load_messages_from_telegram())

    def on_refresh_clicked_bridge(self):
        print("[LOG] Клик по 'Обновить'.")
        self.safe_execute(self.refresh_all_dropdowns())

    def on_reaction_changed_bridge(self, event_data):
        print("[LOG] Реакция изменена.")
        try:
            emoji = event_data.control.data
            if event_data.control.value:
                self.selected_reactions.add(emoji)
            else:
                self.selected_reactions.discard(emoji)
            self.executed_request = self.safe_execute(self.load_messages_from_telegram())
        except Exception as ex:
            print(f"[UI Error] Ошибка парсинга чекбокса: {ex}")

    def on_download_clicked_bridge(self):
        print("[LOG] Клик по кнопке Download.")
        self.open_download_dialog()

    # В секцию "ЧИСТЫЕ МЕТОДЫ БЕЗ СИСТЕМНЫХ АРГУМЕНТОВ"
    def on_audio_changed_bridge(self):
        print(f"[LOG] Фильтр audio изменен на: {self.audio_checkbox.value}")
        self.safe_execute(self.load_messages_from_telegram())

    # =========================================================================
    # АСИНХРОННАЯ БИЗНЕС-ЛОГИКА
    # =========================================================================

    async def to_text(self):
        """
        Сохраняет текущий список сообщений в файл ./messages.txt 
        и открывает его в системном текстовом редакторе.
        """
        print("[UI Async] Запуск сохранения сообщений в текст...")
        
        # 1. Проверяем, есть ли сообщения в списке
        if not self.posts_list.controls:
            print("[UI Warning] Список сообщений пуст. Нечего сохранять.")
            self.selected_post_text.value = "Ошибка: список постов пуст!"
            self.page.update()
            return

        try:
            # 2. Собираем текстовые данные из элементов ListTile
            forum_id = self.forum_dropdown.value
            forum_name = self.get_forum_username(forum_id)

            lines = []

            # форум
            forum_text = next(
                (opt.text for opt in self.forum_dropdown.options if opt.key == self.forum_dropdown.value), 
                "Ничего не выбрано"
            )
            line = f"{forum_text}"
            lines.append(line)

            # топик
            topic_text = next(
                (opt.text for opt in self.topic_dropdown.options if opt.key == self.topic_dropdown.value), 
                ""
            )
            line = f"{topic_text}"
            lines.append(line)

            # Получаем период
            # Находим выбранную радиокнопку по совпадению value
            active_radio = next(
                (control for control in self.filter_radio_group.content.controls 
                 if isinstance(control, ft.Radio) and control.value == self.filter_radio_group.value),
                None
            )

            active_filter_text = active_radio.label if active_radio else ""
            line = f"{active_filter_text}"
            lines.append(line)

            # раздел
            subject_text = self.subject_dropdown.content
            line = f"{subject_text}"
            lines.append(line)

            # реакции
            marked_emojis = list(self.selected_reactions) # Выведет, например: ['🔥', '👍']
            line = f"{marked_emojis}"
            lines.append(line)

            for control in self.posts_list.controls:
                if isinstance(control, ft.ListTile):
                    msg_data = control.data
                    if msg_data:
                        # Извлекаем данные (в соответствии со структурой в load_messages_from_telegram)
                        total = msg_data.get("total", "0")
                        title = msg_data.get("title", "Без названия")
                        reactions = msg_data.get("reactions", "")
                        msg_id = msg_data.get("id", "N/A")
                        
                        # Формируем красивую строку для файла
                        line = f"{total} | https://t.me/{forum_name}/{msg_id}"
                        lines.append(line)
            
            # 3. Записываем строки в файл с кодировкой UTF-8
            file_path = "messages.txt"
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            
            print(f"[UI Async] Успешно сохранено {len(lines)} строк в {file_path}")
            self.selected_post_text.value = f"Сохранено постов: {len(lines)}"
            self.page.update()
            
            # 4. Открываем файл в системном текстовом редакторе (Linux / xdg-open)
            try:
                if os.name == 'nt':
                    os.startfile(file_path)
                else:
                    subprocess.Popen(["xdg-open", file_path])
                print(f"[UI System Call] Выполнение команды: xdg-open {file_path}")
            except Exception as system_ex:
                print(f"[UI System Error] Не удалось открыть файл через xdg-open: {system_ex}")
                
        except Exception as ex:
            print(f"[UI Critical Error] Ошибка в методе to_text: {ex}")
            traceback.print_exc()

    async def wait_for_telegram_and_refresh(self):
        try:
            print("[UI Async] Проверка статуса Telegram...")
            while not hasattr(self.page, "telegram_ready") or not self.page.telegram_ready.is_set():
                await asyncio.sleep(0.3)
            
            print("[UI Async] Подключение к Telegram подтверждено!")
            await self.refresh_all_dropdowns()
        except Exception as ex:
            print(f"[UI Critical Error] Init failure: {ex}")
            traceback.print_exc()

    async def on_forum_selected_async(self):
        forum_id = self.forum_dropdown.value
        print(f"[UI Async Loops] Выполнение on_forum_selected_async для ID: {forum_id}")
        
        if not forum_id:
            return

        self.topic_dropdown.value = None
        self.topic_dropdown.options = []
        self.topic_dropdown.update()
        
        self.loader.visible = True
        self.page.update()

        try:
            data = await self.backend.get_topics_for_forum(forum_id)
            print(f"[Backend Log] Получено топиков: {len(data)}")
            
            new_options = [
                ft.dropdown.Option(key=str(item.get("id", "")), text=str(item.get("title", "Без названия"))) 
                for item in data if item.get("id") is not None
            ]
            self.topic_dropdown.options = new_options
            

            if new_options:
                self.topic_dropdown.value = new_options[0].key
                # Поиск закрепа временно отключён — не запускаем при старте.
                # await self.load_pinned_message()
            else:
                self.topic_dropdown.value = None
                
            self.topic_dropdown.update()
            self.page.update()
            
            # Затем загружаем сообщения
            await self.load_messages_from_telegram()
            
        except Exception as ex:
            print(f"[UI Critical Error] Ошибка обновления топиков: {ex}")
            traceback.print_exc()
        finally:
            self.loader.visible = False
            self.page.update()

    def on_topic_selected_bridge(self):
        print("[LOG] Топик изменен. Обновляем закрепы и сообщения.")
        self.safe_execute(self.on_topic_selected_async())

    async def on_topic_selected_async(self):
        """
        Вызывается при ручном переключении топика пользователем.
        Перезагружает список закрепов для нового топика, а затем обновляет сообщения.
        """
        self.loader.visible = True
        self.page.update()
        try:
            # await self.load_pinned_message ()
            # Затем обновляем основную ленту сообщений
            await self.load_messages_from_telegram()
        except Exception as ex:
            print(f"[UI Critical Error] Ошибка при смене топика: {ex}")
            traceback.print_exc()
        finally:
            self.loader.visible = False
            self.page.update()



    def update_dropdown_options(self, dropdown_component, file_name):
        try:
            data = self.backend.load_json_config(file_name)
            options = [ft.dropdown.Option(key=str(item.get("id", "")), text=str(item.get("title", "Без названия"))) for item in data]
            dropdown_component.options = options
            
            if options and options[0].key != "error":
                dropdown_component.value = options[0].key
            else:
                dropdown_component.value = None
        except Exception as ex:
            print(f"[UI Error] Ошибка чтения конфига {file_name}: {ex}")

    async def refresh_all_dropdowns(self):
        self.loader.visible = True
        self.page.update()
        try:
            print("[UI Async] Запрос списка форумов...")
            forums_data = await self.backend.get_forums()
            self._forums_data = forums_data
            self.forum_dropdown.options = [
                ft.dropdown.Option(key=str(item.get("id", "")), text=str(item.get("title", "Без названия")))
                for item in forums_data
            ]
            if self.forum_dropdown.options:
                self.forum_dropdown.value = self.forum_dropdown.options[0].key
                self.forum_dropdown.update()
                await self.on_forum_selected_async()
            else:
                self.forum_dropdown.value = None
                self.forum_dropdown.update()
                # --- ИЗМЕНЕНИЕ: Вместо старого конфига сбрасываем состояние предметов ---
        except Exception as ex:
            print(f"[UI Critical Error] Ошибка при полном обновлении данных: {ex}")
            traceback.print_exc()
        finally:
            self.loader.visible = False
            self.page.update()

    async def load_pinned_message(self):
        # ВРЕМЕННО ОТКЛЮЧЕНО: поиск закрепа не запускается вообще.
        # Чтобы вернуть — удалите `return` и раскомментируйте вызовы
        # в on_forum_selected_async и on_topic_selected_async.
        return
        message = await self.backend.get_pinned_message(
            forum_id=self.forum_dropdown.value,
            topic_id=self.topic_dropdown.value
        )

        if message:
            text = message.get("text","")
            first_line = text.splitlines()[0]
            title = first_line if len(first_line) < 60 else first_line[:57] + "..."
            self.subject_dropdown.content = message.get("date","YYYY-MM-DD")[:10] + " " + title
            raw_text = message.get("raw_text","")
            self.subject_dropdown.tooltip = ft.Tooltip(
                message=raw_text,
                bgcolor=ft.Colors.BLUE_GREY_700,
                text_style=ft.TextStyle(color=ft.Colors.WHITE),
            )
            self.subject_dropdown.update()

            self.pinned_id = message.get("id",None)
        else:
            self.subject_dropdown.content = ""
            self.subject_dropdown.tooltip.message = ""
            self.pinned_id = None


    async def show_pins_popup(self):
        try:
            print("[show_pins_popup] START")
            forum_id = self.forum_dropdown.value
            topic_id = self.topic_dropdown.value
            print(f"[show_pins_popup] forum_id={forum_id!r} topic_id={topic_id!r}")

            if not forum_id or not topic_id:
                self.selected_post_text.value = "Выберите форум и топик"
                self.page.update()
                return

            self.loader.visible = True
            self.page.update()
            try:
                pins = await self.backend.get_last_pinned_messages(
                    forum_id, topic_id, limit=100
                )
            except Exception as ex:
                print(f"[show_pins_popup] backend failed: {ex!r}")
                import traceback
                traceback.print_exc()
                pins = []
            finally:
                self.loader.visible = False
                self.page.update()

            print(f"[show_pins_popup] pins count = {len(pins)}")
            if not pins:
                self.selected_post_text.value = "Закрепов не найдено"
                self.page.update()
                return

            header = ft.Row(
                controls=[
                    ft.Text("id",    width=80,    weight=ft.FontWeight.BOLD),
                    ft.Text("date",  width=110,   weight=ft.FontWeight.BOLD),
                    ft.Text("title", expand=True, weight=ft.FontWeight.BOLD),
                ],
                spacing=8,
            )

            data_rows: list[ft.Control] = []
            for pin in pins:
                mid        = pin.get("id", "")
                date_short = (pin.get("date") or "")[:10]
                text       = pin.get("raw_text") or ""
                first_line = text.splitlines()[0] if text else ""
                if not first_line:
                    first_line = f"Сообщение {mid}"
                title = first_line[:200]

                row = ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Text(str(mid), width=80),
                            ft.Text(date_short, width=110),
                            ft.Text(title, expand=True, max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS),
                        ],
                        spacing=8,
                    ),
                    on_click=lambda e, m=mid, d=date_short, t=title, rt=text:
                        self.select_pin(m, d, t, rt),
                    padding=6,
                    border_radius=4,
                    bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                )
                data_rows.append(row)


            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text(f"Последние закрепы ({len(pins)})"),
                content=ft.Container(
                    content=ft.Column(
                        controls=[header] + data_rows,
                        spacing=4,
                        tight=True,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    width=760,
                    height=320,
                ),
                actions=[
                    ft.TextButton("Закрыть",
                                  on_click=lambda e: self.close_pins_popup()),
                ],
            )

            self._pins_dialog = dialog
            # Flet 1.0.5: показываем диалог через page.show_dialog()
            self.page.show_dialog(dialog)
            print("[show_pins_popup] dialog shown via page.show_dialog()")

            try:
                self.page.on_keyboard_event = self._on_page_keyboard
            except Exception as e:
                print(f"[show_pins_popup] on_keyboard_event attach failed: {e}")
            print("[show_pins_popup] dialog opened")

        except Exception as e:
            print(f"[show_pins_popup] CRASH: {e!r}")
            import traceback
            traceback.print_exc()

    def close_pins_popup(self):
        if self._pins_dialog is None:
            return
        # Flet 1.0.5: закрываем верхний диалог через page.pop_dialog()
        try:
            self.page.pop_dialog()
        except Exception as e:
            print(f"[close_pins_popup] pop_dialog failed: {e!r}")
        self._pins_dialog = None
        try:
            self.page.on_keyboard_event = None
            self.page.update()
        except Exception:
            pass

    def _on_page_keyboard(self, e: ft.KeyboardEvent):
        if e.key == "Escape" and self._pins_dialog is not None:
            print("[LOG] Escape: закрываем popup закрепов.")
            self.close_pins_popup()


    def select_pin(self, pinned_id, date_short, title, raw_text: str = ""):
        """
        Пользователь выбрал строку в popup: сохраняем pinned_id, закрываем popup,
        переключаем фильтр на «pin» и перезагружаем сообщения.
        """
        print(f"[LOG] Выбран закреп: id={pinned_id}")
        self.pinned_id = pinned_id

        short_title = title if len(title) <= 40 else title[:37] + "..."
        self.subject_dropdown.content = f"{date_short} {short_title}"
        
        if raw_text:
            self.subject_dropdown.tooltip = ft.Tooltip(
                message=raw_text,
                bgcolor=ft.Colors.BLUE_GREY_700,
                text_style=ft.TextStyle(color=ft.Colors.WHITE),
            )
        else:
            self.subject_dropdown.tooltip = None

        try:
            self.subject_dropdown.update()
        except Exception:
            pass

        # Переключаем фильтр на "pin" и грузим сообщения диапазона этого закрепа
        self.filter_radio_group.value = "pin"
        try:
            self.filter_radio_group.update()
        except Exception:
            pass

        self.close_pins_popup()
        self.safe_execute(self.load_messages_from_telegram())


    def _on_pins_dialog_key(self, e: ft.KeyboardEvent):
        if e.key == "Escape" and self._pins_dialog is not None:
            print("[LOG] Escape: закрываем popup закрепов.")
            self.close_pins_popup()


    # =========================================================================
    # Диалог скачивания
    # =========================================================================
    def open_download_dialog(self):
        if self._download_dialog is not None:
            print("[Download] Диалог уже открыт.")
            return
        if not self.forum_dropdown.value or not self.topic_dropdown.value:
            self.selected_post_text.value = "Выберите форум и топик"
            self.page.update()
            return

        available = sum(
            1 for c in self.posts_list.controls
            if isinstance(c, ft.ListTile) and c.data and c.data.get("id") is not None
        )
        if available == 0:
            self.selected_post_text.value = "Нет постов для скачивания"
            self.page.update()
            return

        # ---- Радио-выбор количества треков ----
        self._dl_radio = ft.RadioGroup(
            content=ft.Row(
                controls=[
                    ft.Radio(value="1",  label="1"),
                    ft.Radio(value="5",  label="5"),
                    ft.Radio(value="7",  label="7"),
                    ft.Radio(value="10", label="10"),
                    ft.Radio(value="20", label="20"),
                    ft.Radio(value="30", label="30"),
                ],
                spacing=8,
            ),
            value="10",
        )
        self._dl_radio.on_change = lambda e: self.on_download_radio_changed()

        # ---- Папки: mp3 (OUTPUT_DIR) и video (для результата make_video) ----
        try:
            out_dir = os.path.abspath(config.OUTPUT_DIR)
        except Exception:
            out_dir = str(config.OUTPUT_DIR)

        # Куда будет сохранён output.mp4: по умолчанию <OUTPUT_DIR>/video,
        # либо родитель config.MAKE_VIDEO_OUTPUT, если задан.
        try:
            if getattr(config, "MAKE_VIDEO_OUTPUT", None):
                video_dir_abs = os.path.abspath(
                    os.path.dirname(str(config.MAKE_VIDEO_OUTPUT))
                )
            else:
                video_dir_abs = os.path.abspath(os.path.join(out_dir, "video"))
        except Exception:
            video_dir_abs = os.path.join(out_dir, "video")

        self._dl_path_link = ft.Container(
            content=ft.Text(out_dir, size=12, color=ft.Colors.BLUE_400, italic=True),
            on_click=lambda e: self._open_folder(out_dir),
            tooltip="Открыть папку в файловом менеджере",
            padding=0,
            ink=True,
        )

        self._dl_video_path_link = ft.Container(
            content=ft.Text(video_dir_abs, size=11, color=ft.Colors.BLUE_400, italic=True),
            on_click=lambda e: self._open_folder(video_dir_abs),
            tooltip="Открыть папку с результатом (video.mp4)",
            padding=0,
            ink=True,
        )

        # ---- Виджеты скачивания ----
        self._dl_current_file_text = ft.Text("", size=12, italic=True)
        self._dl_bytes_bar        = ft.ProgressBar(value=0)
        self._dl_bytes_text       = ft.Text("", size=11, color=ft.Colors.GREY_700)
        self._dl_progress         = ft.ProgressBar(value=0)
        self._dl_progress_text    = ft.Text("0/0", size=12)

        # ---- Виджеты сборки видео ----
        self._dl_video_progress     = ft.ProgressBar(value=0)
        self._dl_video_segment_text = ft.Text("Сегмент: 0 / 0", size=11,
                                              color=ft.Colors.GREY_700)

        self._dl_close_btn = ft.TextButton(
            "Отмена",
            on_click=lambda e: self.close_download_dialog(),
        )
        
        # ---- Собираем диалог ----
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Скачивание"),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Row(
                            controls=[
                                ft.Text("Треков:", weight=ft.FontWeight.BOLD),
                                self._dl_radio,
                            ],
                            spacing=10,
                            alignment=ft.MainAxisAlignment.START,
                        ),
                        ft.Column(
                            controls=[
                                ft.Text("Папка:", weight=ft.FontWeight.BOLD, size=12),
                                self._dl_path_link,
                            ],
                            spacing=2,
                            tight=True,
                        ),
                        self._dl_current_file_text,
                        self._dl_bytes_bar,
                        self._dl_bytes_text,
                        self._dl_progress,
                        self._dl_progress_text,
                        ft.Divider(height=1, thickness=1,
                                   color=ft.Colors.SURFACE_CONTAINER_HIGHEST),
                        ft.Text("Сборка видео:", weight=ft.FontWeight.BOLD, size=12),
                        self._dl_video_progress,
                        self._dl_video_segment_text,
                        self._dl_video_path_link,
                    ],
                    tight=True,
                    spacing=10,
                ),
                width=560,
            ),
            actions=[
                self._dl_close_btn,
            ],
        )

        # ---- Показываем и стартуем скачивание ----
        self._download_dialog = dialog
        self._dl_cancelled = False
        self._dl_generation += 1
        my_gen = self._dl_generation

        self.page.show_dialog(dialog)

        # Начальные значения — уже после создания всех виджетов
        self._dl_current_file_text.value = ""
        self._dl_bytes_bar.value = 0
        self._dl_bytes_text.value = ""
        self._dl_progress.value = 0
        self._dl_progress_text.value = "0/0"
        self._dl_video_progress.value = 0
        self._dl_video_segment_text.value = "Сегмент: 0 / 0"
        self.page.update()

        try:
            n = int(self._dl_radio.value)
        except (TypeError, ValueError):
            n = 10
            self._dl_radio.value = "10"

        print(f"[Download] Старт сразу после открытия: N={n}, поколение {my_gen}.")
        self.current_download_task = asyncio.run_coroutine_threadsafe(
            self.run_download_async(my_gen, n), self.main_loop
        )


    def on_download_radio_changed(self):
        if self._download_dialog is None:
            return
        try:
            n = int(self._dl_radio.value)
        except (TypeError, ValueError):
            n = 10

        print(f"[Download] Смена N на {n}, перезапуск (поколение {self._dl_generation + 1}).")
        self._dl_generation += 1
        my_gen = self._dl_generation

        self._dl_progress.value = 0
        self._dl_progress_text.value = "0/0"
        self._dl_current_file_text.value = ""
        self._dl_bytes_bar.value = 0
        self._dl_bytes_text.value = ""
        self._dl_video_progress.value = 0
        self._dl_video_segment_text.value = "Сегмент: 0 / 0"
        self.page.update()

        self.current_download_task = asyncio.run_coroutine_threadsafe(
            self.run_download_async(my_gen, n), self.main_loop
        )

    async def run_download_async(self, my_gen: int, n: int):
        try:
            forum_id = self.forum_dropdown.value
            topic_id = self.topic_dropdown.value
            if not forum_id or not topic_id:
                if self._dl_generation == my_gen:
                    self._dl_progress_text.value = "Выберите форум и топик"
                    self.page.update()
                return

            # n уже пришёл из open_download_dialog / on_download_radio_changed —
            # это ровно то, что выбрано в _dl_radio на момент запуска.
            if n <= 0:
                if self._dl_generation == my_gen:
                    self._dl_progress_text.value = "N должно быть > 0"
                    self.page.update()
                return

            ids = []
            for control in self.posts_list.controls:
                if isinstance(control, ft.ListTile) and control.data:
                    mid = control.data.get("id")
                    if mid is not None:
                        ids.append(mid)
                    if len(ids) >= n:
                        break

            if not ids:
                if self._dl_generation == my_gen:
                    self._dl_progress_text.value = "Нет постов для скачивания"
                    self.page.update()
                return

            total = len(ids)
            print(f"[Download] Старт: {total} треков (поколение {my_gen}).")


            def on_bytes(filename, received, total):
                # троттлинг: не чаще ~5 раз в секунду
                now = asyncio.get_event_loop().time()
                if now - self._dl_last_update_ts < 0.2:
                    return
                self._dl_last_update_ts = now

                if self._dl_generation != my_gen:
                    return
                try:
                    if total and total > 0:
                        pct = received * 100 // total
                        self._dl_bytes_bar.value = received / total
                        self._dl_bytes_text.value = (
                            f"{_human_bytes(received)} / {_human_bytes(total)}  ({pct}%)"
                        )
                    else:
                        self._dl_bytes_bar.value = 0
                        self._dl_bytes_text.value = f"{_human_bytes(received)}"
                    self.page.update()
                except Exception as e:
                    print(f"[Download] on_bytes error: {e}")


            def on_current_file(idx, tot, filename):
                if self._dl_generation != my_gen:
                    return
                try:
                    self._dl_current_file_text.value = (
                        f"Скачивание аудио: {filename}…"
                    )
                    self._dl_bytes_bar.value = 0
                    self._dl_bytes_text.value = ""
                    self.page.update()
                except Exception as e:
                    print(f"[Download] on_current_file error: {e}")

            def on_progress(idx, tot):
                if self._dl_generation != my_gen:
                    return
                try:
                    self._dl_progress.value = (idx / tot) if tot else 0
                    self._dl_progress_text.value = f"{idx}/{tot}"
                    self.page.update()
                except Exception as e:
                    print(f"[Download] on_progress error: {e}")

            def should_cancel():
                return self._dl_cancelled or self._dl_generation != my_gen

            reports = await self.backend.download_audios_and_images_by_ids(
                forum_id=forum_id,
                topic_id=topic_id,
                message_ids=ids,
                on_progress=on_progress,
                should_cancel=should_cancel,
                on_current_file=on_current_file,
                on_bytes=on_bytes,
            )            

            if self._dl_generation != my_gen:
                print(f"[Download] Поколение {my_gen} устарело — результат отброшен.")
                return

            if self._dl_cancelled:
                self._dl_progress_text.value = f"Отменено ({len(reports)}/{total})"
                self.page.update()
            else:
                self._dl_progress_text.value = f"Готово ({len(reports)}/{total})"
                self._dl_current_file_text.value = ""
                self._dl_bytes_bar.value = 0
                self._dl_bytes_text.value = ""
                self.page.update()

                # После завершения скачивания — генерируем description.txt
                # ровно так же, как это делает main.py.
                await self._generate_description_async(my_gen)

                # Затем собираем видео из mp3, как в main.py (--make_video).
                await self._generate_video_async(my_gen)

        except asyncio.CancelledError:
            print(f"[Download] Задача поколения {my_gen} отменена.")
            raise
        except Exception as ex:
            print(f"[Download] Ошибка: {ex}")
            traceback.print_exc()
            if self._dl_generation == my_gen:
                self._dl_progress_text.value = f"Ошибка: {ex}"
                self.page.update()

    async def _generate_description_async(self, my_gen: int):
        """
        Генерирует description.txt после успешного завершения скачивания.
        Путь и параметры совпадают с тем, что делает main.py:
            src_dir   = Path(config.OUTPUT_DIR)
            desc_file = src_dir / "video" / "description.txt"

        Запускается в отдельном потоке (asyncio.to_thread), чтобы не
        блокировать event loop UI, если внутри много ffprobe/IO.
        """
        try:
            import make_description
        except Exception as e:
            print(f"[Download] make_description недоступен: {e}")
            return

        src_dir = Path(config.OUTPUT_DIR)
        desc_dir = src_dir / "video"
        desc_file = desc_dir / "description.txt"

        print(f"[Download] Генерация описания: {desc_file}")
        try:
            rc = await asyncio.to_thread(
                make_description.generate_description,
                src_dir=src_dir,
                out_file=desc_file,
            )
        except Exception as e:
            print(f"[Download] Ошибка генерации описания: {e}")
            traceback.print_exc()
            return

        # Если пока шло описание, пользователь уже открыл новый диалог/перезапустил —
        # просто не трогаем UI, но файл на диске уже лежит.
        if self._dl_generation != my_gen:
            print(f"[Download] Поколение {my_gen} устарело, "
                  f"описание всё равно записано: {desc_file}")
            return

        if rc != 0:
            print(f"[Download] ⚠️  Описание не сгенерировано (код {rc}).")
            try:
                self._dl_progress_text.value = (
                    f"Готово ({len(src_dir.iterdir()) if src_dir.exists() else 0} файлов), "
                    f"описание не создано (код {rc})"
                )
                self.page.update()
            except Exception:
                pass
        else:
            print(f"[Download] Описание готово: {desc_file}")


    async def _generate_video_async(self, my_gen: int):
        """
        Собирает mp4 из скачанных mp3 — по аналогии с --make_video в main.py.

        Пути по умолчанию:
            output        = <OUTPUT_DIR>/video/output.mp4
            default_video = <cwd>/default.mp4  (или config.DEFAULT_VIDEO, если задан)
            mp3_dir       = <OUTPUT_DIR>

        Любой из них можно переопределить через config:
            config.MAKE_VIDEO_OUTPUT  — путь к итоговому mp4
            config.DEFAULT_VIDEO      — путь к видео-заглушке

        Запускается в отдельном потоке, чтобы не блокировать UI на ffmpeg.
        """
        try:
            import make_video
        except Exception as e:
            print(f"[Download] make_video недоступен: {e}")
            return

        src_dir = Path(config.OUTPUT_DIR)
        out_dir = src_dir / "video"
        out_dir.mkdir(parents=True, exist_ok=True)

        # Аналоги дефолтов main.py, но с возможностью переопределить через config.
        output = Path(getattr(config, "MAKE_VIDEO_OUTPUT", out_dir / "output.mp4"))
        default_video = Path(getattr(config, "DEFAULT_VIDEO", "default.mp4"))

        print(f"[Download] Генерация видео: {output}")
        print(f"[Download]   mp3_dir={src_dir}, default_video={default_video}")

        # Сдвигаем прогресс на 1%, чтобы визуально было заметно, что сборка началась
        # (до первого колбэка on_segment может пройти заметное время — ffmpeg на первом сегменте).
        if self._dl_generation == my_gen:
            try:
                self._dl_video_progress.value = 0.01
                self._dl_video_segment_text.value = "Сборка видео запущена…"
                self.page.update()
            except Exception as e:
                print(f"[Download] video start-indicator error: {e}")


        def on_segment_worker(idx, total):
            # Этот колбэк дёргается из worker-потока (to_thread),
            # поэтому обновляем UI в главном loop через call_soon_threadsafe.
            if self._dl_generation != my_gen:
                return

            def _apply():
                if self._dl_generation != my_gen:
                    return
                try:
                    self._dl_video_progress.value = (idx / total) if total else 0
                    self._dl_video_segment_text.value = f"Сегмент: {idx} / {total}"
                    self.page.update()
                except Exception as e:
                    print(f"[Download] video progress UI error: {e}")

            try:
                self.main_loop.call_soon_threadsafe(_apply)
            except Exception as e:
                print(f"[Download] call_soon_threadsafe failed: {e}")

        try:
            rc = await asyncio.to_thread(
                make_video.run_conversion,
                output=output,
                default_video=default_video,
                mp3_dir=src_dir,
                on_segment=on_segment_worker,
            )
        except Exception as e:
            print(f"[Download] Ошибка генерации видео: {e}")
            traceback.print_exc()
            return

        # Если пользователь уже открыл новый диалог/перезапустил — не трогаем UI,
        # но файл на диске уже готов.
        if self._dl_generation != my_gen:
            print(f"[Download] Поколение {my_gen} устарело, "
                  f"видео всё равно записано: {output}")
            return

        if rc != 0:
            print(f"[Download] ⚠️  Видео не сгенерировано (код {rc}).")
            try:
                self._dl_video_segment_text.value = (
                    f"Видео не создано (код {rc})"
                )
                self.page.update()
            except Exception:
                pass
        else:
            print(f"[Download] Видео готово: {output}")
            try:
                self._dl_video_progress.value = 1
                self._dl_video_segment_text.value = (
                    f"Готово, видео: {output.name}"
                )
                if self._dl_close_btn is not None:
                    self._dl_close_btn.content = "Закрыть"
                self.page.update()
            except Exception:
                pass


    def _open_folder(self, path: str):
        """Открывает папку в системном файловом менеджере."""
        try:
            if path:
                # если папки ещё нет — создаём, чтобы файловый менеджер не падал
                os.makedirs(path, exist_ok=True)

            if os.name == 'nt':
                os.startfile(path)
            elif sys.platform == 'darwin':
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
            print(f"[Download] Открываю папку: {path}")
        except Exception as e:
            print(f"[Download] Не удалось открыть папку {path}: {e}")


    def close_download_dialog(self):
        print("[Download] Закрыть: отмена и закрытие диалога.")
        # Просим цикл остановиться и «обесцениваем» текущее поколение,
        # чтобы никакие поздние колбэки не писали в уже закрытый UI.
        self._dl_cancelled = True
        self._dl_generation += 1

        if self._download_dialog is not None:
            try:
                self.page.pop_dialog()
            except Exception as e:
                print(f"[Download] pop_dialog failed: {e!r}")
            self._download_dialog = None

        self.current_download_task = None
        try:
            self.page.update()
        except Exception:
            pass


    async def load_messages_from_telegram(self):
        if not self.forum_dropdown.value or not self.topic_dropdown.value:
            self.posts_list.controls.clear()
            self.page.update()
            return
        print(f"[UI Async] Загрузка сообщений...")
        self.loader.visible = True
        self.page.update()
        try:
            start_pinned_id = None
            # UI использует короткое "pin"; tg_backend исторически ждёт "pinned".
            # Маппим здесь, чтобы не менять контракт tg_backend.

            messages,pinned_list = await self.backend.fetch_telegram_messages(
                forum_id        = self.forum_dropdown.value,
                topic_id        = self.topic_dropdown.value,
                pinned_id       = self.pinned_id,
                start_pinned_id = self.pinned_id,
                time_filter     = self.filter_radio_group.value,
                reactions       = list(self.selected_reactions),
                audio_only      = self.audio_checkbox.value
            )            
            
            # messages list
            self.posts_list.controls.clear()
            
            for msg in messages:
                reaction_controls = []
                for emoji in msg.get('reactions', []):
                    clean_emoji = emoji.strip()
                    if not clean_emoji:
                        continue
                    reaction_controls.append(ft.Text(clean_emoji, size=18))

                # Помещаем картинки-эмодзи в горизонтальный ряд
                trailing_row = ft.Row(
                    controls=reaction_controls,
                    tight=True,
                    alignment=ft.MainAxisAlignment.END,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=4
                )
                
                self.posts_list.controls.append(
                    ft.ListTile(
                        leading=ft.Container(
                            content=ft.Text(msg["total"], weight=ft.FontWeight.BOLD, size=24),
                            alignment=ft.Alignment(0, 0),
                            width=48, height=30,
                            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                            border_radius=6
                        ),
                        title=ft.Text(msg["title"], size=16, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        trailing=trailing_row, # <-- Передаем цветной UI-ряд вместо обычного монохромного ft.Text
                        on_click=self.on_post_selected,
                        data=msg,
                        dense=True
                    )
                )
        except asyncio.CancelledError:
            print("[UI Async] Загрузка была успешно отменена пользователем.")
            raise                 
        except Exception as ex:
            print(f"[UI Critical Error] Ошибка загрузки постов: {ex}")
            traceback.print_exc()
        finally:
            self.loader.visible = False
            self.page.update()

    async def download_audios_and_images(self):
        """
        Скачивает аудио и соседние картинки для первых 10 сообщений из posts_list.
        ID сообщений берутся из атрибута .data каждого ft.ListTile.
        """
        if not self.forum_dropdown.value or not self.topic_dropdown.value:
            self.selected_post_text.value = "Выберите форум и топик"
            self.page.update()
            return

        # ---- Собираем до 10 ID из ListTile.data ----
        message_ids = []
        for control in self.posts_list.controls:
            if isinstance(control, ft.ListTile) and control.data:
                msg_id = control.data.get("id")
                if msg_id is not None:
                    message_ids.append(msg_id)
                if len(message_ids) >= 10:
                    break

        if not message_ids:
            self.selected_post_text.value = "Нет постов для скачивания"
            self.page.update()
            return

        self.loader.visible = True
        self.selected_post_text.value = f"Скачивание {len(message_ids)} файлов..."
        self.page.update()

        try:
            reports = await self.backend.download_audios_and_images_by_ids(
                forum_id=self.forum_dropdown.value,
                topic_id=self.topic_dropdown.value,
                message_ids=message_ids,
            )
            total   = len(reports)
            audios  = sum(1 for r in reports if r.get("audio_path"))
            images  = sum(1 for r in reports if r.get("image_path"))
            self.selected_post_text.value = (
                f"Готово: аудио {audios}/{total}, картинок {images}/{total}"
            )
            print(f"[UI Async] {self.selected_post_text.value}")
        except asyncio.CancelledError:
            print("[UI Async] Скачивание отменено пользователем.")
            raise
        except Exception as ex:
            print(f"[UI Critical Error] Ошибка скачивания: {ex}")
            traceback.print_exc()
            self.selected_post_text.value = f"Ошибка: {ex}"
        finally:
            self.loader.visible = False
            self.page.update()
            

    def on_post_selected(self, e):
        # Получаем данные выбранного сообщения
        msg_data = e.control.data
        post_title = msg_data.get("title", "")
        
        # Обновляем текст в UI
        self.selected_post_text.value = f"Выбран пост: {post_title}"
        self.page.update()
        
        # Формируем параметры для ссылки
        # Предполагается, что в msg хранится id сообщения (например, msg["id"])
        message_id = msg_data.get("id", "") 
        forum_id = self.forum_dropdown.value
        topic_id = self.topic_dropdown.value
        username = self.get_forum_username(forum_id)
        
        if forum_id and message_id:
            # Формируем URL по вашему шаблону
            tg_url = f"tg://resolve?domain={username}&thread={topic_id}&post={message_id}"
            print(f"[UI System Call] Выполнение команды: xdg-open {tg_url}")
            
            try:
                # Безопасный запуск xdg-open без shell=True
                if os.name == 'nt':
                    os.startfile(tg_url)
                else:
                    subprocess.Popen(["xdg-open", tg_url])
            except Exception as ex:
                print(f"[UI System Error] Не удалось открыть ссылку через xdg-open: {ex}")

    def get_forum_username(self, forum_id):
        # Находим username по id
        username = next((user["username"] for user in self._forums_data if str(user["id"]) == forum_id), None)

        return username

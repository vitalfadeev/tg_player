import os
import flet as ft
import traceback
import asyncio
import subprocess


class TelegramPlayerUI:
    def __init__(self, page: ft.Page, backend):
        self.page = page
        self.backend = backend
        self.selected_reactions = set()

        self.current_fetch_task = None
        
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
        self.subject_dropdown.pinned_id = None
        
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
        self.posts_list = ft.ListView(expand=True, spacing=2, height=350) 

        # === НОВОЕ: кнопка Download ===
        self.download_btn = ft.Button(
            "Download",
            on_click=lambda e: self.on_download_clicked_bridge()
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
                    ft.Radio(value="day", label="день"),
                    ft.Radio(value="pinned", label="закреп"),
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
        for emoji_text in self.EMOJI_ICONS:  # Итерируемся по ключам-эмодзи
            # Текстовая подпись с эмодзи прямо в чекбоксе
            cb = ft.Checkbox(
                label=emoji_text,
                value=False,
                data=emoji_text,
                label_style=ft.TextStyle(size=18),  # крупнее, чтобы эмодзи читалось
            )
            reaction_controls.append(cb)

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
                self.audio_checkbox,        # <-- Новый чекбокс встанет сразу за реакциями
                ft.Container(expand=True),  # «Пружина», выталкивающая кнопку в самый край
                self.to_text_btn
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
            # === НОВОЕ: строка под ListView с кнопкой справа ===
            ft.Row(
                controls=[self.download_btn],
                alignment=ft.MainAxisAlignment.END,
            ),
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
        print("[LOG] Нажата кнопка диапазона закрепа.")
        # Принудительно переключаем радио-фильтр в режим "pinned"
        if self.filter_radio_group.value != "pinned":
            self.filter_radio_group.value = "pinned"
            self.filter_radio_group.update()
                
        self.safe_execute(self.load_messages_from_telegram())

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

    # В секцию "ЧИСТЫЕ МЕТОДЫ БЕЗ СИСТЕМНЫХ АРГУМЕНТОВ"
    def on_audio_changed_bridge(self):
        print(f"[LOG] Фильтр audio изменен на: {self.audio_checkbox.value}")
        self.safe_execute(self.load_messages_from_telegram())

    def on_download_clicked_bridge(self):
        print("[LOG] Клик по кнопке 'Download'.")
        self.safe_execute(self.download_tracks())

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
                await self.load_pinned_message ()
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
            await self.load_pinned_message ()
            # Затем обновляем основную ленту сообщений
            await self.load_messages_from_telegram()
        except Exception as ex:
            print(f"[UI Critical Error] Ошибка при смене топика: {ex}")
            traceback.print_exc()
        finally:
            self.loader.visible = False
            self.page.update()

            async def download_tracks(self):
                """
                Запускает загрузку треков по текущему контексту UI:
                форум + топик + сообщения из posts_list + текущие фильтры.
                """
                print("[UI Async] Запуск download_tracks...")

                if not self.forum_dropdown.value or not self.topic_dropdown.value:
                    print("[UI Warning] Не выбраны форум или топик — загрузка отменена.")
                    self.selected_post_text.value = "Download: не выбран форум/топик"
                    self.page.update()
                    return

                self.loader.visible = True
                self.page.update()

                try:
                    # Собираем данные из текущего ListView
                    messages = [
                        control.data
                        for control in self.posts_list.controls
                        if isinstance(control, ft.ListTile) and control.data
                    ]
                    print(f"[UI Async] Сообщений для загрузки: {len(messages)}")

                    # Вызываем backend, если у него есть соответствующий метод
                    if hasattr(self.backend, "download_tracks"):
                        await self.backend.download_tracks(
                            forum_id   = self.forum_dropdown.value,
                            topic_id   = self.topic_dropdown.value,
                            messages   = messages,
                            reactions  = list(self.selected_reactions),
                            audio_only = self.audio_checkbox.value,
                        )
                    else:
                        print("[UI Warning] backend.download_tracks не реализован — заглушка.")

                    self.selected_post_text.value = f"Download: {len(messages)} постов"
                    self.page.update()

                except asyncio.CancelledError:
                    print("[UI Async] download_tracks отменён.")
                    raise
                except Exception as ex:
                    print(f"[UI Critical Error] Ошибка в download_tracks: {ex}")
                    traceback.print_exc()
                finally:
                    self.loader.visible = False
                    self.page.update()

    async def download_tracks(self):
        """
        Запускает загрузку треков по текущему контексту UI:
        форум + топик + сообщения из posts_list + текущие фильтры.
        """
        print("[UI Async] Запуск download_tracks...")

        if not self.forum_dropdown.value or not self.topic_dropdown.value:
            print("[UI Warning] Не выбраны форум или топик — загрузка отменена.")
            self.selected_post_text.value = "Download: не выбран форум/топик"
            self.page.update()
            return

        self.loader.visible = True
        self.page.update()

        try:
            # Собираем данные из текущего ListView
            messages = [
                control.data
                for control in self.posts_list.controls
                if isinstance(control, ft.ListTile) and control.data
            ]
            print(f"[UI Async] Сообщений для загрузки: {len(messages)}")

            # Вызываем backend, если у него есть соответствующий метод
            if hasattr(self.backend, "download_tracks"):
                await self.backend.download_tracks(
                    forum_id   = self.forum_dropdown.value,
                    topic_id   = self.topic_dropdown.value,
                    messages   = messages,
                    reactions  = list(self.selected_reactions),
                    audio_only = self.audio_checkbox.value,
                )
            else:
                print("[UI Warning] backend.download_tracks не реализован — заглушка.")

            self.selected_post_text.value = f"Download: {len(messages)} постов"
            self.page.update()

        except asyncio.CancelledError:
            print("[UI Async] download_tracks отменён.")
            raise
        except Exception as ex:
            print(f"[UI Critical Error] Ошибка в download_tracks: {ex}")
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

            self.subject_dropdown.pinned_id = message.get("id",None)
        else:
            self.subject_dropdown.content = ""
            self.subject_dropdown.tooltip.message = ""
            self.subject_dropdown.pinned_id = None

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
            messages,pinned_list = await self.backend.fetch_telegram_messages(
                forum_id        = self.forum_dropdown.value,
                topic_id        = self.topic_dropdown.value,
                pinned_id       = self.subject_dropdown.pinned_id,
                start_pinned_id = self.subject_dropdown.pinned_id,
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

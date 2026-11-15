# auth_dialog.py
import asyncio
import flet as ft
from telethon.errors import SessionPasswordNeededError


class AuthDialog:
    """
    Диалог авторизации Telegram для первого запуска.

    Показывается, когда `await client.is_user_authorized()` == False
    (нет session-файла или сессия не валидна).

    Этапы:
        1. Ввод номера телефона  → send_code_request(phone)
        2. Ввод кода из Telegram → sign_in(phone, code)
        3. (если включена 2FA)   → sign_in(password=...)
    """

    def __init__(self, page: ft.Page, client):
        self.page = page
        self.client = client
        self._future: asyncio.Future | None = None
        self._busy = False

        self.phone: str | None = None
        self._need_password = False

    # ------------------------------------------------------------------ API
    async def authenticate(self) -> bool:
        """
        Показывает диалог и ждёт, пока пользователь либо войдёт (True),
        либо отменит (False). Пока окно открыто — корутина «висит».
        """
        self._future = asyncio.get_running_loop().create_future()
        self._build_and_show()
        return await self._future

    # ------------------------------------------------------------ Внутреннее
    def _build_and_show(self):
        self._phone_field = ft.TextField(
            label="Номер телефона",
            hint_text="+7XXXXXXXXXX",
            autofocus=True,
            keyboard_type=ft.KeyboardType.PHONE,
        )
        self._code_field = ft.TextField(
            label="Код из Telegram",
            hint_text="код придёт в приложение / SMS",
            disabled=True,
            keyboard_type=ft.KeyboardType.NUMBER,
        )
        self._password_field = ft.TextField(
            label="Пароль 2FA",
            password=True,
            can_reveal_password=True,
            visible=False,
            disabled=True,
        )
        self._status = ft.Text("", color=ft.Colors.RED_600, size=12)

        self._send_code_btn = ft.Button(
            "Получить код",
            on_click=self._on_send_code_clicked,
        )
        self._sign_in_btn = ft.Button(
            "Войти",
            disabled=True,
            on_click=self._on_sign_in_clicked,
        )
        cancel_btn = ft.TextButton(
            "Отмена",
            on_click=self._on_cancel_clicked,
        )

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Вход в Telegram"),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text(
                            "Первый запуск: требуется авторизация в Telegram.",
                            size=12,
                            color=ft.Colors.GREY_700,
                        ),
                        self._phone_field,
                        ft.Row(controls=[self._send_code_btn]),
                        self._code_field,
                        self._password_field,
                        self._status,
                    ],
                    tight=True,
                    spacing=10,
                ),
                width=420,
            ),
            actions=[cancel_btn, self._sign_in_btn],
        )

        self.page.show_dialog(dialog)
        try:
            self.page.update()
        except Exception:
            pass

    # ---------------------------------------------------------- обработчики
    def _on_send_code_clicked(self, e):
        self.page.run_task(self._send_code)

    def _on_sign_in_clicked(self, e):
        self.page.run_task(self._sign_in)

    def _on_cancel_clicked(self, e):
        self._finish(False)

    # ------------------------------------------------------ бизнес-логика
    async def _send_code(self):
        from telethon.tl.functions.auth import ResendCodeRequest
        from telethon.errors import SendCodeUnavailableError
        
        if self._busy:
            return
        self._busy = True
        try:
            phone = (self._phone_field.value or "").strip()
            if not phone:
                self._set_status("Введите номер телефона", error=True)
                return

            self._send_code_btn.disabled = True
            self._set_status("Отправка кода…", error=False)
            try:
                sent_code = await self.client.send_code_request(phone)
                print(f"[AUTH] Код отправлен. Тип: {type(sent_code.type).__name__}")

                # Покажем пользователю, куда ждать код
                if hasattr(sent_code.type, 'length'):
                    # Это SMS или звонок
                    self._set_status(
                        f"Код отправлен по SMS (длина: {sent_code.type.length}). Проверьте сообщения.",
                        error=False
                    )
                else:
                    # Это код в приложении Telegram
                    self._set_status(
                        "Код отправлен в приложение Telegram. Проверьте чат с «Telegram» (не SMS).",
                        error=False
                    )
            except Exception as ex:
                self._send_code_btn.disabled = False
                self._set_status(f"Ошибка: {ex}", error=True)
                return

            self.phone = phone
            self._phone_field.disabled = True
            self._code_field.disabled = False
            self._sign_in_btn.disabled = False
            self._set_status(
                "Код отправлен. Введите его из Telegram.", error=False
            )
            try:
                await self._code_field.focus()
            except Exception:
                pass
        finally:
            self._busy = False

    async def _sign_in(self):
        if self._busy:
            return
        self._busy = True
        try:
            # --- Шаг 2FA ---
            if self._need_password:
                password = self._password_field.value or ""
                if not password:
                    self._set_status("Введите пароль 2FA", error=True)
                    return
                self._sign_in_btn.disabled = True
                self._set_status("Проверка пароля…", error=False)
                try:
                    await self.client.sign_in(password=password)
                except Exception as ex:
                    self._sign_in_btn.disabled = False
                    self._set_status(f"Ошибка: {ex}", error=True)
                    return
                self._finish(True)
                return

            # --- Шаг кода ---
            code = (self._code_field.value or "").strip()
            if not code:
                self._set_status("Введите код", error=True)
                return
            self._sign_in_btn.disabled = True
            self._set_status("Проверка кода…", error=False)
            try:
                await self.client.sign_in(phone=self.phone, code=code)
            except SessionPasswordNeededError:
                self._need_password = True
                self._password_field.visible = True
                self._password_field.disabled = False
                self._code_field.disabled = True
                self._sign_in_btn.disabled = False
                self._set_status(
                    "Требуется пароль двухфакторной аутентификации.",
                    error=False,
                )
                try:
                    await self._password_field.focus()
                except Exception:
                    pass
                return
            except Exception as ex:
                self._sign_in_btn.disabled = False
                self._set_status(f"Ошибка: {ex}", error=True)
                return

            self._finish(True)
        finally:
            self._busy = False

    # ------------------------------------------------------------ утилиты
    def _set_status(self, text: str, error: bool = False):
        self._status.value = text
        self._status.color = (
            ft.Colors.RED_600 if error else ft.Colors.GREEN_600
        )
        try:
            self.page.update()
        except Exception:
            pass

    def _finish(self, ok: bool):
        try:
            self.page.pop_dialog()
        except Exception:
            pass
        if self._future and not self._future.done():
            self._future.set_result(ok)

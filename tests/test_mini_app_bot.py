"""Optional Telegram Mini App menu configuration."""

from unittest.mock import AsyncMock

import pytest

from app.main import configure_mini_app_menu


@pytest.mark.asyncio
async def test_menu_configuration_is_disabled_without_url() -> None:
    bot = AsyncMock()
    await configure_mini_app_menu(bot, None)
    bot.set_chat_menu_button.assert_not_awaited()


@pytest.mark.asyncio
async def test_menu_configuration_uses_https_web_app() -> None:
    bot = AsyncMock()
    await configure_mini_app_menu(bot, "https://mini.example.org")
    button = bot.set_chat_menu_button.await_args.kwargs["menu_button"]
    assert button.text == "Открыть приложение"
    assert button.web_app.url == "https://mini.example.org"

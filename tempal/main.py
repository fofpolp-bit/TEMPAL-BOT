"""Entry point — boots aiogram and starts long polling."""

from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web

from .config import load_settings
from .game.storage import build_store
from .handlers import admin_router, gameplay_router, info_router, lobby_router
from .handlers.common import BotContext
from .middleware import ContextMiddleware, ChatAccessMiddleware
from .services.retry_middleware import RetryAfterMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def amain() -> None:
    settings = load_settings()
    store = build_store(settings.database_url, settings.state_file)
    ctx = BotContext(settings=settings, store=store)

    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    bot.session.middleware(RetryAfterMiddleware())
    dp = Dispatcher()

    context_mw = ContextMiddleware(ctx)
    dp.message.middleware(context_mw)
    dp.callback_query.middleware(context_mw)

    if settings.operate_chat_ids or settings.allowed_chat_ids:
        logger.info(
            "Chat whitelist active. operate=%s allowed=%s",
            ",".join(str(x) for x in sorted(settings.operate_chat_ids)) or "(any)",
            ",".join(str(x) for x in sorted(settings.allowed_chat_ids)) or "(any)",
        )
        access_mw = ChatAccessMiddleware(
            settings.allowed_chat_ids,
            settings.operate_chat_ids,
            bot=bot,
        )
        dp.message.middleware(access_mw)
        dp.callback_query.middleware(access_mw)
    else:
        logger.info("No chat whitelist set — bot answers in any chat")

    dp.include_router(lobby_router)
    dp.include_router(gameplay_router)
    dp.include_router(admin_router)
    dp.include_router(info_router)

    port_env = os.environ.get("PORT")
    if port_env:
        app = web.Application()

        async def health(_request: web.Request) -> web.Response:
            return web.Response(text="ok")

        app.router.add_get("/", health)
        app.router.add_get("/health", health)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", int(port_env))
        await site.start()
        logger.info("HTTP healthcheck listening on :%s", port_env)

    logger.info("Тempal bot is up. Polling…")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()

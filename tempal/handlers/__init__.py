"""Aiogram routers."""

from .lobby import router as lobby_router
from .gameplay import router as gameplay_router
from .admin import router as admin_router
from .info import router as info_router

__all__ = ["lobby_router", "gameplay_router", "admin_router", "info_router"]

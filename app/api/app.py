"""FastAPI factory independent from aiogram polling."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import authenticated_telegram, identity_service
from app.api.schemas import HealthResponse, MeResponse
from app.auth.telegram import TelegramIdentity
from app.config import Settings
from app.db.session import create_engine, create_session_factory
from app.operations import check_database
from app.services.http_identity import HttpIdentityForbidden, HttpIdentityService


def create_app(
    settings: Settings | None = None,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    owned_engine = None
    if session_factory is None:
        owned_engine = create_engine(resolved_settings.database_url)
        session_factory = create_session_factory(owned_engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if owned_engine is not None:
            await check_database(owned_engine)
        try:
            yield
        finally:
            if owned_engine is not None:
                await owned_engine.dispose()

    application = FastAPI(
        title="Ministry of Culture API",
        version="1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.session_factory = session_factory
    if resolved_settings.mini_app_allowed_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=resolved_settings.mini_app_allowed_origins,
            allow_credentials=False,
            allow_methods=["GET"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @application.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @application.get("/api/v1/me", response_model=MeResponse)
    async def me(
        club_id: Annotated[UUID, Query()],
        identity: Annotated[TelegramIdentity, Depends(authenticated_telegram)],
        service: Annotated[HttpIdentityService, Depends(identity_service)],
    ) -> MeResponse:
        try:
            view = await service.me(identity.telegram_id, club_id)
            return MeResponse.model_validate(view)
        except HttpIdentityForbidden:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CLUB_ACCESS_DENIED",
            ) from None

    return application

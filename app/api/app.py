"""FastAPI factory independent from aiogram polling."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import (
    authenticated_telegram,
    identity_service,
    membership_service,
)
from app.api.schemas import (
    HealthResponse,
    MeResponse,
    OnboardingCompleteProfileRequest,
    OnboardingRegisterRequest,
    OnboardingResponse,
)
from app.auth.telegram import TelegramIdentity
from app.config import Settings
from app.db.session import create_engine, create_session_factory
from app.operations import check_database
from app.services.http_identity import HttpIdentityForbidden, HttpIdentityService
from app.services.membership import (
    MembershipError,
    MembershipErrorCode,
    MembershipService,
)


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
            allow_methods=["GET", "POST"],
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

    @application.post(
        "/api/v1/onboarding/register",
        response_model=OnboardingResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def register_onboarding(
        payload: OnboardingRegisterRequest,
        identity: Annotated[TelegramIdentity, Depends(authenticated_telegram)],
        service: Annotated[MembershipService, Depends(membership_service)],
        identity_projection: Annotated[HttpIdentityService, Depends(identity_service)],
    ) -> OnboardingResponse:
        try:
            registration = await service.register_with_invite(
                identity.telegram_id,
                payload.invite_token,
                first_name=payload.first_name,
                last_name=payload.last_name,
                school_nickname=payload.school_nickname,
            )
            view = await identity_projection.me(
                identity.telegram_id, registration.club_id
            )
            return OnboardingResponse.model_validate(view)
        except MembershipError as error:
            raise _onboarding_error(error) from None

    @application.post(
        "/api/v1/onboarding/profile",
        response_model=OnboardingResponse,
    )
    async def complete_onboarding_profile(
        payload: OnboardingCompleteProfileRequest,
        identity: Annotated[TelegramIdentity, Depends(authenticated_telegram)],
        service: Annotated[MembershipService, Depends(membership_service)],
        identity_projection: Annotated[HttpIdentityService, Depends(identity_service)],
    ) -> OnboardingResponse:
        try:
            await service.complete_member_profile(
                identity.telegram_id,
                payload.club_id,
                first_name=payload.first_name,
                last_name=payload.last_name,
                school_nickname=payload.school_nickname,
            )
            view = await identity_projection.me(identity.telegram_id, payload.club_id)
            return OnboardingResponse.model_validate(view)
        except MembershipError as error:
            raise _onboarding_error(error) from None

    return application


def _onboarding_error(error: MembershipError) -> HTTPException:
    if error.code in {
        MembershipErrorCode.INVITE_INVALID,
        MembershipErrorCode.INVITE_EXPIRED,
        MembershipErrorCode.INVITE_EXHAUSTED,
    }:
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=MembershipErrorCode.INVITE_INVALID.value,
        )
    if error.code in {
        MembershipErrorCode.BLOCKED,
        MembershipErrorCode.INACTIVE,
        MembershipErrorCode.NOT_MEMBER,
        MembershipErrorCode.USER_NOT_FOUND,
    }:
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ONBOARDING_ACCESS_DENIED",
        )
    if error.code in {
        MembershipErrorCode.MEMBERSHIP_EXISTS,
        MembershipErrorCode.ALREADY_LINKED,
        MembershipErrorCode.PENDING_EXISTS,
        MembershipErrorCode.PROFILE_ALREADY_COMPLETE,
        MembershipErrorCode.SCHOOL_NICKNAME_CONFLICT,
    }:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error.code.value,
        )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=error.code.value,
    )

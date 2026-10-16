"""Public HTTP response contracts."""

from uuid import UUID

from pydantic import BaseModel

from app.domain.enums import MembershipRole, MembershipStatus
from app.services.http_identity import RegistrationState


class ProfileResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    display_name: str
    first_name: str | None
    last_name: str | None
    school_nickname: str | None


class MeResponse(BaseModel):
    model_config = {"from_attributes": True}

    authenticated: bool = True
    telegram_id: int
    registration_state: RegistrationState
    club_id: UUID | None = None
    membership_id: UUID | None = None
    membership_role: MembershipRole | None = None
    membership_status: MembershipStatus | None = None
    profile: ProfileResponse | None = None


class HealthResponse(BaseModel):
    status: str

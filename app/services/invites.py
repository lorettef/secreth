"""Shared secure invitation-token generation."""

import secrets


def generate_invite_token() -> str:
    return secrets.token_urlsafe(24)

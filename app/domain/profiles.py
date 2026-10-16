"""Normalization rules for registered club-member profiles."""

import unicodedata


MAX_PROFILE_FIELD_LENGTH = 255


def normalize_profile_text(value: str) -> str:
    """Normalize compatibility characters and all runs of whitespace."""

    return " ".join(unicodedata.normalize("NFKC", value).split())

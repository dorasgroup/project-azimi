"""Canonical UserRole domain enum."""

from enum import StrEnum


class UserRole(StrEnum):
    SUPER_ADMIN = "super_admin"
    USER = "user"

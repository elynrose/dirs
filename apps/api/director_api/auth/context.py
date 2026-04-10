from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MembershipRole = Literal["owner", "admin", "member"]


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Per-request identity and tenant scope (SaaS mode) or synthetic single-tenant context."""

    tenant_id: str
    user_id: str | None
    role: str | None

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None

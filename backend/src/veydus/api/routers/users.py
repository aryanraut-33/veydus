# ─────────────────────────────────────────────────────────────────
# VEYDUS — User Identity & Scope Router
# ─────────────────────────────────────────────────────────────────
# What:  FastAPI router providing user profile and resolved scope inspection.
# How:   - GET /users/me: Returns authenticated user's identifier, organization,
#          assigned department, and effective hierarchy level.
# Why:   HLD §11 requirement: Allows client frontends to inspect the current
#        user's scope and authorization parameters for UI filtering and display.
# Tools: FastAPI (APIRouter, Depends), pydantic v2, uuid.UUID,
#        veydus.auth.dependencies.get_current_user_scope, veydus.authz.models.UserScope.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from veydus.auth.dependencies import get_current_user_scope

if TYPE_CHECKING:
    from veydus.authz.models import UserScope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


class UserScopeResponse(BaseModel):
    """Current authenticated user scope."""

    org_id: UUID = Field(description="Organization tenant identifier")
    user_id: UUID = Field(description="User identifier")
    department_id: UUID = Field(description="Assigned department identifier")
    hierarchy_level: int = Field(description="Authorized hierarchy level clearance")


@router.get("/me", response_model=UserScopeResponse)
async def get_my_scope(
    scope: UserScope = Depends(get_current_user_scope),
) -> UserScopeResponse:
    """Returns the caller's server-resolved identity and authorization scope."""
    return UserScopeResponse(
        org_id=scope.org_id,
        user_id=scope.user_id,
        department_id=scope.department_id,
        hierarchy_level=scope.hierarchy_level,
    )

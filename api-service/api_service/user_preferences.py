"""Typed preferences owned by the authenticated user."""
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend_common.db import User


class UserPreferences(BaseModel):
    light_mode: bool = False
    assistant_approval: bool = True


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    light_mode: bool | None = None
    assistant_approval: bool | None = None


def read_preferences(db: Session, user_id: str) -> UserPreferences:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    return UserPreferences(light_mode=user.light_mode, assistant_approval=user.assistant_approval)


def update_preferences(db: Session, user_id: str, update: PreferenceUpdate) -> UserPreferences:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    for key, value in update.model_dump(exclude_none=True).items():
        setattr(user, key, value)
    db.commit()
    return read_preferences(db, user_id)

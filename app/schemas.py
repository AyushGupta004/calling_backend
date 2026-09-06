from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class UserBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    username: str = Field(..., min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_.-]+$")


class UserCreate(UserBase):
    pass


class UserResponse(UserBase):
    id: str
    created_at: datetime
    is_online: Optional[bool] = None

    model_config = ConfigDict(from_attributes=True)


class UserSearchResult(BaseModel):
    id: str
    name: str
    username: str
    is_online: Optional[bool] = None

    model_config = ConfigDict(from_attributes=True)


class ContactCreate(BaseModel):
    user_id: str
    contact_id: str


class ContactResponse(BaseModel):
    id: int
    user_id: str
    contact_id: str
    name: str
    username: str
    created_at: datetime
    is_online: Optional[bool] = None
    contact: Optional[UserSearchResult] = None

    model_config = ConfigDict(from_attributes=True)


class SignalingMessage(BaseModel):
    type: str
    to_user_id: Optional[str] = None
    call_id: Optional[str] = None
    sdp: Optional[Any] = None
    candidate: Optional[Any] = None
    reason: Optional[str] = None

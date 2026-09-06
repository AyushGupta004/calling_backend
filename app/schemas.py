from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------
# User Schemas
# ---------------------------------------------------------

class UserBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Display name of the user")
    username: str = Field(..., min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_.-]+$", description="Unique username")


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


# ---------------------------------------------------------
# Contact Schemas
# ---------------------------------------------------------

class ContactCreate(BaseModel):
    user_id: str = Field(..., description="ID of the user adding the contact")
    contact_id: str = Field(..., description="ID of the user to be added as contact")


class ContactResponse(BaseModel):
    id: int = Field(..., description="Contact table record ID")
    user_id: str = Field(..., description="Requesting user ID")
    contact_id: str = Field(..., description="Target contact user ID")
    name: str = Field(..., description="Contact's display name")
    username: str = Field(..., description="Contact's username")
    created_at: datetime
    is_online: Optional[bool] = None
    contact: Optional[UserSearchResult] = None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------
# Signaling Message Schemas
# ---------------------------------------------------------

class SignalingMessage(BaseModel):
    type: str = Field(..., description="Message type: call-request, call-accept, call-reject, offer, answer, ice-candidate, hang-up, ping, pong")
    sender_id: Optional[str] = None
    target_user_id: Optional[str] = None
    room_id: Optional[str] = None
    sdp: Optional[Any] = None
    candidate: Optional[Any] = None
    data: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None

    model_config = ConfigDict(extra="allow")

from pydantic import BaseModel, EmailStr, Field
from typing import Optional


class StaffCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    title: str = Field(..., min_length=2, max_length=120)
    responsibility: Optional[str] = Field(None, max_length=300)


class StaffUpdate(BaseModel):
    name: Optional[str] = None
    title: Optional[str] = None
    responsibility: Optional[str] = None


class StaffResponse(BaseModel):
    id: str
    org_id: str
    name: str
    email: str
    title: str
    responsibility: Optional[str]
    created_at: str


class StaffLoginRequest(BaseModel):
    email: EmailStr
    event_id: str = Field(..., description="The event the staff member is logging into")


class StaffLoginResponse(BaseModel):
    id: str
    name: str
    email: str
    title: str
    responsibility: Optional[str]
    event_id: str

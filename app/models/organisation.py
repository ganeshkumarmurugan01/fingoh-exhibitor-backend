from pydantic import BaseModel, Field
from typing import Optional
import re


def slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    slug = re.sub(r"^-+|-+$", "", slug)
    return slug


class OrganisationCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)

    def to_db(self) -> dict:
        return {
            "name": self.name,
            "slug": slugify(self.name),
        }


class OrganisationResponse(BaseModel):
    id: str
    name: str
    slug: str
    plan: str
    created_at: str


class ProfileResponse(BaseModel):
    id: str
    org_id: Optional[str]
    name: Optional[str]
    title: Optional[str]
    role: str
    created_at: str


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    title: Optional[str] = None

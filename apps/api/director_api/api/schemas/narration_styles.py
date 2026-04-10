from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class NarrationStyleItemOut(BaseModel):
    ref: str
    kind: str
    title: str
    prompt: str
    is_builtin: bool


class NarrationStylesListOut(BaseModel):
    styles: list[NarrationStyleItemOut]


class NarrationStyleCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    prompt_text: str = Field(min_length=10, max_length=12000)


class NarrationStylePatchBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    prompt_text: str | None = Field(default=None, min_length=10, max_length=12000)


class NarrationStyleRowOut(BaseModel):
    id: uuid.UUID
    title: str
    prompt_text: str

    model_config = {"from_attributes": True}

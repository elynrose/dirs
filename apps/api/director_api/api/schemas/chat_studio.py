from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ChatStudioGuideMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=12000)


class ChatStudioGuideRequest(BaseModel):
    """Conversation for the setup guide; last message should be from the user."""

    messages: list[ChatStudioGuideMessage] = Field(..., min_length=1, max_length=48)
    """Optional client-side brief fields (title, topic, target_runtime_minutes, narration_style, …)."""

    current_brief: dict[str, Any] | None = None
    project_id: UUID | None = None

from pydantic import BaseModel, ConfigDict, Field


class LlmPromptItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    prompt_key: str
    title: str
    description: str = ""
    default_content: str
    effective_content: str
    is_custom: bool


class LlmPromptPatchBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=120_000)

from pydantic import BaseModel, Field


class ConversationIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class ConversationAccepted(BaseModel):
    id: str
    status: str
    status_url: str

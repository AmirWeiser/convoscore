from pydantic import BaseModel, Field, field_validator


class ConversationIn(BaseModel):
    """The single validation contract for both ingestion paths - the API
    parses request bodies through this model, and the worker parses S3
    object content through the same model, so the two paths can never
    silently accept different shapes. See DECISIONS.md."""

    text: str = Field(min_length=1, max_length=20_000)

    @field_validator("text")
    @classmethod
    def _not_whitespace_only(cls, value: str) -> str:
        # min_length=1 counts raw characters, so "   " (3 spaces) already
        # passes it - reject anything that has no non-whitespace content.
        if not value.strip():
            raise ValueError("text must not be empty or whitespace-only")
        return value


class ConversationAccepted(BaseModel):
    id: str
    status: str
    status_url: str

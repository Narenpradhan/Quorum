from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class VoteRequest(BaseModel):
    """Payload to cast a vote on a specific poll option."""

    option_id: int = Field(..., description="ID of the selected option")
    voter_fingerprint: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Unique voter identifier/hash for auditing",
    )


class VoteResponse(BaseModel):
    """Acknowledged vote event response."""

    status: str = Field("queued", description="Status of the vote event")
    poll_id: int = Field(..., description="Target poll identifier")
    option_id: int = Field(..., description="Target option identifier")


class PollOptionResponse(BaseModel):
    """Details and real-time vote tally for a poll option."""

    id: int
    poll_id: int
    label: str
    vote_count: int = Field(..., description="Effective real-time vote count")
    percentage: float = Field(0.0, description="Percentage of total votes")

    model_config = ConfigDict(from_attributes=True)


class PollResponse(BaseModel):
    """Poll detail with aggregated options and total votes."""

    id: int
    title: str
    description: Optional[str] = None
    created_at: datetime
    options: List[PollOptionResponse]
    total_votes: int = Field(0, description="Sum of votes across all options")

    model_config = ConfigDict(from_attributes=True)


class HealthResponse(BaseModel):
    """Liveness probe response."""

    status: str = "alive"


class ReadinessResponse(BaseModel):
    """Readiness probe response indicating downstream service states."""

    status: str
    database: str
    redis: str

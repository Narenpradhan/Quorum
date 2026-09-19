from datetime import datetime
from typing import List, Optional
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Index,
    func,
)
from sqlalchemy.orm import relationship

from backend.app.database import Base


class Poll(Base):
    """Represents a question or topic up for vote."""

    __tablename__ = "polls"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    options = relationship(
        "PollOption",
        back_populates="poll",
        cascade="all, delete-orphan",
        order_by="PollOption.id",
        lazy="selectin",
    )
    votes = relationship(
        "Vote",
        back_populates="poll",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )


class PollOption(Base):
    """An individual voting choice for a given Poll."""

    __tablename__ = "poll_options"

    id = Column(Integer, primary_key=True, index=True)
    poll_id = Column(
        Integer,
        ForeignKey("polls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label = Column(String(128), nullable=False)
    vote_count = Column(Integer, default=0, nullable=False)

    # Relationships
    poll = relationship("Poll", back_populates="options")
    votes = relationship(
        "Vote",
        back_populates="option",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )


class Vote(Base):
    """Audit log entry for each submitted vote."""

    __tablename__ = "votes"

    id = Column(Integer, primary_key=True, index=True)
    poll_id = Column(
        Integer,
        ForeignKey("polls.id", ondelete="CASCADE"),
        nullable=False,
    )
    option_id = Column(
        Integer,
        ForeignKey("poll_options.id", ondelete="CASCADE"),
        nullable=False,
    )
    voter_hash = Column(String(64), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    poll = relationship("Poll", back_populates="votes")
    option = relationship("PollOption", back_populates="votes")

    __table_args__ = (
        Index("idx_votes_poll_id", "poll_id"),
        Index("idx_votes_created_at", "created_at"),
        Index("uq_votes_poll_voter", "poll_id", "voter_hash", unique=True),
    )

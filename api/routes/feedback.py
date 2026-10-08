"""Feedback: a thumbs up or down for an answer. Sending it again replaces the old one."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from api.auth.principal import PrivateAccess
from api.dependencies import SessionDep
from api.errors import not_found
from shared.db.models import Feedback, FeedbackRating, Message, MessageRole

router = APIRouter(prefix="/v1/messages", tags=["chat"])


class FeedbackRequest(BaseModel):
    rating: FeedbackRating
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None


class FeedbackResponse(BaseModel):
    message_id: uuid.UUID
    rating: FeedbackRating
    comment: str | None
    updated_at: datetime


@router.post("/{message_id}/feedback")
async def give_feedback(
    message_id: uuid.UUID, body: FeedbackRequest, principal: PrivateAccess, session: SessionDep
) -> FeedbackResponse:
    """Rate an answer (the `message_id` of a chat response)."""
    answer_id = await session.scalar(
        select(Message.id).where(
            Message.id == message_id,
            Message.tenant_id == principal.tenant_id,
            Message.role == MessageRole.ASSISTANT,
        )
    )
    if answer_id is None:  # also for other tenants' answers: we do not say they exist
        raise not_found("There is no answer with this ID.")
    updated_at = await session.scalar(
        insert(Feedback)
        .values(
            tenant_id=principal.tenant_id,
            message_id=message_id,
            rating=body.rating,
            comment=body.comment,
        )
        .on_conflict_do_update(
            index_elements=[Feedback.message_id],
            set_={"rating": body.rating, "comment": body.comment, "updated_at": func.now()},
        )
        .returning(Feedback.updated_at)
    )
    await session.commit()
    return FeedbackResponse(
        message_id=message_id,
        rating=body.rating,
        comment=body.comment,
        updated_at=updated_at,
    )

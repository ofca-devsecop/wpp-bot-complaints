"""Tareas de mantenimiento: borradores vencidos."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from sicar_bot.adapters.db.repositories import ConversationRepository, ReportRepository, now_local

log = logging.getLogger(__name__)

ABANDON_AFTER_HOURS = 72


def abandon_stale(
    session_factory: Callable[[], Session],
    *,
    max_age_hours: int = ABANDON_AFTER_HOURS,
    now: datetime | None = None,
) -> int:
    """Marca como abandonados los borradores sin actividad y libera su conversación."""
    cutoff = (now or now_local()) - timedelta(hours=max_age_hours)
    with session_factory() as session:
        count = ReportRepository(session).abandon_stale(cutoff)
        if count:
            ConversationRepository(session).detach_abandoned()
        session.commit()
    log.info("abandoned stale drafts", extra={"count": count})
    return count

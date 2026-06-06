"""FastAPI dependency helpers for database sessions and auth context."""

from collections.abc import Generator

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from backend_common.auth import AuthContext, get_auth_context
from backend_common.db import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """Yield a request-scoped database session and close it afterward.

    Yields
    ------
    Session
        SQLAlchemy session for the current request.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_context(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> AuthContext:
    """Resolve the authenticated request context from the bearer header.

    Parameters
    ----------
    db : Session
        Database session used to look up auth state.
    authorization : str | None
        Optional ``Authorization`` header value from the request.

    Returns
    -------
    AuthContext
        Authorization and identity details for the current request.
    """
    return get_auth_context(db, authorization)

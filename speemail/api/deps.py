from typing import Generator

from sqlalchemy.orm import Session

from speemail.auth.graph_auth import GraphClient, get_graph_client
from speemail.models.database import get_db


def get_db_dep() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a DB session."""
    yield from get_db()


def get_graph_dep() -> GraphClient:
    """FastAPI dependency: returns the shared GraphClient."""
    return get_graph_client()

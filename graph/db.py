"""
graph/db.py — Neo4j connection helpers shared by the loader, fraud queries,
feature builder and the API.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from functools import lru_cache

from neo4j import Driver, GraphDatabase

from core.config import settings

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_driver() -> Driver:
    """One driver per process. The driver owns a connection pool, so it is
    expensive to create and meant to be long-lived and shared."""
    return GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))


def neo4j_available() -> bool:
    try:
        get_driver().verify_connectivity()
        return True
    except Exception as exc:
        log.info("Neo4j not reachable at %s (%s)", settings.neo4j_uri, exc.__class__.__name__)
        return False


def gds_available() -> bool:
    """True if the Graph Data Science plugin is installed in the server."""
    try:
        with get_driver().session() as s:
            s.run("RETURN gds.version() AS v").single()
        return True
    except Exception:
        return False


@contextmanager
def session():
    with get_driver().session() as s:
        yield s


def run(query: str, **params) -> list[dict]:
    """Run a read/write query in an auto-commit transaction; return rows as dicts."""
    with session() as s:
        return [r.data() for r in s.run(query, **params)]

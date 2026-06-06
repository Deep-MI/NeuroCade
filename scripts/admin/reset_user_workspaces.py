#!/usr/bin/env python3
"""Provide the reset user workspaces maintenance script for NeuroCade."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend_common.admin_reset import reset_owned_workspaces  # noqa: E402
from backend_common.db import SessionLocal, User  # noqa: E402
from backend_common.settings import get_settings  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse command-line options for selecting workspace owners."""
    parser = argparse.ArgumentParser(
        description="Delete all owned workspaces for all users, or for selected users.",
    )
    parser.add_argument(
        "--user-id",
        action="append",
        dest="user_ids",
        default=[],
        help="Restrict the reset to workspaces owned by this user id. May be passed multiple times.",
    )
    return parser.parse_args()


def main() -> int:
    """Reset owned workspaces and return a process exit code."""
    args = parse_args()
    settings = get_settings()
    session = SessionLocal()
    try:
        user_ids = args.user_ids or None
        if user_ids:
            known_user_ids = {
                user_id
                for (user_id,) in session.query(User.id).filter(User.id.in_(user_ids)).all()
            }
            missing_user_ids = [user_id for user_id in user_ids if user_id not in known_user_ids]
            if missing_user_ids:
                raise SystemExit(f"Unknown user ids: {', '.join(missing_user_ids)}")

        counts = reset_owned_workspaces(session, settings, user_ids=user_ids)
        session.commit()
    except SystemExit:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    scope = ", ".join(args.user_ids) if args.user_ids else "all users"
    print(
        f"Reset workspaces for {scope}: "
        f"deleted {counts.workspaces_deleted} workspace(s) and {counts.cases_deleted} case(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

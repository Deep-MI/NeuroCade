#!/usr/bin/env python3
"""Provide the reset sample case maintenance script for NeuroCade."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend_common.admin_reset import reset_sample_cases  # noqa: E402
from backend_common.db import SessionLocal, User  # noqa: E402
from backend_common.settings import get_settings  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse user filters for the sample case reset command."""
    parser = argparse.ArgumentParser(
        description="Delete and reseed the repo sample case for all users, or for selected users.",
    )
    parser.add_argument(
        "--user-id",
        action="append",
        dest="user_ids",
        default=[],
        help="Restrict the reset to this user id. May be passed multiple times.",
    )
    return parser.parse_args()


def main() -> int:
    """Reset repo sample cases for all or selected users."""
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

        counts = reset_sample_cases(session, settings, user_ids=user_ids)
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
    print(f"Reset sample case for {scope}: reseeded {counts.sample_cases_reset} user sample case(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

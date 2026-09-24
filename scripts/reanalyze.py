"""Re-reads every stored actionable email with the current date/action extraction.

    python scripts/reanalyze.py

Use it after upgrading the extraction (bump NLP_VERSION in app/services/analysis.py, or run it as is to force a
full pass). Calendar items that came from an email and were never edited by the user are rebuilt from the new
result; anything the user changed is left alone.
"""

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.getcwd())

from sqlalchemy import select, update  # noqa: E402

from app.db.models import Activity, Email, User  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.repositories import activities as activities_repo  # noqa: E402
from app.services.activities_service import activity_from_email  # noqa: E402
from app.services.analysis import reanalyze_outdated  # noqa: E402

UNTOUCHED = timedelta(seconds=2)  # an activity nobody edited has updated_at == created_at


def main() -> None:
    with SessionLocal() as db:
        db.execute(update(Email).values(nlp_version=0))
        db.commit()
        for user_id in db.scalars(select(User.id)):
            analysed = 0
            while chunk := reanalyze_outdated(db, user_id, limit=500):
                analysed += chunk
            rebuilt = 0
            for activity in db.scalars(
                select(Activity).where(
                    Activity.user_id == user_id, Activity.source == "email", Activity.status == "todo"
                )
            ):
                if abs(activity.updated_at - activity.created_at) >= UNTOUCHED or not activity.email_id:
                    continue
                email = db.get(Email, activity.email_id)
                if email is None:
                    continue
                fresh = activity_from_email({c.name: getattr(email, c.name) for c in Email.__table__.columns})
                activities_repo.update(
                    db,
                    activity,
                    title=fresh["title"],
                    notes=fresh["notes"],
                    start_at=fresh["start_at"],
                    all_day=fresh["all_day"],
                )
                rebuilt += 1
            print(f"user {user_id}: {analysed} emails analysed, {rebuilt} calendar items rebuilt")


if __name__ == "__main__":
    main()

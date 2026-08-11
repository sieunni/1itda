from datetime import date

from extensions import db
from models import Job


def is_job_closed(job):
    return job.status == "closed" or bool(job.deadline and job.deadline < date.today())


def close_expired_jobs():
    """Close only public jobs whose deadline has passed.

    Pending jobs have never been approved for public viewing.  Changing those to
    ``closed`` would lose that distinction and could make their detail page
    public, so they intentionally remain pending until reviewed or edited.
    """
    updated = (
        Job.query.filter(
            Job.status == "approved",
            Job.deadline.isnot(None),
            Job.deadline < date.today(),
        )
        .update({Job.status: "closed"}, synchronize_session=False)
    )
    if updated:
        db.session.commit()
    return updated

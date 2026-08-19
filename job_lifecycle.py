from datetime import date

from extensions import db
from models import Job


def is_job_closed(job):
    return job.status == "closed" or bool(job.deadline and job.deadline < date.today())


def close_expired_jobs():
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

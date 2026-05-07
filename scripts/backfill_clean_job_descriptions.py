import os
import sys
from importlib import import_module

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)


def main() -> None:
    session_module = import_module("app.db.session")
    crawler_module = import_module("app.services.crawler_service")
    job_module = import_module("app.models.job")

    db = session_module.get_standalone_db()
    JobCrawler = crawler_module.JobCrawler
    Job = job_module.Job

    updated = 0
    try:
        jobs = db.query(Job).all()
        for job in jobs:
            cleaned = JobCrawler._clean_lines(job.description_raw or "")
            if cleaned and cleaned != job.description_clean:
                job.description_clean = cleaned
                updated += 1

        db.commit()
        print(f"Updated {updated}/{len(jobs)} job descriptions.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()

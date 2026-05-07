import os
import sys
from importlib import import_module

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)


def main() -> None:
    session_module = import_module("app.db.session")
    skill_module = import_module("app.models.skill")
    job_skill_module = import_module("app.models.job_skill")
    normalization_module = import_module("app.services.skill_normalization")

    db = session_module.get_standalone_db()
    Skill = skill_module.Skill
    JobSkill = job_skill_module.JobSkill
    is_non_skill_role_label = normalization_module.is_non_skill_role_label

    deleted_job_skills = 0
    deactivated_skills = 0

    try:
        role_skills = [
            skill
            for skill in db.query(Skill).all()
            if skill.name and is_non_skill_role_label(skill.name)
        ]

        for skill in role_skills:
            deleted_job_skills += (
                db.query(JobSkill)
                .filter(JobSkill.skill_skill_id == skill.skill_id)
                .delete(synchronize_session=False)
            )
            if skill.is_active:
                skill.is_active = False
                deactivated_skills += 1

        db.commit()
        print(f"role skills found: {len(role_skills)}")
        print(f"job_skills deleted: {deleted_job_skills}")
        print(f"skills deactivated: {deactivated_skills}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()

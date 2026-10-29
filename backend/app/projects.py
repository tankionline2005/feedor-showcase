"""Projects: a user works in one of their projects at a time; each has its own plan, channels and
API key. Every user has at least one: it is created with the user, with «Free Trial» (billing.py)."""

import secrets

from sqlalchemy import select

from .models import Project, User


def new_api_key() -> str:
    return "km_" + secrets.token_urlsafe(32)


def new_project(owner_id: int) -> Project:
    return Project(owner_id=owner_id, api_key=new_api_key())


async def current_project(db, user: User, wanted: int | None = None) -> Project:
    """The project the user works in: the one this browser opened (`wanted`) if it is still theirs,
    else the one they opened last anywhere, else their first (remembered on the user; the caller
    commits)."""
    for project_id in (wanted, user.project_id):
        if project_id:
            project = await db.get(Project, project_id)
            if project is not None and project.owner_id == user.id:
                return project
    project = await db.scalar(select(Project).where(Project.owner_id == user.id).order_by(Project.id).limit(1))
    if project is None:
        from .billing import start_trial

        project = new_project(user.id)
        db.add(project)
        await start_trial(db, user, project)  # a new account's first project: «Free Trial», once
        await db.flush()
    user.project_id = project.id
    return project


def project_title(project: Project, index: int) -> str:
    """A project is named after its channel; until one is chosen, by its place among the user's."""
    return project.channel_title or f"Проект {index + 1}"


async def user_projects(db, user_id: int) -> list[Project]:
    return list((await db.execute(
        select(Project).where(Project.owner_id == user_id).order_by(Project.id)
    )).scalars())

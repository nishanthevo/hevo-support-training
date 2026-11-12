"""Shared builders for the enrollment tests."""

from __future__ import annotations

from django.utils import timezone

from apps.accounts.models import Role
from apps.catalog import services as catalog
from apps.catalog.models import Activity
from apps.enrollment.models import Enrollment

QUESTIONS = [{"text": "One", "choices": ["A", "B"], "answer": 0}]


def pin(user, version, *, state=Enrollment.State.ACTIVE, **extra) -> Enrollment:
    """Put `user` on `version` directly (the rule under test is access, not the service)."""
    return Enrollment.objects.create(
        user=user,
        course=version.course,
        version=version,
        state=state,
        enrolled_at=timezone.now(),
        **extra,
    )


def course_with(make_role_user, *, title="Course", publish=True, order="free"):
    """A published course: one module, two lessons; the first has a reading and a quiz."""
    author = make_role_user(Role.Name.TRAINER, username=f"a-{title}", email=f"a-{title}@e.com")
    admin = make_role_user(Role.Name.ADMIN, username=f"adm-{title}", email=f"adm-{title}@e.com")
    course = catalog.create_course(title=title, user=author)
    version = catalog.open_version(course)
    module = catalog.add_node(version.root_node, title="Module")
    first = catalog.add_node(module, title="First")
    reading = Activity.objects.create(
        content_node=first,
        activity_type="reading",
        title="Read",
        config={"source_url": "https://x.io"},
    )
    quiz = Activity.objects.create(
        content_node=first,
        activity_type="mcq",
        title="Quiz",
        config={"questions": QUESTIONS, "pass_threshold": 1},
        max_attempts=3,
    )
    second = catalog.add_node(module, title="Second")
    later = Activity.objects.create(
        content_node=second,
        activity_type="reading",
        title="Later",
        config={"source_url": "https://y.io"},
    )
    catalog.set_learning_order(version, order)
    if publish:
        version.publish(admin)
    return {
        "course": course,
        "version": version,
        "module": module,
        "first": first,
        "second": second,
        "reading": reading,
        "quiz": quiz,
        "later": later,
        "author": author,
        "admin": admin,
    }


def publish_next(world, make_role_user=None):
    """Clone the published version into a draft and publish it: the old one is archived.
    Returns the new version (its content is a plain copy)."""
    draft = catalog.get_or_create_draft(world["course"])
    draft.publish(world["admin"])
    return draft

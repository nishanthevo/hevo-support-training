"""Shared builders for the written-quiz tests."""

from __future__ import annotations

from apps.accounts.models import Role
from apps.catalog import services as catalog
from apps.catalog.models import Activity

CHOICE = {"text": "Pick", "choices": ["A", "B"], "answer": 0}
MULTI = {"text": "Pick some", "choices": ["X", "Y", "Z"], "answer": [0, 2], "explanation": "why"}
WRITTEN = {
    "type": "written",
    "text": "Explain the fix",
    "max_marks": 4,
    "reference_answer": "Re-authorise the source, then resync.",
    "key_points": "re-authorise\nresync",
    "explanation": "Because the token expired.",
}

# 2 choice points + a written question worth 4 = 6 points; 70% needs 5 of 6.
CONFIG = {"questions": [CHOICE, MULTI, WRITTEN], "pass_threshold": 0.7}
ALL_RIGHT = [[0], [0, 2], "Re-authorise the source and resync it."]
ALL_WRONG = [[1], [1], "I don't know."]


def written_course(
    make_role_user, *, order: str = "free", max_attempts: int = 3, config=None, title="Written"
):
    """Lesson One holds the mixed quiz (required); lesson Two a reading, so a course that goes
    in order shows what an unmarked quiz does and doesn't hold up."""
    author = make_role_user(Role.Name.TRAINER, username=f"a-{title}", email=f"a-{title}@e.com")
    admin = make_role_user(Role.Name.ADMIN, username=f"adm-{title}", email=f"adm-{title}@e.com")
    course = catalog.create_course(title=title, user=author)
    version = catalog.open_version(course)
    module = catalog.add_node(version.root_node, title="Module")
    one = catalog.add_node(module, title="One")
    quiz = Activity.objects.create(
        content_node=one,
        activity_type="mcq",
        title="Mixed quiz",
        config=config or CONFIG,
        max_attempts=max_attempts,
        is_required=True,
    )
    two = catalog.add_node(module, title="Two")
    later = Activity.objects.create(
        content_node=two,
        activity_type="reading",
        title="Later",
        config={"source_url": "https://y.io"},
    )
    catalog.set_learning_order(version, order)
    version.publish(admin)
    return {
        "course": course,
        "version": version,
        "one": one,
        "two": two,
        "quiz": quiz,
        "later": later,
        "author": author,
        "admin": admin,
    }


def cast(make_role_user):
    return {
        "learner": make_role_user(Role.Name.LEARNER, username="lea", email="lea@e.com"),
        "other": make_role_user(Role.Name.LEARNER, username="oth", email="oth@e.com"),
        "mentor": make_role_user(Role.Name.MENTOR, username="mia", email="mia@e.com"),
        "mentor2": make_role_user(Role.Name.MENTOR, username="max", email="max@e.com"),
        "trainer": make_role_user(Role.Name.TRAINER, username="tom", email="tom@e.com"),
        "root": make_role_user(Role.Name.ADMIN, username="ada", email="ada@e.com"),
    }

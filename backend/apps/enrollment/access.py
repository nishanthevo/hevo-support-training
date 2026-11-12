"""The one rule for "may this person use this course version?".

Every learner-facing path (the catalog, recording progress, streaming a lesson's video, a
simulation, results) asks `can_use` instead of looking at a version's state itself, so the
rule is written once:

    the course is not archived, and
      * the person has an **active enrollment pinned to this very version** (the version may
        since have been superseded and archived: they stay on it until moved), or
      * the version is **published** and the person is an author (`catalog.author_content`),
        so authors can still open what they publish without enrolling themselves.

Drafts and in-review versions are never learner-visible. An unenrolled learner gets nothing.
"""

from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.accounts.rbac import can
from apps.catalog.models import ContentNode, CourseVersion

from .models import Enrollment, ModuleExclusion

AUTHOR = "catalog.author_content"

# A version that has been published, now or since superseded. A draft or in-review version is
# the authors' workspace: no enrollment makes it learner-visible.
RELEASED = (CourseVersion.State.PUBLISHED, CourseVersion.State.ARCHIVED)


def pinned_version_ids(user) -> set[int]:
    """The versions this person is actively enrolled on."""
    return set(
        Enrollment.objects.filter(user=user, state=Enrollment.State.ACTIVE).values_list(
            "version_id", flat=True
        )
    )


def usable_versions(user) -> QuerySet[CourseVersion]:
    """Every version `user` may use, as a queryset (the list view and report scoping)."""
    live = CourseVersion.objects.filter(
        course__archived_at__isnull=True, root_node__isnull=False, state__in=RELEASED
    )
    allowed = Q(
        pk__in=Enrollment.objects.filter(user=user, state=Enrollment.State.ACTIVE).values("version")
    )
    if can(user, AUTHOR):
        allowed |= Q(state=CourseVersion.State.PUBLISHED)
    return live.filter(allowed)


def module_id_of(node: ContentNode) -> int | None:
    """The module (the `segment`) a lesson, section or module belongs to, or None for the
    course root. Free when the caller loaded `parent__parent` (a section's lesson's module)."""
    if node.kind == ContentNode.Kind.SEGMENT:
        return node.pk
    if node.kind == ContentNode.Kind.TOPIC:
        return node.parent_id
    if node.kind == ContentNode.Kind.SUBTOPIC:
        return node.parent.parent_id if node.parent is not None else None
    return None


def disabled_module_ids(user, version: CourseVersion) -> frozenset[int]:
    """The modules of `version` switched off for `user` (one query).

    Empty for anyone without an active enrollment on this version, so an author opening a
    published course without enrolling sees all of it. Deliberately **not** part of `can_use`:
    that answers "may they use this version at all", this answers "which parts".
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return frozenset()
    return frozenset(
        ModuleExclusion.objects.filter(
            enrollment__user=user,
            enrollment__version=version,
            enrollment__state=Enrollment.State.ACTIVE,
        ).values_list("node_id", flat=True)
    )


def is_node_disabled(user, node: ContentNode) -> bool:
    """Is this lesson/section/module inside a module switched off for `user`?"""
    module_id = module_id_of(node)
    if module_id is None:
        return False
    return module_id in disabled_module_ids(user, node.course_version)


def can_use(user, version: CourseVersion) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if version.course.archived_at is not None or version.state not in RELEASED:
        return False
    if Enrollment.objects.filter(
        user=user, version=version, state=Enrollment.State.ACTIVE
    ).exists():
        return True
    return version.state == CourseVersion.State.PUBLISHED and can(user, AUTHOR)

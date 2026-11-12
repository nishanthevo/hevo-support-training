"""
Remove the retired activity types (PDF, Workshop, External course) from a course's **draft**.

Those types were never finished: they could only ever say "coming later", and authors can no
longer add them. Rows already in *published and archived* versions are history that enrolled
learners are pinned to, so this command never touches them; it deletes only from a version that
is still a draft, through the same service Studio uses (`catalog.services.delete_activity`, which
refuses anything that isn't an editable draft).

    manage.py retire_activity_types --course cx-onboarding-2026 --dry-run
    manage.py retire_activity_types --course cx-onboarding-2026

It does not publish anything or move anyone. After it runs, lessons and sections that held
nothing else are listed: the publish checklist flags an empty lesson, so an author must fill or
remove those before the draft can be published.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog import services
from apps.catalog.models import Activity, ContentNode, Course, CourseVersion

RETIRED = ("pdf", "workshop", "external_course")


def _would_be_empty(node: ContentNode, removing: set[int]) -> bool:
    """Would this lesson hold nothing once `removing` (activity ids) are gone? (The publish
    checklist's own definition: no description, no sections, no activities.)"""
    remaining = [a for a in node.activities.all() if a.pk not in removing]
    return not (node.description.strip() or node.children.exists() or remaining)


class Command(BaseCommand):
    help = "Delete retired activity types (PDF, Workshop, External course) from a course's draft."

    def add_arguments(self, parser):
        parser.add_argument("--course", required=True, help="The course slug.")
        parser.add_argument(
            "--dry-run", action="store_true", help="Say what would be deleted; change nothing."
        )

    def handle(self, *args, **options):
        try:
            course = Course.objects.get(slug=options["course"])
        except Course.DoesNotExist as exc:
            raise CommandError(f"No course with the slug “{options['course']}”.") from exc
        dry = options["dry_run"]

        found = Activity.objects.filter(
            activity_type__in=RETIRED, content_node__course_version__course=course
        ).select_related("content_node__course_version")
        drafts: list[Activity] = []
        elsewhere: dict[str, int] = {}
        for activity in found:
            version = activity.content_node.course_version
            if version.state == CourseVersion.State.DRAFT:
                drafts.append(activity)
            else:
                key = f"v{version.version} ({version.state})"
                elsewhere[key] = elsewhere.get(key, 0) + 1

        self.stdout.write(
            f"{len(drafts)} retired activit{'y' if len(drafts) == 1 else 'ies'} in the draft."
        )
        if elsewhere:
            self.stdout.write(
                "Left alone (published, archived or in review; they are history): "
                + ", ".join(f"{k}: {n}" for k, n in sorted(elsewhere.items()))
                + "."
            )
        if not drafts:
            return

        removing = {a.pk for a in drafts}
        emptied: dict[int, str] = {}
        for activity in drafts:
            node = activity.content_node
            # The lesson an activity lives in (the node itself, or a section's parent).
            holder = node.parent if node.kind == ContentNode.Kind.SUBTOPIC and node.parent else node
            for candidate in {node, holder}:
                if _would_be_empty(candidate, removing):
                    emptied[candidate.pk] = candidate.title

        for activity in drafts:
            self.stdout.write(f"  {activity.get_activity_type_display()}: {activity.title}")

        if dry:
            self.stdout.write(self.style.WARNING("Dry run: nothing was deleted."))
        else:
            with transaction.atomic():
                for activity in drafts:
                    services.delete_activity(activity)
            self.stdout.write(self.style.SUCCESS(f"Deleted {len(drafts)} from the draft."))

        if emptied:
            self.stdout.write(
                "These would hold nothing afterwards, so the draft can't be published until "
                "each gets content or is removed:"
            )
            for title in sorted(emptied.values()):
                self.stdout.write(f"  - {title}")

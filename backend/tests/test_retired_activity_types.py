"""PDF, Workshop and External course were never finished (they could only say "coming later"),
so authors can no longer add them. Rows already in published and archived versions are history:
they must keep working (inert), and the cleanup command must only ever touch a draft."""

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from apps.accounts.models import Role
from apps.catalog import importer, services
from apps.catalog.management.commands.retire_activity_types import RETIRED
from apps.catalog.models import Activity, CourseVersion
from apps.progress import services as progress
from tests.enrollment_helpers import course_with, pin

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(make_role_user):
    """A published v1 whose second lesson also holds a retired Workshop row (as the dev data
    does), a reading and a quiz in the first."""
    w = course_with(make_role_user)
    w["retired"] = Activity.objects.create(
        content_node=w["second"], activity_type="workshop", title="Old workshop", config={}
    )
    w["learner"] = make_role_user(Role.Name.LEARNER, username="lea", email="lea@e.com")
    return w


def run(*args):
    out = io.StringIO()
    call_command("retire_activity_types", *args, stdout=out)
    return out.getvalue()


class TestNobodyCanAddThem:
    @pytest.mark.parametrize("key", sorted(RETIRED))
    def test_the_service_refuses(self, world, key):
        draft = services.get_or_create_draft(world["course"])
        lesson = draft.nodes.get(slug="first")
        with pytest.raises(services.InvalidOperation, match="Unknown activity type"):
            services.add_activity(lesson, activity_type=key, title="x")

    @pytest.mark.parametrize("key", sorted(RETIRED))
    def test_the_studio_picker_does_not_offer_them(self, client, world, key):
        client.force_login(world["author"])
        keys = [t["key"] for t in client.get(reverse("authoring_api:activity-types")).json()]
        assert key not in keys
        assert {"reading", "video", "mcq", "manual_sim", "bot_sim"} <= set(keys)

    @pytest.mark.parametrize("word", ["PDF", "Workshop", "Hands-on", "External course", "course"])
    def test_a_spreadsheet_naming_one_is_told_the_type_is_unknown(self, word):
        text = f"Module,Lesson,Activity,Type\nM,L,A,{word}\n"
        headers, rows = importer.read_table("x.csv", text.encode())
        plan = importer.build_plan(headers, rows, importer.suggest_mapping(headers))
        assert any("Unknown activity type" in m.message for m in plan.errors), word


class TestOldRowsKeepWorking:
    def test_the_learner_still_sees_the_row_as_something_that_cannot_be_completed(
        self, client, world
    ):
        pin(world["learner"], world["version"])
        client.force_login(world["learner"])
        tree = client.get(reverse("catalog_api:course-tree", args=[world["version"].pk])).json()
        lesson = next(
            n for m in tree["root"]["children"] for n in m["children"] if n["title"] == "Second"
        )
        row = next(a for a in lesson["activities"] if a["activity_type"] == "workshop")
        assert row["completable"] is False and row["config"] == {}

    def test_it_cannot_be_completed_and_never_holds_up_the_lesson(self, world):
        pin(world["learner"], world["version"])
        with pytest.raises(progress.NotCompletable):
            progress.complete_activity(world["learner"], world["retired"].pk)
        progress.complete_activity(world["learner"], world["later"].pk)
        progress.complete_lesson(world["learner"], world["second"].pk)  # not blocked by it
        assert progress.course_progress(world["learner"], world["version"].pk)

    def test_the_model_still_lists_them_as_retired_so_old_rows_stay_valid_objects(self, world):
        labels = dict(Activity.ActivityType.choices)
        assert all("retired" in labels[key] for key in RETIRED)

    def test_a_new_draft_copies_it_and_cannot_be_published_until_it_is_removed(self, world):
        draft = services.get_or_create_draft(world["course"])
        assert Activity.objects.filter(
            content_node__course_version=draft, activity_type="workshop"
        ).exists()
        issues = [i.message for i in services.publish_checklist(draft)]
        assert any("Unknown activity type" in m for m in issues)
        run("--course", world["course"].slug)
        assert not [i for i in services.publish_checklist(draft) if "Unknown" in i.message]


class TestTheCleanupCommand:
    def draft(self, world):
        return services.get_or_create_draft(world["course"])

    def test_a_dry_run_says_what_it_would_do_and_changes_nothing(self, world):
        draft = self.draft(world)
        before = Activity.objects.filter(activity_type__in=RETIRED).count()
        out = run("--course", world["course"].slug, "--dry-run")
        assert "1 retired activity in the draft" in out and "Dry run" in out
        assert "Old workshop" in out
        assert Activity.objects.filter(activity_type__in=RETIRED).count() == before
        assert draft.nodes.filter(activities__activity_type="workshop").exists()

    def test_it_deletes_from_the_draft_only_and_leaves_published_history_alone(self, world):
        draft = self.draft(world)
        out = run("--course", world["course"].slug)
        assert "Deleted 1 from the draft" in out
        assert not Activity.objects.filter(
            content_node__course_version=draft, activity_type__in=RETIRED
        ).exists()
        # The published version is history that learners are pinned to: untouched.
        world["version"].refresh_from_db()
        assert world["version"].state == CourseVersion.State.PUBLISHED
        assert Activity.objects.filter(pk=world["retired"].pk).exists()
        assert "published" in out  # it says what it left alone

    def test_it_changes_nothing_when_there_is_no_draft(self, world):
        out = run("--course", world["course"].slug)
        assert "0 retired activities in the draft" in out
        assert Activity.objects.filter(pk=world["retired"].pk).exists()

    def test_it_lists_lessons_that_would_be_left_empty(self, world):
        draft = self.draft(world)
        lesson = draft.nodes.get(slug="second")
        # Make the lesson hold only the retired row.
        Activity.objects.filter(content_node=lesson).exclude(activity_type="workshop").delete()
        out = run("--course", world["course"].slug, "--dry-run")
        assert "hold nothing" in out and "Second" in out

    def test_it_renumbers_what_is_left_in_the_lesson(self, world):
        draft = self.draft(world)
        lesson = draft.nodes.get(slug="second")
        run("--course", world["course"].slug)
        assert [a.order for a in lesson.activities.order_by("order")] == list(
            range(lesson.activities.count())
        )

    def test_an_unknown_course_is_an_error(self):
        with pytest.raises(CommandError, match="No course"):
            run("--course", "nope")

    def test_it_is_safe_to_run_twice(self, world):
        self.draft(world)
        run("--course", world["course"].slug)
        assert "0 retired activities" in run("--course", world["course"].slug)

    def test_the_draft_guard_is_the_services_not_this_command(self, world):
        # delete_activity itself refuses a published version, which is the safety net.
        fresh = Activity.objects.get(pk=world["retired"].pk)  # the version's state as stored
        with pytest.raises(services.NotEditable):
            services.delete_activity(fresh)

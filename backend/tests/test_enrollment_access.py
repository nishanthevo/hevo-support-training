"""The access rule: a learner uses only the course version they are enrolled on.

These run with the real rule (no automatic enrollment on first use), unlike the rest of the
suite, so each learner-facing path is walked here for who is let in and who is not.
"""

import importlib
import json

import pytest
from django.apps import apps as django_apps
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.catalog import services as catalog
from apps.catalog.models import Activity, ContentNode, CourseVersion
from apps.enrollment import access
from apps.enrollment.models import Enrollment
from apps.progress import services as progress
from apps.progress.models import ActivityProgress, LessonCompletion
from tests.enrollment_helpers import course_with, pin, publish_next
from tests.manual_sim_helpers import rated_course
from tests.media_helpers import ready_asset, video_course
from tests.sim_helpers import approved_scenarios, sim_course

pytestmark = [pytest.mark.django_db, pytest.mark.real_enrollment]


@pytest.fixture
def world(make_role_user):
    w = course_with(make_role_user)
    w["learner"] = make_role_user(Role.Name.LEARNER, username="lea", email="lea@e.com")
    w["other"] = make_role_user(Role.Name.LEARNER, username="oth", email="oth@e.com")
    return w


def post(client, name, *args, data=None):
    return client.post(
        reverse(f"progress_api:{name}", args=args),
        json.dumps(data or {}),
        content_type="application/json",
    )


def listed(client, user):
    client.force_login(user)
    return [row["course_id"] for row in client.get(reverse("catalog_api:course-list")).json()]


class TestTheRule:
    def test_an_unenrolled_learner_can_use_nothing(self, world):
        assert not access.can_use(world["learner"], world["version"])
        assert list(access.usable_versions(world["learner"])) == []

    def test_an_active_enrollment_pins_exactly_that_version(self, world):
        pin(world["learner"], world["version"])
        assert access.can_use(world["learner"], world["version"])
        assert [v.pk for v in access.usable_versions(world["learner"])] == [world["version"].pk]
        assert not access.can_use(world["other"], world["version"])

    def test_a_withdrawn_enrollment_is_no_access(self, world):
        pin(world["learner"], world["version"], state=Enrollment.State.WITHDRAWN)
        assert not access.can_use(world["learner"], world["version"])

    def test_an_archived_course_is_closed_to_everyone(self, world):
        pin(world["learner"], world["version"])
        catalog.archive_course(world["course"])
        assert not access.can_use(world["learner"], world["version"])
        assert not access.can_use(world["author"], world["version"])

    def test_a_draft_is_never_usable_even_when_pinned(self, world):
        draft = catalog.get_or_create_draft(world["course"])
        pin(world["learner"], draft)
        assert not access.can_use(world["learner"], draft)

    def test_an_author_may_use_what_is_published_without_enrolling(self, world):
        assert access.can_use(world["author"], world["version"])
        draft = catalog.get_or_create_draft(world["course"])
        assert not access.can_use(world["author"], draft)

    def test_an_author_does_not_get_a_superseded_version_unless_pinned_to_it(self, world):
        newer = publish_next(world)
        world["version"].refresh_from_db()
        assert access.can_use(world["author"], newer)
        assert not access.can_use(world["author"], world["version"])
        pin(world["author"], world["version"])
        assert access.can_use(world["author"], world["version"])

    def test_anonymous_is_refused(self, world):
        from django.contrib.auth.models import AnonymousUser

        assert not access.can_use(AnonymousUser(), world["version"])
        assert not access.can_use(None, world["version"])

    def test_a_learner_stays_on_their_version_when_a_new_one_is_published(self, world):
        pin(world["learner"], world["version"])
        newer = publish_next(world)
        world["version"].refresh_from_db()
        assert world["version"].state == CourseVersion.State.ARCHIVED
        assert access.can_use(world["learner"], world["version"])
        assert not access.can_use(world["learner"], newer)


class TestTheCatalog:
    def test_the_list_is_only_my_courses(self, client, world):
        assert listed(client, world["learner"]) == []
        pin(world["learner"], world["version"])
        assert listed(client, world["learner"]) == [world["course"].pk]
        assert listed(client, world["other"]) == []

    def test_the_tree_of_a_course_you_are_not_on_is_a_404(self, client, world):
        client.force_login(world["learner"])
        url = reverse("catalog_api:course-tree", args=[world["version"].pk])
        assert client.get(url).status_code == 404
        pin(world["learner"], world["version"])
        assert client.get(url).status_code == 200

    def test_an_enrolled_learner_is_shown_their_version_not_the_newest(self, client, world):
        pin(world["learner"], world["version"])
        newer = publish_next(world)
        client.force_login(world["learner"])
        rows = client.get(reverse("catalog_api:course-list")).json()
        assert [r["id"] for r in rows] == [world["version"].pk]
        assert client.get(reverse("catalog_api:course-tree", args=[newer.pk])).status_code == 404
        assert (
            client.get(reverse("catalog_api:course-tree", args=[world["version"].pk])).status_code
            == 200
        )

    def test_an_author_sees_published_courses_and_one_row_per_course(self, client, world):
        assert listed(client, world["author"]) == [world["course"].pk]
        pin(world["author"], world["version"])
        publish_next(world)
        client.force_login(world["author"])
        rows = client.get(reverse("catalog_api:course-list")).json()
        assert [r["id"] for r in rows] == [world["version"].pk]  # their pin wins

    def test_withdrawing_hides_it_again(self, client, world):
        enrollment = pin(world["learner"], world["version"])
        enrollment.state = Enrollment.State.WITHDRAWN
        enrollment.save()
        assert listed(client, world["learner"]) == []


class TestRecordingProgress:
    def test_every_progress_call_refuses_a_course_you_are_not_on(self, client, world):
        client.force_login(world["learner"])
        assert (
            client.get(reverse("progress_api:course", args=[world["version"].pk])).status_code
            == 404
        )
        assert post(client, "activity-start", world["reading"].pk).status_code == 404
        assert post(client, "activity-complete", world["reading"].pk).status_code == 404
        answers = {"answers": [[0]]}
        assert post(client, "activity-attempts", world["quiz"].pk, data=answers).status_code == 404
        assert post(client, "lesson-complete", world["first"].pk).status_code == 404
        assert not ActivityProgress.objects.exists() and not LessonCompletion.objects.exists()

    def test_and_lets_you_in_once_enrolled(self, client, world):
        pin(world["learner"], world["version"])
        client.force_login(world["learner"])
        assert post(client, "activity-complete", world["reading"].pk).status_code == 200
        answers = {"answers": [[0]]}
        assert post(client, "activity-attempts", world["quiz"].pk, data=answers).status_code == 201
        assert post(client, "lesson-complete", world["first"].pk).status_code == 200

    def test_a_superseded_version_keeps_working_for_the_learner_on_it(self, client, world):
        pin(world["learner"], world["version"])
        newer = publish_next(world)
        client.force_login(world["learner"])
        assert post(client, "activity-complete", world["reading"].pk).status_code == 200
        copy = Activity.objects.get(content_node__course_version=newer, title="Read")
        assert post(client, "activity-complete", copy.pk).status_code == 404

    def test_withdrawing_stops_further_recording_but_keeps_what_was_done(self, client, world):
        enrollment = pin(world["learner"], world["version"])
        client.force_login(world["learner"])
        assert post(client, "activity-complete", world["reading"].pk).status_code == 200
        enrollment.state = Enrollment.State.WITHDRAWN
        enrollment.save()
        assert post(client, "activity-complete", world["later"].pk).status_code == 404
        assert ActivityProgress.objects.filter(user=world["learner"]).count() == 1

    def test_the_service_layer_refuses_too(self, world):
        with pytest.raises(progress.NotFound):
            progress.complete_activity(world["learner"], world["reading"].pk)
        with pytest.raises(progress.NotFound):
            progress.course_progress(world["learner"], world["version"].pk)

    def test_another_learners_enrollment_does_not_help(self, client, world):
        pin(world["other"], world["version"])
        client.force_login(world["learner"])
        assert post(client, "activity-complete", world["reading"].pk).status_code == 404


class TestTheOtherLearnerPaths:
    def test_a_rated_simulation(self, client, make_role_user):
        w = rated_course(make_role_user)
        learner = make_role_user(Role.Name.LEARNER, username="lea", email="lea@e.com")
        client.force_login(learner)
        url = reverse("rated_sim_api:activity", args=[w["activity"].pk])
        assert client.get(url).status_code == 404
        assert (
            client.post(reverse("rated_sim_api:open", args=[w["activity"].pk])).status_code == 404
        )
        pin(learner, w["version"])
        assert client.get(url).status_code == 200

    def test_a_practice_ticket(self, client, make_role_user):
        w = sim_course(make_role_user)
        approved_scenarios(3)
        learner = make_role_user(Role.Name.LEARNER, username="lea", email="lea@e.com")
        client.force_login(learner)
        url = reverse("sim_api:start", args=[w["activity"].pk])
        assert client.post(url).status_code == 404
        pin(learner, w["version"])
        assert client.post(url).status_code in (200, 201)

    def test_a_video(self, client, make_role_user, media_dirs):
        uploader = make_role_user(Role.Name.TRAINER, username="up", email="up@e.com")
        asset = ready_asset(uploader, media_dirs)
        _, version, *_ = video_course(make_role_user, asset)
        learner = make_role_user(Role.Name.LEARNER, username="viewer", email="v@e.com")
        client.force_login(learner)
        url = reverse("media_api:video", args=[asset.pk])
        assert client.get(url).status_code in (403, 404)
        pin(learner, version)
        assert client.get(url).status_code == 200

    def test_a_video_stays_playable_on_a_superseded_version(
        self, client, make_role_user, media_dirs
    ):
        uploader = make_role_user(Role.Name.TRAINER, username="up", email="up@e.com")
        asset = ready_asset(uploader, media_dirs)
        course, version, *_ = video_course(make_role_user, asset)
        learner = make_role_user(Role.Name.LEARNER, username="viewer", email="v@e.com")
        pin(learner, version)
        draft = catalog.get_or_create_draft(course)
        draft.publish(make_role_user(Role.Name.ADMIN, username="adm2", email="adm2@e.com"))
        client.force_login(learner)
        assert client.get(reverse("media_api:video", args=[asset.pk])).status_code == 200


class TestLineage:
    def test_a_cloned_version_remembers_what_each_node_and_activity_was_copied_from(self, world):
        newer = publish_next(world)
        for old in ContentNode.objects.filter(course_version=world["version"]):
            copy = ContentNode.objects.get(course_version=newer, slug=old.slug, kind=old.kind)
            assert copy.origin_id == old.pk
        for old in Activity.objects.filter(content_node__course_version=world["version"]):
            copy = Activity.objects.get(content_node__course_version=newer, title=old.title)
            assert copy.origin_id == old.pk

    def test_content_authored_fresh_has_no_origin(self, world):
        draft = catalog.get_or_create_draft(world["course"])
        added = catalog.add_node(draft.nodes.get(slug="module"), title="Brand new")
        assert added.origin_id is None

    def test_the_backfill_matches_versions_by_slug_path_and_leaves_the_unsure_unmatched(
        self, world
    ):
        newer = publish_next(world)
        ContentNode.objects.filter(course_version=newer).update(origin=None)
        Activity.objects.filter(content_node__course_version=newer).update(origin=None)
        catalog_migration = importlib.import_module("apps.catalog.migrations.0004_content_lineage")
        catalog_migration.backfill(django_apps, None)
        copy = ContentNode.objects.get(course_version=newer, slug="first")
        assert copy.origin == world["first"]
        act = Activity.objects.get(content_node__course_version=newer, title="Quiz")
        assert act.origin == world["quiz"]

    def test_content_with_no_matching_path_is_left_unmatched_not_guessed(self, world):
        newer = publish_next(world)
        ContentNode.objects.filter(course_version=newer).update(origin=None)
        ContentNode.objects.filter(course_version=newer, slug="second").update(slug="renamed")
        catalog_migration = importlib.import_module("apps.catalog.migrations.0004_content_lineage")
        catalog_migration.backfill(django_apps, None)
        assert ContentNode.objects.get(course_version=newer, slug="renamed").origin_id is None
        assert ContentNode.objects.get(course_version=newer, slug="first").origin == world["first"]


class TestTheBackfill:
    def run(self):
        module = importlib.import_module("apps.enrollment.migrations.0002_permission_and_backfill")
        module.backfill(django_apps, None)

    def test_everyone_with_progress_is_enrolled_where_their_latest_work_is(self, world):
        # Work on v1, then the course is republished and more work happens on v2.
        progress_row = ActivityProgress.objects
        old = world["version"]
        progress_row.create(
            user=world["learner"], activity=world["reading"], started_at=timezone.now()
        )
        newer = publish_next(world)
        again = Activity.objects.get(content_node__course_version=newer, title="Later")
        progress_row.create(
            user=world["learner"],
            activity=again,
            started_at=timezone.now() + timezone.timedelta(hours=1),
        )
        progress_row.create(
            user=world["other"], activity=world["reading"], started_at=timezone.now()
        )
        self.run()
        mine = Enrollment.objects.get(user=world["learner"], course=world["course"])
        theirs = Enrollment.objects.get(user=world["other"], course=world["course"])
        assert mine.version == newer and theirs.version == old
        assert mine.source == mine.Source.BACKFILL and mine.state == Enrollment.State.ACTIVE

    def test_nothing_is_enrolled_for_someone_with_no_progress_and_it_is_safe_to_rerun(self, world):
        ActivityProgress.objects.create(
            user=world["learner"], activity=world["reading"], started_at=timezone.now()
        )
        self.run()
        self.run()
        assert list(Enrollment.objects.values_list("user__username", flat=True)) == ["lea"]

    def test_a_learner_who_had_course_access_still_sees_the_course_after_the_backfill(
        self, client, world
    ):
        ActivityProgress.objects.create(
            user=world["learner"], activity=world["reading"], started_at=timezone.now()
        )
        self.run()
        assert listed(client, world["learner"]) == [world["course"].pk]


class TestThePermission:
    def test_trainers_and_admins_hold_it_and_an_admin_cannot_lose_it(self, make_role_user):
        from apps.accounts.rbac import ADMIN_LOCKED_ACTIONS, ROLE_PERMISSIONS

        assert "enrollment.manage_enrollments" in ROLE_PERMISSIONS["trainer"]
        assert "enrollment.manage_enrollments" in ROLE_PERMISSIONS["admin"]
        assert "enrollment.manage_enrollments" in ADMIN_LOCKED_ACTIONS
        assert "enrollment.manage_enrollments" not in ROLE_PERMISSIONS["mentor"]
        assert "enrollment.manage_enrollments" not in ROLE_PERMISSIONS["learner"]

    def test_the_migration_grants_it_once_and_additively(self):
        from django.contrib.auth.models import Permission

        from apps.accounts.models import Role as RoleModel

        trainer = RoleModel.objects.create(name="trainer")
        module = importlib.import_module("apps.enrollment.migrations.0002_permission_and_backfill")
        # The migration expects a historical app registry and clears `models_module` when it is
        # done. Run against the real one, put it back, or later permission creation skips the app.
        config = django_apps.get_app_config("enrollment")
        original = config.models_module
        try:
            module.grant(django_apps, None)
        finally:
            config.models_module = original
        perm = Permission.objects.get(
            content_type__app_label="enrollment", codename="manage_enrollments"
        )
        assert trainer.permissions.filter(pk=perm.pk).exists()
        # An admin who later removes it stays removed: running again is the only way back.
        trainer.permissions.remove(perm)
        assert not trainer.permissions.filter(pk=perm.pk).exists()

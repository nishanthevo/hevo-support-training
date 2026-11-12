"""Which modules a learner gets, per enrollment: choosing them, and every place the choice bites.

A module switched off must vanish from the learner's course AND be refused by the server (a
direct URL or id is no way round), and the learning order must never wait on a lesson the
learner can't open. These run with the real enrollment rule, like `test_enrollment_access`.
"""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from apps.accounts.models import Role
from apps.bot_sim.models import SimSession
from apps.catalog import services as catalog
from apps.catalog.models import Activity, ContentNode
from apps.enrollment import access, modules, services
from apps.enrollment.errors import Conflict, EnrollmentError
from apps.enrollment.models import Enrollment, ModuleExclusion
from apps.progress import services as progress
from apps.progress.models import ActivityAttempt, ActivityProgress, LessonCompletion
from tests.enrollment_helpers import QUESTIONS, pin
from tests.media_helpers import ready_asset, video_course
from tests.sim_helpers import approved_scenarios, sim_course

pytestmark = [pytest.mark.django_db, pytest.mark.real_enrollment]


def reading(node, title="Read"):
    return Activity.objects.create(
        content_node=node,
        activity_type="reading",
        title=title,
        config={"source_url": "https://x.io"},
    )


@pytest.fixture
def world(make_role_user):
    """Three modules, each with one lesson holding a reading; the middle one also has a
    section with a reading and a quiz. The course goes in order."""
    author = make_role_user(Role.Name.TRAINER, username="auth", email="auth@e.com")
    admin = make_role_user(Role.Name.ADMIN, username="adm", email="adm@e.com")
    learner = make_role_user(Role.Name.LEARNER, username="lea", email="lea@e.com")
    other = make_role_user(Role.Name.LEARNER, username="oth", email="oth@e.com")
    course = catalog.create_course(title="Course", user=author)
    version = catalog.open_version(course)
    mods, lessons, reads = [], [], []
    for name in ("A", "B", "C"):
        mod = catalog.add_node(version.root_node, title=f"Module {name}")
        lesson = catalog.add_node(mod, title=f"Lesson {name}")
        reads.append(reading(lesson, f"Read {name}"))
        mods.append(mod)
        lessons.append(lesson)
    section = catalog.add_node(lessons[1], title="Section B")
    section_read = reading(section, "Section read")
    quiz = Activity.objects.create(
        content_node=section,
        activity_type="mcq",
        title="Quiz B",
        config={"questions": QUESTIONS, "pass_threshold": 1},
        max_attempts=3,
    )
    catalog.set_learning_order(version, "sequential")
    version.publish(admin)
    return {
        "course": course,
        "version": version,
        "mods": mods,
        "lessons": lessons,
        "reads": reads,
        "section_read": section_read,
        "quiz": quiz,
        "author": author,
        "admin": admin,
        "learner": learner,
        "other": other,
    }


def enrolled(world, user=None, *, off=()):
    """`user` enrolled on the world's version with the given modules switched off."""
    row = pin(user or world["learner"], world["version"])
    for node in off:
        ModuleExclusion.objects.create(enrollment=row, node=node)
    return row


def post(client, name, *args, data=None):
    return client.post(
        reverse(f"progress_api:{name}", args=args),
        json.dumps(data or {}),
        content_type="application/json",
    )


def titles(tree):
    return [m["title"] for m in tree["root"]["children"]]


# ---------------------------------------------------------------------- choosing


class TestChoosingAtEnrollment:
    def test_by_default_every_module_is_on(self, world):
        row, outcome = services.enroll(world["learner"], world["course"])
        assert outcome == services.ENROLLED
        assert not ModuleExclusion.objects.filter(enrollment=row).exists()

    def test_the_ticked_modules_are_on_and_the_rest_are_off(self, world):
        a, b, c = world["mods"]
        row, _ = services.enroll(world["learner"], world["course"], modules=[a.pk, c.pk])
        assert modules.excluded_of(row) == {b.pk}

    def test_ticking_everything_is_the_same_as_the_default(self, world):
        row, _ = services.enroll(
            world["learner"], world["course"], modules=[m.pk for m in world["mods"]]
        )
        assert modules.excluded_of(row) == set()

    @pytest.mark.parametrize("bad", ["empty", "a lesson", "another course's module"])
    def test_a_bad_choice_is_refused_and_nobody_is_enrolled(self, world, make_role_user, bad):
        other_course = catalog.create_course(title="Other", user=world["author"])
        other_version = catalog.open_version(other_course)
        foreign = catalog.add_node(other_version.root_node, title="Foreign")
        choice = {
            "empty": [],
            "a lesson": [world["lessons"][0].pk],
            "another course's module": [foreign.pk],
        }[bad]
        with pytest.raises(EnrollmentError):
            services.enroll(world["learner"], world["course"], modules=choice)
        assert not Enrollment.objects.filter(user=world["learner"]).exists()

    def test_enrolling_someone_already_on_it_does_not_overwrite_their_choice(self, world):
        a, b, c = world["mods"]
        row, _ = services.enroll(world["learner"], world["course"], modules=[a.pk])
        _, outcome = services.enroll(world["learner"], world["course"], modules=[b.pk, c.pk])
        assert outcome == services.ALREADY
        assert modules.excluded_of(row) == {b.pk, c.pk}

    def test_re_enrolling_after_withdrawal_starts_from_what_is_asked_now(self, world):
        a, b, c = world["mods"]
        row, _ = services.enroll(world["learner"], world["course"], modules=[a.pk])
        services.withdraw(row, "left")
        row, outcome = services.enroll(world["learner"], world["course"])
        assert outcome == services.REACTIVATED
        assert modules.excluded_of(row) == set()  # not the stale selection
        services.withdraw(row, "left again")
        row, _ = services.enroll(world["learner"], world["course"], modules=[c.pk])
        assert modules.excluded_of(row) == {a.pk, b.pk}


class TestChangingTheirModules:
    def test_set_modules_makes_the_selection_exactly_what_is_ticked(self, world):
        a, b, c = world["mods"]
        row = enrolled(world, off=[b])
        change = modules.set_modules(row, [b.pk, c.pk])
        assert modules.excluded_of(row) == {a.pk}
        assert change.before_off == [b.pk] and change.after_off == [a.pk]

    def test_at_least_one_module_must_stay_on(self, world):
        row = enrolled(world)
        with pytest.raises(EnrollmentError, match="at least one module"):
            modules.set_modules(row, [])

    def test_a_withdrawn_learner_cannot_be_changed(self, world):
        row = enrolled(world)
        services.withdraw(row, "left")
        with pytest.raises(Conflict):
            modules.set_modules(row, [world["mods"][0].pk])

    def test_switching_a_module_back_on_restores_what_they_did_in_it(self, client, world):
        a, b, c = world["mods"]
        row = enrolled(world)
        LessonCompletion.objects.create(
            user=world["learner"],
            node=world["lessons"][1],
            completed_at=world["version"].published_at,
        )
        modules.set_modules(row, [a.pk, c.pk])
        client.force_login(world["learner"])
        data = client.get(reverse("progress_api:course", args=[world["version"].pk])).json()
        assert world["lessons"][1].pk not in data["completed_lessons"]
        modules.set_modules(row, [a.pk, b.pk, c.pk])
        data = client.get(reverse("progress_api:course", args=[world["version"].pk])).json()
        assert world["lessons"][1].pk in data["completed_lessons"]

    def test_it_is_refused_while_a_practice_ticket_is_open_in_that_module(
        self, client, make_role_user
    ):
        w = sim_course(make_role_user)
        approved_scenarios(3)
        version = w["version"]
        module = w["activity"].content_node.parent
        spare = ContentNode.objects.create(
            course_version=version,
            parent=version.root_node,
            kind=ContentNode.Kind.SEGMENT,
            title="Spare",
            slug="spare",
            order=9,
        )
        learner = make_role_user(Role.Name.LEARNER, username="lea", email="lea@e.com")
        row = pin(learner, version)
        client.force_login(learner)
        assert client.post(reverse("sim_api:start", args=[w["activity"].pk])).status_code in (
            200,
            201,
        )
        with pytest.raises(Conflict, match="practice ticket"):
            modules.set_modules(row, [spare.pk])
        # Switching off a module with no live ticket in it is fine.
        SimSession.objects.update(state=SimSession.State.ABANDONED)
        modules.set_modules(row, [spare.pk])
        assert modules.excluded_of(row) == {module.pk}


# --------------------------------------------------------------- what they see


class TestWhatTheLearnerSees:
    def test_the_tree_leaves_out_a_switched_off_module_and_everything_in_it(self, client, world):
        enrolled(world, off=[world["mods"][1]])
        client.force_login(world["learner"])
        tree = client.get(reverse("catalog_api:course-tree", args=[world["version"].pk])).json()
        assert titles(tree) == ["Module A", "Module C"]
        text = json.dumps(tree)
        assert "Lesson B" not in text and "Quiz B" not in text and "Section B" not in text

    def test_another_learner_and_an_author_still_see_everything(self, client, world):
        enrolled(world, off=[world["mods"][1]])
        enrolled(world, world["other"])
        url = reverse("catalog_api:course-tree", args=[world["version"].pk])
        for who in (world["other"], world["author"]):
            client.force_login(who)
            assert titles(client.get(url).json()) == ["Module A", "Module B", "Module C"]

    def test_a_switched_off_module_is_not_in_their_progress_either(self, client, world):
        a, b, c = world["mods"]
        enrolled(world, off=[b])
        now = world["version"].published_at
        # Work the learner did in B before it was switched off, plus one thing in A.
        for act in (world["reads"][0], world["section_read"]):
            ActivityProgress.objects.create(user=world["learner"], activity=act, started_at=now)
        ActivityAttempt.objects.create(
            user=world["learner"],
            activity=world["quiz"],
            number=1,
            score=1.0,
            passed=True,
            submitted_at=now,
        )
        LessonCompletion.objects.create(
            user=world["learner"], node=world["lessons"][1], completed_at=now
        )
        client.force_login(world["learner"])
        data = client.get(reverse("progress_api:course", args=[world["version"].pk])).json()
        assert data["completed_lessons"] == []
        assert str(world["reads"][0].pk) in data["activities"]
        for gone in (world["section_read"].pk, world["quiz"].pk):
            assert str(gone) not in data["activities"] and str(gone) not in data["attempts"]

    def test_with_nothing_switched_off_the_hiding_costs_no_extra_queries(
        self, world, django_assert_num_queries
    ):
        lessons, activities = progress._hidden_in(world["version"], frozenset())
        assert (lessons, activities) == (set(), set())
        with django_assert_num_queries(0):
            progress._hidden_in(world["version"], frozenset())


class TestTheServerRefusesToo:
    """Hiding the module in the sidebar is not enough: a direct id or URL is refused."""

    def test_every_recording_call_is_a_404_for_a_switched_off_module(self, client, world):
        enrolled(world, off=[world["mods"][1]])
        client.force_login(world["learner"])
        lesson, read, section_read, quiz = (
            world["lessons"][1],
            world["reads"][1],
            world["section_read"],
            world["quiz"],
        )
        assert post(client, "activity-start", read.pk).status_code == 404
        assert post(client, "activity-complete", read.pk).status_code == 404
        # An activity on a *section* of the lesson (two levels under the module).
        assert post(client, "activity-start", section_read.pk).status_code == 404
        assert post(client, "activity-complete", section_read.pk).status_code == 404
        assert (
            post(client, "activity-attempts", quiz.pk, data={"answers": [[0]]}).status_code == 404
        )
        assert post(client, "lesson-complete", lesson.pk).status_code == 404
        assert not LessonCompletion.objects.exists()
        assert not Activity.objects.filter(progress__user=world["learner"]).exists()

    def test_the_service_layer_refuses_too(self, world):
        enrolled(world, off=[world["mods"][1]])
        with pytest.raises(progress.NotFound):
            progress.complete_activity(world["learner"], world["reads"][1].pk)
        with pytest.raises(progress.NotFound):
            progress.complete_lesson(world["learner"], world["lessons"][1].pk)
        with pytest.raises(progress.NotFound):
            progress.uncomplete_lesson(world["learner"], world["lessons"][1].pk)
        with pytest.raises(progress.NotFound):
            progress.submit_attempt(world["learner"], world["quiz"].pk, [[0]])

    def test_the_modules_that_are_on_still_work(self, client, world):
        enrolled(world, off=[world["mods"][1]])
        client.force_login(world["learner"])
        assert post(client, "activity-complete", world["reads"][0].pk).status_code == 200
        assert post(client, "lesson-complete", world["lessons"][0].pk).status_code == 200

    def test_someone_with_nothing_switched_off_is_unaffected(self, client, world):
        enrolled(world)
        client.force_login(world["learner"])
        assert post(client, "activity-complete", world["reads"][1].pk).status_code in (200, 409)

    def test_a_different_learners_switch_off_does_not_apply_to_me(self, client, world):
        enrolled(world, off=[world["mods"][0]])
        enrolled(world, world["other"])
        client.force_login(world["other"])
        assert post(client, "activity-complete", world["reads"][0].pk).status_code == 200

    def test_a_withdrawn_enrollments_selection_is_irrelevant_they_have_no_access_at_all(
        self, world
    ):
        row = enrolled(world, off=[world["mods"][1]])
        services.withdraw(row, "left")
        assert access.disabled_module_ids(world["learner"], world["version"]) == frozenset()
        assert not access.can_use(world["learner"], world["version"])


class TestTheOtherLearnerPaths:
    def test_a_rated_simulation_in_a_switched_off_module(self, client, make_role_user):
        from tests.manual_sim_helpers import rated_course

        w = rated_course(make_role_user)
        learner = make_role_user(Role.Name.LEARNER, username="lea", email="lea@e.com")
        row = pin(learner, w["version"])
        client.force_login(learner)
        url = reverse("rated_sim_api:activity", args=[w["activity"].pk])
        assert client.get(url).status_code == 200
        ModuleExclusion.objects.create(enrollment=row, node=w["activity"].content_node.parent)
        assert client.get(url).status_code == 404
        assert (
            client.post(reverse("rated_sim_api:open", args=[w["activity"].pk])).status_code == 404
        )

    def test_a_practice_ticket_in_a_switched_off_module(self, client, make_role_user):
        w = sim_course(make_role_user)
        approved_scenarios(3)
        learner = make_role_user(Role.Name.LEARNER, username="lea", email="lea@e.com")
        row = pin(learner, w["version"])
        ModuleExclusion.objects.create(enrollment=row, node=w["activity"].content_node.parent)
        client.force_login(learner)
        assert client.post(reverse("sim_api:start", args=[w["activity"].pk])).status_code == 404
        assert not SimSession.objects.exists()

    def test_a_video_in_a_switched_off_module_does_not_stream(
        self, client, make_role_user, media_dirs
    ):
        uploader = make_role_user(Role.Name.TRAINER, username="up", email="up@e.com")
        asset = ready_asset(uploader, media_dirs)
        _, version, *_ = video_course(make_role_user, asset)
        learner = make_role_user(Role.Name.LEARNER, username="viewer", email="v@e.com")
        row = pin(learner, version)
        client.force_login(learner)
        url = reverse("media_api:video", args=[asset.pk])
        assert client.get(url).status_code == 200
        use = Activity.objects.get(activity_type="video", config__media_asset=asset.pk)
        ModuleExclusion.objects.create(enrollment=row, node=use.content_node.parent)
        assert client.get(url).status_code in (403, 404)


# --------------------------------------------------------------------- sequencing


class TestTheLearningOrder:
    def test_a_switched_off_module_never_holds_up_the_ones_after_it(self, client, world):
        a, b, c = world["mods"]
        enrolled(world, off=[b])
        client.force_login(world["learner"])
        url = reverse("progress_api:course", args=[world["version"].pk])
        data = client.get(url).json()
        # Lesson C waits on A only; B isn't in their course.
        assert data["locked"][str(world["lessons"][2].pk)]["blocked_by"] == world["lessons"][0].pk
        assert str(world["lessons"][1].pk) not in data["locked"]
        assert data["next_lesson_id"] == world["lessons"][0].pk

        assert post(client, "activity-complete", world["reads"][0].pk).status_code == 200
        assert post(client, "lesson-complete", world["lessons"][0].pk).status_code == 200
        data = client.get(url).json()
        assert data["locked"] == {}
        assert data["next_lesson_id"] == world["lessons"][2].pk
        # ...and C can be done straight away.
        assert post(client, "activity-complete", world["reads"][2].pk).status_code == 200

    def test_with_the_module_on_the_same_lesson_is_locked_behind_it(self, client, world):
        enrolled(world)
        client.force_login(world["learner"])
        assert post(client, "activity-complete", world["reads"][0].pk).status_code == 200
        assert post(client, "lesson-complete", world["lessons"][0].pk).status_code == 200
        data = client.get(reverse("progress_api:course", args=[world["version"].pk])).json()
        assert data["locked"][str(world["lessons"][2].pk)]["blocked_by"] == world["lessons"][1].pk
        assert post(client, "activity-complete", world["reads"][2].pk).status_code == 409


# ------------------------------------------------------------------------ reporting


class TestStaffReporting:
    def done(self, world, *indexes):
        for i in indexes:
            LessonCompletion.objects.create(
                user=world["learner"],
                node=world["lessons"][i],
                completed_at=world["version"].published_at,
            )

    def row(self, world):
        from apps.progress import reporting

        return reporting.learners_queryset().get(pk=world["learner"].pk)

    def test_the_learner_list_does_not_count_lessons_in_a_switched_off_module(self, world):
        enrolled(world, off=[world["mods"][1]])
        self.done(world, 0, 1)  # A, and B (done before B was switched off)
        assert self.row(world).lessons_completed == 1

    def test_with_everything_on_every_completed_lesson_counts(self, world):
        enrolled(world)
        self.done(world, 0, 1)
        assert self.row(world).lessons_completed == 2

    def test_someone_elses_exclusion_does_not_hide_my_lessons(self, world):
        enrolled(world, off=[world["mods"][1]])
        enrolled(world, world["other"])
        LessonCompletion.objects.create(
            user=world["other"],
            node=world["lessons"][1],
            completed_at=world["version"].published_at,
        )
        from apps.progress import reporting

        mine = reporting.learners_queryset().get(pk=world["other"].pk)
        assert mine.lessons_completed == 1

    def test_the_detail_shows_totals_and_activities_for_the_modules_they_have(self, world):
        from apps.progress import reporting

        enrolled(world, off=[world["mods"][1]])
        self.done(world, 0, 1)
        ActivityProgress.objects.create(
            user=world["learner"],
            activity=world["reads"][0],
            started_at=world["version"].published_at,
        )
        (course,) = reporting.learner_detail(world["learner"])["courses"]
        assert (course["lessons_total"], course["lessons_completed"]) == (2, 1)
        listed = {a["title"] for a in course["activities"]}
        assert listed == {"Read A", "Read C"}  # nothing from B, not even its section's

    def test_the_detail_for_someone_with_everything_on_lists_it_all(self, world):
        from apps.progress import reporting

        enrolled(world)
        self.done(world, 0)
        (course,) = reporting.learner_detail(world["learner"])["courses"]
        assert (course["lessons_total"], course["lessons_completed"]) == (3, 1)
        assert {a["title"] for a in course["activities"]} >= {"Read B", "Section read", "Quiz B"}

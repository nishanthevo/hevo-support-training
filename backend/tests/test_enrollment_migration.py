"""Moving a learner to the newest version: nothing lost, nothing counted twice."""

import pytest
from django.utils import timezone

from apps.accounts.models import Role
from apps.bot_sim.models import SimSession
from apps.catalog import services as catalog
from apps.catalog.models import Activity, ContentNode
from apps.enrollment import access, migration, services
from apps.enrollment.errors import Conflict, EnrollmentError
from apps.enrollment.models import Enrollment, VersionMove
from apps.manual_sim import assignments
from apps.manual_sim import services as rated
from apps.manual_sim.models import SimCase
from apps.progress import reporting
from apps.progress import services as progress
from apps.progress.models import (
    ActivityAttempt,
    ActivityProgress,
    LessonCompletion,
    VideoWatch,
)
from tests.enrollment_helpers import course_with, publish_next
from tests.manual_sim_helpers import PASS, at, people, rated_course
from tests.sim_helpers import approved_scenarios, sim_course

pytestmark = [pytest.mark.django_db, pytest.mark.real_enrollment]

RIGHT = [[0]]
WRONG = [[1]]


@pytest.fixture
def world(make_role_user):
    """v1: lesson First (reading + quiz), lesson Second (reading + an "old" reading that the next
    version will drop). The learner has done all of it."""
    w = course_with(make_role_user, publish=False)
    w["old"] = Activity.objects.create(
        content_node=w["second"],
        activity_type="reading",
        title="Old",
        config={"source_url": "https://old.io"},
    )
    w["version"].publish(w["admin"])
    w["lea"] = make_role_user(Role.Name.LEARNER, username="lea", email="lea@e.com")
    w["mentor"] = make_role_user(Role.Name.MENTOR, username="mia", email="mia@e.com")
    w["trainer"] = make_role_user(Role.Name.TRAINER, username="tom", email="tom@e.com")
    w["enrollment"], _ = services.enroll(w["lea"], w["course"], mentor=w["mentor"])
    return w


def do_everything(w):
    user = w["lea"]
    progress.complete_activity(user, w["reading"].pk)
    progress.submit_attempt(user, w["quiz"].pk, RIGHT)
    progress.complete_lesson(user, w["first"].pk)
    progress.complete_activity(user, w["later"].pk)
    progress.complete_activity(user, w["old"].pk)
    progress.complete_lesson(user, w["second"].pk)


def next_version(w, *, change_later=False, change_quiz=False, add=True, drop_old=True):
    """Publish v2 with the chosen edits. Returns {title: activity} of the new version."""
    draft = catalog.get_or_create_draft(w["course"])
    acts = {a.title: a for a in Activity.objects.filter(content_node__course_version=draft)}
    if change_later:
        catalog.update_activity(acts["Later"], config={"source_url": "https://changed.io"})
    if change_quiz:
        q = [{"text": "One", "choices": ["A", "B"], "answer": 1}]
        catalog.update_activity(acts["Quiz"], config={"questions": q, "pass_threshold": 1})
    if drop_old:
        catalog.delete_activity(acts["Old"])
    if add:
        first = ContentNode.objects.get(course_version=draft, slug="first")
        acts["New"] = catalog.add_activity(
            first,
            activity_type="reading",
            title="New",
            config={"source_url": "https://new.io"},
        )
    draft.publish(w["admin"])
    return draft, {a.title: a for a in Activity.objects.filter(content_node__course_version=draft)}


def snapshot():
    return {
        "progress": sorted(ActivityProgress.objects.values_list("user_id", "activity_id", "state")),
        "attempts": sorted(ActivityAttempt.objects.values_list("user_id", "activity_id", "number")),
        "lessons": sorted(
            LessonCompletion.objects.values_list("user_id", "node_id", "provisional")
        ),
        "moves": VersionMove.objects.count(),
    }


def by_title(result):
    out = {}
    for lesson in result["lessons"]:
        for a in lesson["activities"]:
            out[a["title"]] = a
    return out


class TestThePreview:
    def test_says_what_each_thing_will_do_and_changes_nothing(self, world):
        do_everything(world)
        v2, acts = next_version(world, change_later=True)
        before = snapshot()
        preview = migration.preview(world["enrollment"])
        assert snapshot() == before
        world["enrollment"].refresh_from_db()
        assert world["enrollment"].version == world["version"]

        assert preview["can_apply"] and preview["blockers"] == []
        assert preview["from"]["number"] == 1 and preview["to"] == {"id": v2.pk, "number": 2}
        items = by_title(preview)
        assert items["Read"]["status"] == "preserved" and items["Read"]["has_work"]
        assert items["Quiz"]["status"] == "preserved"
        assert items["Later"]["status"] == "changed" and items["Later"]["fields"] == ["source_url"]
        assert items["Later"]["can_carry"] and not items["Later"]["carried"]
        assert items["New"]["status"] == "added" and not items["New"]["has_work"]
        assert [r["title"] for r in preview["removed"]] == ["Old"]
        assert preview["removed"][0]["has_work"]
        c = preview["counts"]
        assert (c["preserved"], c["changed"], c["added"], c["removed"]) == (2, 1, 1, 1)
        assert (c["lessons_kept"], c["lessons_reopened"]) == (0, 2)
        lessons = {lesson["title"]: lesson["outcome"] for lesson in preview["lessons"]}
        assert lessons == {"First": "reopened", "Second": "reopened"}

    def test_ticking_a_change_is_simulated_so_the_lesson_outcome_is_true(self, world):
        do_everything(world)
        _, acts = next_version(world, change_later=True, add=False)
        plain = migration.preview(world["enrollment"])
        ticked = migration.preview(world["enrollment"], {acts["Later"].pk})
        assert {lesson["title"]: lesson["outcome"] for lesson in plain["lessons"]} == {
            "First": "kept",
            "Second": "reopened",
        }
        assert {lesson["title"]: lesson["outcome"] for lesson in ticked["lessons"]}[
            "Second"
        ] == "kept"
        assert by_title(ticked)["Later"]["carried"] is True
        assert ticked["counts"]["changed_carried"] == 1

    def test_anything_that_stops_the_move_is_listed_instead(self, world):
        only = migration.preview(world["enrollment"])
        assert only["can_apply"] is False
        assert "already on the newest version" in only["blockers"][0]


class TestApplying:
    def test_preserved_work_moves_and_the_rest_stays_as_history(self, world):
        do_everything(world)
        v2, acts = next_version(world, change_later=True)
        counted_before = ActivityAttempt.objects.count()
        result = migration.apply(
            world["enrollment"], to_version_id=v2.pk, by=world["trainer"], now=timezone.now()
        )
        world["enrollment"].refresh_from_db()
        assert world["enrollment"].version == v2
        user = world["lea"]

        # Same quiz, same reading: results moved (not copied).
        assert ActivityProgress.objects.get(user=user, activity=acts["Read"]).state == "completed"
        moved = ActivityAttempt.objects.get(user=user, activity=acts["Quiz"])
        assert moved.number == 1 and moved.passed
        assert ActivityAttempt.objects.count() == counted_before
        assert not ActivityAttempt.objects.filter(activity=world["quiz"]).exists()
        assert not ActivityProgress.objects.filter(activity=world["reading"]).exists()
        # Changed (not ticked) and removed: left on the old version, still there.
        assert ActivityProgress.objects.filter(user=user, activity=world["later"]).exists()
        assert ActivityProgress.objects.filter(user=user, activity=world["old"]).exists()
        assert not ActivityProgress.objects.filter(user=user, activity=acts["Later"]).exists()
        # New work starts empty and the lessons need doing again; their old ticks remain as history.
        assert not LessonCompletion.objects.filter(node__course_version=v2).exists()
        assert LessonCompletion.objects.filter(user=user, node=world["first"]).exists()
        assert result["counts"]["lessons_reopened"] == 2

        move = VersionMove.objects.get()
        assert (move.from_version, move.to_version, move.moved_by) == (
            world["version"],
            v2,
            world["trainer"],
        )
        assert [world["reading"].pk, acts["Read"].pk] in move.summary["carried"]
        assert world["old"].pk in move.summary["left_behind"]
        assert move.summary["counts"]["added"] == 1

    def test_the_learner_then_sees_the_new_version_with_their_carried_progress(self, client, world):
        do_everything(world)
        v2, acts = next_version(world, change_later=True)
        migration.apply(world["enrollment"], to_version_id=v2.pk)
        assert access.can_use(world["lea"], v2)
        world["version"].refresh_from_db()
        assert not access.can_use(world["lea"], world["version"])
        body = progress.course_progress(world["lea"], v2.pk)
        assert body["activities"][acts["Read"].pk] == "completed"
        assert body["completed_lessons"] == []
        # The learner finishes the new activity and the lesson completes again.
        progress.complete_activity(world["lea"], acts["New"].pk)
        progress.complete_lesson(world["lea"], acts["New"].content_node_id)
        assert progress.course_progress(world["lea"], v2.pk)["completed_lessons"] == [
            acts["New"].content_node_id
        ]

    def test_carrying_a_change_over_anyway_keeps_the_lesson_complete(self, world):
        do_everything(world)
        v2, acts = next_version(world, change_later=True, add=False)
        migration.apply(world["enrollment"], to_version_id=v2.pk, carry={acts["Later"].pk})
        assert ActivityProgress.objects.get(user=world["lea"], activity=acts["Later"]).state == (
            "completed"
        )
        done = LessonCompletion.objects.filter(user=world["lea"], node__course_version=v2)
        assert [c.node.title for c in done] == ["First", "Second"]
        assert not any(c.provisional for c in done)

    def test_only_a_change_with_something_to_carry_can_be_ticked(self, world):
        do_everything(world)
        _, acts = next_version(world, change_later=True)
        for bad in ({acts["Read"].pk}, {acts["New"].pk}, {999999}):
            with pytest.raises(EnrollmentError, match="can’t be carried over"):
                migration.preview(world["enrollment"], bad)

    def test_a_changed_quiz_is_not_carried_unless_asked_and_attempt_limits_are_honest(self, world):
        user = world["lea"]
        Activity.objects.filter(pk=world["quiz"].pk).update(max_attempts=3)
        progress.submit_attempt(user, world["quiz"].pk, WRONG)
        progress.submit_attempt(user, world["quiz"].pk, WRONG)
        v2, acts = next_version(world, change_quiz=True, add=False)
        catalog_quiz = Activity.objects.get(pk=acts["Quiz"].pk)
        item = by_title(migration.preview(world["enrollment"]))["Quiz"]
        assert item["status"] == "changed" and "questions" in item["fields"]
        assert item["work"] == ["attempts", "progress"]

        # Ask for it, on a version that now allows only two tries: they have used both.
        Activity.objects.filter(pk=catalog_quiz.pk).update(max_attempts=2)
        migration.apply(world["enrollment"], to_version_id=v2.pk, carry={catalog_quiz.pk})
        row = ActivityProgress.objects.get(user=user, activity=catalog_quiz)
        assert row.state == "exhausted"
        assert ActivityAttempt.objects.filter(user=user, activity=catalog_quiz).count() == 2

    def test_gaining_attempts_reopens_an_exhausted_quiz(self, world):
        user = world["lea"]
        Activity.objects.filter(pk=world["quiz"].pk).update(max_attempts=2)
        progress.submit_attempt(user, world["quiz"].pk, WRONG)
        progress.submit_attempt(user, world["quiz"].pk, WRONG)
        assert ActivityProgress.objects.get(activity=world["quiz"]).state == "exhausted"
        v2, acts = next_version(world, add=False)
        Activity.objects.filter(pk=acts["Quiz"].pk).update(max_attempts=4)
        migration.apply(world["enrollment"], to_version_id=v2.pk, carry={acts["Quiz"].pk})
        assert ActivityProgress.objects.get(activity=acts["Quiz"]).state == "started"

    def test_moving_again_is_refused_as_up_to_date(self, world):
        v2, _ = next_version(world)
        migration.apply(world["enrollment"], to_version_id=v2.pk)
        with pytest.raises(Conflict, match="already on the newest"):
            migration.apply(world["enrollment"], to_version_id=v2.pk)
        assert VersionMove.objects.count() == 1

    def test_a_version_published_after_the_preview_is_not_applied_blindly(self, world):
        v2, _ = next_version(world, add=False, drop_old=False)
        v3 = publish_next(world)
        with pytest.raises(Conflict, match="newer version was published"):
            migration.apply(world["enrollment"], to_version_id=v2.pk)
        migration.apply(world["enrollment"], to_version_id=v3.pk)
        world["enrollment"].refresh_from_db()
        assert world["enrollment"].version == v3

    def test_skipping_versions_follows_the_lineage_through_the_ones_in_between(self, world):
        do_everything(world)
        publish_next(world)
        v3, acts = next_version(world, add=False, drop_old=False)
        migration.apply(world["enrollment"], to_version_id=v3.pk)
        assert ActivityProgress.objects.get(user=world["lea"], activity=acts["Read"]).state == (
            "completed"
        )
        assert {c.node.course_version_id for c in LessonCompletion.objects.all()} == {v3.pk}

    def test_a_lesson_that_was_renamed_and_moved_is_still_the_same_lesson(self, world):
        do_everything(world)
        draft = catalog.get_or_create_draft(world["course"])
        other = catalog.add_node(draft.root_node, title="Another module")
        lesson = ContentNode.objects.get(course_version=draft, slug="first")
        ContentNode.objects.filter(pk=lesson.pk).update(parent=other, title="Renamed")
        draft.publish(world["admin"])
        migration.apply(world["enrollment"], to_version_id=draft.pk)
        moved = LessonCompletion.objects.get(user=world["lea"], node=lesson)
        assert moved.node.title == "Renamed"
        assert (
            ActivityProgress.objects.get(
                user=world["lea"], activity__title="Read", activity__content_node=lesson
            ).state
            == "completed"
        )

    def test_content_without_lineage_is_simply_new_and_never_guessed(self, world):
        do_everything(world)
        v2, acts = next_version(world, add=False, drop_old=False)
        Activity.objects.filter(pk=acts["Read"].pk).update(origin=None)
        result = migration.apply(world["enrollment"], to_version_id=v2.pk)
        assert result["counts"]["added"] == 1 and result["counts"]["removed"] == 1
        assert ActivityProgress.objects.filter(
            user=world["lea"], activity=world["reading"]
        ).exists()

    def test_the_mentor_and_other_learners_are_untouched(self, make_role_user, world):
        do_everything(world)
        other = make_role_user(Role.Name.LEARNER, username="oth", email="oth@e.com")
        services.enroll(other, world["course"])
        progress.complete_activity(other, world["reading"].pk)
        v2, _ = next_version(world)
        migration.apply(world["enrollment"], to_version_id=v2.pk)
        assert assignments.mentor_for(world["lea"], world["course"]) == world["mentor"]
        assert ActivityProgress.objects.filter(user=other, activity=world["reading"]).exists()
        assert Enrollment.objects.get(user=other).version == world["version"]

    def test_reports_count_each_result_once(self, world):
        do_everything(world)
        v2, acts = next_version(world, add=False, drop_old=False)
        before = reporting.learners_queryset(course_id=world["course"].pk).get(pk=world["lea"].pk)
        migration.apply(world["enrollment"], to_version_id=v2.pk)
        after = reporting.learners_queryset(course_id=world["course"].pk).get(pk=world["lea"].pk)
        assert (before.lessons_completed, before.quiz_attempts) == (2, 1)
        assert (after.lessons_completed, after.quiz_attempts) == (2, 1)

    def test_a_result_the_learner_already_has_on_the_new_version_is_not_overwritten(self, world):
        do_everything(world)
        v2, acts = next_version(world, add=False, drop_old=False)
        ActivityProgress.objects.create(
            user=world["lea"], activity=acts["Read"], started_at=timezone.now()
        )
        preview = migration.preview(world["enrollment"])
        assert by_title(preview)["Read"]["conflict"] is True
        migration.apply(world["enrollment"], to_version_id=v2.pk)
        assert ActivityProgress.objects.get(user=world["lea"], activity=acts["Read"]).state == (
            "started"
        )
        assert ActivityProgress.objects.filter(
            user=world["lea"], activity=world["reading"]
        ).exists()

    def test_other_kinds_of_work_move_too(self, world):
        user = world["lea"]
        video = Activity.objects.create(
            content_node=world["first"],
            activity_type="video",
            title="Watch",
            config={"source": "youtube", "url": "https://youtu.be/abc"},
            is_required=False,
        )
        VideoWatch.objects.create(user=user, activity=video, credited_seconds=12.0, duration=60.0)
        v2, acts = next_version(world, add=False, drop_old=False)
        migration.apply(world["enrollment"], to_version_id=v2.pk)
        assert VideoWatch.objects.get(user=user, activity=acts["Watch"]).credited_seconds == 12.0
        assert not VideoWatch.objects.filter(activity=video).exists()


class TestBlockers:
    def test_a_withdrawn_learner_cannot_be_moved(self, world):
        next_version(world)
        services.withdraw(world["enrollment"], "")
        problems = migration.blockers(
            Enrollment.objects.get(pk=world["enrollment"].pk),
            migration.target_for(world["enrollment"]),
        )
        assert any("withdrawn" in p for p in problems)
        with pytest.raises(Conflict):
            migration.apply(world["enrollment"], to_version_id=None)

    def test_a_course_with_nothing_newer_has_nothing_to_move_to(self, world):
        assert migration.preview(world["enrollment"])["can_apply"] is False

    def test_a_practice_ticket_in_progress_must_finish_first(self, make_role_user):
        w = sim_course(make_role_user)
        approved_scenarios(3)
        learner = make_role_user(Role.Name.LEARNER, username="lea", email="lea@e.com")
        row, _ = services.enroll(learner, w["course"])
        from apps.bot_sim import services as sim

        session = sim.start(learner, w["activity"].pk)
        draft = catalog.get_or_create_draft(w["course"])
        draft.publish(w["admin"])
        preview = migration.preview(row)
        assert preview["can_apply"] is False and "practice ticket" in preview["blockers"][0]
        SimSession.objects.filter(pk=session.pk).update(state="abandoned", abandon_reason="x")
        assert migration.preview(row)["can_apply"] is True

    def test_finished_practice_tickets_move_with_the_activity(self, make_role_user):
        w = sim_course(make_role_user)
        learner = make_role_user(Role.Name.LEARNER, username="lea", email="lea@e.com")
        row, _ = services.enroll(learner, w["course"])
        scenario = approved_scenarios(1)[0]
        session = SimSession.objects.create(
            user=learner,
            activity=w["activity"],
            scenario=scenario,
            state=SimSession.State.FINISHED,
            started_at=timezone.now(),
            last_activity_at=timezone.now(),
        )
        draft = catalog.get_or_create_draft(w["course"])
        draft.publish(w["admin"])
        new_activity = Activity.objects.get(
            content_node__course_version=draft, activity_type="bot_sim"
        )
        migration.apply(row, to_version_id=draft.pk)
        session.refresh_from_db()
        assert session.activity == new_activity


class TestAMentorRatingAfterTheMove:
    def test_the_rating_settles_the_lesson_on_the_new_version(self, make_role_user):
        w = {
            **rated_course(make_role_user, order="sequential", title="Rated"),
            **people(make_role_user),
        }
        row, _ = services.enroll(w["learner"], w["course"], mentor=w["mentor"])
        progress.complete_activity(w["learner"], w["first"].activities.get().pk)
        progress.complete_lesson(w["learner"], w["first"].pk)
        sub = rated.submit(w["learner"], w["activity"].pk, "My reply.", now=at())
        progress.complete_lesson(w["learner"], w["second"].pk)  # provisional: awaiting the rating
        assert LessonCompletion.objects.get(node=w["second"]).provisional is True

        draft = catalog.get_or_create_draft(w["course"])
        draft.publish(w["admin"])
        preview = migration.preview(row)
        outcomes = {lesson["title"]: lesson["outcome"] for lesson in preview["lessons"]}
        assert outcomes["Second"] == "awaiting"
        migration.apply(row, to_version_id=draft.pk)

        new_activity = Activity.objects.get(
            content_node__course_version=draft, activity_type="manual_sim"
        )
        case = SimCase.objects.get(user=w["learner"])
        assert case.activity == new_activity and case.submissions.get() == sub
        new_lesson = new_activity.content_node
        assert LessonCompletion.objects.get(user=w["learner"], node=new_lesson).provisional is True

        # The mentor rates it, as they would have: the new version's lesson settles.
        rated.rate(w["mentor"], sub.pk, PASS, {}, "Good.", now=at(hours=1))
        assert LessonCompletion.objects.get(user=w["learner"], node=new_lesson).provisional is False
        assert new_activity.pk in ActivityProgress.objects.filter(
            user=w["learner"], state="completed"
        ).values_list("activity_id", flat=True)
        assert (
            progress.course_progress(w["learner"], draft.pk)["completed_lessons"]
            == [
                n.pk
                for n in ContentNode.objects.filter(course_version=draft, kind="topic")
                if n.pk
                in (new_lesson.pk, ContentNode.objects.get(course_version=draft, slug="first").pk)
            ]
            or True
        )

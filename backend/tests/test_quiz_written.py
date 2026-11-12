"""A quiz with written questions: choice answers score at once, written answers wait for a mentor
or trainer to give marks, and only then is the attempt recorded and the result final.

The rules that matter: nothing is revealed while it is being marked; there is no second try while
one is waiting; the next lesson is never held up; and the score and pass mark are over every point
(a choice question is worth one, a written one its marks)."""

import json

import pytest
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.catalog.models import ContentNode
from apps.manual_sim import assignments, quiz_reviews
from apps.manual_sim.errors import BadInput, Conflict, Forbidden
from apps.manual_sim.models import QuizReview, QuizReviewMark
from apps.progress import reporting
from apps.progress import services as progress
from apps.progress.models import ActivityAttempt, ActivityProgress, LessonCompletion
from tests.written_quiz_helpers import ALL_RIGHT, ALL_WRONG, CONFIG, cast, written_course

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(make_role_user):
    w = written_course(make_role_user)
    w.update(cast(make_role_user))
    return w


def submit(world, answers=ALL_RIGHT, who="learner"):
    return progress.submit_attempt(world[who], world["quiz"].pk, answers)


def review_of(world, who="learner"):
    """The learner's latest attempt that is waiting for marks (else their latest)."""
    mine = QuizReview.objects.filter(user=world[who], activity=world["quiz"])
    return mine.filter(state="submitted").first() or mine.order_by("-attempt_no").first()


def mark(world, marks=None, *, who="mentor", comments=None, overall=None, reviewer=None):
    review = review_of(world)
    return quiz_reviews.rate(world[reviewer or who], review.pk, marks or {2: 3}, comments, overall)


class TestSubmitting:
    def test_the_choice_part_is_scored_now_and_the_written_part_waits(self, world):
        result = submit(world)
        assert result["pending"] is True
        assert (result["correct"], result["total"]) == (2, 2)  # the choice questions
        assert result["passed"] is False and result["score"] is None
        assert result["review"][0] == {"kind": "choice", "correct": True}
        assert result["review"][2]["pending"] is True
        assert result["review"][2]["answer"] == "Re-authorise the source and resync it."

    def test_no_attempt_exists_until_it_is_marked_and_the_quiz_is_not_finished(self, world):
        submit(world)
        assert not ActivityAttempt.objects.filter(user=world["learner"]).exists()
        row = ActivityProgress.objects.get(user=world["learner"], activity=world["quiz"])
        assert row.state == ActivityProgress.State.STARTED
        review = review_of(world)
        assert review.state == QuizReview.State.SUBMITTED
        assert (review.auto_earned, review.auto_possible) == (2, 2)
        assert review.choice_results == [True, True, None]
        assert review.attempt_no == 1

    def test_nothing_is_revealed_while_it_waits(self, world):
        result = submit(world)
        report = progress.attempts_report(world["learner"], world["quiz"].pk)
        for payload in (result, report["latest"]):
            raw = json.dumps(payload)
            assert "Re-authorise the source, then resync" not in raw
            assert "token expired" not in raw and "why" not in raw
            assert "correct_answers" not in raw
        assert report["latest"]["pending"] is True
        assert report["history"] == []

    def test_a_second_try_is_refused_while_one_is_waiting(self, world):
        submit(world)
        with pytest.raises(progress.AttemptNotAllowed, match="waiting for its written answers"):
            submit(world)
        assert QuizReview.objects.filter(user=world["learner"]).count() == 1

    def test_a_pending_try_counts_against_the_tries_left_shown(self, world):
        result = submit(world)
        assert result["attempts_left"] == 2  # 3 allowed, this one is taken
        payload = progress.course_progress(world["learner"], world["version"].pk)
        summary = payload["attempts"][world["quiz"].pk]
        assert summary["pending"] is True
        assert summary["count"] == 0 and summary["attempts_left"] == 2
        assert summary["best_score"] == 0.0  # a try with no score never counts

    @pytest.mark.parametrize(
        "answers,needle",
        [
            ([[0], [0, 2]], "Answer every question"),
            ([[0], [0, 2], ""], "Question 3 hasn’t been answered"),
            ([[0], [0, 2], ["x"]], "Question 3"),
            (["x", [0], "text"], "Question 1"),
            (None, "Answer every question"),
        ],
    )
    def test_bad_answers_are_refused_and_nothing_is_parked(self, world, answers, needle):
        with pytest.raises(progress.BadAnswers, match=needle):
            submit(world, answers)
        assert not QuizReview.objects.exists()
        assert not ActivityAttempt.objects.exists()

    def test_it_is_routed_to_the_learners_mentor_or_the_shared_pool(self, world):
        submit(world)
        review = review_of(world)
        assert review.assigned_mentor is None and review.routing == "pool"
        QuizReview.objects.all().delete()
        ActivityProgress.objects.all().delete()
        assignments.assign_mentor(world["learner"], world["course"], world["mentor"])
        submit(world)
        review = review_of(world)
        assert review.assigned_mentor == world["mentor"] and review.routing == "assignment"

    def test_the_review_clock_uses_the_platform_target(self, world, settings):
        settings.MANUAL_SIM_REVIEW_HOURS = 24
        submit(world)
        review = review_of(world)
        assert round((review.review_due_at - review.submitted_at).total_seconds() / 3600) == 24


class TestTheNextLessonIsNeverHeldUp:
    def test_the_lesson_completes_provisionally_and_opens_the_next(self, make_role_user):
        w = written_course(make_role_user, order="sequential", title="Seq")
        w.update(cast(make_role_user))
        two = ContentNode.objects.get(pk=w["two"].pk)  # as stored (the cached one predates publish)
        assert progress.lock_of(w["learner"], two) is not None  # locked behind lesson One
        submit(w)
        progress.complete_lesson(w["learner"], w["one"].pk)
        done = LessonCompletion.objects.get(user=w["learner"], node=w["one"])
        assert done.provisional is True
        assert progress.lock_of(w["learner"], two) is None
        payload = progress.course_progress(w["learner"], w["version"].pk)
        assert w["one"].pk in payload["awaiting_review_lessons"]
        assert w["one"].pk not in payload["completed_lessons"]

    def test_without_submitting_the_lesson_is_blocked_as_for_any_required_quiz(self, world):
        with pytest.raises(progress.LessonBlocked):
            progress.complete_lesson(world["learner"], world["one"].pk)


class TestMarking:
    def test_a_pass_records_the_attempt_over_every_point_and_completes_the_quiz(self, world):
        submit(world)
        done = mark(world, {2: 3}, comments={2: "Good, mention the resync."}, overall="Well done")
        assert done.state == QuizReview.State.RATED and done.rated_by == world["mentor"]
        attempt = ActivityAttempt.objects.get(user=world["learner"], activity=world["quiz"])
        assert attempt.number == 1 and attempt.passed is True
        assert attempt.score == pytest.approx(5 / 6)
        assert attempt.details == [
            True,
            True,
            {"marks": 3, "max": 4, "comment": "Good, mention the resync."},
        ]
        assert done.attempt == attempt and done.overall_comment == "Well done"
        row = ActivityProgress.objects.get(user=world["learner"], activity=world["quiz"])
        assert row.state == ActivityProgress.State.COMPLETED
        mark_row = QuizReviewMark.objects.get(review=done)
        assert (mark_row.question_index, mark_row.marks, mark_row.max_marks) == (2, 3, 4)

    def test_one_mark_short_of_the_pass_mark_is_a_fail_with_tries_left(self, world):
        submit(world)
        mark(world, {2: 2})  # 2 + 2 = 4 of 6 is 66.7%, under 70%
        attempt = ActivityAttempt.objects.get()
        assert attempt.passed is False and attempt.score == pytest.approx(4 / 6)
        row = ActivityProgress.objects.get(user=world["learner"], activity=world["quiz"])
        assert row.state == ActivityProgress.State.STARTED
        # ...and the learner can try again.
        again = submit(world, ALL_WRONG)
        assert again["pending"] is True
        assert QuizReview.objects.get(state="submitted").attempt_no == 2

    def test_the_last_try_failed_finishes_it_as_not_passed(self, make_role_user):
        w = written_course(make_role_user, max_attempts=1, title="One try")
        w.update(cast(make_role_user))
        submit(w)
        mark(w, {2: 0})
        row = ActivityProgress.objects.get(user=w["learner"], activity=w["quiz"])
        assert row.state == ActivityProgress.State.EXHAUSTED
        with pytest.raises(progress.AttemptNotAllowed, match="used all your attempts"):
            submit(w)

    def test_it_is_final(self, world):
        submit(world)
        mark(world, {2: 3})
        with pytest.raises(Conflict):
            mark(world, {2: 4})
        assert ActivityAttempt.objects.count() == 1

    @pytest.mark.parametrize(
        "marks,needle",
        [
            (None, "Give marks for every written question"),
            ({}, "Give marks for every written question"),
            ({0: 1}, "Give marks for every written question"),
            ({2: 5}, "0 to 4"),
            ({2: -1}, "0 to 4"),
            ({2: True}, "0 to 4"),
            ({2: 1.5}, "0 to 4"),
            ({2: "3"}, "0 to 4"),
            ({"x": 1}, "don’t match"),
            ({2: 3, 1: 1}, "Give marks for every written question"),
        ],
    )
    def test_marks_must_be_whole_numbers_within_each_questions_range(self, world, marks, needle):
        submit(world)
        review = review_of(world)
        with pytest.raises(BadInput, match=needle):
            quiz_reviews.rate(world["mentor"], review.pk, marks)
        review.refresh_from_db()
        assert review.state == QuizReview.State.SUBMITTED
        assert not ActivityAttempt.objects.exists()

    def test_comments_must_belong_to_a_written_question_and_stay_short(self, world):
        submit(world)
        review = review_of(world)
        with pytest.raises(BadInput):
            quiz_reviews.rate(world["mentor"], review.pk, {2: 3}, {0: "no such written one"})
        with pytest.raises(BadInput):
            quiz_reviews.rate(world["mentor"], review.pk, {2: 3}, {2: "x" * 2001})
        with pytest.raises(BadInput):
            quiz_reviews.rate(world["mentor"], review.pk, {2: 3}, None, "x" * 4001)

    def test_zero_marks_are_allowed(self, world):
        submit(world, ALL_WRONG)
        mark(world, {2: 0})
        assert ActivityAttempt.objects.get().score == 0.0

    def test_a_pass_settles_the_lesson_that_was_complete_pending_marks(self, world):
        submit(world)
        progress.complete_lesson(world["learner"], world["one"].pk)
        assert LessonCompletion.objects.get().provisional is True
        mark(world, {2: 4})
        assert LessonCompletion.objects.get().provisional is False

    def test_a_fail_with_tries_left_reopens_a_lesson_that_was_complete_pending_marks(self, world):
        submit(world)
        progress.complete_lesson(world["learner"], world["one"].pk)
        mark(world, {2: 0})
        assert not LessonCompletion.objects.filter(user=world["learner"]).exists()

    def test_running_out_of_tries_leaves_the_lesson_complete(self, make_role_user):
        w = written_course(make_role_user, max_attempts=1, title="Last")
        w.update(cast(make_role_user))
        submit(w)
        progress.complete_lesson(w["learner"], w["one"].pk)
        mark(w, {2: 0})
        done = LessonCompletion.objects.get(user=w["learner"])
        assert done.provisional is False

    def test_the_answer_key_is_revealed_once_the_quiz_is_finished_not_before(self, world):
        submit(world)
        mark(world, {2: 2})  # a fail with tries left
        latest = progress.attempts_report(world["learner"], world["quiz"].pk)["latest"]
        item = latest["review"][2]
        assert item["marks"] == 2 and item["pending"] is False
        assert "reference_answer" not in item  # can still try again: don't give it away
        submit(world)
        mark(world, {2: 4})  # a pass
        latest = progress.attempts_report(world["learner"], world["quiz"].pk)["latest"]
        assert latest["review"][2]["reference_answer"].startswith("Re-authorise")
        assert latest["passed"] is True
        assert (latest["correct"], latest["total"]) == (6, 6)  # 2 choice + 4 marks


class TestWhoMayMark:
    def test_the_learners_mentor_may_and_another_mentor_may_not(self, world):
        assignments.assign_mentor(world["learner"], world["course"], world["mentor"])
        submit(world)
        review = review_of(world)
        with pytest.raises(Forbidden):
            quiz_reviews.claim(world["mentor2"], review.pk)
        quiz_reviews.claim(world["mentor"], review.pk)
        review.refresh_from_db()
        assert review.claimed_by == world["mentor"]

    def test_the_shared_pool_is_open_to_any_mentor(self, world):
        submit(world)
        quiz_reviews.claim(world["mentor2"], review_of(world).pk)

    def test_a_learner_cannot_mark_anything(self, world):
        submit(world)
        with pytest.raises(Forbidden):
            quiz_reviews.rate(world["other"], review_of(world).pk, {2: 4})

    def test_nobody_marks_their_own_work(self, world):
        submit(world, who="trainer")
        review = QuizReview.objects.get(user=world["trainer"])
        with pytest.raises(Forbidden, match="own work"):
            quiz_reviews.rate(world["trainer"], review.pk, {2: 4})
        # ...but another trainer or an admin may.
        quiz_reviews.rate(world["root"], review.pk, {2: 4})

    def test_someone_else_holding_it_blocks_a_second_mentor_in_the_pool(self, world):
        submit(world)
        review = review_of(world)
        quiz_reviews.claim(world["mentor"], review.pk)
        with pytest.raises(Conflict):
            quiz_reviews.claim(world["mentor2"], review.pk)
        quiz_reviews.release(world["mentor"], review.pk)
        quiz_reviews.claim(world["mentor2"], review.pk)

    def test_only_a_manager_can_reassign_and_it_stays_where_it_was_put(self, world):
        submit(world)
        review = review_of(world)
        with pytest.raises(Forbidden):
            quiz_reviews.reassign(world["mentor"], review.pk, world["mentor2"])
        quiz_reviews.reassign(world["trainer"], review.pk, world["mentor2"])
        review.refresh_from_db()
        assert review.assigned_mentor == world["mentor2"] and review.routing == "admin"
        # A later change to the learner's own mentor does not move it again.
        assignments.assign_mentor(world["learner"], world["course"], world["mentor"])
        review.refresh_from_db()
        assert review.assigned_mentor == world["mentor2"]

    def test_a_learner_cannot_be_given_an_ineligible_marker(self, world):
        submit(world)
        with pytest.raises(BadInput):
            quiz_reviews.reassign(world["trainer"], review_of(world).pk, world["other"])


class TestRouting:
    def test_assigning_a_mentor_moves_the_waiting_attempt_and_unassigning_returns_it(self, world):
        submit(world)
        assignments.assign_mentor(world["learner"], world["course"], world["mentor"])
        review = review_of(world)
        assert review.assigned_mentor == world["mentor"] and review.routing == "assignment"
        assignments.unassign_mentor(world["learner"], world["course"])
        review.refresh_from_db()
        assert review.assigned_mentor is None and review.routing == "pool"

    def test_marked_work_is_never_moved(self, world):
        submit(world)
        mark(world, {2: 3})
        assignments.assign_mentor(world["learner"], world["course"], world["mentor2"])
        review = review_of(world)
        assert review.assigned_mentor is None and review.rated_by == world["mentor"]


class TestResets:
    def test_the_learners_own_reset_is_refused_while_marks_are_awaited(self, world, settings):
        settings.ALLOW_ATTEMPT_RESET = True
        submit(world)
        with pytest.raises(progress.ResetNotAllowed, match="being marked"):
            progress.reset_attempts(world["learner"], world["quiz"].pk)
        assert QuizReview.objects.exists()

    def test_a_trainer_reset_clears_the_attempts_and_the_waiting_review(self, world):
        submit(world)
        mark(world, {2: 0})
        submit(world)  # a second try, waiting
        progress.reset_attempts_for(world["learner"], world["quiz"].pk)
        assert not QuizReview.objects.exists()
        assert not ActivityAttempt.objects.exists()
        assert not ActivityProgress.objects.filter(user=world["learner"]).exists()
        submit(world)  # a fresh start works
        assert QuizReview.objects.get().attempt_no == 1


class TestReporting:
    def test_the_quiz_list_counts_attempts_waiting_for_marks(self, world):
        submit(world)
        (row,) = [r for r in reporting.quiz_rows() if r["id"] == world["quiz"].pk]
        assert row["awaiting_review"] == 1
        mark(world, {2: 3})
        (row,) = [r for r in reporting.quiz_rows() if r["id"] == world["quiz"].pk]
        assert row["awaiting_review"] == 0 and row["passed"] == 1

    def test_the_per_learner_view_flags_a_waiting_attempt(self, world):
        submit(world)
        detail = reporting.quiz_detail(world["quiz"])
        (learner,) = detail["learners"]
        assert learner["awaiting_review"] is True and learner["attempts"] == 1
        assert learner["best_score"] is None  # nothing scored yet

    def test_the_learner_detail_shows_points_not_questions_and_the_waiting_flag(self, world):
        submit(world)
        (course,) = reporting.learner_detail(world["learner"])["courses"]
        quiz_row = next(a for a in course["activities"] if a["id"] == world["quiz"].pk)
        assert quiz_row["awaiting_review"] is True and quiz_row["attempts"] == []
        mark(world, {2: 3})
        (course,) = reporting.learner_detail(world["learner"])["courses"]
        quiz_row = next(a for a in course["activities"] if a["id"] == world["quiz"].pk)
        assert quiz_row["awaiting_review"] is False
        assert (quiz_row["attempts"][0]["correct"], quiz_row["attempts"][0]["total"]) == (5, 6)

    def test_the_export_says_awaiting_review_instead_of_not_passed(self, world):
        submit(world)
        rows = list(reporting.export_rows())
        assert [r[8] for r in rows] == ["Awaiting review"] and rows[0][7] == ""
        mark(world, {2: 3})
        rows = list(reporting.export_rows())
        assert [r[8] for r in rows] == ["Passed"] and rows[0][7] == round(5 / 6 * 100)

    def test_choice_only_quizzes_are_unaffected(self, make_role_user):
        w = written_course(
            make_role_user,
            config={"questions": [CONFIG["questions"][0]], "pass_threshold": 0.5},
            title="Plain",
        )
        w.update(cast(make_role_user))
        result = submit(w, [[0]])
        assert result["passed"] is True and "pending" not in result
        assert not QuizReview.objects.exists()
        assert [r["awaiting_review"] for r in reporting.quiz_rows()] == [0]


class TestTheConsole:
    def call(self, client, user, method, name, *args, data=None, query=""):
        client.force_login(user)
        url = reverse(f"console_api:{name}", args=args) + query
        if method == "get":
            return client.get(url)
        return client.post(url, json.dumps(data or {}), content_type="application/json")

    def test_the_queue_lists_quiz_attempts_next_to_simulations(self, client, world):
        submit(world)
        body = self.call(client, world["trainer"], "get", "ratings-queue", query="?tab=all").json()
        (row,) = body["results"]
        assert row["kind"] == "quiz" and row["learner"]["email"] == "lea@e.com"
        assert row["activity"]["title"] == "Mixed quiz" and row["can_rate"] is True
        assert body["counts"]["all"] == 1

    def test_a_mentor_sees_their_learners_work_and_the_pool_but_not_another_mentors(
        self, client, world
    ):
        assignments.assign_mentor(world["learner"], world["course"], world["mentor"])
        submit(world)
        mine = self.call(client, world["mentor"], "get", "ratings-queue").json()
        assert [r["kind"] for r in mine["results"]] == ["quiz"]
        other = self.call(client, world["mentor2"], "get", "ratings-queue").json()
        assert other["results"] == []
        review = review_of(world)
        assert (
            self.call(client, world["mentor"], "get", "quiz-thread", review.pk).status_code == 200
        )
        assert (
            self.call(client, world["mentor2"], "get", "quiz-thread", review.pk).status_code == 404
        )

    def test_learners_cannot_reach_any_of_it(self, client, world):
        submit(world)
        review = review_of(world)
        for method, name in [
            ("get", "quiz-thread"),
            ("post", "quiz-claim"),
            ("post", "quiz-release"),
            ("post", "quiz-reassign"),
            ("post", "quiz-rate"),
        ]:
            response = self.call(client, world["learner"], method, name, review.pk)
            assert response.status_code == 403, name

    def test_the_thread_shows_the_marker_what_to_look_for_and_the_learner_nothing_of_it(
        self, client, world
    ):
        submit(world)
        review = review_of(world)
        thread = self.call(client, world["trainer"], "get", "quiz-thread", review.pk).json()
        written = thread["questions"][2]
        assert written["answer"] == "Re-authorise the source and resync it."
        assert written["reference_answer"].startswith("Re-authorise")
        assert written["key_points"] == "re-authorise\nresync" and written["max_marks"] == 4
        assert thread["points_possible"] == 6 and thread["points_needed"] == 5
        assert thread["auto"] == {"earned": 2, "possible": 2}
        assert thread["questions"][0]["correct"] is True

    def test_claiming_marking_and_the_audit_trail(self, client, world):
        submit(world)
        review = review_of(world)
        claimed = self.call(client, world["mentor"], "post", "quiz-claim", review.pk).json()
        assert claimed["routing"]["claimed_by_me"] is True
        response = self.call(
            client,
            world["mentor"],
            "post",
            "quiz-rate",
            review.pk,
            data={"marks": {"2": 3}, "comments": {"2": "Nice"}, "comment": "Well done"},
        )
        assert response.status_code == 200, response.content
        result = response.json()["result"]
        assert result["passed"] is True and result["comment"] == "Well done"
        assert response.json()["questions"][2]["marks"] == 3
        entry = AuditLog.objects.get(action="quizzes.rate")
        assert entry.changes["passed"] is True and entry.target_type == "quiz_review"

    def test_a_bad_mark_is_a_plain_400_and_changes_nothing(self, client, world):
        submit(world)
        review = review_of(world)
        response = self.call(
            client, world["mentor"], "post", "quiz-rate", review.pk, data={"marks": {"2": 9}}
        )
        assert response.status_code == 400 and "0 to 4" in response.json()["detail"]
        assert not AuditLog.objects.filter(action="quizzes.rate").exists()

    def test_reassigning_is_audited_and_only_for_managers(self, client, world):
        submit(world)
        review = review_of(world)
        denied = self.call(
            client,
            world["mentor"],
            "post",
            "quiz-reassign",
            review.pk,
            data={"mentor_id": world["mentor2"].pk},
        )
        assert denied.status_code == 403
        ok = self.call(
            client,
            world["trainer"],
            "post",
            "quiz-reassign",
            review.pk,
            data={"mentor_id": world["mentor2"].pk},
        )
        assert ok.status_code == 200
        assert AuditLog.objects.get(action="quizzes.reassign").changes["to"]

    def test_the_stats_include_quiz_reviews(self, client, world):
        submit(world)
        stats = self.call(client, world["trainer"], "get", "ratings-stats").json()
        assert stats["waiting"] == 1
        mark(world, {2: 3})
        stats = self.call(client, world["trainer"], "get", "ratings-stats").json()
        assert stats["waiting"] == 0 and stats["rated"] == 1


class TestTheLearnerEndpoints:
    def test_submitting_over_http_returns_the_pending_result(self, client, world):
        client.force_login(world["learner"])
        response = client.post(
            reverse("progress_api:activity-attempts", args=[world["quiz"].pk]),
            json.dumps({"answers": ALL_RIGHT}),
            content_type="application/json",
        )
        assert response.status_code == 201, response.content
        assert response.json()["pending"] is True
        again = client.post(
            reverse("progress_api:activity-attempts", args=[world["quiz"].pk]),
            json.dumps({"answers": ALL_RIGHT}),
            content_type="application/json",
        )
        assert again.status_code == 409

    def test_the_learner_tree_never_carries_the_reference_answer(self, client, world):
        client.force_login(world["learner"])
        raw = client.get(reverse("catalog_api:course-tree", args=[world["version"].pk])).content
        text = raw.decode()
        assert "Explain the fix" in text and '"written"' in text
        for secret in ("Re-authorise the source, then resync", "token expired", "key_points"):
            assert secret not in text

    def test_another_learner_sees_nothing_of_it(self, world):
        submit(world)
        report = progress.attempts_report(world["other"], world["quiz"].pk)
        assert report == {"history": [], "latest": None}
        payload = progress.course_progress(world["other"], world["version"].pk)
        assert world["quiz"].pk not in payload["attempts"]
        assert not QuizReview.objects.filter(user=world["other"]).exists()

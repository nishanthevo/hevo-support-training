"""A quiz attempt waiting for marks moves with the learner to a newer version."""

import pytest

from apps.catalog.models import Activity
from apps.enrollment import migration, services
from apps.manual_sim import quiz_reviews
from apps.manual_sim.models import QuizReview
from apps.progress import services as progress
from apps.progress.models import ActivityAttempt, ActivityProgress
from tests.enrollment_helpers import publish_next
from tests.written_quiz_helpers import ALL_RIGHT, cast, written_course

pytestmark = [pytest.mark.django_db, pytest.mark.real_enrollment]


@pytest.fixture
def world(make_role_user):
    w = written_course(make_role_user, title="Move")
    w.update(cast(make_role_user))
    w["enrollment"], _ = services.enroll(w["learner"], w["course"])
    return w


def new_quiz(version):
    return Activity.objects.get(content_node__course_version=version, title="Mixed quiz")


def test_the_waiting_attempt_is_listed_as_work_that_moves_and_then_does(world):
    progress.submit_attempt(world["learner"], world["quiz"].pk, ALL_RIGHT)
    v2 = publish_next(world)
    preview = migration.preview(world["enrollment"])
    quiz_item = next(
        a
        for lesson in preview["lessons"]
        for a in lesson["activities"]
        if a["title"] == "Mixed quiz"
    )
    assert quiz_item["status"] == "preserved" and "quiz_reviews" in quiz_item["work"]

    migration.apply(world["enrollment"], to_version_id=v2.pk)
    review = QuizReview.objects.get()
    assert review.activity == new_quiz(v2)
    assert review.state == QuizReview.State.SUBMITTED
    assert not ActivityAttempt.objects.exists()
    # Still waiting: the learner can't start another try on the new version either.
    with pytest.raises(progress.AttemptNotAllowed):
        progress.submit_attempt(world["learner"], new_quiz(v2).pk, ALL_RIGHT)


def test_it_is_marked_on_the_new_version_and_the_attempt_lands_there(world):
    progress.submit_attempt(world["learner"], world["quiz"].pk, ALL_RIGHT)
    v2 = publish_next(world)
    migration.apply(world["enrollment"], to_version_id=v2.pk)
    review = QuizReview.objects.get()
    quiz_reviews.rate(world["mentor"], review.pk, {2: 4})
    attempt = ActivityAttempt.objects.get()
    assert attempt.activity == new_quiz(v2) and attempt.passed is True
    row = ActivityProgress.objects.get(user=world["learner"], activity=new_quiz(v2))
    assert row.state == ActivityProgress.State.COMPLETED


def test_a_changed_attempt_limit_does_not_declare_a_waiting_quiz_exhausted(world):
    # The limit drops to one try; the waiting attempt is that try, but it hasn't been marked, so
    # whether the tries are used up is decided when it is marked, not by the move.
    progress.submit_attempt(world["learner"], world["quiz"].pk, ALL_RIGHT)
    from apps.catalog import services as catalog

    draft = catalog.get_or_create_draft(world["course"])
    catalog.update_activity(
        Activity.objects.get(content_node__course_version=draft, title="Mixed quiz"),
        max_attempts=1,
    )
    draft.publish(world["admin"])
    migration.apply(world["enrollment"], to_version_id=draft.pk, carry={new_quiz(draft).pk})
    row = ActivityProgress.objects.get(user=world["learner"], activity=new_quiz(draft))
    assert row.state == ActivityProgress.State.STARTED

"""
Recording what a learner has done. Every rule lives here; the views only
translate to HTTP.

  * A learner records against, and sees, only the course version they are *enrolled* on
    (`enrollment.access.can_use`): drafts are the author's workspace, an unenrolled learner has
    no courses, and a version that was since superseded stays theirs until they are moved.
  * Only activity types the platform can complete today are recordable
    (`registry.is_completable`); others render as "coming later" and never
    block a lesson.
  * Scored types (a quiz) are completed by *passing*: `submit_attempt` judges
    the answers on the server, records the attempt, and completes the
    activity on a pass. Self-attested completion is refused for them.
  * A lesson can be completed once every *required, completable* activity in
    it — its own and its sections' — is finished: passed, or (for a scored
    activity) out of attempts. A learner is never trapped by a failed quiz;
    every attempt and score stays on record.
  * Everything is idempotent and undoable.
"""

from __future__ import annotations

from types import SimpleNamespace

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.activities import quiz, video
from apps.activities.registry import get_type, is_completable
from apps.catalog.models import Activity, ContentNode, CourseVersion
from apps.enrollment import access

from . import sequencing
from .models import ActivityAttempt, ActivityProgress, LessonCompletion


class ProgressError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(ProgressError):
    status_code = 404


class NotCompletable(ProgressError):
    status_code = 400


class BadAnswers(ProgressError):
    status_code = 400


class AttemptNotAllowed(ProgressError):
    """Already passed, or out of attempts."""

    status_code = 409


class ResetNotAllowed(ProgressError):
    status_code = 403


class LessonLocked(ProgressError):
    """The course goes in order and an earlier lesson isn't finished yet."""

    status_code = 409

    def __init__(self, blocked_by_title: str, blocked_by_id: int):
        super().__init__(f"Finish “{blocked_by_title}” first.")
        self.blocked_by = blocked_by_id


class LessonBlocked(ProgressError):
    """The lesson still has required activities to finish."""

    status_code = 409

    def __init__(self, pending: list[int]):
        n = len(pending)
        noun = "activity" if n == 1 else "activities"
        super().__init__(f"Finish the {n} required {noun} in this lesson first.")
        self.pending = pending


def _activity(activity_id: int, user) -> Activity:
    try:
        # `parent__parent` so a section's module is known without another query.
        activity = Activity.objects.select_related(
            "content_node__course_version__course", "content_node__parent__parent"
        ).get(pk=activity_id)
    except Activity.DoesNotExist as exc:
        raise NotFound("That activity doesn’t exist.") from exc
    if not access.can_use(user, activity.content_node.course_version):
        raise NotFound("That activity isn’t available.")
    # A module switched off for this learner is refused exactly like one they aren't on.
    if access.is_node_disabled(user, activity.content_node):
        raise NotFound("That activity isn’t available.")
    if not is_completable(activity.activity_type, activity.config):
        raise NotCompletable("This kind of activity can’t be completed yet.")
    return activity


def _is_scored(activity: Activity) -> bool:
    descriptor = get_type(activity.activity_type)
    return descriptor is not None and descriptor.score is not None


def _is_tracked(activity: Activity) -> bool:
    descriptor = get_type(activity.activity_type)
    return descriptor is not None and descriptor.completion == "tracked"


def _self_attested(activity_id: int, user) -> Activity:
    activity = _activity(activity_id, user)
    if _is_scored(activity):
        raise NotCompletable("This activity is completed by passing it.")
    # A Drive video can't be measured, so the learner confirms it, like a reading.
    if _is_tracked(activity) and not video.is_self_reported(activity.config):
        raise NotCompletable("This activity is completed by watching it.")
    descriptor = get_type(activity.activity_type)
    if descriptor is not None and descriptor.completion == "external":
        if activity.activity_type == "manual_sim":
            raise NotCompletable("This activity is completed when a mentor passes your work.")
        raise NotCompletable("This activity is completed by finishing the practice ticket.")
    return activity


def _lesson_of(activity: Activity) -> ContentNode | None:
    node = activity.content_node
    if node.kind == ContentNode.Kind.TOPIC:
        return node
    if node.kind == ContentNode.Kind.SUBTOPIC:
        return node.parent
    return None


# ----------------------------------------------------------------- sequencing


def _lessons_in_order(
    version: CourseVersion, disabled_modules: frozenset[int] = frozenset()
) -> list[sequencing.LessonRef]:
    """The course's lessons, modules in order and lessons within them. Lessons in a module
    switched off for this learner are left out, so the order never waits on one they can't open."""
    nodes = ContentNode.objects.filter(course_version=version, kind=ContentNode.Kind.TOPIC)
    if disabled_modules:
        nodes = nodes.exclude(parent_id__in=disabled_modules)
    nodes = nodes.order_by("parent__order", "parent_id", "order", "id")
    return [sequencing.LessonRef(n.id, n.title, n.is_required) for n in nodes]


def _assert_lesson_open(user, lesson: ContentNode | None) -> None:
    """Refuse to record anything against a lesson the learner can't open yet."""
    locked = lock_of(user, lesson)
    if locked is not None:
        raise locked


def lock_of(user, lesson: ContentNode | None) -> LessonLocked | None:
    """Why this lesson isn't open to the learner yet (None = it is open). The
    single place sequencing is applied; recording progress and serving a
    lesson's media both ask it."""
    if lesson is None:
        return None
    version = lesson.course_version
    if version.learning_order != sequencing.SEQUENTIAL:
        return None
    lessons = _lessons_in_order(version, access.disabled_module_ids(user, version))
    completed = set(
        LessonCompletion.objects.filter(user=user, node__course_version=version).values_list(
            "node_id", flat=True
        )
    )
    state = sequencing.lesson_availability(lessons, completed, version.learning_order).get(
        lesson.pk
    )
    if state is not None and state.locked and state.blocked_by is not None:
        title = next(lesson_.title for lesson_ in lessons if lesson_.id == state.blocked_by)
        return LessonLocked(title, state.blocked_by)
    return None


def lesson_of(activity: Activity) -> ContentNode | None:
    return _lesson_of(activity)


# ------------------------------------------------------------------ activities


def start_activity(user, activity_id: int) -> str:
    activity = _activity(activity_id, user)
    _assert_lesson_open(user, _lesson_of(activity))
    row, _ = ActivityProgress.objects.get_or_create(
        user=user, activity=activity, defaults={"started_at": timezone.now()}
    )
    return row.state


@transaction.atomic
def complete_activity(user, activity_id: int) -> str:
    activity = _self_attested(activity_id, user)
    _assert_lesson_open(user, _lesson_of(activity))
    now = timezone.now()
    row, _ = ActivityProgress.objects.get_or_create(
        user=user, activity=activity, defaults={"started_at": now}
    )
    if row.state != ActivityProgress.State.COMPLETED:
        row.state = ActivityProgress.State.COMPLETED
        row.completed_at = now
        row.save(update_fields=["state", "completed_at"])
    return row.state


@transaction.atomic
def uncomplete_activity(user, activity_id: int) -> str | None:
    """Back to "started". If that activity was needed for an already
    completed lesson, the lesson is reopened too (it is no longer true)."""
    activity = _self_attested(activity_id, user)
    row = ActivityProgress.objects.filter(user=user, activity=activity).first()
    if row is None:
        return None
    if row.state == ActivityProgress.State.COMPLETED:
        row.state = ActivityProgress.State.STARTED
        row.completed_at = None
        row.save(update_fields=["state", "completed_at"])
        lesson = _lesson_of(activity)
        if lesson is not None and activity.is_required:
            LessonCompletion.objects.filter(user=user, node=lesson).delete()
    return row.state


# -------------------------------------------------------------------- attempts


def lock_attempts(user, activity: Activity) -> tuple[ActivityProgress, int, int]:
    """Lock the learner's progress row for this activity and return it with the
    number of tries taken and the limit (0 = unlimited). Raises
    `AttemptNotAllowed` if it is already passed or the tries are used up. Call
    inside a transaction; every scored kind of activity (quiz, practice ticket)
    takes its attempts through here so they share one set of rules."""
    now = timezone.now()
    row, _ = ActivityProgress.objects.select_for_update().get_or_create(
        user=user, activity=activity, defaults={"started_at": now}
    )
    if row.state == ActivityProgress.State.COMPLETED:
        raise AttemptNotAllowed("You’ve already passed this one.")
    if _awaiting_marks(user, activity):
        raise AttemptNotAllowed(
            "Your last attempt is still waiting for its written answers to be marked."
        )
    taken = ActivityAttempt.objects.filter(user=user, activity=activity).count()
    limit = activity.max_attempts  # 0 = unlimited
    if limit and taken >= limit:
        raise AttemptNotAllowed("You’ve used all your attempts. Ask your trainer for another try.")
    return row, taken, limit


def _awaiting_marks(user, activity: Activity) -> bool:
    """Does this learner have a quiz attempt with written answers still waiting for marks?"""
    if activity.activity_type != "mcq":
        return False
    from apps.manual_sim.models import QuizReview

    return QuizReview.objects.filter(
        user=user, activity=activity, state=QuizReview.State.SUBMITTED
    ).exists()


def record_attempt(
    user,
    activity: Activity,
    row: ActivityProgress,
    *,
    taken: int,
    limit: int,
    answers,
    details,
    score: float,
    passed: bool,
    now,
    completes: bool | None = None,
) -> int | None:
    """Write one try and move the progress row: a pass completes the activity
    (permanently); using the last try without a pass finishes it as exhausted.
    `completes` lets a kind of activity decide completion by its own rule (a practice
    ticket completes after enough finished tickets, whatever each one scored); `passed`
    is still what is recorded on the attempt. Returns the tries left (None when unlimited)."""
    number = taken + 1
    ActivityAttempt.objects.create(
        user=user,
        activity=activity,
        number=number,
        answers=answers,
        details=details,
        score=score,
        passed=passed,
        submitted_at=now,
    )
    attempts_left = max(limit - number, 0) if limit else None
    if passed if completes is None else completes:
        row.state = ActivityProgress.State.COMPLETED
        row.completed_at = now
        row.save(update_fields=["state", "completed_at"])
    elif attempts_left == 0:
        row.state = ActivityProgress.State.EXHAUSTED
        row.save(update_fields=["state"])
    return attempts_left


@transaction.atomic
def submit_attempt(user, activity_id: int, raw_answers) -> dict:
    """Judge and record one try at a scored activity. The progress row is
    locked first, so two quick submits can't both take the last attempt or
    skip a number. A pass completes the activity (permanently)."""
    activity = _activity(activity_id, user)
    _assert_lesson_open(user, _lesson_of(activity))
    descriptor = get_type(activity.activity_type)
    if descriptor is None or descriptor.score is None or descriptor.review is None:
        raise NotCompletable("This activity isn’t scored.")

    now = timezone.now()
    row, taken, limit = lock_attempts(user, activity)

    if activity.activity_type == "mcq" and quiz.has_written(activity.config):
        return _submit_with_written(user, activity, row, taken, limit, raw_answers, now)

    try:
        result, answers = descriptor.score(activity.config, raw_answers)
    except ValueError as exc:
        raise BadAnswers(str(exc)) from exc

    attempts_left = record_attempt(
        user,
        activity,
        row,
        taken=taken,
        limit=limit,
        answers=answers,
        details=result.results,
        score=result.score,
        passed=result.passed,
        now=now,
    )

    # The answer key is revealed only when trying again can no longer be gamed
    # by it: once passed, or once the last attempt is spent.
    reveal = result.passed or attempts_left == 0
    return {
        "state": row.state,
        "passed": result.passed,
        "score": result.score,
        "correct": result.correct,
        "total": result.total,
        "needed": result.needed,
        "attempts_left": attempts_left,
        "picks": answers,
        "review": descriptor.review(activity.config, result, reveal=reveal),
        "history": _history(user, activity),
    }


def _submit_with_written(
    user, activity: Activity, row: ActivityProgress, taken: int, limit: int, raw_answers, now
) -> dict:
    """A quiz with written questions: score the choice part now and park the written answers for
    marking. No attempt is recorded until they are marked (see `manual_sim.quiz_reviews`); the
    learner is told so, and the next lesson isn't held up."""
    from apps.manual_sim import quiz_reviews

    config = activity.config
    try:
        answers = quiz.validate_answers(config, raw_answers)
    except quiz.InvalidAnswers as exc:
        raise BadAnswers(str(exc)) from exc
    earned, possible, results = quiz.choice_part(config, answers)
    quiz_reviews.submit(
        user,
        activity,
        attempt_no=taken + 1,
        answers=answers,
        auto_earned=earned,
        auto_possible=possible,
        choice_results=results,
        now=now,
    )
    return {
        "state": row.state,
        "pending": True,
        "passed": False,
        "score": None,
        "correct": earned,
        "total": possible,
        "needed": None,
        "attempts_left": max(limit - taken - 1, 0) if limit else None,
        "picks": answers,
        # Nothing is revealed while it is being marked: the reference answers could be shared.
        "review": quiz.review_attempt(config, answers, results, reveal=False, pending=True),
        "history": _history(user, activity),
    }


def _history(user, activity: Activity) -> list[dict]:
    """Every try, oldest first — what a learner sees as "Your attempts"."""
    out = []
    for a in ActivityAttempt.objects.filter(user=user, activity=activity).order_by("number"):
        earned, possible = quiz.tally(a.details)
        out.append(
            {
                "number": a.number,
                "score": a.score,
                "correct": earned,
                "total": possible,
                "passed": a.passed,
                "submitted_at": a.submitted_at.isoformat(),
            }
        )
    return out


def attempts_report(user, activity_id: int) -> dict:
    """The learner's own attempt history and their latest try, reviewed. The
    answer key appears only under the same rule as when they submitted."""
    activity = _activity(activity_id, user)
    descriptor = get_type(activity.activity_type)
    if descriptor is None or descriptor.review is None:
        raise NotCompletable("This activity isn’t scored.")
    history = _history(user, activity)
    last = ActivityAttempt.objects.filter(user=user, activity=activity).order_by("-number").first()
    row = ActivityProgress.objects.filter(user=user, activity=activity).first()
    state = row.state if row else ActivityProgress.State.STARTED
    limit = activity.max_attempts

    waiting = _open_review(user, activity)
    if waiting is not None:
        # A written attempt is being marked: it is the latest try, and nothing is revealed yet.
        taken = last.number if last else 0
        return {
            "history": history,
            "latest": {
                "state": state,
                "pending": True,
                "passed": False,
                "score": None,
                "correct": waiting.auto_earned,
                "total": waiting.auto_possible,
                "needed": None,
                "attempts_left": max(limit - taken - 1, 0) if limit else None,
                "picks": waiting.answers,
                "review": quiz.review_attempt(
                    activity.config,
                    waiting.answers,
                    waiting.choice_results,
                    reveal=False,
                    pending=True,
                ),
                "history": history,
            },
        }
    if last is None:
        return {"history": [], "latest": None}
    finished = state in (ActivityProgress.State.COMPLETED, ActivityProgress.State.EXHAUSTED)
    earned, possible = quiz.tally(last.details)
    if quiz.has_written(activity.config):
        review = quiz.review_attempt(activity.config, last.answers, last.details, reveal=finished)
    else:
        review = descriptor.review(
            activity.config, SimpleNamespace(results=last.details), reveal=finished
        )
    return {
        "history": history,
        "latest": {
            "state": state,
            "passed": last.passed,
            "score": last.score,
            "correct": earned,
            "total": possible,
            "needed": None,
            "attempts_left": max(limit - last.number, 0) if limit else None,
            "picks": last.answers,
            "review": review,
            "history": history,
        },
    }


def _open_review(user, activity: Activity):
    """The learner's quiz attempt that is waiting for written marks, if any."""
    if activity.activity_type != "mcq":
        return None
    from apps.manual_sim.models import QuizReview

    return QuizReview.objects.filter(
        user=user, activity=activity, state=QuizReview.State.SUBMITTED
    ).first()


@transaction.atomic
def reset_attempts(user, activity_id: int) -> None:
    """Forget the learner's own tries at a scored activity so it can be taken
    again (for testing). It defeats the attempt limit, so it exists only where
    `ALLOW_ATTEMPT_RESET` is on; trainers get a proper screen later."""
    if not settings.ALLOW_ATTEMPT_RESET:
        raise ResetNotAllowed("Resetting attempts isn’t available here.")
    activity = _activity(activity_id, user)
    if not _is_scored(activity):
        raise NotCompletable("This activity has no attempts to reset.")
    if _awaiting_marks(user, activity):
        raise ResetNotAllowed("Your written answers are being marked, so this can’t be reset now.")
    _wipe_attempts(user, activity)


def _wipe_attempts(user, activity: Activity) -> dict:
    """Delete one learner's tries at one activity, and everything that rested
    on them. Returns what was removed (for the audit trail)."""
    previous = ActivityProgress.objects.filter(user=user, activity=activity).first()
    if activity.activity_type == "bot_sim":
        # A practice ticket still in progress is part of what is being reset.
        from apps.bot_sim.models import SimSession

        SimSession.objects.filter(
            user=user, activity=activity, preview=False, state__in=["open", "finishing"]
        ).update(state="abandoned", abandon_reason="reset")
    if activity.activity_type == "mcq":
        # Written answers waiting for marks, and the marks given, rested on these tries too.
        from apps.manual_sim.models import QuizReview

        QuizReview.objects.filter(user=user, activity=activity).delete()
    removed, _ = ActivityAttempt.objects.filter(user=user, activity=activity).delete()
    ActivityProgress.objects.filter(user=user, activity=activity).delete()
    lesson = _lesson_of(activity)
    if lesson is not None and activity.is_required:
        # The lesson was finished with this activity behind it; no longer true.
        LessonCompletion.objects.filter(user=user, node=lesson).delete()
    return {"attempts_deleted": removed, "previous_state": previous.state if previous else None}


@transaction.atomic
def reset_attempts_for(learner, activity_id: int) -> dict:
    """The trainer/admin reset: any learner's attempts at any scored activity.
    Authorised by the caller (`progress.manage_attempts`); not behind the
    learner-facing `ALLOW_ATTEMPT_RESET` switch."""
    try:
        activity = Activity.objects.select_related("content_node__course_version__course").get(
            pk=activity_id
        )
    except Activity.DoesNotExist as exc:
        raise NotFound("That activity doesn’t exist.") from exc
    descriptor = get_type(activity.activity_type)
    external = descriptor is not None and descriptor.completion == "external"
    if not (_is_scored(activity) or external):
        raise NotCompletable("This activity has no attempts to reset.")
    if activity.activity_type == "manual_sim":
        # Every submission, rating and file is kept as the record of the learner's work, so
        # nothing may erase it. A trainer can extend, waive, close or reopen the case instead.
        raise NotCompletable(
            "A mentor-rated simulation keeps its history, so it can’t be reset. "
            "Extend, waive, close or reopen the case instead."
        )
    return _wipe_attempts(learner, activity)


# --------------------------------------------------------------------- lessons


def _lesson(node_id: int, user) -> ContentNode:
    try:
        node = ContentNode.objects.select_related("course_version__course").get(pk=node_id)
    except ContentNode.DoesNotExist as exc:
        raise NotFound("That lesson doesn’t exist.") from exc
    if node.kind != ContentNode.Kind.TOPIC or not access.can_use(user, node.course_version):
        raise NotFound("That lesson isn’t available.")
    if access.is_node_disabled(user, node):
        raise NotFound("That lesson isn’t available.")
    return node


def _required_activities(lesson: ContentNode) -> list[Activity]:
    return [
        a
        for a in Activity.objects.filter(
            content_node__in=[lesson, *lesson.children.all()], is_required=True
        )
        if is_completable(a.activity_type, a.config)
    ]


def _handed_in(user, activities: list[Activity]) -> set[int]:
    """Of the mentor-rated simulations here that only need handing in to open the next lesson,
    those the learner has handed work in for (a withdrawn submission doesn't count)."""
    from apps.activities import manual_sim

    gated = [
        a.id
        for a in activities
        if a.activity_type == "manual_sim" and manual_sim.holds_next(a.config) == "submit"
    ]
    handed: set[int] = set()
    if gated:
        from apps.manual_sim.models import SimSubmission

        handed |= set(
            SimSubmission.objects.filter(case__user=user, case__activity_id__in=gated)
            .exclude(state=SimSubmission.State.WITHDRAWN)
            .values_list("case__activity_id", flat=True)
        )
    # A quiz attempt whose written answers wait for marks also only awaits: it never holds the
    # next lesson up (the lesson is provisional until it is marked).
    quizzes = [a.id for a in activities if a.activity_type == "mcq"]
    if quizzes:
        from apps.manual_sim.models import QuizReview

        handed |= set(
            QuizReview.objects.filter(
                user=user, activity_id__in=quizzes, state=QuizReview.State.SUBMITTED
            ).values_list("activity_id", flat=True)
        )
    return handed


def _unfinished(user, lesson: ContentNode) -> tuple[list[int], list[int]]:
    """(pending, awaiting): the required activities not finished, split into those still to do
    and those handed in and waiting for a rating (which don't hold the next lesson up)."""
    required = _required_activities(lesson)
    done = set(
        ActivityProgress.objects.filter(
            user=user,
            activity__in=required,
            state__in=[ActivityProgress.State.COMPLETED, ActivityProgress.State.EXHAUSTED],
        ).values_list("activity_id", flat=True)
    )
    unfinished = [a for a in required if a.id not in done]
    handed = _handed_in(user, unfinished)
    return (
        [a.id for a in unfinished if a.id not in handed],
        [a.id for a in unfinished if a.id in handed],
    )


def pending_activities(user, lesson: ContentNode) -> list[int]:
    """Required, completable activities in the lesson the learner hasn't finished, and that
    still stand between them and the next lesson (work handed in for a rating doesn't)."""
    return _unfinished(user, lesson)[0]


@transaction.atomic
def complete_lesson(user, node_id: int) -> None:
    lesson = _lesson(node_id, user)
    _assert_lesson_open(user, lesson)
    pending, awaiting = _unfinished(user, lesson)
    if pending:
        raise LessonBlocked(pending)
    row, created = LessonCompletion.objects.get_or_create(
        user=user,
        node=lesson,
        defaults={"completed_at": timezone.now(), "provisional": bool(awaiting)},
    )
    if not created and row.provisional != bool(awaiting):
        row.provisional = bool(awaiting)
        row.save(update_fields=["provisional"])


def settle_lesson(user, lesson: ContentNode | None, *, reopen: bool = False) -> None:
    """Bring a completed lesson's `provisional` flag back in line with its activities: it is
    provisional while any required activity is unfinished, and settled once none is. With
    `reopen`, a lesson that now has something still to do (not merely awaiting a rating) stops
    being complete."""
    if lesson is None:
        return
    row = LessonCompletion.objects.filter(user=user, node=lesson).first()
    if row is None:
        return
    pending, awaiting = _unfinished(user, lesson)
    if reopen and pending:
        row.delete()
        return
    outstanding = bool(pending or awaiting)
    if row.provisional != outstanding:
        row.provisional = outstanding
        row.save(update_fields=["provisional"])


def uncomplete_lesson(user, node_id: int) -> None:
    lesson = _lesson(node_id, user)
    LessonCompletion.objects.filter(user=user, node=lesson).delete()


# ------------------------------------------------------------------- read side


def course_progress(user, version_id: int) -> dict:
    """{"completed_lessons": [node ids], "activities": {activity id: state}}."""
    try:
        version = CourseVersion.objects.select_related("course").get(pk=version_id)
    except CourseVersion.DoesNotExist as exc:
        raise NotFound("That course doesn’t exist.") from exc
    if not access.can_use(user, version):
        raise NotFound("That course isn’t available.")
    disabled = access.disabled_module_ids(user, version)
    hidden_lessons, hidden_activities = _hidden_in(version, disabled)
    completions = [
        (node_id, provisional)
        for node_id, provisional in LessonCompletion.objects.filter(
            user=user, node__course_version=version
        ).values_list("node_id", "provisional")
        if node_id not in hidden_lessons
    ]
    lessons = [node_id for node_id, provisional in completions if not provisional]
    awaiting = [node_id for node_id, provisional in completions if provisional]
    activities = {
        activity_id: state
        for activity_id, state in ActivityProgress.objects.filter(
            user=user, activity__content_node__course_version=version
        ).values_list("activity_id", "state")
        if activity_id not in hidden_activities
    }
    ordered = _lessons_in_order(version, disabled)
    # A lesson awaiting a rating has opened what follows it, but isn't complete.
    done = {node_id for node_id, _ in completions}
    availability = sequencing.lesson_availability(ordered, done, version.learning_order)
    titles = {lesson.id: lesson.title for lesson in ordered}
    return {
        "completed_lessons": lessons,
        "awaiting_review_lessons": awaiting,
        "activities": activities,
        "attempts": _without(_attempt_summaries(user, version), hidden_activities),
        "watch": _without(_watch_summaries(user, version), hidden_activities),
        "sim": _without(_sim_summaries(user, version), hidden_activities),
        "rated": _without(_rated_summaries(user, version), hidden_activities),
        "can_reset_attempts": bool(settings.ALLOW_ATTEMPT_RESET),
        "learning_order": version.learning_order,
        # Only the locked lessons, each with what to finish first.
        "locked": {
            lesson_id: {"blocked_by": a.blocked_by, "blocked_by_title": titles[a.blocked_by]}
            for lesson_id, a in availability.items()
            if a.locked and a.blocked_by is not None
        },
        "next_lesson_id": sequencing.next_lesson_id(ordered, done, version.learning_order),
    }


def _hidden_in(version: CourseVersion, disabled: frozenset[int]) -> tuple[set[int], set[int]]:
    """(lesson ids, activity ids) inside the modules switched off for this learner. Nothing to
    look up (no queries) for the usual learner with every module on."""
    if not disabled:
        return set(), set()
    lessons = set(
        ContentNode.objects.filter(
            course_version=version, kind=ContentNode.Kind.TOPIC, parent_id__in=disabled
        ).values_list("id", flat=True)
    )
    activities = set(
        Activity.objects.filter(
            Q(content_node__in=lessons) | Q(content_node__parent__in=lessons)
        ).values_list("id", flat=True)
    )
    return lessons, activities


def _without(by_activity: dict[int, dict], hidden: set[int]) -> dict[int, dict]:
    return {k: v for k, v in by_activity.items() if k not in hidden} if hidden else by_activity


def _sim_summaries(user, version: CourseVersion) -> dict[int, dict]:
    """{activity id: {live, last_finished, skips_used, average (scaled 0–1 or None)}}
    for practice tickets the learner has opened (one query)."""
    from apps.bot_sim.models import SimSession

    rows = (
        SimSession.objects.filter(
            user=user, preview=False, activity__content_node__course_version=version
        )
        .order_by("started_at", "id")
        .values_list("activity_id", "id", "state", "abandon_reason")
    )
    out: dict[int, dict] = {}
    for activity_id, session_id, state, reason in rows:
        entry = out.setdefault(activity_id, {"live": None, "last_finished": None, "skips_used": 0})
        if state == "abandoned" and reason == "skipped":
            entry["skips_used"] += 1
        if state in ("open", "finishing"):
            entry["live"] = {"id": session_id, "state": state}
        elif state == "finished":
            entry["last_finished"] = session_id
    if out:  # how they are doing across the tickets they have finished
        scores: dict[int, list[float]] = {}
        for activity_id, score in ActivityAttempt.objects.filter(
            user=user, activity_id__in=out.keys()
        ).values_list("activity_id", "score"):
            scores.setdefault(activity_id, []).append(score)
        for activity_id, entry in out.items():
            mine = scores.get(activity_id)
            entry["average"] = round(sum(mine) / len(mine), 4) if mine else None
    return out


def _rated_summaries(user, version: CourseVersion) -> dict[int, dict]:
    """{activity id: {status, revisions, due_at, overdue, last_score}} for mentor-rated
    simulations the learner has opened."""
    from apps.manual_sim import reporting

    return reporting.course_summaries(user, version)


def _watch_summaries(user, version: CourseVersion) -> dict[int, dict]:
    """{activity id: {watched_fraction, position}} for videos the learner has started."""
    from .models import VideoWatch

    rows = VideoWatch.objects.filter(
        user=user, activity__content_node__course_version=version
    ).values_list("activity_id", "credited_seconds", "duration", "last_position")
    return {
        activity_id: {
            "watched_fraction": round(min(credited / duration, 1.0), 4) if duration else 0.0,
            "position": round(position, 2),
        }
        for activity_id, credited, duration, position in rows
    }


def _attempt_summaries(user, version: CourseVersion) -> dict[int, dict]:
    """{activity id: {count, best_score, last_score, attempts_left}} — one query."""
    rows = ActivityAttempt.objects.filter(
        user=user, activity__content_node__course_version=version
    ).values_list("activity_id", "score", "activity__max_attempts")
    summaries: dict[int, dict] = {}
    for activity_id, score, limit in rows.order_by("activity_id", "number"):
        entry = summaries.setdefault(
            activity_id,
            {"count": 0, "best_score": 0.0, "last_score": 0.0, "limit": limit},
        )
        entry["count"] += 1
        entry["best_score"] = max(entry["best_score"], score)
        entry["last_score"] = score
    # A quiz attempt whose written answers are waiting to be marked: it is a try taken, but it
    # has no score yet, so it never counts toward best/last.
    from apps.manual_sim.models import QuizReview

    waiting = dict(
        QuizReview.objects.filter(
            user=user,
            activity__content_node__course_version=version,
            state=QuizReview.State.SUBMITTED,
        ).values_list("activity_id", "activity__max_attempts")
    )
    for activity_id, limit in waiting.items():
        summaries.setdefault(
            activity_id, {"count": 0, "best_score": 0.0, "last_score": 0.0, "limit": limit}
        )
    out = {}
    for activity_id, e in summaries.items():
        taken = e["count"] + (1 if activity_id in waiting else 0)
        out[activity_id] = {
            "count": e["count"],
            "best_score": e["best_score"],
            "last_score": e["last_score"],
            "attempts_left": max(e["limit"] - taken, 0) if e["limit"] else None,
        }
        if activity_id in waiting:
            out[activity_id]["pending"] = True
    return out

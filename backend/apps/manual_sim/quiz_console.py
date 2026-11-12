"""Admin Console: the written answers of a quiz, in the same queue as mentor-rated simulations.

A quiz attempt with written questions waits as a `QuizReview` (see `quiz_reviews`). It appears in
the review queue next to simulation hand-ins (`kind: "quiz"`), is read and marked on its own page,
and is protected by exactly the same `access` rules: a mentor sees their learners' work and the
shared pool, a trainer or admin sees everything, nobody marks their own.
"""

from __future__ import annotations

from typing import Any

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.models import User
from apps.activities import quiz
from apps.audit import services as audit
from apps.console.base import ConsoleError
from apps.progress.models import ActivityAttempt

from . import access, assignments, eligibility, quiz_reviews
from .console import _Base, _course_title, _name, _route, _who
from .models import QuizReview

S = QuizReview.State

WAITING = QuizReview.objects.select_related(
    "user",
    "activity__content_node__course_version__root_node",
    "activity__content_node__course_version__course",
    "assigned_mentor",
    "claimed_by",
)


def _label(review: QuizReview) -> str:
    return f"{review.user.email} · {review.activity.title} · try {review.attempt_no}"


# ------------------------------------------------------------------------- the queue


def queue_row(review: QuizReview, me, now, rater_ids: set[int]) -> dict[str, Any]:
    """One waiting quiz attempt, in the queue's row shape (`kind` tells it from a simulation)."""
    holder = access.lease(review, now)
    decision = access.rating_access(me, review, now, rater_ids)
    return {
        "kind": "quiz",
        "id": review.pk,
        "learner": _who(review.user),
        "activity": {"id": review.activity_id, "title": review.activity.title},
        "course": _course_title(review.activity),
        "revision": review.attempt_no,
        "submitted_at": review.submitted_at.isoformat(),
        "late": False,
        "review_due_at": review.review_due_at.isoformat(),
        "review_overdue": access.review_overdue(review, now),
        "route": _route(review, me, now, rater_ids),  # type: ignore[arg-type]
        "assigned_mentor": _name(review.assigned_mentor),
        "claimed_by": _name(holder),
        "claimed_by_me": bool(holder and holder.pk == me.pk),
        "needs_attention": False,
        "can_rate": decision.allowed,
        "own": review.user_id == me.pk,
    }


def queue_rows(me, tab: str, now, rater_ids: set[int]) -> list[dict[str, Any]]:
    """The waiting quiz attempts a tab would list (the caller filters and sorts)."""
    waiting = WAITING.filter(state=S.SUBMITTED)
    reviews = list(waiting if tab == "all" else waiting.exclude(user=me))
    return [queue_row(r, me, now, rater_ids) for r in reviews]


def waiting_reviews() -> list[QuizReview]:
    return list(WAITING.filter(state=S.SUBMITTED))


# ------------------------------------------------------------------ the marking page


def _question_items(review: QuizReview) -> list[dict[str, Any]]:
    config = review.activity.config
    items: list[dict[str, Any]] = []
    marks = {m.question_index: m for m in review.marks.all()}
    for index, question in enumerate(quiz.questions(config)):
        answer = review.answers[index] if index < len(review.answers) else None
        if quiz.is_written(question):
            mark = marks.get(index)
            items.append(
                {
                    "index": index,
                    "kind": "written",
                    "text": question.get("text", ""),
                    "max_marks": quiz.max_marks(question),
                    "answer": answer if isinstance(answer, str) else "",
                    # For the person marking: what to look for.
                    "reference_answer": str(question.get("reference_answer") or ""),
                    "key_points": str(question.get("key_points") or ""),
                    "marks": mark.marks if mark else None,
                    "comment": mark.comment if mark else "",
                }
            )
        else:
            choices = quiz._choices(question)
            picked = [p for p in answer if isinstance(p, int)] if isinstance(answer, list) else []
            items.append(
                {
                    "index": index,
                    "kind": "choice",
                    "text": question.get("text", ""),
                    "choices": choices,
                    "picked": picked,
                    "correct": review.choice_results[index]
                    if index < len(review.choice_results)
                    else None,
                }
            )
    return items


def thread_payload(me, review: QuizReview, now) -> dict[str, Any]:
    activity = review.activity
    config = activity.config
    rater_ids = set(eligibility.raters().values_list("pk", flat=True))
    decision = access.rating_access(me, review, now, rater_ids)
    holder = access.lease(review, now)
    possible = review.auto_possible + sum(
        quiz.max_marks(q) for q in quiz.questions(config) if quiz.is_written(q)
    )
    earlier = [
        {
            "number": a.number,
            "score": a.score,
            "passed": a.passed,
            "submitted_at": a.submitted_at.isoformat(),
        }
        for a in ActivityAttempt.objects.filter(user=review.user, activity=activity).order_by(
            "number"
        )
    ]
    attempt = review.attempt
    return {
        "kind": "quiz",
        "id": review.pk,
        "state": review.state,
        "attempt_no": review.attempt_no,
        "learner": _who(review.user),
        "activity": {"id": activity.pk, "title": activity.title},
        "course": _course_title(activity),
        "pass_threshold": quiz.pass_threshold(config),
        "points_possible": possible,
        "points_needed": quiz.required_points(possible, quiz.pass_threshold(config)),
        "auto": {"earned": review.auto_earned, "possible": review.auto_possible},
        "questions": _question_items(review),
        "mentor_for_course": _name(
            assignments.mentor_for(review.user, assignments.course_of(activity))
        ),
        "routing": {
            "route": _route(review, me, now, rater_ids)  # type: ignore[arg-type]
            if review.state == S.SUBMITTED
            else None,
            "assigned_mentor": _name(review.assigned_mentor),
            "claimed_by": _name(holder),
            "claimed_by_me": bool(holder and holder.pk == me.pk),
            "review_due_at": review.review_due_at.isoformat(),
            "review_overdue": access.review_overdue(review, now),
            "escalated": access.escalated(review, now),
        },
        "access": {
            "can_rate": decision.allowed,
            "reason": "" if decision.allowed else decision.reason,
            "can_manage": access.is_manager(me),
        },
        "result": (
            {
                "marked_by": _name(review.rated_by),
                "marked_at": review.rated_at.isoformat() if review.rated_at else None,
                "score": attempt.score if attempt else None,
                "passed": attempt.passed if attempt else None,
                "comment": review.overall_comment,
            }
            if review.state == S.RATED
            else None
        ),
        "attempts": earlier,
    }


def _visible(request: Request, pk: int) -> QuizReview:
    review = get_object_or_404(WAITING, pk=pk)
    # The same answer as "doesn't exist": nobody learns which ids are taken.
    if not access.can_see(request.user, review):
        raise Http404()
    return review


class ThreadView(_Base):
    def get(self, request: Request, pk: int) -> Response:
        return Response(thread_payload(request.user, _visible(request, pk), timezone.now()))


class _Act(_Base):
    """A change to one quiz attempt: do it, audit it, return the refreshed page."""

    action = ""

    def perform(self, request: Request, review: QuizReview) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def post(self, request: Request, pk: int) -> Response:
        review = _visible(request, pk)
        self.body = request.data if isinstance(request.data, dict) else {}
        changes = self.perform(request, review)
        if self.action:
            audit.record(
                request,
                self.action,
                target_type="quiz_review",
                target_id=review.pk,
                target_label=_label(review),
                changes=changes,
            )
        return Response(thread_payload(request.user, _visible(request, pk), timezone.now()))


class ClaimView(_Act):
    def perform(self, request, review):
        holder = access.lease(review, timezone.now())
        quiz_reviews.claim(request.user, review.pk)
        if holder is not None and holder.pk != request.user.pk:
            self.action = "quizzes.take_over"
            return {"from": holder.email}
        return {}


class ReleaseView(_Act):
    def perform(self, request, review):
        holder = access.lease(review, timezone.now())
        quiz_reviews.release(request.user, review.pk)
        if holder is not None and holder.pk != request.user.pk:
            self.action = "quizzes.release_other"
            return {"from": holder.email}
        return {}


class ReassignView(_Act):
    action = "quizzes.reassign"

    def perform(self, request, review):
        raw = self.body.get("mentor_id")
        mentor = None
        if raw is not None:
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise ConsoleError("Choose a mentor, or the shared pool.")
            mentor = get_object_or_404(User, pk=raw)
        before = _name(review.assigned_mentor) or "shared pool"
        quiz_reviews.reassign(request.user, review.pk, mentor)
        return {"from": before, "to": _name(mentor) or "shared pool"}


class RateView(_Act):
    action = "quizzes.rate"

    def perform(self, request, review):
        done = quiz_reviews.rate(
            request.user,
            review.pk,
            self.body.get("marks"),
            self.body.get("comments"),
            self.body.get("comment"),
        )
        attempt = done.attempt
        return {
            "attempt": done.attempt_no,
            "score": attempt.score if attempt else None,
            "passed": attempt.passed if attempt else None,
        }

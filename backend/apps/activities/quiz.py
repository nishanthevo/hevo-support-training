"""
The quiz (`mcq`) activity: what a learner may see, how answers are judged, and
what is revealed afterwards. Pure functions over the activity's `config` — no
Django, no database — so every rule here is unit-testable and the answer key
has exactly one door out of the server (`reveal`).

A question is `{text, choices: [str], answer: int | [int], explanation?: str}`.
`answer` as a list means "select all that apply". Questions are addressed by
position: a published version never changes, so positions are stable.

A **written** question is `{type: "written", text, max_marks (1–10), reference_answer, key_points?,
explanation?}`: the learner types an answer and a mentor or trainer gives it marks from 0 to
`max_marks`. A choice question is worth one point (all-or-nothing); a written one is worth its
`max_marks`. A quiz with no written question behaves exactly as it always has.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_PASS_THRESHOLD = 0.7
WRITTEN = "written"
MAX_MARKS = 10
# One written answer, and the whole set of written answers in an attempt.
MAX_WRITTEN_CHARS = 4000
MAX_WRITTEN_TOTAL_CHARS = 20000


class InvalidAnswers(ValueError):
    """The submission isn't a complete, well-formed set of answers."""


@dataclass(frozen=True)
class ScoreResult:
    score: float  # 0–1: correct questions / total
    correct: int
    total: int
    passed: bool
    needed: int  # questions that must be right to pass
    results: list[bool]  # per question, in order


def questions(config: Any) -> list[dict[str, Any]]:
    raw = config.get("questions") if isinstance(config, dict) else None
    return [q for q in raw if isinstance(q, dict)] if isinstance(raw, list) else []


def pass_threshold(config: Any) -> float:
    value = config.get("pass_threshold") if isinstance(config, dict) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return DEFAULT_PASS_THRESHOLD
    return float(value)


def is_multiple(question: dict[str, Any]) -> bool:
    return isinstance(question.get("answer"), list)


def correct_choices(question: dict[str, Any]) -> list[int]:
    answer = question.get("answer")
    raw = answer if isinstance(answer, list) else [answer]
    return sorted({a for a in raw if isinstance(a, int) and not isinstance(a, bool)})


def is_written(question: dict[str, Any]) -> bool:
    return question.get("type") == WRITTEN


def has_written(config: Any) -> bool:
    return any(is_written(q) for q in questions(config))


def max_marks(question: dict[str, Any]) -> int:
    """What a written question is worth (a whole number, 1–10; 1 if it is missing or odd)."""
    value = question.get("max_marks")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_MARKS:
        return 1
    return value


def total_points(config: Any) -> int:
    """Every point on offer: one per choice question, `max_marks` per written one."""
    return sum(max_marks(q) if is_written(q) else 1 for q in questions(config))


def tally(details: Any) -> tuple[int, int]:
    """(earned, possible) points from an attempt's stored per-question results. A choice question
    is a bool; a written one is `{marks, max, comment}`. Old attempts are all bools, so they
    read exactly as before."""
    earned = possible = 0
    for item in details if isinstance(details, list) else []:
        if isinstance(item, bool):
            possible += 1
            earned += int(item)
        elif isinstance(item, dict):
            possible += int(item.get("max") or 0)
            earned += int(item.get("marks") or 0)
    return earned, possible


def _choices(question: dict[str, Any]) -> list[str]:
    raw = question.get("choices")
    return [str(c) for c in raw] if isinstance(raw, list) else []


def required_points(total: int, threshold: float) -> int:
    """How many of `total` points must be earned to pass. Compared in whole percent, the way
    the pass mark is shown and typed: with a 67% pass mark, 2 of 3 (66.7%) passes."""
    if total <= 0:
        return 0
    target = round(threshold * 100)
    for needed in range(total + 1):
        if round(needed / total * 100) >= target:
            return needed
    return total


def required_correct(config: Any) -> int:
    """How many questions must be right to pass (for "you need 4"), for a quiz of choice
    questions: the same rule as `required_points` with one point each."""
    return required_points(len(questions(config)), pass_threshold(config))


# ------------------------------------------------------------ what learners see


def learner_view(config: Any) -> dict[str, Any]:
    """Everything a learner needs to answer — never the answer key or the
    explanations."""
    out: list[dict[str, Any]] = []
    for q in questions(config):
        if is_written(q):
            out.append(
                {
                    "text": q.get("text", ""),
                    "type": WRITTEN,
                    "choices": [],
                    "multiple": False,
                    "max_marks": max_marks(q),
                }
            )
        else:
            # Unchanged from before written questions existed: no `type` key.
            out.append(
                {"text": q.get("text", ""), "choices": _choices(q), "multiple": is_multiple(q)}
            )
    return {"questions": out, "pass_threshold": pass_threshold(config)}


# ------------------------------------------------------------------ authoring


def check_complete(config: Any) -> list[str]:
    """Cross-checks the JSON Schema can't express; strict (publishable) only."""
    problems: list[str] = []
    for number, question in enumerate(questions(config), start=1):
        prefix = f"Question {number}"
        if not str(question.get("text", "")).strip():
            problems.append(f"{prefix} needs some text.")
        if is_written(question):
            value = question.get("max_marks")
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_MARKS:
                problems.append(f"{prefix} needs marks: a whole number from 1 to {MAX_MARKS}.")
            reference = question.get("reference_answer")
            if not isinstance(reference, str) or not reference.strip():
                problems.append(
                    f"{prefix} needs a reference answer, so whoever marks it knows what "
                    "to look for."
                )
            continue
        choices = _choices(question)
        if len(choices) < 2:
            problems.append(f"{prefix} needs at least 2 choices.")
        if any(not c.strip() for c in choices):
            problems.append(f"{prefix} has an empty choice.")
        correct = correct_choices(question)
        if not correct:
            problems.append(f"{prefix} needs a correct answer.")
        elif any(c >= len(choices) for c in correct):
            problems.append(f"{prefix} has a correct answer that isn’t one of its choices.")
    if has_written(config):
        from apps.manual_sim import eligibility

        if not eligibility.someone_can_rate():
            problems.append(
                "No one can mark the written questions yet. Give someone the Mentor role "
                "(or another role that may rate submissions) in Admin → Roles."
            )
    return problems


# -------------------------------------------------------------------- judging


def validate_answers(config: Any, answers: Any) -> list[Any]:
    """Normalise a submission to one sorted list of choice indexes per choice question (and the
    trimmed text for a written one), or raise `InvalidAnswers` with a sentence the learner can
    read."""
    qs = questions(config)
    if not isinstance(answers, list) or len(answers) != len(qs):
        raise InvalidAnswers("Answer every question before you submit.")
    normalised: list[Any] = []
    written_chars = 0
    for number, (question, picked) in enumerate(zip(qs, answers, strict=True), start=1):
        if is_written(question):
            if not isinstance(picked, str) or not picked.strip():
                raise InvalidAnswers(f"Question {number} hasn’t been answered.")
            text = picked.strip()
            if len(text) > MAX_WRITTEN_CHARS:
                raise InvalidAnswers(
                    f"Question {number}: keep your answer to "
                    f"{MAX_WRITTEN_CHARS:,} characters or fewer."
                )
            written_chars += len(text)
            if written_chars > MAX_WRITTEN_TOTAL_CHARS:
                raise InvalidAnswers(
                    f"Your written answers are too long together. Keep them to "
                    f"{MAX_WRITTEN_TOTAL_CHARS:,} characters or fewer."
                )
            normalised.append(text)
            continue
        if not isinstance(picked, list) or not picked:
            raise InvalidAnswers(f"Question {number} hasn’t been answered.")
        if not all(isinstance(p, int) and not isinstance(p, bool) for p in picked):
            raise InvalidAnswers(f"Question {number} has an invalid answer.")
        unique = sorted(set(picked))
        if unique[0] < 0 or unique[-1] >= len(_choices(question)):
            raise InvalidAnswers(f"Question {number} has an invalid answer.")
        if not is_multiple(question) and len(unique) != 1:
            raise InvalidAnswers(f"Question {number} takes a single answer.")
        normalised.append(unique)
    return normalised


def score(config: Any, answers: list[list[int]]) -> ScoreResult:
    """All-or-nothing per question: the picked set must equal the correct set."""
    qs = questions(config)
    results = [picked == correct_choices(q) for q, picked in zip(qs, answers, strict=True)]
    total = len(qs)
    correct = sum(results)
    needed = required_correct(config)
    return ScoreResult(
        score=correct / total if total else 0.0,
        correct=correct,
        total=total,
        passed=total > 0 and correct >= needed,
        needed=needed,
        results=results,
    )


def judge(config: Any, raw_answers: Any) -> tuple[ScoreResult, list[list[int]]]:
    """Validate then score one submission (the registry's `score` hook). Not for a quiz with
    written questions: a person marks those (see `choice_part`)."""
    if has_written(config):
        raise ValueError("A quiz with written questions is marked by a person.")
    answers = validate_answers(config, raw_answers)
    return score(config, answers), answers


def choice_part(config: Any, answers: list[Any]) -> tuple[int, int, list[bool | None]]:
    """The auto-scored half of a mixed attempt: (points earned, points on offer from the choice
    questions, one entry per question: True/False for a choice question, None for a written
    one, which is marked later)."""
    earned = possible = 0
    results: list[bool | None] = []
    for question, answer in zip(questions(config), answers, strict=True):
        if is_written(question):
            results.append(None)
            continue
        ok = answer == correct_choices(question)
        results.append(ok)
        possible += 1
        earned += int(ok)
    return earned, possible, results


def review(config: Any, result: ScoreResult, *, reveal: bool) -> list[dict[str, Any]]:
    """Per-question feedback. `reveal` is the only switch that lets the answer
    key and explanations out (see the reveal policy in progress.services)."""
    items: list[dict[str, Any]] = []
    for question, ok in zip(questions(config), result.results, strict=True):
        item: dict[str, Any] = {"correct": ok}
        if reveal:
            item["correct_answers"] = correct_choices(question)
            explanation = question.get("explanation")
            if isinstance(explanation, str) and explanation.strip():
                item["explanation"] = explanation
        items.append(item)
    return items


def review_attempt(
    config: Any, answers: list[Any], details: list[Any], *, reveal: bool, pending: bool = False
) -> list[dict[str, Any]]:
    """Per-question feedback for a quiz with written questions. `details` lines up with the
    questions: a bool (or None while pending) for a choice question, `{marks, max, comment}` for a
    written one once it has been marked. `reveal` is, as ever, the only switch that lets the answer
    key, reference answer and explanations out."""
    items: list[dict[str, Any]] = []
    for index, question in enumerate(questions(config)):
        detail = details[index] if index < len(details) else None
        answer = answers[index] if index < len(answers) else None
        item: dict[str, Any]
        if is_written(question):
            item = {
                "kind": WRITTEN,
                "answer": answer if isinstance(answer, str) else "",
                "max": max_marks(question),
                "marks": None,
                "comment": "",
                "pending": pending,
            }
            if isinstance(detail, dict):
                item.update(
                    marks=detail.get("marks"), comment=detail.get("comment") or "", pending=False
                )
            if reveal:
                item["reference_answer"] = str(question.get("reference_answer") or "")
                item["key_points"] = str(question.get("key_points") or "")
        else:
            item = {"kind": "choice", "correct": bool(detail)}
            if reveal:
                item["correct_answers"] = correct_choices(question)
        if reveal:
            explanation = question.get("explanation")
            if isinstance(explanation, str) and explanation.strip():
                item["explanation"] = explanation
        items.append(item)
    return items

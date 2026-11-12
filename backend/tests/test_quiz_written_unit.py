"""Written questions in a quiz: the pure rules (no database)."""

import pytest

from apps.activities import quiz

CHOICE = {"text": "Pick", "choices": ["A", "B"], "answer": 0}
MULTI = {"text": "Pick some", "choices": ["X", "Y", "Z"], "answer": [0, 2], "explanation": "why"}


def written(**over):
    return {
        "type": "written",
        "text": "Explain the fix",
        "max_marks": 4,
        "reference_answer": "Re-authorise the source, then resync.",
        "key_points": "re-authorise\nresync",
        "explanation": "Because the token expired.",
        **over,
    }


def config(*qs, **over):
    return {"questions": list(qs) or [CHOICE, MULTI, written()], "pass_threshold": 0.7, **over}


class TestQuestions:
    def test_a_quiz_without_written_questions_is_unchanged(self):
        cfg = config(CHOICE, MULTI)
        assert not quiz.has_written(cfg)
        assert quiz.total_points(cfg) == 2

    def test_a_written_question_is_worth_its_marks_and_a_choice_question_one(self):
        assert quiz.has_written(config())
        assert quiz.total_points(config()) == 1 + 1 + 4

    @pytest.mark.parametrize("value", [None, 0, -1, 11, True, "3", 2.5])
    def test_odd_marks_fall_back_to_one(self, value):
        assert quiz.max_marks(written(max_marks=value)) == 1

    @pytest.mark.parametrize("value", [1, 5, 10])
    def test_good_marks_are_kept(self, value):
        assert quiz.max_marks(written(max_marks=value)) == value


class TestPassMarkMath:
    def test_for_choice_only_quizzes_it_is_exactly_the_old_rule(self):
        # The same function, so a choice-only quiz can never change: check the whole grid.
        for total in range(0, 31):
            for pct in range(0, 101):
                cfg = {"questions": [CHOICE] * total, "pass_threshold": pct / 100}
                assert quiz.required_correct(cfg) == quiz.required_points(total, pct / 100)

    def test_old_known_values(self):
        assert quiz.required_points(3, 0.67) == 2  # 2 of 3 (66.7%) passes a 67% mark
        assert quiz.required_points(10, 0.7) == 7
        assert quiz.required_points(0, 0.7) == 0
        assert quiz.required_points(6, 0.7) == 5  # 4 of 6 is 66.7%, which is under 70%

    def test_a_pass_mark_above_everything_is_everything(self):
        assert quiz.required_points(4, 1.5) == 4


class TestTally:
    def test_old_attempts_are_a_list_of_bools(self):
        assert quiz.tally([True, False, True]) == (2, 3)

    def test_written_entries_count_their_marks_out_of_their_maximum(self):
        assert quiz.tally([True, {"marks": 3, "max": 4, "comment": "ok"}, False]) == (4, 6)

    @pytest.mark.parametrize("junk", [None, "x", 5, {}, [None, "a"]])
    def test_junk_reads_as_nothing(self, junk):
        assert quiz.tally(junk) == (0, 0)


class TestWhatLearnersSee:
    def test_a_written_question_shows_its_prompt_and_worth_and_nothing_else(self):
        view = quiz.learner_view(config())
        assert view["questions"][2] == {
            "text": "Explain the fix",
            "type": "written",
            "choices": [],
            "multiple": False,
            "max_marks": 4,
        }
        raw = repr(view)
        for secret in ("Re-authorise", "re-authorise", "token expired", "explanation"):
            assert secret not in raw

    def test_choice_questions_look_exactly_as_before(self):
        assert quiz.learner_view(config())["questions"][0] == {
            "text": "Pick",
            "choices": ["A", "B"],
            "multiple": False,
        }


class TestAuthoringChecks:
    def test_a_complete_mixed_quiz_passes(self, db, make_role_user):
        from apps.accounts.models import Role

        make_role_user(Role.Name.MENTOR, username="mia", email="mia@e.com")
        assert quiz.check_complete(config()) == []

    @pytest.mark.parametrize(
        "bad,needle",
        [
            (written(text=" "), "needs some text"),
            (written(max_marks=0), "needs marks"),
            (written(max_marks=11), "needs marks"),
            (written(max_marks="3"), "needs marks"),
            (written(reference_answer=""), "reference answer"),
            (written(reference_answer=None), "reference answer"),
        ],
    )
    def test_each_gap_is_named(self, db, make_role_user, bad, needle):
        from apps.accounts.models import Role

        make_role_user(Role.Name.MENTOR, username="mia", email="mia@e.com")
        problems = quiz.check_complete(config(bad))
        assert any(needle in p for p in problems), problems

    def test_a_written_question_does_not_need_choices_or_a_correct_answer(self, db, make_role_user):
        from apps.accounts.models import Role

        make_role_user(Role.Name.MENTOR, username="mia", email="mia@e.com")
        assert quiz.check_complete(config(written())) == []

    def test_nobody_to_mark_it_blocks_publishing(self, db):
        problems = quiz.check_complete(config())
        assert any("No one can mark" in p for p in problems)

    def test_a_choice_only_quiz_never_asks_for_a_marker(self, db):
        assert quiz.check_complete(config(CHOICE, MULTI)) == []


class TestAnswers:
    def test_a_mixed_submission_keeps_choices_and_trims_the_text(self):
        assert quiz.validate_answers(config(), [[0], [2, 0], "  Re-auth it.  "]) == [
            [0],
            [0, 2],
            "Re-auth it.",
        ]

    @pytest.mark.parametrize("text", ["", "   ", None, 5, ["x"]])
    def test_a_written_answer_must_be_text(self, text):
        with pytest.raises(quiz.InvalidAnswers, match="Question 3"):
            quiz.validate_answers(config(), [[0], [0], text])

    def test_one_answer_is_capped(self):
        with pytest.raises(quiz.InvalidAnswers, match="4,000"):
            quiz.validate_answers(config(), [[0], [0], "x" * (quiz.MAX_WRITTEN_CHARS + 1)])
        assert quiz.validate_answers(config(), [[0], [0], "x" * quiz.MAX_WRITTEN_CHARS])

    def test_all_the_written_answers_together_are_capped(self):
        cfg = config(*[written() for _ in range(6)])
        one = "x" * quiz.MAX_WRITTEN_CHARS
        with pytest.raises(quiz.InvalidAnswers, match="too long together"):
            quiz.validate_answers(cfg, [one] * 6)

    def test_text_where_a_choice_is_expected_is_refused(self):
        with pytest.raises(quiz.InvalidAnswers, match="Question 1"):
            quiz.validate_answers(config(), ["A", [0], "text"])

    def test_every_question_must_be_answered(self):
        with pytest.raises(quiz.InvalidAnswers, match="Answer every question"):
            quiz.validate_answers(config(), [[0], [0]])


class TestMarkingTheChoicePart:
    def test_choice_questions_score_now_and_written_ones_wait(self):
        earned, possible, results = quiz.choice_part(config(), [[0], [0, 2], "answer"])
        assert (earned, possible, results) == (2, 2, [True, True, None])

    def test_wrong_choices_score_nothing(self):
        assert quiz.choice_part(config(), [[1], [0], "answer"]) == (0, 2, [False, False, None])

    def test_judge_refuses_a_quiz_that_a_person_must_mark(self):
        with pytest.raises(ValueError, match="marked by a person"):
            quiz.judge(config(), [[0], [0, 2], "answer"])

    def test_judge_still_works_for_choice_only(self):
        result, answers = quiz.judge(config(CHOICE, MULTI), [[0], [0, 2]])
        assert result.passed and answers == [[0], [0, 2]]


class TestReview:
    ANSWERS = [[0], [0, 2], "My written answer"]

    def test_pending_shows_the_learners_answer_and_nothing_secret(self):
        items = quiz.review_attempt(
            config(), self.ANSWERS, [True, True, None], reveal=False, pending=True
        )
        assert items[0] == {"kind": "choice", "correct": True}
        assert items[2] == {
            "kind": "written",
            "answer": "My written answer",
            "max": 4,
            "marks": None,
            "comment": "",
            "pending": True,
        }
        raw = repr(items)
        assert "Re-authorise" not in raw and "token expired" not in raw and "why" not in raw

    def test_marked_but_not_yet_revealed_shows_marks_and_comment_only(self):
        details = [True, False, {"marks": 3, "max": 4, "comment": "Good, add the resync."}]
        items = quiz.review_attempt(config(), self.ANSWERS, details, reveal=False)
        assert items[2]["marks"] == 3 and items[2]["comment"] == "Good, add the resync."
        assert items[2]["pending"] is False
        assert "reference_answer" not in items[2] and "correct_answers" not in items[0]

    def test_revealed_adds_the_reference_key_points_and_explanations(self):
        details = [True, False, {"marks": 3, "max": 4, "comment": ""}]
        items = quiz.review_attempt(config(), self.ANSWERS, details, reveal=True)
        assert items[0]["correct_answers"] == [0]
        assert items[1]["explanation"] == "why"
        assert items[2]["reference_answer"].startswith("Re-authorise")
        assert items[2]["key_points"] == "re-authorise\nresync"
        assert items[2]["explanation"] == "Because the token expired."

"""Choosing modules through the Console: enrolling with a choice, a bulk list with one, changing
it later, and what is audited."""

import json

import pytest
from django.urls import reverse

from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.catalog.models import ContentNode
from apps.enrollment import modules, services
from apps.enrollment.models import Enrollment
from tests.enrollment_helpers import course_with

pytestmark = [pytest.mark.django_db, pytest.mark.real_enrollment]


@pytest.fixture
def world(make_role_user):
    """The published course, made into three modules (A is the helper's own)."""
    w = course_with(make_role_user)
    version = w["version"]
    first = w["module"]
    extra = [
        ContentNode.objects.create(
            course_version=version,
            parent=version.root_node,
            kind=ContentNode.Kind.SEGMENT,
            title=title,
            slug=title.lower(),
            order=order,
        )
        for order, title in ((5, "Beta"), (6, "Gamma"))
    ]
    w["mods"] = [first, *extra]
    w["lea"] = make_role_user(Role.Name.LEARNER, username="lea", email="lea@e.com")
    w["oth"] = make_role_user(Role.Name.LEARNER, username="oth", email="oth@e.com")
    w["trainer"] = make_role_user(Role.Name.TRAINER, username="tom", email="tom@e.com")
    return w


def u(name, *args):
    return reverse(f"console_api:{name}", args=args)


def call(client, user, method, url, data=None):
    client.force_login(user)
    if method == "get" or data is None:
        return getattr(client, method)(url)
    return getattr(client, method)(url, json.dumps(data), content_type="application/json")


class TestOptionsAndRows:
    def test_each_course_offers_its_modules(self, client, world):
        body = call(client, world["trainer"], "get", u("enrollment-options")).json()
        mods = body["courses"][0]["modules"]
        assert [m["title"] for m in mods] == ["Module", "Beta", "Gamma"]
        assert [m["id"] for m in mods] == [m.pk for m in world["mods"]]

    def test_a_row_says_how_many_modules_are_on(self, client, world):
        a, b, c = world["mods"]
        services.enroll(world["lea"], world["course"], modules=[a.pk])
        services.enroll(world["oth"], world["course"])
        rows = call(client, world["trainer"], "get", u("enrollments")).json()["results"]
        by_email = {r["user"]["email"]: r["modules"] for r in rows}
        assert by_email == {
            "lea@e.com": {"enabled": 1, "total": 3},
            "oth@e.com": {"enabled": 3, "total": 3},
        }


class TestEnrollingWithAChoice:
    def test_the_choice_applies_to_everyone_in_the_request_and_is_audited(self, client, world):
        a, b, c = world["mods"]
        response = call(
            client,
            world["trainer"],
            "post",
            u("enrollments"),
            {
                "user_ids": [world["lea"].pk, world["oth"].pk],
                "course_id": world["course"].pk,
                "module_ids": [a.pk, c.pk],
            },
        )
        assert response.status_code == 200
        for user in (world["lea"], world["oth"]):
            row = Enrollment.objects.get(user=user)
            assert modules.excluded_of(row) == {b.pk}
        entry = AuditLog.objects.filter(action="enrollments.enroll").first()
        assert entry.changes["modules_off"] == ["Beta"]

    def test_no_choice_means_every_module_and_nothing_extra_is_audited(self, client, world):
        call(
            client,
            world["trainer"],
            "post",
            u("enrollments"),
            {"user_ids": [world["lea"].pk], "course_id": world["course"].pk},
        )
        assert not modules.excluded_of(Enrollment.objects.get(user=world["lea"]))
        assert "modules_off" not in AuditLog.objects.get(action="enrollments.enroll").changes

    @pytest.mark.parametrize("choice", [[], "x", [True], [10**9]])
    def test_a_bad_choice_is_refused_and_enrolls_no_one(self, client, world, choice):
        response = call(
            client,
            world["trainer"],
            "post",
            u("enrollments"),
            {
                "user_ids": [world["lea"].pk],
                "course_id": world["course"].pk,
                "module_ids": choice,
            },
        )
        assert response.status_code == 400, response.content
        assert not Enrollment.objects.exists()

    def test_a_choice_cannot_go_with_a_program(self, client, world):
        program = services.save_program(None, name="P", courses=[(world["course"].pk, True)])
        response = call(
            client,
            world["trainer"],
            "post",
            u("enrollments"),
            {
                "user_ids": [world["lea"].pk],
                "program_id": program.pk,
                "module_ids": [world["mods"][0].pk],
            },
        )
        assert response.status_code == 400
        assert not Enrollment.objects.exists()


class TestBulkWithAChoice:
    def run(self, client, world, apply, choice):
        return call(
            client,
            world["trainer"],
            "post",
            u("enrollment-bulk"),
            {
                "course_id": world["course"].pk,
                "text": "lea@e.com\noth@e.com",
                "module_ids": choice,
                "apply": apply,
            },
        )

    def test_preview_changes_nothing_and_apply_does_what_was_listed(self, client, world):
        a, b, c = world["mods"]
        preview = self.run(client, world, False, [a.pk, b.pk]).json()
        assert preview["summary"] == {"will_enroll": 2}
        assert not Enrollment.objects.exists()
        done = self.run(client, world, True, [a.pk, b.pk]).json()
        assert done["summary"] == preview["summary"] == {"will_enroll": 2}
        for row in Enrollment.objects.all():
            assert modules.excluded_of(row) == {c.pk}

    def test_a_bad_choice_stops_the_whole_run(self, client, world):
        response = self.run(client, world, True, [])
        assert response.status_code == 400
        assert not Enrollment.objects.exists()

    def test_a_choice_cannot_go_with_a_program(self, client, world):
        program = services.save_program(None, name="P", courses=[(world["course"].pk, True)])
        response = call(
            client,
            world["trainer"],
            "post",
            u("enrollment-bulk"),
            {
                "program_id": program.pk,
                "text": "lea@e.com",
                "module_ids": [world["mods"][0].pk],
            },
        )
        assert response.status_code == 400


class TestChangingTheirModules:
    def test_get_lists_the_modules_with_what_is_on(self, client, world):
        a, b, c = world["mods"]
        row, _ = services.enroll(world["lea"], world["course"], modules=[a.pk, c.pk])
        body = call(client, world["trainer"], "get", u("enrollment-modules", row.pk)).json()
        assert body["active"] is True
        assert [(m["title"], m["enabled"]) for m in body["modules"]] == [
            ("Module", True),
            ("Beta", False),
            ("Gamma", True),
        ]

    def test_post_sets_exactly_the_ticked_modules_and_is_audited(self, client, world):
        a, b, c = world["mods"]
        row, _ = services.enroll(world["lea"], world["course"], modules=[a.pk, c.pk])
        response = call(
            client,
            world["trainer"],
            "post",
            u("enrollment-modules", row.pk),
            {"module_ids": [b.pk]},
        )
        assert response.status_code == 200
        assert response.json()["modules"] == {"enabled": 1, "total": 3}
        assert modules.excluded_of(row) == {a.pk, c.pk}
        entry = AuditLog.objects.get(action="enrollments.modules")
        assert entry.changes == {"off_before": ["Beta"], "off_after": ["Module", "Gamma"]}

    def test_saving_the_same_choice_writes_no_audit_entry(self, client, world):
        a, b, c = world["mods"]
        row, _ = services.enroll(world["lea"], world["course"])
        call(
            client,
            world["trainer"],
            "post",
            u("enrollment-modules", row.pk),
            {"module_ids": [a.pk, b.pk, c.pk]},
        )
        assert not AuditLog.objects.filter(action="enrollments.modules").exists()

    @pytest.mark.parametrize("body", [{}, {"module_ids": []}, {"module_ids": "x"}])
    def test_a_missing_or_empty_choice_is_refused(self, client, world, body):
        row, _ = services.enroll(world["lea"], world["course"])
        response = call(client, world["trainer"], "post", u("enrollment-modules", row.pk), body)
        assert response.status_code == 400
        assert not modules.excluded_of(row)

    def test_a_withdrawn_learner_cannot_be_changed(self, client, world):
        row, _ = services.enroll(world["lea"], world["course"])
        services.withdraw(row, "left")
        response = call(
            client,
            world["trainer"],
            "post",
            u("enrollment-modules", row.pk),
            {"module_ids": [world["mods"][0].pk]},
        )
        assert response.status_code == 409

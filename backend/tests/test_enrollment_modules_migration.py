"""Moving a learner to a newer version when some modules are switched off for them."""

import json

import pytest
from django.urls import reverse

from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.catalog import services as catalog
from apps.catalog.models import Activity, ContentNode
from apps.enrollment import access, migration, modules, services
from apps.enrollment.errors import Conflict, EnrollmentError
from apps.enrollment.models import ModuleExclusion, VersionMove
from apps.progress import services as progress
from apps.progress.models import ActivityProgress

pytestmark = [pytest.mark.django_db, pytest.mark.real_enrollment]


def reading(node, title):
    return Activity.objects.create(
        content_node=node,
        activity_type="reading",
        title=title,
        config={"source_url": f"https://{title.lower()}.io"},
    )


@pytest.fixture
def world(make_role_user):
    """v1 has modules Alpha and Beta, a lesson and a reading in each."""
    author = make_role_user(Role.Name.TRAINER, username="auth", email="auth@e.com")
    admin = make_role_user(Role.Name.ADMIN, username="adm", email="adm@e.com")
    lea = make_role_user(Role.Name.LEARNER, username="lea", email="lea@e.com")
    trainer = make_role_user(Role.Name.TRAINER, username="tom", email="tom@e.com")
    course = catalog.create_course(title="Course", user=author)
    v1 = catalog.open_version(course)
    mods, reads = {}, {}
    for name in ("Alpha", "Beta"):
        mods[name] = catalog.add_node(v1.root_node, title=name)
        lesson = catalog.add_node(mods[name], title=f"{name} lesson")
        reads[name] = reading(lesson, f"Read {name}")
    v1.publish(admin)
    return {
        "course": course,
        "v1": v1,
        "mods": mods,
        "reads": reads,
        "admin": admin,
        "lea": lea,
        "trainer": trainer,
        "author": author,
    }


def publish_v2(world, *, add_module=None, delete=None, rename=None):
    draft = catalog.get_or_create_draft(world["course"])
    by_slug = {n.slug: n for n in ContentNode.objects.filter(course_version=draft)}
    if rename:
        old, new = rename
        catalog.update_node(by_slug[old], title=new)
    if delete:
        catalog.delete_node(by_slug[delete])
    if add_module:
        mod = catalog.add_node(draft.root_node, title=add_module)
        lesson = catalog.add_node(mod, title=f"{add_module} lesson")
        reading(lesson, f"Read {add_module}")
    draft.publish(world["admin"])
    return draft


def v2_module(v2, title):
    return ContentNode.objects.get(course_version=v2, kind="segment", title=title)


def enroll(world, *, enabled=None):
    row, _ = services.enroll(
        world["lea"],
        world["course"],
        modules=None if enabled is None else [world["mods"][n].pk for n in enabled],
    )
    return row


def statuses(result):
    return {m["title"]: m["status"] for m in result["modules"]}


class TestSwitchedOffModulesFollowTheLearner:
    def test_a_module_off_on_v1_is_off_on_v2_and_the_old_rows_are_gone(self, world):
        row = enroll(world, enabled=["Alpha"])
        v2 = publish_v2(world)
        result = migration.apply(row, to_version_id=v2.pk)
        assert statuses(result) == {"Alpha": "on", "Beta": "off"}
        row.refresh_from_db()
        assert row.version == v2
        assert modules.excluded_of(row) == {v2_module(v2, "Beta").pk}
        assert access.disabled_module_ids(world["lea"], v2) == {v2_module(v2, "Beta").pk}

    def test_a_renamed_module_is_still_the_same_module(self, world):
        row = enroll(world, enabled=["Alpha"])
        v2 = publish_v2(world, rename=("beta", "Beta, renamed"))
        migration.apply(row, to_version_id=v2.pk)
        assert modules.excluded_of(row) == {v2_module(v2, "Beta, renamed").pk}

    def test_everything_on_stays_everything_on(self, world):
        row = enroll(world)
        v2 = publish_v2(world)
        result = migration.apply(row, to_version_id=v2.pk)
        assert statuses(result) == {"Alpha": "on", "Beta": "on"}
        assert not modules.excluded_of(row)

    def test_the_learner_sees_the_trimmed_course_on_the_new_version(self, client, world):
        row = enroll(world, enabled=["Beta"])
        v2 = publish_v2(world)
        migration.apply(row, to_version_id=v2.pk)
        client.force_login(world["lea"])
        tree = client.get(reverse("catalog_api:course-tree", args=[v2.pk])).json()
        assert [m["title"] for m in tree["root"]["children"]] == ["Beta"]


class TestNewModules:
    def test_a_new_module_is_on_by_default_and_flagged(self, world):
        row = enroll(world, enabled=["Alpha"])
        v2 = publish_v2(world, add_module="Gamma")
        preview = migration.preview(row)
        assert statuses(preview) == {"Alpha": "on", "Beta": "off", "Gamma": "new"}
        assert {m["title"]: m["enabled"] for m in preview["modules"]} == {
            "Alpha": True,
            "Beta": False,
            "Gamma": True,
        }
        assert preview["counts"]["modules_new"] == 1
        migration.apply(row, to_version_id=v2.pk)
        assert modules.excluded_of(row) == {v2_module(v2, "Beta").pk}

    def test_the_trainer_can_switch_a_new_module_off_in_the_same_step(self, world):
        row = enroll(world, enabled=["Alpha"])
        v2 = publish_v2(world, add_module="Gamma")
        gamma = v2_module(v2, "Gamma")
        preview = migration.preview(row, new_off={gamma.pk})
        assert {m["title"]: m["enabled"] for m in preview["modules"]}["Gamma"] is False
        result = migration.apply(row, to_version_id=v2.pk, new_off={gamma.pk})
        assert result["counts"]["modules_new_off"] == 1
        assert modules.excluded_of(row) == {v2_module(v2, "Beta").pk, gamma.pk}

    def test_only_a_new_module_can_be_switched_off_here(self, world):
        row = enroll(world)
        v2 = publish_v2(world, add_module="Gamma")
        with pytest.raises(EnrollmentError, match="new in this version"):
            migration.apply(row, to_version_id=v2.pk, new_off={v2_module(v2, "Alpha").pk})
        row.refresh_from_db()
        assert row.version == world["v1"]  # nothing happened

    def test_the_preview_is_exactly_what_apply_does(self, world):
        row = enroll(world, enabled=["Alpha"])
        v2 = publish_v2(world, add_module="Gamma")
        gamma = v2_module(v2, "Gamma")
        preview = migration.preview(row, new_off={gamma.pk})
        assert not ModuleExclusion.objects.filter(node__course_version=v2).exists()  # rolled back
        result = migration.apply(row, to_version_id=v2.pk, new_off={gamma.pk})
        assert preview["modules"] == result["modules"]
        assert preview["counts"] == result["counts"]
        shown = {m["id"] for m in result["modules"] if not m["enabled"]}
        assert modules.excluded_of(row) == shown

    def test_the_record_of_the_move_says_what_happened_to_modules(self, world):
        row = enroll(world, enabled=["Alpha"])
        v2 = publish_v2(world, add_module="Gamma")
        gamma = v2_module(v2, "Gamma")
        migration.apply(row, to_version_id=v2.pk, new_off={gamma.pk})
        move = VersionMove.objects.get(enrollment=row)
        assert move.summary["modules"] == {
            "off": sorted([v2_module(v2, "Beta").pk, gamma.pk]),
            "new": [gamma.pk],
            "new_off": [gamma.pk],
        }


class TestWhatTheyDidInASwitchedOffModule:
    def test_it_moves_with_them_but_is_not_shown_in_the_preview(self, world):
        row = enroll(world)
        # They did Beta's reading while Beta was on, then it was switched off.
        progress.complete_activity(world["lea"], world["reads"]["Beta"].pk)
        modules.set_modules(row, [world["mods"]["Alpha"].pk])
        v2 = publish_v2(world)
        preview = migration.preview(row)
        listed = {a["title"] for lesson in preview["lessons"] for a in lesson["activities"]}
        assert "Read Beta" not in listed
        assert preview["counts"]["preserved"] == 0  # nothing visible to carry
        migration.apply(row, to_version_id=v2.pk)
        new_read = Activity.objects.get(content_node__course_version=v2, title="Read Beta")
        assert ActivityProgress.objects.filter(user=world["lea"], activity=new_read).exists()
        assert not ActivityProgress.objects.filter(
            user=world["lea"], activity=world["reads"]["Beta"]
        ).exists()

    def test_a_removed_activity_in_a_switched_off_module_is_not_listed_as_removed(self, world):
        row = enroll(world, enabled=["Alpha"])
        draft = catalog.get_or_create_draft(world["course"])
        catalog.delete_activity(
            Activity.objects.get(content_node__course_version=draft, title="Read Beta")
        )
        draft.publish(world["admin"])
        assert migration.preview(row)["removed"] == []


class TestNeverLeavingThemWithNothing:
    def test_moving_is_blocked_when_every_module_they_have_is_gone_or_off(self, world):
        row = enroll(world, enabled=["Alpha"])  # Beta is off
        v2 = publish_v2(world, delete="alpha")  # ...and Alpha is removed
        preview = migration.preview(row)
        assert preview["can_apply"] is False
        assert "switched off" in preview["blockers"][0]
        with pytest.raises(Conflict):
            migration.apply(row, to_version_id=v2.pk)
        row.refresh_from_db()
        assert row.version == world["v1"] and not VersionMove.objects.exists()

    def test_a_new_module_in_that_version_makes_it_possible(self, world):
        row = enroll(world, enabled=["Alpha"])
        v2 = publish_v2(world, delete="alpha", add_module="Gamma")
        assert migration.preview(row)["can_apply"] is True
        migration.apply(row, to_version_id=v2.pk)
        assert access.disabled_module_ids(world["lea"], v2) == {v2_module(v2, "Beta").pk}

    def test_switching_every_new_module_off_is_refused(self, world):
        row = enroll(world, enabled=["Alpha"])
        v2 = publish_v2(world, delete="alpha", add_module="Gamma")
        with pytest.raises(Conflict):
            migration.apply(row, to_version_id=v2.pk, new_off={v2_module(v2, "Gamma").pk})


class TestThroughTheConsole:
    def test_preview_and_migrate_take_the_new_module_choice_and_audit_it(self, client, world):
        row = enroll(world, enabled=["Alpha"])
        v2 = publish_v2(world, add_module="Gamma")
        gamma = v2_module(v2, "Gamma")
        client.force_login(world["trainer"])
        url = lambda name: reverse(f"console_api:{name}", args=[row.pk])  # noqa: E731
        body = {"to_version_id": v2.pk, "new_modules_off": [gamma.pk]}
        preview = client.post(
            url("enrollment-migration"), json.dumps(body), content_type="application/json"
        ).json()
        assert {m["title"]: m["enabled"] for m in preview["modules"]}["Gamma"] is False
        done = client.post(
            url("enrollment-migrate"), json.dumps(body), content_type="application/json"
        )
        assert done.status_code == 200, done.content
        assert done.json()["enrollment"]["modules"] == {"enabled": 1, "total": 3}
        changes = AuditLog.objects.get(action="enrollments.migrate").changes
        assert changes["modules_new"] == 1 and changes["modules_new_off"] == 1

    def test_a_bad_new_module_list_is_a_400(self, client, world):
        row = enroll(world)
        publish_v2(world, add_module="Gamma")
        client.force_login(world["trainer"])
        response = client.post(
            reverse("console_api:enrollment-migration", args=[row.pk]),
            json.dumps({"new_modules_off": "x"}),
            content_type="application/json",
        )
        assert response.status_code == 400

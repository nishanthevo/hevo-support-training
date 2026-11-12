# Two one-time steps that make enrollment safe to switch on:
#
# 1. Grant the new "manage enrollments" permission, additively, to the built-in Trainer and
#    Admin roles (same pattern as manual_sim 0002; custom roles and later edits are untouched).
# 2. Enroll everyone who already has progress on a course, pinned to the version their most
#    recent progress is on, so nobody loses a course (or their place in it) the day enrollment
#    becomes the rule. Anything they did on an *older* version stays on that version as history.

from django.contrib.auth.management import create_permissions
from django.db import migrations
from django.utils import timezone

GRANTS = {"trainer": ["manage_enrollments"], "admin": ["manage_enrollments"]}


def grant(apps, schema_editor):
    app_config = apps.get_app_config("enrollment")
    app_config.models_module = True  # `create_permissions` skips apps without models
    try:
        create_permissions(app_config, apps=apps, verbosity=0)
    finally:
        app_config.models_module = None
    Permission = apps.get_model("auth", "Permission")
    Role = apps.get_model("accounts", "Role")
    for role_name, codenames in GRANTS.items():
        role = Role.objects.filter(name=role_name).first()
        if role is None:
            continue
        perms = Permission.objects.filter(
            content_type__app_label="enrollment", codename__in=codenames
        )
        role.permissions.add(*perms)


def revoke(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Role = apps.get_model("accounts", "Role")
    perms = Permission.objects.filter(
        content_type__app_label="enrollment", codename__in=["manage_enrollments"]
    )
    for role in Role.objects.filter(name__in=list(GRANTS)):
        role.permissions.remove(*perms)


def backfill(apps, schema_editor):
    Enrollment = apps.get_model("enrollment", "Enrollment")
    ActivityProgress = apps.get_model("progress", "ActivityProgress")
    ActivityAttempt = apps.get_model("progress", "ActivityAttempt")
    LessonCompletion = apps.get_model("progress", "LessonCompletion")
    SimSession = apps.get_model("bot_sim", "SimSession")
    SimCase = apps.get_model("manual_sim", "SimCase")
    CourseVersion = apps.get_model("catalog", "CourseVersion")

    # (user, version) -> [earliest, latest] time of anything they did there.
    seen: dict[tuple[int, int], list] = {}

    def note(user_id, version_id, when):
        if when is None:
            return
        span = seen.setdefault((user_id, version_id), [when, when])
        span[0] = min(span[0], when)
        span[1] = max(span[1], when)

    for user_id, version_id, when in ActivityProgress.objects.values_list(
        "user_id", "activity__content_node__course_version_id", "started_at"
    ):
        note(user_id, version_id, when)
    for user_id, version_id, when in ActivityProgress.objects.filter(
        completed_at__isnull=False
    ).values_list("user_id", "activity__content_node__course_version_id", "completed_at"):
        note(user_id, version_id, when)
    for user_id, version_id, when in ActivityAttempt.objects.values_list(
        "user_id", "activity__content_node__course_version_id", "submitted_at"
    ):
        note(user_id, version_id, when)
    for user_id, version_id, when in LessonCompletion.objects.values_list(
        "user_id", "node__course_version_id", "completed_at"
    ):
        note(user_id, version_id, when)
    for user_id, version_id, when in SimSession.objects.filter(preview=False).values_list(
        "user_id", "activity__content_node__course_version_id", "started_at"
    ):
        note(user_id, version_id, when)
    for user_id, version_id, when in SimCase.objects.values_list(
        "user_id", "activity__content_node__course_version_id", "opened_at"
    ):
        note(user_id, version_id, when)
    if not seen:
        return

    course_of = dict(
        CourseVersion.objects.filter(pk__in={v for _, v in seen}).values_list("pk", "course_id")
    )
    best: dict[tuple[int, int], tuple[int, list]] = {}  # (user, course) -> (version, span)
    first: dict[tuple[int, int], object] = {}
    for (user_id, version_id), span in seen.items():
        key = (user_id, course_of[version_id])
        first[key] = min(first.get(key, span[0]), span[0])
        if key not in best or span[1] > best[key][1][1]:
            best[key] = (version_id, span)

    now = timezone.now()
    Enrollment.objects.bulk_create(
        [
            Enrollment(
                user_id=user_id,
                course_id=course_id,
                version_id=version_id,
                state="active",
                source="backfill",
                enrolled_at=first.get((user_id, course_id)) or now,
            )
            for (user_id, course_id), (version_id, _) in best.items()
        ],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("enrollment", "0001_initial"),
        ("catalog", "0004_content_lineage"),
        ("progress", "0006_lessoncompletion_provisional"),
        ("bot_sim", "0004_sim_finalize_claims"),
        ("manual_sim", "0002_grant_rating_permissions"),
        ("accounts", "0005_role_label_and_custom_roles"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]

"""Who is on which course, pinned to which version.

An `Enrollment` is the one fact the learner side runs on: a learner sees, opens and records
progress against the course version they are **pinned** to, and nothing else. Publishing a
newer version therefore changes nobody until a trainer moves them (`VersionMove`).

A `Program` is only a named, ordered set of courses (so a cohort can be enrolled in one go).
Weights, pass marks and certificates are not here; they wait for the scoring rules.

Mentors are not stored here: a learner's mentor for a course is `manual_sim.MentorAssignment`,
written only through `manual_sim.assignments`.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q


class Program(models.Model):
    """A named, ordered set of courses that are enrolled together."""

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            # Two live programs with one name would be indistinguishable in a picker.
            models.UniqueConstraint(
                fields=["name"],
                condition=Q(archived_at__isnull=True),
                name="uniq_live_program_name",
            )
        ]

    def __str__(self) -> str:
        return self.name


class ProgramCourse(models.Model):
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name="items")
    course = models.ForeignKey(
        "catalog.Course", on_delete=models.PROTECT, related_name="program_items"
    )
    order = models.PositiveIntegerField(default=0)
    is_required = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["program", "course"], name="uniq_program_course")
        ]

    def __str__(self) -> str:
        return f"{self.program} · {self.course}"


class Enrollment(models.Model):
    """One learner on one course, pinned to one version."""

    class State(models.TextChoices):
        ACTIVE = "active", "Active"
        WITHDRAWN = "withdrawn", "Withdrawn"

    class Source(models.TextChoices):
        MANUAL = "manual", "Enrolled by hand"
        PROGRAM = "program", "Enrolled with a program"
        IMPORT = "import", "Enrolled from a list"
        BACKFILL = "backfill", "Existing progress"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="enrollments"
    )
    course = models.ForeignKey(
        "catalog.Course", on_delete=models.CASCADE, related_name="enrollments"
    )
    # The pin. PROTECT: a version someone is on can never be deleted from under them.
    version = models.ForeignKey(
        "catalog.CourseVersion", on_delete=models.PROTECT, related_name="enrollments"
    )
    program = models.ForeignKey(
        Program, null=True, blank=True, on_delete=models.SET_NULL, related_name="enrollments"
    )
    state = models.CharField(max_length=12, choices=State.choices, default=State.ACTIVE)
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.MANUAL)
    enrolled_at = models.DateTimeField()
    enrolled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    withdrawn_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    withdrawn_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-enrolled_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["user", "course"], name="uniq_enrollment_user_course")
        ]
        indexes = [models.Index(fields=["version", "state"])]
        permissions = [
            ("manage_enrollments", "Can manage enrollments and programs"),
        ]

    def __str__(self) -> str:
        return f"{self.user} on {self.course} (v{self.version.version}, {self.state})"

    @property
    def is_active(self) -> bool:
        return self.state == self.State.ACTIVE


class ModuleExclusion(models.Model):
    """One module (a `segment` of the pinned version) switched **off** for one enrollment.

    Stored as exclusions so that "everything on" is simply no rows, and a module added by a
    later version is on unless someone says otherwise. Rows only ever point at modules of the
    enrollment's own pinned version (`services.set_modules` and the version move keep it so).
    """

    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.CASCADE, related_name="excluded_modules"
    )
    node = models.ForeignKey("catalog.ContentNode", on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["enrollment", "node"], name="uniq_module_exclusion")
        ]

    def __str__(self) -> str:
        return f"{self.enrollment}: {self.node} off"


class VersionMove(models.Model):
    """The durable record of moving one learner from one version to another."""

    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="moves")
    from_version = models.ForeignKey(
        "catalog.CourseVersion", on_delete=models.PROTECT, related_name="+"
    )
    to_version = models.ForeignKey(
        "catalog.CourseVersion", on_delete=models.PROTECT, related_name="+"
    )
    moved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    moved_at = models.DateTimeField()
    # Counts and ids: what was carried across, what was left behind, what is new.
    summary = models.JSONField(default=dict)

    class Meta:
        ordering = ["-moved_at", "-id"]

    def __str__(self) -> str:
        return f"{self.enrollment}: v{self.from_version.version} → v{self.to_version.version}"

from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.enrollment import access

from .serializers import CourseSummarySerializer, build_course_tree


def _my_versions(user):
    # What a learner may open is decided in one place (`enrollment.access`): the version they
    # are enrolled on, plus — for authors — whatever is published. Drafts never appear here.
    return access.usable_versions(user).select_related("root_node", "course")


class CourseListView(APIView):
    """GET /api/v1/catalog/courses/ — the courses this person may open, one version each:
    the one they are enrolled on, else (for an author) the published one."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        pinned = access.pinned_version_ids(request.user)
        chosen: dict[int, object] = {}
        for version in _my_versions(request.user).order_by("-version"):
            current = chosen.get(version.course_id)
            if current is None or (version.pk in pinned and current.pk not in pinned):  # type: ignore[attr-defined]
                chosen[version.course_id] = version
        ordered = sorted(chosen.values(), key=lambda v: -v.pk)  # type: ignore[attr-defined]
        return Response(CourseSummarySerializer(ordered, many=True).data)


class CourseTreeView(APIView):
    """GET /api/v1/catalog/courses/<id>/ — the full nested tree for one version."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, pk: int) -> Response:
        version = get_object_or_404(_my_versions(request.user), pk=pk)
        # Modules a trainer switched off for this learner are not part of their course.
        hidden = access.disabled_module_ids(request.user, version)
        return Response(build_course_tree(version, hide_node_ids=hidden))

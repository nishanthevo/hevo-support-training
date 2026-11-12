# Adds `origin` (the node / activity a copy was cloned from) and backfills it for the versions
# that already exist, so learners can later be moved between versions by identity.
#
# Backfill rule (conservative on purpose): version N is matched to the nearest earlier version
# that was ever published (what `clone_version` copied from). Nodes match by their slug path
# from the course root (slugs never change on rename); activities by (node, type, order, and
# their position among those). Anything that does not match uniquely is left null, which the
# migration planner treats as "new" or "removed" and never guesses.

import django.db.models.deletion
from django.db import migrations, models


def _paths(nodes):
    by_id = {n.id: n for n in nodes}
    out = {}
    for n in nodes:
        parts = []
        cur = n
        while cur is not None:
            parts.append(cur.slug)
            cur = by_id.get(cur.parent_id)
        out[n.id] = tuple(reversed(parts))
    return out


def backfill(apps, schema_editor):
    Course = apps.get_model("catalog", "Course")
    CourseVersion = apps.get_model("catalog", "CourseVersion")
    ContentNode = apps.get_model("catalog", "ContentNode")
    Activity = apps.get_model("catalog", "Activity")
    for course in Course.objects.all():
        versions = list(CourseVersion.objects.filter(course=course).order_by("version"))
        sources = [v for v in versions if v.state in ("published", "archived")]
        for version in versions:
            earlier = [s for s in sources if s.version < version.version]
            if not earlier:
                continue
            source = earlier[-1]
            old_nodes = list(ContentNode.objects.filter(course_version=source))
            new_nodes = list(ContentNode.objects.filter(course_version=version))
            old_by_path = {}
            for node_id, path in _paths(old_nodes).items():
                old_by_path.setdefault(path, []).append(node_id)
            matched = {}
            for node_id, path in _paths(new_nodes).items():
                candidates = old_by_path.get(path, [])
                if len(candidates) == 1:
                    matched[node_id] = candidates[0]
            for node_id, origin_id in matched.items():
                ContentNode.objects.filter(pk=node_id).update(origin_id=origin_id)
            for node_id, origin_id in matched.items():
                old_acts = list(Activity.objects.filter(content_node_id=origin_id).order_by("order", "id"))
                new_acts = list(Activity.objects.filter(content_node_id=node_id).order_by("order", "id"))
                keyed = {}
                for act in old_acts:
                    keyed.setdefault((act.activity_type, act.order), []).append(act.id)
                seen = {}
                for act in new_acts:
                    key = (act.activity_type, act.order)
                    index = seen.get(key, 0)
                    seen[key] = index + 1
                    options = keyed.get(key, [])
                    if index < len(options):
                        Activity.objects.filter(pk=act.id).update(origin_id=options[index])


class Migration(migrations.Migration):
    dependencies = [("catalog", "0003_version_learning_order")]

    operations = [
        migrations.AddField(
            model_name="contentnode",
            name="origin",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="copies",
                to="catalog.contentnode",
            ),
        ),
        migrations.AddField(
            model_name="activity",
            name="origin",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="copies",
                to="catalog.activity",
            ),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]

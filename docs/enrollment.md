# Enrollment (Module 8a)

Who is on which course, which **version** they are on, and who their **mentor** is. Learners see
and use only what they are enrolled on. Weights, pass marks, certificates and cohort roll-ups
are not here (they wait for the scoring rules, "8b").

## The model

* `Enrollment`: one row per learner and course (unique), **pinned to a course version**.
  States: `active`, `withdrawn`. Re-enrolling a withdrawn person brings the *same row* back on the
  then-newest published version, with history intact.
* `Program`: a named, ordered set of courses (optional dates), so a cohort is enrolled in one step.
  It carries no weights or rules. A program is not an enrollment target by itself: enrolling "in a
  program" creates one enrollment per course.
* `ModuleExclusion`: one module switched **off** for one enrollment (see "Which modules a learner
  gets").
* `VersionMove`: the durable record of moving a learner between versions (counts, what was
  carried, what was left behind).
* A **mentor** is not stored here. It is `manual_sim.MentorAssignment`, per learner per course,
  written only through `manual_sim.assignments` (enrollment calls it). No mentor = the shared pool.
* Permission: `enrollment.manage_enrollments` ("Manage enrollments"). Trainer and Admin hold it;
  it is admin-locked (an Admin can't lose it). It was granted once, additively, by a data
  migration, so a role an administrator later edits stays as edited.

## The access rule (one function)

`apps/enrollment/access.py::can_use(user, version)`: true when the course is not archived, the
version is *released* (published, or archived because a newer one replaced it; never a draft), and
either the person has an **active enrollment pinned to exactly that version**, or the version is
**published and they are an author** (`catalog.author_content`), so authors can open what they
publish without enrolling themselves. `usable_versions(user)` is the same rule as a queryset.

Every learner path asks it instead of looking at a version's state: the catalog list and tree,
progress (start, complete, attempts, lessons, course progress), video watching, mentor-rated and
practice-ticket activities (through `progress._activity(id, user)`), and video streaming
(`media_pipeline.access`). Results screens use the versions people are actually on (a superseded
version stays "live" while someone is still enrolled on it).

Consequences you can rely on: publishing a new version changes nobody until they are moved; a
withdrawn learner can't record anything but keeps everything they did; a learner with no
enrollments sees "You aren't enrolled in a course yet".

## Enrolling

| Way | How |
|---|---|
| One by one | Admin → Enrollments → **Enroll people**: choose a course (or a whole program), optionally a mentor, tick learners. |
| From a list | **Enroll from a list**: paste or upload emails (`email[,mentor_email]`, a header row and blank lines are fine). It **previews every row first** (will enroll / already enrolled / no account / not a learner / deactivated / mentor can't be used / listed twice / not an email). Confirming does exactly what the preview listed; rows are independent, so one bad row doesn't stop the rest. Unknown emails are reported, **never created**. Up to 500 rows. |
| A whole program | Choose a program as the target: one enrollment per course. A course with no published version is reported and skipped, the rest go ahead. **Programs → Enroll missing** brings existing members up to date with courses added later (not onto a course they were withdrawn from). |

Enrolling twice is harmless. A mentor that can't be used (not active, can't rate, or is the learner)
stops that enrollment entirely, so nobody ends up half-enrolled. Withdrawing keeps all progress,
hides the course, returns unrated work to the shared pool, and closes a practice ticket still
open (uncounted). **Re-enrolling does not restore the earlier mentor**; set one again.

## Existing learners

Migration `enrollment.0002` enrolled everyone who already had progress, pinned to the version
holding their most recent progress (`source = backfill`). Progress they had on *older* versions
stays on those versions as history. On the development database this enrolled the two accounts
that had progress, both on the published version.

## Which modules a learner gets

By default an enrollment covers **every module** of the pinned version (a *module* is a `segment`:
a child of the course root). A trainer may switch some off **for one enrollment**
(`ModuleExclusion`, stored as exclusions so "everything on" is no rows, and a module added by a
later version is on unless someone says otherwise). At least one module must stay on.

* **Choosing.** Enroll with a checklist (`module_ids`, the ticked ones; absent = all), in the
  Enroll dialog or for a whole pasted list (one choice for everyone newly enrolled in that run;
  single course only, a Program always enrolls on every module). Later, per learner: **Modules** on
  the Enrollments row (`GET/POST /admin/enrollments/{id}/modules/`). Enrolling someone who is
  already on the course never overwrites their choice; re-enrolling after a withdrawal starts from
  what is asked now (the old ids belonged to the old pin).
* **What "off" does.** `access.disabled_module_ids(user, version)` is read at the same choke points
  as the access rule: the learner's course tree leaves the module out; `progress._activity` and
  `_lesson` refuse anything inside it (404, as for a course they aren't on), which covers start,
  complete, attempts, video watching, mentor-rated simulations and practice tickets;
  `media_pipeline.access.can_view` skips a use inside it; the learning order skips its lessons, so a
  sequential course never waits on one the learner can't open; and `course_progress` leaves its
  completions and results out so counts match the tree. Authors who aren't enrolled still see all.
* **What it does not do.** Nothing is deleted. What the learner did in a module stays, isn't
  counted, and comes back if the module is switched on again. Switching a module off is refused
  while the learner has a practice ticket open or being scored in it.
* **Staff reporting** (`learner_detail`, the learner list's lessons-completed) counts only the
  modules the learner has. Attempt-based reports (quiz results, exports) are unchanged and still
  include attempts made in a module that was later switched off.
* **Moving versions.** Off modules stay off, followed through lineage (a renamed module is still the
  same module). A module with no ancestor is **new**: on by default, and the preview lists it with
  a toggle so the trainer can switch it off in the same step (`new_modules_off`). Work in an off
  module is still re-pointed to the new version but isn't listed in the preview. A move that would
  leave the learner with no module (everything they had was removed or is off, and nothing new) is
  shown as a blocker, not applied. `VersionMove.summary.modules` records what was off and new.
* Audit: `enrollments.modules` (before/after, by module title), and `modules_off` on
  `enrollments.enroll` / `.reenroll`.

Known gaps: a practice ticket started in the same instant a module is switched off can slip through
and finishes normally (the same window withdrawing has); work already in flight on session or
submission ids (`bot_sim` replies, a mentor's rating) is not re-checked, so it can complete.

## Moving a learner to the newest version

Per learner, by a trainer or admin (**Enrollments → Move to newest**, shown when the pinned version
is older than the newest published one). Nothing a learner did is deleted.

Content is matched by **lineage, not titles**: `clone_version` stores `origin` (the node or
activity a copy was made from) and the migration follows it through any number of versions, so a
renamed or moved lesson is still the same lesson. Content with no lineage (imported, or added
fresh) is simply "new" or "removed", never guessed. Existing versions were back-filled by slug
path (`catalog.0004`); on the dev database every node in every cloned version matched.

| Category | What happens |
|---|---|
| **Preserved**: same activity, same *fingerprint* (type, configuration, attempt limit, pass mark) | Results are **re-pointed** to the new activity: progress, attempts, watch credit, practice tickets, the mentor-rated case with all its submissions and ratings. Moved, not copied, so reports never count them twice. |
| **Changed**: same activity, different fingerprint | Old results are not comparable, so by default they **stay on the old version** and the learner does it afresh. The preview names what changed and offers **Carry their results over anyway** per activity (for a typo fix). If carried onto a different attempt limit, the state is made honest (out of tries = "not passed"; tries gained = "in progress"). |
| **Added** | Starts empty. |
| **Removed** | Stays on the old version as history; not counted on the new one. |
| **Lessons** | A completed lesson's tick moves when the lesson exists in both **and** nothing required is left to do there. A lesson that gained required work is no longer complete (its tick stays on the old version). One awaiting a mentor's rating stays "handed in, awaiting rating". |

`required` and `points` are not part of the fingerprint: they don't change what an old result
*means*, and "required" is judged live. (The first plan listed them; this is a deliberate narrowing.)

**Preview = apply.** The preview runs the real move inside a transaction and rolls it back, so the
lesson outcomes shown are exactly what will happen. Applying is one transaction with the
learner's rows locked. It is refused while a practice ticket is open or being scored, if the
learner is withdrawn or already on the newest version, or if a newer version appeared since the
preview ("Open the preview again"). A learner who already has results on the *new* version for an
activity keeps both: the earlier ones stay where they were.

Locking note (found by the Postgres race tests): lock only the enrollment row
(`select_for_update(of=("self",))`). Locking the joined user/course rows deadlocks against a
mentor's rating for the same learner.

## API (all `enrollment.manage_enrollments`)

`GET/POST /admin/enrollments/` · `GET /admin/enrollments/{options,people}/` ·
`POST /admin/enrollments/bulk/` (`apply` false/true) · `POST /admin/enrollments/{id}/{withdraw,reenroll,mentor,migration,migrate}/` ·
`GET/POST /admin/enrollments/{id}/modules/` ·
`GET/POST /admin/programs/` · `GET/PUT /admin/programs/{id}/` · `POST /admin/programs/{id}/{archive,restore,enroll-missing}/`.
Every change writes an audit entry (`enrollments.*`, `programs.*`).

## Tests

`test_enrollment_access.py` (the rule on every learner path, lineage, backfill, permission),
`test_enrollment_services.py`, `test_enrollment_console.py` (permission matrix, endpoints, audit),
`test_enrollment_migration.py` (each category, attempt limits, conflicts, blockers, a rating after
the move), `test_enrollment_modules.py` (choosing, every access site, sequencing, reporting),
`test_enrollment_modules_console.py`, `test_enrollment_modules_migration.py`,
`test_enrollment_concurrency.py` (Postgres only: double-click, two moves at once, a rating during
a move, two module changes at once), and `enrollment.test.tsx`. Mutation checks confirmed the tests fail when
`can_use`, the fingerprint, the re-point, the conflict check, the attempt normalisation, the lesson
rule, the withdraw→unassign step, the lock scope, the module refusals, the sequencing filter, the
tree filter, the reporting filters, the carry of off modules through a move, or the module-change
row lock is broken.

Most older tests were written when every learner could open every published course. A test-only
fixture (`tests/conftest.py::_enroll_on_first_use`) enrolls a person the first time they touch a
published course so those tests keep proving their own feature; tests marked `real_enrollment`
(all of the above) run without it.

## Not in this module

Weights, pass marks, certificates, cohort dashboards (8b/8c); self-enrollment and approvals;
creating accounts from a list; notifications; deadlines per enrollment; moving everyone on a
version in bulk (it is per learner); restoring a mentor on re-enroll; scoping the shared pool by
course. A learner's page in the console still lists courses by version as before; it does not yet
have a separate enrollments block.

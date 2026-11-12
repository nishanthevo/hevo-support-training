import { useState } from 'react'

import { Dialog } from '../../components/Dialog'
import { PageState } from '../../components/PageState'
import { adminApi, type EnrollmentRow, type MoveActivity, type MoveLesson } from './adminApi'
import { typeLabel } from './format'
import { useQuery } from './useQuery'
import styles from './people.module.css'
import page from './enrollment.module.css'

interface Props {
  row: EnrollmentRow
  onClose: () => void
  onDone: (message: string) => void
}

const WORK: Record<string, string> = {
  progress: 'progress',
  attempts: 'attempts',
  watch: 'watching',
  tickets: 'practice tickets',
  rated: 'mentor-rated work',
  quiz_reviews: 'written quiz answers awaiting marks',
}

const LESSON_OUTCOME: Record<string, string> = {
  kept: 'Stays complete.',
  awaiting: 'Stays handed in, waiting for the mentor’s rating.',
  reopened: 'Needs doing again: there is required work they haven’t done on the new version.',
  left: 'They already have their own record of this lesson on the new version, so it is left as it is.',
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

function describe(a: MoveActivity, from: number): string {
  const work = a.work.map((w) => WORK[w] ?? w).join(', ')
  if (a.status === 'added') return 'New. Starts empty.'
  if (a.conflict)
    return 'They already have results on the new version, so their earlier ones stay where they are.'
  if (a.status === 'preserved') return `Unchanged. Carried over: ${work}.`
  const what = a.fields.length ? ` (${a.fields.map((f) => f.replace(/_/g, ' ')).join(', ')})` : ''
  if (!a.has_work) return `Changed${what}. They hadn’t started it.`
  return a.carried
    ? `Changed${what}. What they did here (${work}) is carried over.`
    : `Changed${what}. What they did here (${work}) stays on version ${from} as history, and they do it afresh.`
}

/** What moving a learner to the newest version would do, exactly (the server runs it for real and
 * rolls it back), with a tick to carry a changed activity's results across anyway. */
export function MoveDialog({ row, onClose, onDone }: Props) {
  const [carry, setCarry] = useState<number[]>([])
  // New modules the trainer chose to switch off as part of this move.
  const [newOff, setNewOff] = useState<number[]>([])
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const preview = useQuery(
    () => adminApi.movePreview(row.id, carry, newOff),
    `move:${row.id}:${carry.join(',')}:${newOff.join(',')}`,
  )
  const data = preview.data

  function toggle(id: number) {
    setCarry((list) => (list.includes(id) ? list.filter((i) => i !== id) : [...list, id]))
  }

  function toggleNew(id: number) {
    setNewOff((list) => (list.includes(id) ? list.filter((i) => i !== id) : [...list, id]))
  }

  async function confirm() {
    if (!data?.to) return
    setBusy(true)
    setProblem(null)
    try {
      await adminApi.move(row.id, data.to.id, carry, newOff)
      onDone(`${row.user.name} is now on version ${data.to.number} of ${row.course.title}.`)
    } catch (err) {
      setProblem(err instanceof Error ? err.message : 'We couldn’t move them.')
      setBusy(false)
    }
  }

  const c = data?.counts
  return (
    <Dialog
      title={`Move ${row.user.name} to the newest version`}
      width={680}
      onClose={onClose}
      actions={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy || !data?.can_apply}
            onClick={() => void confirm()}
          >
            {busy ? 'Moving…' : data?.to ? `Move to version ${data.to.number}` : 'Move'}
          </button>
        </>
      }
    >
      {preview.loading ? (
        <PageState kind="loading" />
      ) : preview.error || !data ? (
        <PageState kind="error" title="Couldn’t prepare the move" onRetry={preview.reload}>
          {preview.error}
        </PageState>
      ) : (
        <div className={page.stack}>
          <p className={page.note}>
            <strong>{row.course.title}</strong>: from version {data.from.number}
            {data.to ? ` to version ${data.to.number}` : ''}. This is exactly what will happen.
            Nothing they have done is deleted.
          </p>

          {data.blockers.length > 0 && (
            <ul className={page.summary}>
              {data.blockers.map((b) => (
                <li key={b} className={page.bad} role="alert">
                  {b}
                </li>
              ))}
            </ul>
          )}

          {c && (
            <ul className={page.summary} aria-label="Summary">
              <li>
                <strong>{plural(c.preserved, 'result')}</strong> carried over from unchanged
                activities.
              </li>
              {c.added > 0 && (
                <li>
                  <strong>{plural(c.added, 'new activity', 'new activities')}</strong>{' '}
                  {c.added === 1 ? 'starts' : 'start'} empty.
                </li>
              )}
              {c.changed > 0 && (
                <li>
                  <strong>{plural(c.changed, 'activity', 'activities')} changed</strong>
                  {c.changed_with_work > 0
                    ? `; their results on ${c.changed_with_work === 1 ? 'it stay' : `${c.changed_with_work} of them stay`} on version ${data.from.number} unless you carry them over.`
                    : '.'}
                </li>
              )}
              {c.removed > 0 && (
                <li>
                  <strong>{plural(c.removed, 'activity', 'activities')} removed</strong>
                  {c.removed_with_work > 0
                    ? `; what they did on ${c.removed_with_work === 1 ? 'it stays' : `${c.removed_with_work} of them stays`} on version ${data.from.number} as history.`
                    : '.'}
                </li>
              )}
              <li>
                Lessons: {c.lessons_kept + c.lessons_awaiting}{' '}
                {c.lessons_kept + c.lessons_awaiting === 1 ? 'stays' : 'stay'} complete
                {c.lessons_reopened > 0
                  ? `, ${c.lessons_reopened} ${c.lessons_reopened === 1 ? 'needs' : 'need'} doing again`
                  : ''}
                .
              </li>
            </ul>
          )}

          {(data.modules ?? []).some((m) => m.status !== 'on') && (
            <section className={page.lesson}>
              <h3>Modules</h3>
              <ul>
                {(data.modules ?? [])
                  .filter((m) => m.status === 'new')
                  .map((m) => (
                    <li key={m.id} className={page.item}>
                      <strong>{m.title}</strong>
                      <span className={page.muted}> · new in this version</span>
                      <label className={page.carry}>
                        <input
                          type="checkbox"
                          checked={!newOff.includes(m.id)}
                          onChange={() => toggleNew(m.id)}
                          aria-label={`Give them ${m.title}`}
                        />
                        Give them this module
                      </label>
                    </li>
                  ))}
                {(data.modules ?? [])
                  .filter((m) => m.status === 'off')
                  .map((m) => (
                    <li key={m.id} className={page.item}>
                      <strong>{m.title}</strong>
                      <span className={page.muted}> · stays off, as it is for them now</span>
                    </li>
                  ))}
              </ul>
            </section>
          )}

          {(data.lessons ?? []).map((lesson: MoveLesson) => {
            const shown = lesson.activities.filter((a) => a.status !== 'preserved' || a.has_work)
            return (
              <section key={lesson.id} className={page.lesson}>
                <h3>
                  {lesson.title}
                  {lesson.module && <span className={page.muted}> · {lesson.module}</span>}
                </h3>
                {lesson.outcome && <p className={page.note}>{LESSON_OUTCOME[lesson.outcome]}</p>}
                {shown.length > 0 && (
                  <ul>
                    {shown.map((a) => (
                      <li key={a.id} className={page.item}>
                        <strong>{a.title}</strong>
                        <span className={page.muted}> · {typeLabel(a.type)}</span>
                        <br />
                        {describe(a, data.from.number)}
                        {a.can_carry && (
                          <label className={page.carry}>
                            <input
                              type="checkbox"
                              checked={carry.includes(a.id)}
                              onChange={() => toggle(a.id)}
                              aria-label={`Carry over ${a.title}`}
                            />
                            Carry their results over anyway (for a small fix that doesn’t change
                            what the result means)
                          </label>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            )
          })}

          {(data.removed ?? []).length > 0 && (
            <section className={page.lesson}>
              <h3>No longer in the course</h3>
              <ul>
                {(data.removed ?? []).map((r) => (
                  <li key={r.id} className={page.item}>
                    <strong>{r.title}</strong>
                    <span className={page.muted}>
                      {' '}
                      · {typeLabel(r.type)} · {r.lesson}
                    </span>
                    <br />
                    {r.has_work
                      ? `What they did stays on version ${data.from.number} as history.`
                      : 'They hadn’t started it.'}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {(data.left_lessons ?? []).length > 0 && (
            <p className={page.muted}>
              Lessons that are no longer in the course keep their completion on version{' '}
              {data.from.number}: {(data.left_lessons ?? []).map((l) => l.title).join(', ')}.
            </p>
          )}
        </div>
      )}
      {problem && (
        <p className={styles.problem} role="alert">
          {problem}
        </p>
      )}
    </Dialog>
  )
}

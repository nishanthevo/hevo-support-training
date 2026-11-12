import { useState } from 'react'

import { Dialog } from '../../components/Dialog'
import { adminApi, type EnrollOptions, type EnrollResult, type Person } from './adminApi'
import { hasTarget, targetBody, type Target } from './targetModel'
import { EnrollTarget } from './EnrollTarget'
import { ModuleChecklist, moduleBody } from './ModuleChecklist'
import { useDebounced, useQuery } from './useQuery'
import styles from './people.module.css'
import page from './enrollment.module.css'

interface Props {
  options: EnrollOptions
  onClose: () => void
  onDone: (message: string) => void
}

const OUTCOME: Record<string, string> = {
  enrolled: 'Enrolled',
  reactivated: 'Enrolled again',
  already: 'Already enrolled',
}

/** Pick people, pick a course or program (and a mentor), enrol them. */
export function EnrollDialog({ options, onClose, onDone }: Props) {
  const [target, setTarget] = useState<Target>({ kind: 'course', id: '' })
  const [mentor, setMentor] = useState('')
  const [search, setSearch] = useState('')
  // null = every module (the default); otherwise the ticked module ids.
  const [picked, setPicked] = useState<number[] | null>(null)
  const [chosen, setChosen] = useState<Person[]>([])
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [results, setResults] = useState<EnrollResult[] | null>(null)
  const term = useDebounced(search)

  const courseId = target.kind === 'course' && target.id ? Number(target.id) : undefined
  const found = useQuery(
    () => adminApi.enrollPeople({ search: term, course: courseId }),
    `people:${term}:${courseId ?? ''}`,
  )
  const ids = new Set(chosen.map((p) => p.id))
  const modules = courseId ? (options.courses.find((c) => c.id === courseId)?.modules ?? []) : []
  const ticked = picked ?? modules.map((m) => m.id)

  function toggle(person: Person) {
    setChosen((list) =>
      list.some((p) => p.id === person.id)
        ? list.filter((p) => p.id !== person.id)
        : [...list, person],
    )
  }

  async function submit() {
    setBusy(true)
    setProblem(null)
    try {
      const reply = await adminApi.enroll({
        user_ids: chosen.map((p) => p.id),
        ...targetBody(target),
        mentor_id: mentor ? Number(mentor) : null,
        ...(target.kind === 'course' ? moduleBody(modules, ticked) : {}),
      })
      setResults(reply.results)
    } catch (err) {
      setProblem(err instanceof Error ? err.message : 'We couldn’t enroll them.')
    } finally {
      setBusy(false)
    }
  }

  if (results) {
    const failed = results.filter((r) => r.outcome === 'failed')
    const fresh = results.filter(
      (r) => r.outcome === 'enrolled' || r.outcome === 'reactivated' || r.outcome === 'program',
    )
    return (
      <Dialog
        title="Enrollment finished"
        onClose={() =>
          onDone(`Enrolled ${fresh.length} ${fresh.length === 1 ? 'person' : 'people'}.`)
        }
        actions={
          <button
            type="button"
            className="btn btn-primary"
            onClick={() =>
              onDone(`Enrolled ${fresh.length} ${fresh.length === 1 ? 'person' : 'people'}.`)
            }
          >
            Done
          </button>
        }
      >
        <ul className={page.summary}>
          {results.map((r) => (
            <li key={r.user.id} className={r.outcome === 'failed' ? page.bad : undefined}>
              <strong>{r.user.name}</strong>:{' '}
              {r.outcome === 'failed'
                ? r.detail
                : r.outcome === 'program'
                  ? (r.courses ?? [])
                      .map((c) => (c.outcome === 'skipped' ? c.detail : OUTCOME[c.outcome]))
                      .filter((v, i, all) => all.indexOf(v) === i)
                      .join('; ')
                  : OUTCOME[r.outcome]}
            </li>
          ))}
        </ul>
        {failed.length > 0 && (
          <p className={page.muted}>Those who couldn’t be enrolled are listed above.</p>
        )}
      </Dialog>
    )
  }

  return (
    <Dialog
      title="Enroll people"
      width={560}
      onClose={onClose}
      actions={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={
              busy ||
              chosen.length === 0 ||
              !hasTarget(target) ||
              (modules.length > 1 && ticked.length === 0)
            }
            onClick={() => void submit()}
          >
            {busy
              ? 'Enrolling…'
              : `Enroll ${chosen.length || ''} ${chosen.length === 1 ? 'person' : 'people'}`.replace(
                  '  ',
                  ' ',
                )}
          </button>
        </>
      }
    >
      <div className={page.stack}>
        <EnrollTarget
          options={options}
          target={target}
          onTarget={(t) => {
            setTarget(t)
            setPicked(null)
          }}
          mentor={mentor}
          onMentor={setMentor}
        />
        <ModuleChecklist modules={modules} ticked={ticked} onChange={setPicked} />
        <div className={page.stack}>
          <label className={styles.field}>
            <span>Find people</span>
            <input
              type="search"
              className="input"
              placeholder="Name, email or employee id"
              aria-label="Find people"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
          {chosen.length > 0 && (
            <ul className={page.chips} aria-label="Chosen people">
              {chosen.map((p) => (
                <li key={p.id} className={page.chip}>
                  {p.name}
                  <button type="button" aria-label={`Remove ${p.name}`} onClick={() => toggle(p)}>
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
          {found.data && (
            <ul className={page.people} aria-label="People found">
              {found.data.results.length === 0 && (
                <li>
                  <span className={page.muted} style={{ padding: 'var(--sp-3)', display: 'block' }}>
                    No learners match. Add them first under People → Users.
                  </span>
                </li>
              )}
              {found.data.results.map((p) => (
                <li key={p.id}>
                  <label aria-disabled={p.on_course || undefined}>
                    <input
                      type="checkbox"
                      checked={ids.has(p.id)}
                      disabled={p.on_course}
                      onChange={() => toggle(p)}
                    />
                    <span className={styles.person}>
                      <strong>{p.name}</strong>
                      <span className={styles.sub}>
                        {p.name !== p.email ? p.email : ''}
                        {p.on_course
                          ? `${p.name !== p.email ? ' · ' : ''}Already on this course`
                          : ''}
                      </span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>
        {problem && (
          <p className={styles.problem} role="alert">
            {problem}
          </p>
        )}
      </div>
    </Dialog>
  )
}

import { useState } from 'react'

import { Dialog } from '../../components/Dialog'
import { adminApi, type BulkReply, type EnrollOptions } from './adminApi'
import { hasTarget, targetBody, type Target } from './targetModel'
import { EnrollTarget } from './EnrollTarget'
import { ModuleChecklist, moduleBody } from './ModuleChecklist'
import styles from './people.module.css'
import page from './enrollment.module.css'

interface Props {
  options: EnrollOptions
  onClose: () => void
  onDone: (message: string) => void
}

const WILL = ['will_enroll', 'will_reactivate']

/** Paste (or upload) email addresses, see exactly what would happen to each, then confirm. */
export function BulkEnrollDialog({ options, onClose, onDone }: Props) {
  const [target, setTarget] = useState<Target>({ kind: 'course', id: '' })
  const [mentor, setMentor] = useState('')
  const [text, setText] = useState('')
  // null = every module (the default); otherwise the ticked module ids, for everyone on the list.
  const [picked, setPicked] = useState<number[] | null>(null)
  const [reply, setReply] = useState<BulkReply | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const courseId = target.kind === 'course' && target.id ? Number(target.id) : undefined
  const modules = courseId ? (options.courses.find((c) => c.id === courseId)?.modules ?? []) : []
  const ticked = picked ?? modules.map((m) => m.id)

  const body = (apply: boolean) => ({
    text,
    ...targetBody(target),
    mentor_id: mentor ? Number(mentor) : null,
    ...(target.kind === 'course' ? moduleBody(modules, ticked) : {}),
    apply,
  })

  async function run(apply: boolean) {
    setBusy(true)
    setProblem(null)
    try {
      setReply(await adminApi.bulkEnroll(body(apply)))
    } catch (err) {
      setProblem(err instanceof Error ? err.message : 'We couldn’t read that list.')
    } finally {
      setBusy(false)
    }
  }

  async function load(file: File | undefined) {
    if (!file) return
    setText(await file.text())
    setReply(null)
  }

  const actionable = reply ? reply.rows.filter((r) => WILL.includes(r.status)).length : 0
  const applied = reply?.applied === true
  const done = reply
    ? reply.rows.filter((r) => r.courses.some((c) => c.state === 'done')).length
    : 0
  const left = reply
    ? reply.rows.filter((r) => !WILL.includes(r.status) && r.status !== 'already_enrolled')
    : []

  return (
    <Dialog
      title={applied ? 'List enrolled' : 'Enroll from a list'}
      width={640}
      onClose={
        applied
          ? () => onDone(`Enrolled ${done} ${done === 1 ? 'person' : 'people'} from the list.`)
          : onClose
      }
      actions={
        applied ? (
          <button
            type="button"
            className="btn btn-primary"
            onClick={() =>
              onDone(`Enrolled ${done} ${done === 1 ? 'person' : 'people'} from the list.`)
            }
          >
            Done
          </button>
        ) : (
          <>
            <button type="button" className="btn" onClick={onClose}>
              Cancel
            </button>
            {reply ? (
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy || actionable === 0}
                onClick={() => void run(true)}
              >
                {busy
                  ? 'Enrolling…'
                  : `Enroll ${actionable} ${actionable === 1 ? 'person' : 'people'}`}
              </button>
            ) : (
              <button
                type="button"
                className="btn btn-primary"
                disabled={
                  busy ||
                  !text.trim() ||
                  !hasTarget(target) ||
                  (modules.length > 1 && ticked.length === 0)
                }
                onClick={() => void run(false)}
              >
                {busy ? 'Checking…' : 'Check the list'}
              </button>
            )}
          </>
        )
      }
    >
      <div className={page.stack}>
        {!applied && (
          <>
            <EnrollTarget
              options={options}
              target={target}
              onTarget={(t) => {
                setTarget(t)
                setPicked(null)
                setReply(null)
              }}
              mentor={mentor}
              onMentor={(m) => {
                setMentor(m)
                setReply(null)
              }}
              mentorLabel="Mentor for everyone on the list (optional)"
            />
            <ModuleChecklist
              modules={modules}
              ticked={ticked}
              legend="Modules for everyone newly enrolled"
              onChange={(ids) => {
                setPicked(ids)
                setReply(null)
              }}
            />
            <label className={styles.field}>
              <span>Email addresses</span>
              <textarea
                className={`input ${page.paste}`}
                placeholder={'ann@example.com\nbob@example.com, mentor@example.com'}
                aria-label="Email addresses"
                value={text}
                onChange={(e) => {
                  setText(e.target.value)
                  setReply(null)
                }}
              />
              <span className={page.muted}>
                One per line. To give someone a different mentor, add the mentor’s email after a
                comma. People without an account are listed, not created.
              </span>
            </label>
            <label className={styles.field}>
              <span>Or upload a CSV or text file</span>
              <input
                type="file"
                accept=".csv,.txt,text/csv,text/plain"
                aria-label="Upload a list"
                onChange={(e) => void load(e.target.files?.[0])}
              />
            </label>
          </>
        )}

        {reply && (
          <div className={page.stack}>
            <p className={page.note} role="status">
              {applied
                ? `Enrolled ${done} ${done === 1 ? 'person' : 'people'}.${left.length ? ` ${left.length} could not be enrolled — see below.` : ''}`
                : `${actionable} will be enrolled. ${reply.rows.length - actionable} won’t change.`}
            </p>
            <table className={page.report}>
              <thead>
                <tr>
                  <th scope="col">Email</th>
                  <th scope="col">Result</th>
                </tr>
              </thead>
              <tbody>
                {(applied
                  ? reply.rows.filter((r) => !r.courses.some((c) => c.state === 'done'))
                  : reply.rows
                ).map((r) => (
                  <tr key={`${r.line}:${r.email}`}>
                    <td>
                      {r.email}
                      {r.mentor_email && (
                        <span className={page.muted}> · mentor {r.mentor_email}</span>
                      )}
                    </td>
                    <td
                      className={
                        WILL.includes(r.status)
                          ? page.good
                          : r.status === 'already_enrolled'
                            ? undefined
                            : page.bad
                      }
                    >
                      {r.label}
                      {r.detail && <span className={page.muted}> — {r.detail}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {problem && (
          <p className={styles.problem} role="alert">
            {problem}
          </p>
        )}
      </div>
    </Dialog>
  )
}

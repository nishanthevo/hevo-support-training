import { useState } from 'react'

import { Dialog } from '../../components/Dialog'
import { adminApi, type EnrollmentRow, type Person } from './adminApi'
import styles from './people.module.css'
import page from './enrollment.module.css'

interface Props {
  row: EnrollmentRow
  mentors: Person[]
  onClose: () => void
  onDone: (message: string) => void
}

/** Who rates this learner's mentor-rated work on this course. Blank means the shared pool. */
export function MentorDialog({ row, mentors, onClose, onDone }: Props) {
  const [mentor, setMentor] = useState(row.mentor ? String(row.mentor.id) : '')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  async function save() {
    setBusy(true)
    setProblem(null)
    try {
      await adminApi.setMentor(row.id, mentor ? Number(mentor) : null)
      onDone(
        mentor
          ? `${row.user.name}’s mentor for ${row.course.title} is now ${mentors.find((m) => String(m.id) === mentor)?.name}.`
          : `${row.user.name}’s work on ${row.course.title} now goes to the shared pool.`,
      )
    } catch (err) {
      setProblem(err instanceof Error ? err.message : 'We couldn’t change the mentor.')
      setBusy(false)
    }
  }

  return (
    <Dialog
      title={`Mentor for ${row.user.name}`}
      onClose={onClose}
      actions={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={() => void save()}
          >
            {busy ? 'Saving…' : 'Save'}
          </button>
        </>
      }
    >
      <div className={page.stack}>
        <p className={page.note}>
          On <strong>{row.course.title}</strong>. Their waiting work is re-routed to the new mentor;
          work already rated is left alone.
        </p>
        <label className={styles.field}>
          <span>Mentor</span>
          <select
            className="input"
            aria-label="Mentor"
            value={mentor}
            onChange={(e) => setMentor(e.target.value)}
          >
            <option value="">No mentor — shared pool</option>
            {mentors
              .filter((m) => m.id !== row.user.id)
              .map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
          </select>
        </label>
        {problem && (
          <p className={styles.problem} role="alert">
            {problem}
          </p>
        )}
      </div>
    </Dialog>
  )
}

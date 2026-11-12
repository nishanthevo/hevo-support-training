import { useState } from 'react'

import { Dialog } from '../../components/Dialog'
import { adminApi, type EnrollmentRow } from './adminApi'
import styles from './people.module.css'
import page from './enrollment.module.css'

interface Props {
  row: EnrollmentRow
  onClose: () => void
  onDone: (message: string) => void
}

export function WithdrawDialog({ row, onClose, onDone }: Props) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  async function withdraw() {
    setBusy(true)
    setProblem(null)
    try {
      await adminApi.withdraw(row.id, reason)
      onDone(`${row.user.name} is withdrawn from ${row.course.title}.`)
    } catch (err) {
      setProblem(err instanceof Error ? err.message : 'We couldn’t withdraw them.')
      setBusy(false)
    }
  }

  return (
    <Dialog
      title={`Withdraw ${row.user.name}?`}
      role="alertdialog"
      width={460}
      onClose={onClose}
      actions={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-danger"
            disabled={busy}
            onClick={() => void withdraw()}
          >
            {busy ? 'Working…' : 'Withdraw'}
          </button>
        </>
      }
    >
      <div className={page.stack}>
        <p className={page.note}>
          They stop seeing <strong>{row.course.title}</strong> and can’t record anything on it.
          Everything they did is kept, and you can enroll them again later. Work still waiting for a
          mentor returns to the shared pool.
        </p>
        <label className={styles.field}>
          <span>Reason (optional)</span>
          <input
            className="input"
            maxLength={300}
            value={reason}
            aria-label="Reason"
            onChange={(e) => setReason(e.target.value)}
          />
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

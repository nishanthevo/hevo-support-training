import { useState } from 'react'

import { Dialog } from '../../components/Dialog'
import { PageState } from '../../components/PageState'
import { adminApi, type EnrollmentRow } from './adminApi'
import { ModuleChecklist } from './ModuleChecklist'
import { useQuery } from './useQuery'
import styles from './people.module.css'
import page from './enrollment.module.css'

interface Props {
  row: EnrollmentRow
  onClose: () => void
  onDone: (message: string) => void
}

/** Which modules of the course this learner gets. */
export function ModulesDialog({ row, onClose, onDone }: Props) {
  const loaded = useQuery(() => adminApi.enrollmentModules(row.id), `modules:${row.id}`)
  // null until they tick something: the checklist then shows what the server says is on.
  const [picked, setPicked] = useState<number[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const modules = loaded.data?.modules ?? []
  const was = modules.filter((m) => m.enabled).map((m) => m.id)
  const ticked = picked ?? was
  const unchanged = ticked.length === was.length && was.every((id) => ticked.includes(id))

  async function save() {
    setBusy(true)
    setProblem(null)
    try {
      const updated = await adminApi.setModules(row.id, ticked)
      onDone(
        `${row.user.name} now has ${updated.modules.enabled} of ${updated.modules.total} modules of ${row.course.title}.`,
      )
    } catch (err) {
      setProblem(err instanceof Error ? err.message : 'We couldn’t change the modules.')
      setBusy(false)
    }
  }

  return (
    <Dialog
      title={`Modules for ${row.user.name}`}
      width={520}
      onClose={onClose}
      actions={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy || !loaded.data || ticked.length === 0 || unchanged}
            onClick={() => void save()}
          >
            {busy ? 'Saving…' : 'Save'}
          </button>
        </>
      }
    >
      {loaded.loading ? (
        <PageState kind="loading" />
      ) : loaded.error || !loaded.data ? (
        <PageState kind="error" title="Couldn’t load the modules" onRetry={loaded.reload}>
          {loaded.error}
        </PageState>
      ) : (
        <div className={page.stack}>
          <p className={page.note}>
            On <strong>{row.course.title}</strong>. A module that is off is hidden from them and
            they can’t open it. What they already did in it is kept and not counted, and comes back
            if you switch it on again.
          </p>
          <ModuleChecklist modules={modules} ticked={ticked} onChange={setPicked} />
          {problem && (
            <p className={styles.problem} role="alert">
              {problem}
            </p>
          )}
        </div>
      )}
    </Dialog>
  )
}

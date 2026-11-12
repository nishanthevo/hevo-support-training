import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { PageState } from '../../components/PageState'
import { ApiError } from '../../lib/api'
import { AdminPage } from './AdminPage'
import { adminApi, type QuizReviewQuestion, type QuizReviewThread } from './adminApi'
import { formatDateTime, untilText } from './format'
import { ReassignDialog } from './RatingPage'
import { useQuery } from './useQuery'
import styles from './ratings.module.css'

/** Mark the written answers of one quiz attempt: each written question gets whole marks from 0 up
 * to what it is worth, and an optional comment. The learner's choice answers were already scored. */
export function QuizReviewPage() {
  const { reviewId } = useParams()
  return <Workspace key={reviewId} id={reviewId ?? ''} />
}

function Workspace({ id }: { id: string }) {
  const load = useQuery(() => adminApi.quizReviewThread(id), id)
  const [thread, setThread] = useState<QuizReviewThread | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const data = thread ?? load.data

  if (load.loading) return <PageState kind="loading" />
  if (load.error || !data)
    return (
      <PageState kind="error" title="Couldn’t open this attempt" onRetry={load.reload}>
        {load.errorStatus === 404 ? 'It doesn’t exist, or it isn’t yours to see.' : load.error}
      </PageState>
    )
  return (
    <Review
      t={data}
      note={note}
      onThread={(next, message) => {
        setThread(next)
        if (message) setNote(message)
      }}
    />
  )
}

function whoHasIt(t: QuizReviewThread): string {
  const r = t.routing
  if (t.state !== 'submitted') return ''
  if (r.claimed_by)
    return r.claimed_by_me ? 'You are marking this.' : `${r.claimed_by} is marking this.`
  if (r.escalated)
    return `The review is overdue, so any mentor may take it${r.assigned_mentor ? ` (it was with ${r.assigned_mentor})` : ''}.`
  if (r.route === 'pool') return 'In the shared pool: any mentor may take it.'
  if (r.route === 'mine') return 'Assigned to you.'
  return r.assigned_mentor ? `With ${r.assigned_mentor}.` : ''
}

function Review({
  t,
  note,
  onThread,
}: {
  t: QuizReviewThread
  note: string | null
  onThread: (next: QuizReviewThread, message?: string) => void
}) {
  const open = t.state === 'submitted'
  const due = untilText(t.routing.review_due_at)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function run(task: () => Promise<QuizReviewThread>, message?: string) {
    setBusy(true)
    setProblem(null)
    try {
      onThread(await task(), message)
    } catch (err) {
      setProblem(
        err instanceof ApiError && err.message
          ? err.message
          : 'That didn’t work. Please try again.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <AdminPage
      eyebrow="Review"
      title={`${t.activity.title}: attempt ${t.attempt_no}`}
      hint={
        <>
          {t.learner.name} · {t.course} · <Link to="/admin/ratings">Back to the queue</Link>
        </>
      }
    >
      {note && (
        <p role="status" className={styles.note}>
          {note}
        </p>
      )}
      {problem && (
        <p className={styles.problem} role="alert">
          {problem}
        </p>
      )}
      <div className={styles.page}>
        <div className={styles.col}>
          {open && (
            <p className={styles.banner} role="status">
              {whoHasIt(t)} Review{' '}
              <span className={due.overdue ? styles.overdue : undefined}>
                {due.overdue ? due.text : `due ${due.text}`}
              </span>{' '}
              ({formatDateTime(t.routing.review_due_at)}).
            </p>
          )}
          <section className={styles.section} aria-label="The answers">
            <h2>The answers</h2>
            <p className={styles.hint}>
              The choice questions were scored automatically ({t.auto.earned} of {t.auto.possible}).
              Mark the written ones below.
            </p>
            <ol className={styles.thread}>
              {t.questions.map((q) => (
                <li key={q.index}>
                  <Question q={q} />
                </li>
              ))}
            </ol>
          </section>
          {t.attempts.length > 0 && (
            <section className={styles.section} aria-label="Earlier attempts">
              <h2>Earlier attempts</h2>
              <ul className={styles.criterion}>
                {t.attempts.map((a) => (
                  <li key={a.number}>
                    Attempt {a.number}: {Math.round(a.score * 100)}% ·{' '}
                    {a.passed ? 'passed' : 'not passed'} · {formatDateTime(a.submitted_at)}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>

        <aside className={styles.col} aria-label="Marking">
          {open && t.access.can_rate ? (
            <MarkForm t={t} busy={busy} run={run} />
          ) : (
            <div className={styles.form}>
              <p>{open ? t.access.reason : outcome(t)}</p>
            </div>
          )}
          {open && <Manage t={t} busy={busy} run={run} />}
        </aside>
      </div>
    </AdminPage>
  )
}

function outcome(t: QuizReviewThread): string {
  const r = t.result
  if (!r) return 'This attempt has been marked.'
  const score = r.score == null ? '' : ` (${Math.round(r.score * 100)}%)`
  return `Marked by ${r.marked_by ?? 'someone'}${r.marked_at ? ` on ${formatDateTime(r.marked_at)}` : ''}: ${
    r.passed ? 'passed' : 'not passed'
  }${score}.${r.comment ? ` “${r.comment}”` : ''}`
}

function Question({ q }: { q: QuizReviewQuestion }) {
  if (q.kind === 'choice') {
    const right = q.correct === true
    return (
      <div className={styles.answerBox}>
        <p className={styles.prose}>
          <strong>{q.index + 1}.</strong> {q.text}
        </p>
        <ul>
          {(q.choices ?? []).map((choice, c) => (
            <li key={c}>
              {(q.picked ?? []).includes(c) ? '● ' : '○ '}
              {choice}
            </li>
          ))}
        </ul>
        <p className={styles.hint}>
          {right ? 'Correct (scored automatically).' : 'Not correct (scored automatically).'}
        </p>
      </div>
    )
  }
  return (
    <div className={styles.answerBox}>
      <p className={styles.prose}>
        <strong>{q.index + 1}.</strong> {q.text}{' '}
        <span className={styles.hint}>
          Worth {q.max_marks} {q.max_marks === 1 ? 'mark' : 'marks'}
        </span>
      </p>
      <p className={styles.hint}>The learner’s answer</p>
      <p className={styles.answerText}>{q.answer}</p>
      {q.reference_answer && (
        <>
          <p className={styles.hint}>A good answer (for markers only)</p>
          <p className={styles.refText}>{q.reference_answer}</p>
        </>
      )}
      {q.key_points && (
        <>
          <p className={styles.hint}>Key points</p>
          <p className={styles.refText}>{q.key_points}</p>
        </>
      )}
      {q.marks != null && (
        <p className={styles.prose}>
          Marked: <strong>{q.marks}</strong> of {q.max_marks}
          {q.comment ? `. “${q.comment}”` : ''}
        </p>
      )}
    </div>
  )
}

function MarkForm({
  t,
  busy,
  run,
}: {
  t: QuizReviewThread
  busy: boolean
  run: (task: () => Promise<QuizReviewThread>, message?: string) => Promise<void>
}) {
  const written = t.questions.filter((q) => q.kind === 'written')
  const [marks, setMarks] = useState<Record<number, string>>({})
  const [comments, setComments] = useState<Record<number, string>>({})
  const [overall, setOverall] = useState('')

  const parsed = written.map((q) => {
    const raw = marks[q.index]
    const n = raw === undefined || raw.trim() === '' ? null : Number(raw)
    const ok = n !== null && Number.isInteger(n) && n >= 0 && n <= (q.max_marks ?? 1)
    return { q, n, ok }
  })
  const complete = parsed.every((p) => p.ok)
  const given = parsed.reduce((sum, p) => sum + (p.ok && p.n !== null ? p.n : 0), 0)
  const total = t.auto.earned + given
  const wouldPass = complete && total >= t.points_needed

  function submit() {
    void run(
      () =>
        adminApi.rateQuizReview(t.id, {
          marks: Object.fromEntries(parsed.map((p) => [String(p.q.index), p.n as number])),
          comments: Object.fromEntries(
            Object.entries(comments)
              .filter(([, text]) => text.trim())
              .map(([index, text]) => [index, text.trim()]),
          ),
          comment: overall.trim(),
        }),
      'Marks saved. The learner can see their result now.',
    )
  }

  return (
    <div className={styles.form}>
      <h2>Marks</h2>
      {written.map((q) => {
        const p = parsed.find((x) => x.q.index === q.index)
        return (
          <div key={q.index} className={styles.field}>
            <label htmlFor={`marks-${q.index}`}>
              Question {q.index + 1}: marks out of {q.max_marks}
            </label>
            <input
              id={`marks-${q.index}`}
              type="number"
              min={0}
              max={q.max_marks}
              step={1}
              className="input"
              value={marks[q.index] ?? ''}
              aria-invalid={marks[q.index] !== undefined && p && !p.ok ? true : undefined}
              onChange={(e) => setMarks((m) => ({ ...m, [q.index]: e.target.value }))}
            />
            <label htmlFor={`comment-${q.index}`}>
              Comment on question {q.index + 1} (optional)
            </label>
            <textarea
              id={`comment-${q.index}`}
              className="input"
              rows={3}
              value={comments[q.index] ?? ''}
              onChange={(e) => setComments((c) => ({ ...c, [q.index]: e.target.value }))}
            />
          </div>
        )
      })}
      <label htmlFor="overall" className={styles.field}>
        Overall comment (optional)
        <textarea
          id="overall"
          className="input"
          rows={3}
          value={overall}
          onChange={(e) => setOverall(e.target.value)}
        />
      </label>
      <p className={styles.hint} role="status">
        {complete
          ? `Choice questions ${t.auto.earned} + your marks ${given} = ${total} of ${t.points_possible}. It needs ${t.points_needed} to pass, so this would ${wouldPass ? 'pass' : 'not pass'}.`
          : `Give every written question whole marks from 0 to its maximum. It needs ${t.points_needed} of ${t.points_possible} to pass.`}
      </p>
      <button
        type="button"
        className="btn btn-primary"
        disabled={busy || !complete}
        onClick={submit}
      >
        {busy ? 'Saving…' : 'Submit marks'}
      </button>
    </div>
  )
}

function Manage({
  t,
  busy,
  run,
}: {
  t: QuizReviewThread
  busy: boolean
  run: (task: () => Promise<QuizReviewThread>, message?: string) => Promise<void>
}) {
  const [reassigning, setReassigning] = useState(false)
  const holder = t.routing.claimed_by
  const canTakeOver = t.access.can_manage && holder && !t.routing.claimed_by_me
  const canClaim = t.access.can_rate && !holder
  return (
    <div className={styles.actionRow}>
      {canClaim && (
        <button
          type="button"
          className="btn btn-sm"
          disabled={busy}
          onClick={() => void run(() => adminApi.claimQuizReview(t.id), 'You are marking this.')}
        >
          Claim it
        </button>
      )}
      {t.routing.claimed_by_me && (
        <button
          type="button"
          className="btn btn-sm"
          disabled={busy}
          onClick={() => void run(() => adminApi.releaseQuizReview(t.id), 'Released.')}
        >
          Release it
        </button>
      )}
      {canTakeOver && (
        <button
          type="button"
          className="btn btn-sm"
          disabled={busy}
          onClick={() => void run(() => adminApi.claimQuizReview(t.id), 'You have taken it over.')}
        >
          Take it over
        </button>
      )}
      {t.access.can_manage && (
        <button
          type="button"
          className="btn btn-sm"
          disabled={busy}
          onClick={() => setReassigning(true)}
        >
          Reassign…
        </button>
      )}
      {reassigning && (
        <ReassignDialog
          t={t}
          onClose={() => setReassigning(false)}
          onPick={(id) => {
            setReassigning(false)
            void run(() => adminApi.reassignQuizReview(t.id, id), 'Reassigned.')
          }}
        />
      )}
    </div>
  )
}

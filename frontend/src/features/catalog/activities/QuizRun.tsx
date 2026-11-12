import { useEffect, useRef, useState } from 'react'

import type { Answer } from '../../../lib/api'
import { pluralize } from '../treeUtils'
import styles from './QuizActivity.module.css'
import { isAnswered, MAX_WRITTEN_CHARS, type Question } from './quizTypes'

interface Props {
  activityId: number
  questions: Question[]
  picks: Answer[]
  onPick: (question: number, choice: number, multiple: boolean) => void
  /** Typing in a written question. */
  onWrite: (question: number, text: string) => void
  /** Sends the answers; the parent shows the result. */
  onSubmit: () => void
  busy: boolean
  canRecord: boolean
  preview: boolean
  problem: string | null
}

/** One question at a time: answer, Next, … then Submit on the last one.
 * Going Back keeps every answer. */
export function QuizRun({
  activityId,
  questions,
  picks,
  onPick,
  onWrite,
  onSubmit,
  busy,
  canRecord,
  preview,
  problem,
}: Props) {
  const [step, setStep] = useState(0)
  const promptRef = useRef<HTMLLegendElement>(null)
  const moved = useRef(false)

  // After Next/Back, bring the new question to a screen-reader's attention.
  useEffect(() => {
    if (moved.current) promptRef.current?.focus()
    moved.current = true
  }, [step])

  const question = questions[step]
  const last = step === questions.length - 1
  const answered = isAnswered(picks[step])
  const everyAnswered = picks.every(isAnswered)
  const mine = Array.isArray(picks[step]) ? (picks[step] as number[]) : []
  const text = typeof picks[step] === 'string' ? (picks[step] as string) : ''
  const hasWritten = questions.some((q) => q.written)

  return (
    <div className={styles.run}>
      <p className={styles.step}>
        Question {step + 1} of {questions.length}
        <span className={styles.track} aria-hidden="true">
          <span
            className={styles.trackFill}
            style={{ width: `${((step + 1) / questions.length) * 100}%` }}
          />
        </span>
      </p>

      <fieldset className={styles.question} disabled={busy}>
        <legend ref={promptRef} tabIndex={-1} className={styles.prompt}>
          {question.text}
        </legend>
        {question.written ? (
          <>
            <p className={styles.hint}>
              Write your answer. A mentor or trainer will mark it
              {question.maxMarks > 1 ? ` (worth ${question.maxMarks} marks)` : ''}.
            </p>
            <textarea
              className={`input ${styles.written}`}
              aria-label={`Your answer to question ${step + 1}`}
              value={text}
              maxLength={MAX_WRITTEN_CHARS}
              rows={7}
              onChange={(e) => onWrite(step, e.target.value)}
            />
            <p className={styles.counter}>
              {text.length.toLocaleString()} of {MAX_WRITTEN_CHARS.toLocaleString()} characters
            </p>
          </>
        ) : (
          <>
            {question.multiple && <p className={styles.hint}>Select all that apply.</p>}
            <ul className={styles.choices}>
              {question.choices.map((choice, c) => (
                <li key={c}>
                  <label className={styles.choice} data-picked={mine.includes(c) || undefined}>
                    <input
                      type={question.multiple ? 'checkbox' : 'radio'}
                      name={`quiz-${activityId}-${step}`}
                      checked={mine.includes(c)}
                      onChange={() => onPick(step, c, question.multiple)}
                    />
                    <span>{choice}</span>
                  </label>
                </li>
              ))}
            </ul>
          </>
        )}
      </fieldset>

      {problem && (
        <p role="alert" className={styles.problem}>
          {problem}
        </p>
      )}

      <div className={styles.footer}>
        {step > 0 && (
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={busy}
            onClick={() => setStep(step - 1)}
          >
            Back
          </button>
        )}
        {last ? (
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={!everyAnswered || busy || !canRecord}
            onClick={onSubmit}
          >
            Submit answers
          </button>
        ) : (
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={!answered}
            onClick={() => setStep(step + 1)}
          >
            Next
          </button>
        )}
        {preview && last && (
          <span className={styles.progress}>Answers aren’t scored in preview.</span>
        )}
        {!preview && last && everyAnswered && hasWritten && (
          <span className={styles.progress}>
            Your written answers are marked by a person, so your final score comes later.
          </span>
        )}
        {!preview && last && !everyAnswered && (
          <span className={styles.progress}>
            {pluralize(picks.filter((p) => !isAnswered(p)).length, 'question')} left unanswered
          </span>
        )}
      </div>
    </div>
  )
}

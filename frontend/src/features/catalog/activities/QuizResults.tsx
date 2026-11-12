import { useEffect, useRef, useState } from 'react'

import { Icon } from '../../../components/Icon'
import type { Answer, AttemptResult, QuestionReview } from '../../../lib/api'
import { pluralize } from '../treeUtils'
import styles from './QuizActivity.module.css'
import { percent, shortDate, type Question } from './quizTypes'

interface Props {
  result: AttemptResult
  questions: Question[]
  passMark: number
  /** Can the learner start another try right now? */
  canTryAgain: boolean
  /** Best score across every attempt (for the "out of attempts" line). */
  bestScore: number
  onTryAgain: () => void
}

/** How it went — percentage, what's needed, attempts left — with every
 * attempt listed and the questions reviewable. The answer key shows only
 * when the server says it may (passed, or out of attempts). */
export function QuizResults({
  result,
  questions,
  passMark,
  canTryAgain,
  bestScore,
  onTryAgain,
}: Props) {
  const [reviewing, setReviewing] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => ref.current?.focus(), [])

  const outOfAttempts = !result.passed && result.attempts_left === 0
  const hasWritten = questions.some((q) => q.written)
  const waiting = result.pending === true
  const unit = hasWritten ? 'points' : 'correct'

  return (
    <div className={styles.results}>
      <div ref={ref} tabIndex={-1} role="status" className={styles.result}>
        {waiting || result.score == null ? (
          <>
            <p className={styles.verdict}>Awaiting review</p>
            <p className={styles.note}>
              Your choice answers scored {result.correct} of {result.total}. Your written answers
              are being marked by a mentor or trainer, so your final score and result aren’t ready
              yet. You can carry on with the course; you’ll see your marks here once they’re in.
            </p>
          </>
        ) : (
          <>
            <p className={styles.verdict}>
              {result.passed ? 'Passed' : 'Not passed'}
              <span className={styles.big}> · {percent(result.score)}</span>
            </p>
            <p className={styles.note}>
              {result.correct} of {result.total} {unit}.{' '}
              {result.passed
                ? `Pass mark ${percent(passMark)}.`
                : outOfAttempts
                  ? `You’ve used all your attempts — your best score was ${percent(bestScore)}. You can continue with the lesson.`
                  : `${
                      result.needed != null
                        ? `You need ${result.needed} of ${result.total} to pass.`
                        : `Pass mark ${percent(passMark)}.`
                    }${
                      result.attempts_left === null
                        ? ''
                        : ` ${pluralize(result.attempts_left, 'attempt')} left.`
                    }`}
            </p>
          </>
        )}
      </div>

      <div className={styles.footer}>
        {canTryAgain && (
          <button type="button" className="btn btn-primary btn-sm" onClick={onTryAgain}>
            Try again
          </button>
        )}
        <button
          type="button"
          className="btn btn-sm"
          aria-expanded={reviewing}
          onClick={() => setReviewing((r) => !r)}
        >
          {reviewing ? 'Hide answers' : 'Review answers'}
        </button>
      </div>

      {result.history.length > 0 && (
        <section aria-label="Your attempts">
          <h5 className={styles.subhead}>Your attempts</h5>
          <ol className={styles.history}>
            {result.history.map((a) => (
              <li key={a.number} className={styles.attempt}>
                <span>Attempt {a.number}</span>
                <span className={styles.attemptScore}>{percent(a.score ?? 0)}</span>
                <span className={a.passed ? styles.right : styles.muted}>
                  {a.passed ? 'Passed' : 'Not passed'}
                </span>
                <span className={styles.date}>{shortDate(a.submitted_at)}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {reviewing && (
        <ol className={styles.questions}>
          {questions.map((question, q) => {
            const verdict = result.review[q]
            const answer = result.picks[q]
            if (question.written) {
              return (
                <li key={q}>
                  <WrittenReview
                    number={q + 1}
                    question={question}
                    verdict={verdict}
                    text={answer}
                  />
                </li>
              )
            }
            const mine = Array.isArray(answer) ? answer : []
            return (
              <li key={q}>
                <fieldset className={styles.question} disabled>
                  <legend className={styles.prompt}>
                    <span className={styles.number}>{q + 1}.</span> {question.text}
                  </legend>
                  {question.multiple && <p className={styles.hint}>Select all that apply.</p>}
                  <ul className={styles.choices}>
                    {question.choices.map((choice, c) => {
                      const isKey = verdict?.correct_answers?.includes(c) ?? false
                      return (
                        <li key={c}>
                          <label
                            className={`${styles.choice} ${isKey ? styles.key : ''}`}
                            data-picked={mine.includes(c) || undefined}
                          >
                            <input
                              type={question.multiple ? 'checkbox' : 'radio'}
                              name={`review-${q}`}
                              checked={mine.includes(c)}
                              readOnly
                            />
                            <span>{choice}</span>
                            {isKey && <span className={styles.tag}>Correct answer</span>}
                          </label>
                        </li>
                      )
                    })}
                  </ul>
                </fieldset>
                {verdict && verdict.correct !== undefined && (
                  <p
                    className={`${styles.verdictLine} ${verdict.correct ? styles.right : styles.wrong}`}
                  >
                    <Icon name={verdict.correct ? 'check' : 'close'} size={14} strokeWidth={2} />
                    {verdict.correct ? 'Correct' : 'Not quite'}
                  </p>
                )}
                {verdict?.explanation && (
                  <p className={styles.explanation}>{verdict.explanation}</p>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}

/** One written question in the review: what was asked, what the learner wrote, and, once a
 * person has marked it, the marks and comment. The reference answer shows only if the server
 * sent it (the quiz is finished). */
function WrittenReview({
  number,
  question,
  verdict,
  text,
}: {
  number: number
  question: Question
  verdict: QuestionReview | undefined
  text: Answer | undefined
}) {
  const mine = typeof text === 'string' ? text : (verdict?.answer ?? '')
  const marked = verdict != null && verdict.marks != null
  return (
    <div className={styles.writtenReview}>
      <p className={styles.prompt}>
        <span className={styles.number}>{number}.</span> {question.text}
      </p>
      <p className={styles.yourAnswerLabel}>Your answer</p>
      <p className={styles.yourAnswer}>{mine}</p>
      {marked ? (
        <p className={`${styles.verdictLine} ${styles.right}`}>
          <Icon name="check" size={14} strokeWidth={2} />
          {verdict.marks} of {verdict.max ?? question.maxMarks}{' '}
          {(verdict.max ?? question.maxMarks) === 1 ? 'mark' : 'marks'}
        </p>
      ) : (
        <p className={styles.muted}>Waiting to be marked.</p>
      )}
      {verdict?.comment && <p className={styles.explanation}>{verdict.comment}</p>}
      {verdict?.reference_answer && (
        <div className={styles.reference}>
          <p className={styles.yourAnswerLabel}>A good answer</p>
          <p className={styles.yourAnswer}>{verdict.reference_answer}</p>
        </div>
      )}
      {verdict?.explanation && <p className={styles.explanation}>{verdict.explanation}</p>}
    </div>
  )
}

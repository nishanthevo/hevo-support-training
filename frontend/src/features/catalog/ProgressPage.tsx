import { Link } from 'react-router-dom'

import { PageState } from '../../components/PageState'
import { ProgressBar } from '../../components/ProgressBar'
import {
  progressApi,
  type ProgressCourse,
  type ProgressQuiz,
  type ProgressRated,
  type ProgressTicket,
} from '../../lib/api'
import { percent, shortDate } from './activities/quizTypes'
import styles from './ProgressPage.module.css'
import { pluralize } from './treeUtils'
import { useAsync } from './useAsync'

const QUIZ_STATUS: Record<ProgressQuiz['status'], string> = {
  not_started: 'Not started',
  in_progress: 'In progress',
  passed: 'Passed',
  not_passed: 'Not passed',
  awaiting_review: 'Awaiting review',
}

const RATED_STATUS: Record<string, string> = {
  not_submitted: 'Not handed in',
  waiting: 'Waiting for a mentor',
  changes_requested: 'Changes requested',
  passed: 'Passed',
  waived: 'Waived',
  not_passed: 'Closed, not passed',
}

const outOfFive = (n: number) => `${Number(n.toFixed(2))} / 5`

/** Your own results across the courses you are enrolled on: just the facts the platform records
 * about you. There is deliberately no overall score, pass/fail for a course or ranking here. */
export function ProgressPage() {
  const { data, loading, error, reload } = useAsync(() => progressApi.summary(), 'progress-summary')

  if (loading) return <PageState kind="loading" />
  if (error || !data) {
    return (
      <PageState kind="error" title="Couldn’t load your progress" onRetry={reload}>
        {error}
      </PageState>
    )
  }
  if (data.courses.length === 0) {
    return (
      <PageState kind="empty" title="You aren’t enrolled in a course yet">
        Ask your trainer to enroll you. Your progress will appear here.
      </PageState>
    )
  }

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <p className="eyebrow">Learning</p>
        <h1 className={styles.heading}>My progress</h1>
        <p className={styles.lede}>Where you are on each course, and how your work has gone.</p>
      </header>
      {data.courses.map((course) => (
        <CourseSection key={course.version_id} course={course} />
      ))}
    </div>
  )
}

function CourseSection({ course }: { course: ProgressCourse }) {
  const base = `/courses/${course.course_id}`
  const lessonLink = (id: number) => `${base}/lessons/${id}`
  const complete = course.lessons_total > 0 && course.lessons_done === course.lessons_total
  const headingId = `course-${course.version_id}`

  return (
    <section className={styles.course} aria-labelledby={headingId}>
      <h2 id={headingId} className={styles.courseTitle}>
        <Link to={base}>{course.title}</Link>
      </h2>
      <div className={styles.overview}>
        <ProgressBar
          value={course.lessons_done}
          max={course.lessons_total}
          label={`${course.title} progress`}
        />
        <p className={styles.counts}>
          {course.lessons_done} of {pluralize(course.lessons_total, 'lesson')} complete
          {course.lessons_awaiting_review > 0 &&
            ` · ${course.lessons_awaiting_review} waiting for marks or a rating`}
        </p>
        <p className={styles.next}>
          {complete ? (
            'All lessons complete.'
          ) : course.next_lesson ? (
            <>
              Next: <Link to={lessonLink(course.next_lesson.id)}>{course.next_lesson.title}</Link>
            </>
          ) : (
            'Nothing left to start right now.'
          )}
        </p>
      </div>

      {course.quizzes.length > 0 && (
        <Table label={`Quizzes in ${course.title}`} heading="Quizzes">
          <thead>
            <tr>
              <th scope="col">Quiz</th>
              <th scope="col">Status</th>
              <th scope="col" className={styles.num}>
                Attempts
              </th>
              <th scope="col" className={styles.num}>
                Best
              </th>
              <th scope="col" className={styles.num}>
                Latest
              </th>
            </tr>
          </thead>
          <tbody>
            {course.quizzes.map((q) => (
              <tr key={q.activity_id}>
                <th scope="row">
                  <Link to={lessonLink(q.lesson_id)}>{q.title}</Link>
                  <span className={styles.sub}>{q.lesson_title}</span>
                </th>
                <td>{QUIZ_STATUS[q.status]}</td>
                <td className={styles.num}>
                  {q.attempts_used}
                  {q.max_attempts > 0 ? ` of ${q.max_attempts}` : ''}
                </td>
                <td className={styles.num}>{q.best == null ? '–' : percent(q.best)}</td>
                <td className={styles.num}>{q.latest == null ? '–' : percent(q.latest)}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      {course.practice.length > 0 && (
        <Table label={`Practice tickets in ${course.title}`} heading="Practice tickets">
          <thead>
            <tr>
              <th scope="col">Practice</th>
              <th scope="col" className={styles.num}>
                Finished
              </th>
              <th scope="col" className={styles.num}>
                Average
              </th>
              <th scope="col">Recent</th>
            </tr>
          </thead>
          <tbody>
            {course.practice.map((t) => (
              <TicketRow key={t.activity_id} t={t} to={lessonLink(t.lesson_id)} />
            ))}
          </tbody>
        </Table>
      )}

      {course.rated.length > 0 && (
        <Table label={`Mentor-rated work in ${course.title}`} heading="Mentor-rated work">
          <thead>
            <tr>
              <th scope="col">Work</th>
              <th scope="col">Status</th>
              <th scope="col" className={styles.num}>
                Score
              </th>
              <th scope="col">Feedback</th>
            </tr>
          </thead>
          <tbody>
            {course.rated.map((r) => (
              <RatedRow key={r.activity_id} r={r} to={lessonLink(r.lesson_id)} />
            ))}
          </tbody>
        </Table>
      )}
    </section>
  )
}

function Table({
  label,
  heading,
  children,
}: {
  label: string
  heading: string
  children: React.ReactNode
}) {
  return (
    <div className={styles.block}>
      <h3 className={styles.blockTitle}>{heading}</h3>
      <div className={styles.scroll}>
        <table className={styles.table} aria-label={label}>
          {children}
        </table>
      </div>
    </div>
  )
}

function TicketRow({ t, to }: { t: ProgressTicket; to: string }) {
  return (
    <tr>
      <th scope="row">
        <Link to={to}>{t.title}</Link>
        <span className={styles.sub}>
          {t.lesson_title}
          {t.in_progress ? ' · a ticket is in progress' : ''}
        </span>
      </th>
      <td className={styles.num}>{t.finished}</td>
      <td className={styles.num}>{t.average == null ? '–' : outOfFive(t.average)}</td>
      <td>
        {t.recent.length === 0
          ? '–'
          : t.recent.map((r) => `${outOfFive(r.score)} (${shortDate(r.at)})`).join(' · ')}
      </td>
    </tr>
  )
}

function RatedRow({ r, to }: { r: ProgressRated; to: string }) {
  return (
    <tr>
      <th scope="row">
        <Link to={to}>{r.title}</Link>
        <span className={styles.sub}>{r.lesson_title}</span>
      </th>
      <td>
        {RATED_STATUS[r.status] ?? r.status}
        {r.overdue && <span className={styles.sub}>past its deadline</span>}
        {r.due_at && !r.overdue && ['not_submitted', 'changes_requested'].includes(r.status) && (
          <span className={styles.sub}>due {shortDate(r.due_at)}</span>
        )}
      </td>
      <td className={styles.num}>{r.last_score == null ? '–' : outOfFive(r.last_score)}</td>
      <td className={styles.feedback}>{r.feedback ?? '–'}</td>
    </tr>
  )
}

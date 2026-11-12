import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from './../../App'
import { AuthProvider } from '../../lib/auth'
import type { ProgressCourse } from '../../lib/api'

afterEach(() => vi.unstubAllGlobals())

const course = (over: Partial<ProgressCourse> = {}): ProgressCourse => ({
  course_id: 1,
  version_id: 10,
  title: 'Support basics',
  lessons_total: 4,
  lessons_done: 1,
  lessons_awaiting_review: 0,
  next_lesson: { id: 7, title: 'Reading a failure log' },
  quizzes: [
    {
      activity_id: 20,
      lesson_id: 3,
      lesson_title: 'Pipelines',
      title: 'Sources quiz',
      required: true,
      status: 'passed',
      attempts_used: 2,
      max_attempts: 3,
      best: 0.83,
      latest: 0.83,
    },
    {
      activity_id: 21,
      lesson_id: 3,
      lesson_title: 'Pipelines',
      title: 'Mixed quiz',
      required: true,
      status: 'awaiting_review',
      attempts_used: 1,
      max_attempts: 0,
      best: null,
      latest: null,
    },
    {
      activity_id: 22,
      lesson_id: 7,
      lesson_title: 'Failures',
      title: 'Logs quiz',
      required: false,
      status: 'not_started',
      attempts_used: 0,
      max_attempts: 3,
      best: null,
      latest: null,
    },
  ],
  practice: [
    {
      activity_id: 30,
      lesson_id: 8,
      lesson_title: 'Tickets',
      title: 'Practice ticket',
      required: true,
      finished: 3,
      average: 3.5,
      recent: [
        { at: '2026-11-10T10:00:00Z', score: 4 },
        { at: '2026-11-09T10:00:00Z', score: 3.25 },
      ],
      in_progress: true,
    },
  ],
  rated: [
    {
      activity_id: 40,
      lesson_id: 9,
      lesson_title: 'Customer reply',
      title: 'Reply to a customer',
      required: true,
      status: 'changes_requested',
      revisions: 1,
      last_score: 2.5,
      due_at: '2026-11-20T10:00:00Z',
      overdue: false,
      feedback: 'Name the root cause next time.',
      rated_at: '2026-11-09T10:00:00Z',
    },
  ],
  ...over,
})

function mockServer(courses: ProgressCourse[], status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const path = url.split('?')[0]
      const reply = (code: number, body: unknown) => ({
        ok: code >= 200 && code < 300,
        status: code,
        statusText: 'x',
        json: async () => body ?? {},
      })
      if (path.includes('/auth/session/')) {
        return reply(200, {
          authenticated: true,
          user: {
            id: 1,
            username: 'l',
            email: 'l@e.com',
            employee_id: null,
            must_change_password: false,
            roles: ['learner'],
            capabilities: [],
          },
        })
      }
      if (path === '/api/v1/progress/summary/') {
        return status === 200 ? reply(200, { courses }) : reply(status, { detail: 'Nope.' })
      }
      return reply(404, { detail: 'not mocked: ' + path })
    }),
  )
}

function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('My progress', () => {
  it('is a tab next to My courses, and opens the page', async () => {
    mockServer([course()])
    renderAt('/')
    const link = await screen.findByRole('link', { name: 'My progress' })
    expect(link).toHaveAttribute('href', '/progress')
    fireEvent.click(link)
    expect(await screen.findByRole('heading', { name: 'My progress' })).toBeInTheDocument()
  })

  it('shows lessons done of total, what is next, and links to it', async () => {
    mockServer([course()])
    renderAt('/progress')
    const section = (await screen.findByRole('region', { name: 'Support basics' })) as HTMLElement
    expect(within(section).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '1')
    expect(section).toHaveTextContent('1 of 4 lessons complete')
    expect(within(section).getByRole('link', { name: 'Reading a failure log' })).toHaveAttribute(
      'href',
      '/courses/1/lessons/7',
    )
    expect(within(section).getByRole('link', { name: 'Support basics' })).toHaveAttribute(
      'href',
      '/courses/1',
    )
  })

  it('says when lessons wait for marks, and when everything is complete', async () => {
    mockServer([
      course({ lessons_awaiting_review: 2 }),
      course({
        course_id: 2,
        version_id: 11,
        title: 'Billing',
        lessons_total: 2,
        lessons_done: 2,
        next_lesson: null,
        quizzes: [],
        practice: [],
        rated: [],
      }),
    ])
    renderAt('/progress')
    expect(await screen.findByText(/2 waiting for marks or a rating/)).toBeInTheDocument()
    const billing = screen.getByRole('region', { name: 'Billing' })
    expect(billing).toHaveTextContent('All lessons complete.')
    // A course with nothing scored has no empty tables.
    expect(within(billing).queryByRole('table')).not.toBeInTheDocument()
  })

  it('lists each quiz with its status, tries (of the limit) and best and latest scores', async () => {
    mockServer([course()])
    renderAt('/progress')
    const table = await screen.findByRole('table', { name: 'Quizzes in Support basics' })
    const passed = within(table).getByText('Sources quiz').closest('tr')!
    expect(passed).toHaveTextContent('Passed')
    expect(passed).toHaveTextContent('2 of 3')
    expect(passed).toHaveTextContent('83%')
    const waiting = within(table).getByText('Mixed quiz').closest('tr')!
    expect(waiting).toHaveTextContent('Awaiting review')
    expect(waiting).toHaveTextContent('1') // one try taken
    expect(waiting).not.toHaveTextContent('%') // nothing has been scored yet
    const fresh = within(table).getByText('Logs quiz').closest('tr')!
    expect(fresh).toHaveTextContent('Not started')
    expect(within(table).getByRole('link', { name: 'Sources quiz' })).toHaveAttribute(
      'href',
      '/courses/1/lessons/3',
    )
  })

  it('shows practice tickets out of 5, never as a percentage', async () => {
    mockServer([course()])
    renderAt('/progress')
    const table = await screen.findByRole('table', { name: 'Practice tickets in Support basics' })
    expect(table).toHaveTextContent('3.5 / 5')
    expect(table).toHaveTextContent('4 / 5')
    expect(table).toHaveTextContent('3.25 / 5')
    expect(table).toHaveTextContent('a ticket is in progress')
    expect(table).not.toHaveTextContent('%')
  })

  it('shows mentor-rated work with the score out of 5, the deadline and the feedback', async () => {
    mockServer([course()])
    renderAt('/progress')
    const table = await screen.findByRole('table', { name: 'Mentor-rated work in Support basics' })
    expect(table).toHaveTextContent('Changes requested')
    expect(table).toHaveTextContent('2.5 / 5')
    expect(table).toHaveTextContent('Name the root cause next time.')
    expect(table).toHaveTextContent(/due /)
    expect(table).not.toHaveTextContent('%')
  })

  it('flags work that is past its deadline', async () => {
    const base = course()
    mockServer([
      course({
        rated: [{ ...base.rated[0], status: 'not_submitted', overdue: true, feedback: null }],
      }),
    ])
    renderAt('/progress')
    const table = await screen.findByRole('table', { name: 'Mentor-rated work in Support basics' })
    expect(table).toHaveTextContent('Not handed in')
    expect(table).toHaveTextContent('past its deadline')
    expect(table).not.toHaveTextContent('due ')
  })

  it('adds no overall score, grade, rank or percentage for a course', async () => {
    mockServer([course()])
    renderAt('/progress')
    const section = await screen.findByRole('region', { name: 'Support basics' })
    for (const word of [/overall/i, /rank/i, /grade/i, /certificate/i, /leaderboard/i]) {
      expect(section).not.toHaveTextContent(word)
    }
  })

  it('says plainly when the learner is not enrolled anywhere', async () => {
    mockServer([])
    renderAt('/progress')
    expect(await screen.findByText('You aren’t enrolled in a course yet')).toBeInTheDocument()
  })

  it('offers a retry when it cannot load', async () => {
    mockServer([], 500)
    renderAt('/progress')
    expect(await screen.findByText('Couldn’t load your progress')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /try again|retry/i })).toBeInTheDocument()
  })
})

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../../App'
import { AuthProvider } from '../../lib/auth'
import type { TreeNode } from '../../lib/api'
import { blankAnswers, isAnswered, parseQuestions } from './activities/quizTypes'

afterEach(() => vi.unstubAllGlobals())

const QUIZ = 20
const QUESTIONS = [
  { text: 'Pick one', choices: ['A', 'B'], multiple: false },
  { text: 'Pick some', choices: ['X', 'Y', 'Z'], multiple: true },
  { text: 'Explain the fix', type: 'written', choices: [], multiple: false, max_marks: 4 },
]

const node = (
  id: number,
  kind: TreeNode['kind'],
  title: string,
  extra: Partial<TreeNode> = {},
  children: TreeNode[] = [],
): TreeNode => ({
  id,
  kind,
  title,
  slug: title.toLowerCase(),
  description: '',
  is_required: true,
  estimated_minutes: null,
  activities: [],
  children,
  ...extra,
})

const tree: TreeNode = node(1, 'course', 'Course', {}, [
  node(2, 'segment', 'Module', {}, [
    node(3, 'topic', 'Lesson', {
      activities: [
        {
          id: QUIZ,
          activity_type: 'mcq',
          title: 'Mixed quiz',
          is_required: true,
          points: 0,
          order: 0,
          max_attempts: 3,
          config: { questions: QUESTIONS, pass_threshold: 0.7 },
          completable: true,
        },
      ],
    }),
  ]),
])

interface Server {
  /** Is a written attempt waiting to be marked? */
  pending: boolean
  /** A marked attempt to show (state 'completed' = passed). */
  marked: boolean
  calls: { method: string; path: string; body?: unknown }[]
}

function mockServer(initial: Partial<Server> = {}): Server {
  const server: Server = { pending: false, marked: false, calls: [], ...initial }
  const reviewPending = [
    { kind: 'choice', correct: true },
    { kind: 'choice', correct: true },
    {
      kind: 'written',
      answer: 'Re-authorise and resync.',
      max: 4,
      marks: null,
      comment: '',
      pending: true,
    },
  ]
  const reviewMarked = [
    { kind: 'choice', correct: true, correct_answers: [0], explanation: 'Because.' },
    { kind: 'choice', correct: true, correct_answers: [0, 2] },
    {
      kind: 'written',
      answer: 'Re-authorise and resync.',
      max: 4,
      marks: 3,
      comment: 'Good. Mention the resync first.',
      pending: false,
      reference_answer: 'Re-authorise the source, then resync.',
    },
  ]
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = (init.method ?? 'GET').toUpperCase()
      const path = url.split('?')[0]
      server.calls.push({
        method,
        path,
        body: init.body ? JSON.parse(String(init.body)) : undefined,
      })
      const reply = (status: number, body: unknown) => ({
        ok: status >= 200 && status < 300,
        status,
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
      if (path === '/api/v1/catalog/courses/') {
        return reply(200, [
          { id: 1, course_id: 1, title: 'Course', description: '', version: 1, published_at: null },
        ])
      }
      if (path === '/api/v1/catalog/courses/1/') {
        return reply(200, {
          id: 1,
          course_id: 1,
          version: 1,
          published_at: null,
          learning_order: 'free',
          root: tree,
        })
      }
      if (path === '/api/v1/progress/courses/1/') {
        return reply(200, {
          completed_lessons: [],
          activities: server.marked
            ? { [QUIZ]: 'completed' }
            : server.pending
              ? { [QUIZ]: 'started' }
              : {},
          attempts: server.pending
            ? {
                [QUIZ]: { count: 0, best_score: 0, last_score: 0, attempts_left: 2, pending: true },
              }
            : server.marked
              ? { [QUIZ]: { count: 1, best_score: 5 / 6, last_score: 5 / 6, attempts_left: 2 } }
              : {},
          can_reset_attempts: false,
          learning_order: 'free',
          locked: {},
          next_lesson_id: 3,
        })
      }
      if (path === `/api/v1/progress/activities/${QUIZ}/attempts/` && method === 'GET') {
        if (server.pending) {
          return reply(200, {
            history: [],
            latest: {
              state: 'started',
              pending: true,
              passed: false,
              score: null,
              correct: 2,
              total: 2,
              needed: null,
              attempts_left: 2,
              picks: [[0], [0, 2], 'Re-authorise and resync.'],
              review: reviewPending,
              history: [],
            },
          })
        }
        if (server.marked) {
          const history = [
            {
              number: 1,
              score: 5 / 6,
              correct: 5,
              total: 6,
              passed: true,
              submitted_at: '2026-11-11T10:00:00Z',
            },
          ]
          return reply(200, {
            history,
            latest: {
              state: 'completed',
              passed: true,
              score: 5 / 6,
              correct: 5,
              total: 6,
              needed: null,
              attempts_left: 2,
              picks: [[0], [0, 2], 'Re-authorise and resync.'],
              review: reviewMarked,
              history,
            },
          })
        }
        return reply(200, { history: [], latest: null })
      }
      if (path === `/api/v1/progress/activities/${QUIZ}/attempts/` && method === 'POST') {
        server.pending = true
        return reply(201, {
          state: 'started',
          pending: true,
          passed: false,
          score: null,
          correct: 2,
          total: 2,
          needed: null,
          attempts_left: 2,
          picks: [[0], [0, 2], 'Re-authorise and resync.'],
          review: reviewPending,
          history: [],
        })
      }
      if (path.match(/\/progress\/activities\/\d+\/start\//))
        return reply(200, { state: 'started' })
      if (path === '/api/v1/progress/lessons/3/complete/') return reply(200, {})
      return reply(404, { detail: 'not mocked: ' + path })
    }),
  )
  return server
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

async function startQuiz() {
  const start = await screen.findByRole('button', { name: 'Start quiz' })
  await waitFor(() => expect(start).toBeEnabled())
  fireEvent.click(start)
  await screen.findByText(/Question 1 of 3/)
}

describe('parsing written questions', () => {
  it('knows a written question from a choice question', () => {
    const [one, , three] = parseQuestions({ questions: QUESTIONS })
    expect(one.written).toBe(false)
    expect(three).toMatchObject({ written: true, maxMarks: 4, choices: [] })
  })

  it('starts each kind with the right blank answer', () => {
    expect(blankAnswers(parseQuestions({ questions: QUESTIONS }))).toEqual([[], [], ''])
  })

  it('counts a written answer only when it has text', () => {
    expect(isAnswered('')).toBe(false)
    expect(isAnswered('   ')).toBe(false)
    expect(isAnswered('words')).toBe(true)
    expect(isAnswered([])).toBe(false)
    expect(isAnswered([1])).toBe(true)
  })
})

describe('taking a quiz with a written question', () => {
  it('offers a text box, says a person marks it, and sends choices and text together', async () => {
    const server = mockServer()
    renderAt('/courses/1/lessons/3')
    await startQuiz()
    fireEvent.click(screen.getByLabelText('A'))
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByText(/Question 2 of 3/)
    fireEvent.click(screen.getByLabelText('X'))
    fireEvent.click(screen.getByLabelText('Z'))
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByText(/Question 3 of 3/)

    const box = screen.getByRole('textbox', { name: 'Your answer to question 3' })
    expect(screen.getByText(/worth 4 marks/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Submit answers' })).toBeDisabled()
    fireEvent.change(box, { target: { value: '   ' } })
    expect(screen.getByRole('button', { name: 'Submit answers' })).toBeDisabled() // blanks don't count
    fireEvent.change(box, { target: { value: 'Re-authorise and resync.' } })
    expect(screen.getByText(/marked by a person/)).toBeInTheDocument()
    expect(screen.getByText(/24 of 4,000 characters/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Submit answers' }))

    await screen.findByText('Awaiting review')
    const post = server.calls.find((c) => c.method === 'POST' && c.path.endsWith('/attempts/'))
    expect(post?.body).toEqual({ answers: [[0], [0, 2], 'Re-authorise and resync.'] })
  })

  it('shows "awaiting review" with no score, and no way to try again meanwhile', async () => {
    mockServer()
    renderAt('/courses/1/lessons/3')
    await startQuiz()
    fireEvent.click(screen.getByLabelText('A'))
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByText(/Question 2 of 3/)
    fireEvent.click(screen.getByLabelText('X'))
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    fireEvent.change(await screen.findByRole('textbox'), {
      target: { value: 'Re-authorise and resync.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Submit answers' }))

    const result = await screen.findByRole('status')
    expect(result).toHaveTextContent('Awaiting review')
    expect(result).toHaveTextContent('scored 2 of 2')
    expect(result).not.toHaveTextContent('%')
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Review answers' })).toBeInTheDocument()
    // The learner's own text is there to read; no reference answer exists to show.
    fireEvent.click(screen.getByRole('button', { name: 'Review answers' }))
    expect(screen.getByText('Re-authorise and resync.')).toBeInTheDocument()
    expect(screen.getByText('Waiting to be marked.')).toBeInTheDocument()
    expect(screen.queryByText('A good answer')).not.toBeInTheDocument()
  })

  it('does not hold up the lesson while the written answers wait', async () => {
    mockServer({ pending: true })
    renderAt('/courses/1/lessons/3')
    const complete = await screen.findByRole('button', { name: 'Mark complete' })
    await waitFor(() => expect(complete).toBeEnabled())
    expect(screen.queryByText(/required activity above first/)).not.toBeInTheDocument()
  })

  it('says so on the activity row, and "Start quiz"/"Try again" are not offered', async () => {
    mockServer({ pending: true })
    renderAt('/courses/1/lessons/3')
    const row = (await screen.findByText('Mixed quiz')).closest('li')!
    await waitFor(() => expect(row).toHaveTextContent('Written answers awaiting review'))
    expect(
      within(row).queryByRole('button', { name: /Start quiz|Try again/ }),
    ).not.toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'View results' })).toBeInTheDocument()
  })

  it('shows the marks, the marker’s comment and the good answer once it is marked', async () => {
    mockServer({ marked: true })
    renderAt('/courses/1/lessons/3')
    const row = (await screen.findByText('Mixed quiz')).closest('li')!
    fireEvent.click(await within(row).findByRole('button', { name: 'View results' }))
    const result = await screen.findByRole('status')
    expect(result).toHaveTextContent('Passed')
    expect(result).toHaveTextContent('83%')
    expect(result).toHaveTextContent('5 of 6 points')
    fireEvent.click(screen.getByRole('button', { name: 'Review answers' }))
    expect(screen.getByText(/3 of 4 marks/)).toBeInTheDocument()
    expect(screen.getByText('Good. Mention the resync first.')).toBeInTheDocument()
    expect(screen.getByText('Re-authorise the source, then resync.')).toBeInTheDocument()
    expect(screen.getByText('A good answer')).toBeInTheDocument()
  })
})

import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { mockApi, page, renderAt } from './testHelpers'

afterEach(() => vi.unstubAllGlobals())

const RATE = ['manual_sim.rate_submissions']
const MANAGE = ['manual_sim.manage_reviews']
const TRAINER = ['progress.view_results', ...RATE, ...MANAGE]
const hoursFromNow = (h: number) => new Date(Date.now() + h * 3600_000).toISOString()

const simRow = {
  kind: 'sim',
  id: 7,
  learner: { id: 4, name: 'Lea Learner', email: 'lea@e.com' },
  activity: { id: 14, title: 'Customer reply' },
  course: 'Support basics',
  revision: 2,
  submitted_at: '2026-10-27T10:00:00Z',
  late: false,
  review_due_at: hoursFromNow(5),
  review_overdue: false,
  route: 'pool',
  assigned_mentor: null,
  claimed_by: null,
  claimed_by_me: false,
  needs_attention: false,
  can_rate: true,
  own: false,
}
const quizRow = {
  ...simRow,
  kind: 'quiz',
  id: 9,
  activity: { id: 20, title: 'Mixed quiz' },
  revision: 1,
}

const THREAD_URL = 'GET /api/v1/admin/ratings/quiz-reviews/9/'
const POST = (action: string) => `POST /api/v1/admin/ratings/quiz-reviews/9/${action}/`

function thread(over: Record<string, unknown> = {}) {
  return {
    kind: 'quiz',
    id: 9,
    state: 'submitted',
    attempt_no: 1,
    learner: { id: 4, name: 'Lea Learner', email: 'lea@e.com' },
    activity: { id: 20, title: 'Mixed quiz' },
    course: 'Support basics',
    pass_threshold: 0.7,
    points_possible: 6,
    points_needed: 5,
    auto: { earned: 2, possible: 2 },
    questions: [
      {
        index: 0,
        kind: 'choice',
        text: 'Pick one',
        choices: ['A', 'B'],
        picked: [0],
        correct: true,
      },
      {
        index: 1,
        kind: 'choice',
        text: 'Pick some',
        choices: ['X', 'Y', 'Z'],
        picked: [0, 2],
        correct: true,
      },
      {
        index: 2,
        kind: 'written',
        text: 'Explain the fix',
        max_marks: 4,
        answer: 'Re-authorise the source and resync it.',
        reference_answer: 'Re-authorise the source, then resync.',
        key_points: 're-authorise\nresync',
        marks: null,
        comment: '',
      },
    ],
    mentor_for_course: null,
    routing: {
      route: 'pool',
      assigned_mentor: null,
      claimed_by: null,
      claimed_by_me: false,
      review_due_at: hoursFromNow(5),
      review_overdue: false,
      escalated: false,
    },
    access: { can_rate: true, reason: '', can_manage: false },
    result: null,
    attempts: [],
    ...over,
  }
}

describe('the queue lists written quiz answers beside simulations', () => {
  it('labels each kind and links it to its own page', async () => {
    mockApi(RATE, {
      'GET /api/v1/admin/ratings/queue/': {
        ...page([simRow, quizRow]),
        tab: 'pool',
        counts: { all: 2, mine: 0, pool: 2 },
      },
    })
    renderAt('/admin/ratings')
    fireEvent.click(await screen.findByRole('tab', { name: /Shared pool/ }))
    const quiz = (await screen.findByText('Mixed quiz')).closest('tr')!
    expect(quiz).toHaveTextContent('Written quiz answers · Support basics')
    expect(quiz).toHaveTextContent('Attempt 1')
    expect(within(quiz).getByRole('link', { name: /Lea Learner/ })).toHaveAttribute(
      'href',
      '/admin/ratings/quiz/9',
    )
    const sim = screen.getByText('Customer reply').closest('tr')!
    expect(sim).not.toHaveTextContent('Written quiz answers')
    expect(within(sim).getByRole('link', { name: /Lea Learner/ })).toHaveAttribute(
      'href',
      '/admin/ratings/7',
    )
  })
})

describe('marking the written answers', () => {
  const setup = (
    over: Record<string, unknown> = {},
    caps = RATE,
    extra: Record<string, unknown> = {},
  ) => mockApi(caps, { [THREAD_URL]: thread(over), ...extra })

  it('shows every question: the choice results, the written answer and what to look for', async () => {
    setup()
    renderAt('/admin/ratings/quiz/9')
    expect(
      await screen.findByRole('heading', { name: 'Mixed quiz: attempt 1' }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/The choice questions were scored automatically \(2 of 2\)/),
    ).toBeInTheDocument()
    expect(screen.getByText('Re-authorise the source and resync it.')).toBeInTheDocument()
    expect(screen.getByText('Re-authorise the source, then resync.')).toBeInTheDocument()
    expect(screen.getByText(/re-authorise\s+resync/)).toBeInTheDocument()
    expect(screen.getByText(/Worth 4 marks/)).toBeInTheDocument()
    expect(screen.getAllByText('Correct (scored automatically).')).toHaveLength(2)
    expect(screen.getByText(/In the shared pool: any mentor may take it\./)).toBeInTheDocument()
  })

  it('gives a mark box and comment per written question, and only those', async () => {
    setup()
    renderAt('/admin/ratings/quiz/9')
    expect(await screen.findByLabelText('Question 3: marks out of 4')).toBeInTheDocument()
    expect(screen.getByLabelText('Comment on question 3 (optional)')).toBeInTheDocument()
    expect(screen.queryByLabelText(/Question 1: marks/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Submit marks' })).toBeDisabled()
  })

  it('adds up the points as marks are typed and says whether it would pass', async () => {
    setup()
    renderAt('/admin/ratings/quiz/9')
    const marks = await screen.findByLabelText('Question 3: marks out of 4')
    expect(screen.getByText(/It needs 5 of 6 to pass/)).toBeInTheDocument()
    fireEvent.change(marks, { target: { value: '3' } })
    expect(screen.getByText(/2 \+ your marks 3 = 5 of 6.*would pass/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Submit marks' })).toBeEnabled()
    fireEvent.change(marks, { target: { value: '2' } })
    expect(screen.getByText(/= 4 of 6.*would not pass/)).toBeInTheDocument()
  })

  it.each(['5', '-1', '2.5', 'abc', ''])(
    'refuses %j as a mark without calling the server',
    async (bad) => {
      const calls = setup()
      renderAt('/admin/ratings/quiz/9')
      const marks = await screen.findByLabelText('Question 3: marks out of 4')
      fireEvent.change(marks, { target: { value: bad } })
      expect(screen.getByRole('button', { name: 'Submit marks' })).toBeDisabled()
      expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0)
    },
  )

  it('sends the marks, comments and overall comment, then shows the outcome', async () => {
    const calls = setup({}, RATE, {
      [POST('rate')]: thread({
        state: 'rated',
        result: {
          marked_by: 'Mia Mentor',
          marked_at: '2026-11-11T10:00:00Z',
          score: 5 / 6,
          passed: true,
          comment: 'Well done',
        },
        questions: [
          ...thread().questions.slice(0, 2),
          { ...thread().questions[2], marks: 3, comment: 'Mention the resync first.' },
        ],
      }),
    })
    renderAt('/admin/ratings/quiz/9')
    fireEvent.change(await screen.findByLabelText('Question 3: marks out of 4'), {
      target: { value: '3' },
    })
    fireEvent.change(screen.getByLabelText('Comment on question 3 (optional)'), {
      target: { value: '  Mention the resync first.  ' },
    })
    fireEvent.change(screen.getByLabelText('Overall comment (optional)'), {
      target: { value: 'Well done' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Submit marks' }))

    expect(await screen.findByText(/Marks saved/)).toBeInTheDocument()
    const sent = calls.find((c) => c.method === 'POST' && c.path.endsWith('/rate/'))
    expect(sent?.body).toEqual({
      marks: { '2': 3 },
      comments: { '2': 'Mention the resync first.' },
      comment: 'Well done',
    })
    expect(screen.queryByRole('button', { name: 'Submit marks' })).not.toBeInTheDocument()
    expect(screen.getByText(/Marked by Mia Mentor.*passed \(83%\)/)).toBeInTheDocument()
    expect(
      screen.getByText(
        (_, el) =>
          el?.tagName === 'P' &&
          (el.textContent ?? '').startsWith('Marked: 3 of 4. “Mention the resync first.”'),
      ),
    ).toBeInTheDocument()
  })

  it('shows a refusal from the server and keeps the form', async () => {
    setup({}, RATE, {
      [POST('rate')]: { status: 409, body: { detail: 'This attempt isn’t waiting for marking.' } },
    })
    renderAt('/admin/ratings/quiz/9')
    fireEvent.change(await screen.findByLabelText('Question 3: marks out of 4'), {
      target: { value: '3' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Submit marks' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('isn’t waiting for marking')
    expect(screen.getByRole('button', { name: 'Submit marks' })).toBeEnabled()
  })

  it('explains why you can’t mark it, instead of showing a form', async () => {
    setup({
      access: { can_rate: false, reason: 'You can’t rate your own work.', can_manage: false },
    })
    renderAt('/admin/ratings/quiz/9')
    expect(await screen.findByText('You can’t rate your own work.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Submit marks' })).not.toBeInTheDocument()
  })

  it('says it doesn’t exist, or isn’t yours, on a 404', async () => {
    mockApi(RATE, { [THREAD_URL]: { status: 404, body: { detail: 'Not found.' } } })
    renderAt('/admin/ratings/quiz/9')
    expect(
      await screen.findByText('It doesn’t exist, or it isn’t yours to see.'),
    ).toBeInTheDocument()
  })
})

describe('claiming, releasing and reassigning', () => {
  it('claims it, then releases it', async () => {
    const claimed = thread({
      routing: { ...thread().routing, claimed_by: 'u@example.com', claimed_by_me: true },
    })
    const calls = mockApi(RATE, {
      [THREAD_URL]: thread(),
      [POST('claim')]: claimed,
      [POST('release')]: thread(),
    })
    renderAt('/admin/ratings/quiz/9')
    fireEvent.click(await screen.findByRole('button', { name: 'Claim it' }))
    expect(
      await screen.findByText('You are marking this.', { selector: '[role=status]' }),
    ).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: 'Release it' }))
    await waitFor(() => expect(calls.some((c) => c.path.endsWith('/release/'))).toBe(true))
    expect(await screen.findByRole('button', { name: 'Claim it' })).toBeInTheDocument()
  })

  it('lets a manager take it over from someone else', async () => {
    const calls = mockApi(TRAINER, {
      [THREAD_URL]: thread({
        routing: { ...thread().routing, claimed_by: 'Max Mentor', claimed_by_me: false },
        access: { can_rate: true, reason: '', can_manage: true },
      }),
      [POST('claim')]: thread(),
    })
    renderAt('/admin/ratings/quiz/9')
    expect(await screen.findByText(/Max Mentor is marking this/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Claim it' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Take it over' }))
    await waitFor(() => expect(calls.some((c) => c.path.endsWith('/claim/'))).toBe(true))
  })

  it('offers Reassign only to managers, and sends the choice', async () => {
    mockApi(RATE, { [THREAD_URL]: thread() })
    renderAt('/admin/ratings/quiz/9')
    await screen.findByRole('button', { name: 'Claim it' })
    expect(screen.queryByRole('button', { name: 'Reassign…' })).not.toBeInTheDocument()
  })

  it('a manager reassigns it to a mentor', async () => {
    const calls = mockApi(TRAINER, {
      [THREAD_URL]: thread({ access: { can_rate: true, reason: '', can_manage: true } }),
      'GET /api/v1/admin/ratings/mentors/': {
        results: [{ id: 9, name: 'Mia Mentor', email: 'mia@e.com' }],
      },
      [POST('reassign')]: thread({
        routing: { ...thread().routing, assigned_mentor: 'Mia Mentor' },
      }),
    })
    renderAt('/admin/ratings/quiz/9')
    fireEvent.click(await screen.findByRole('button', { name: 'Reassign…' }))
    const choose = await screen.findByLabelText('Send it to')
    await screen.findByRole('option', { name: /Mia Mentor/ })
    fireEvent.change(choose, { target: { value: '9' } })
    fireEvent.click(screen.getByRole('button', { name: 'Reassign' }))
    await waitFor(() => expect(calls.some((c) => c.path.endsWith('/reassign/'))).toBe(true))
    expect(calls.find((c) => c.path.endsWith('/reassign/'))?.body).toEqual({ mentor_id: 9 })
  })
})

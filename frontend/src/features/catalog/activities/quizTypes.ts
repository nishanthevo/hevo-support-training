import type { Answer } from '../../../lib/api'

export interface Question {
  text: string
  choices: string[]
  multiple: boolean
  /** A written question: typed text instead of choices, marked by a person. */
  written: boolean
  /** What a written question is worth. */
  maxMarks: number
}

/** The shortest a blank answer can be: nothing but spaces doesn't count as an answer. */
export const MAX_WRITTEN_CHARS = 4000

/** An empty answer for each kind of question. */
export function blankAnswers(questions: Question[]): Answer[] {
  return questions.map((q) => (q.written ? '' : []))
}

export function isAnswered(answer: Answer | undefined): boolean {
  return typeof answer === 'string' ? answer.trim().length > 0 : (answer ?? []).length > 0
}

/** The learner payload carries questions and choices only — never the key. */
export function parseQuestions(config: Record<string, unknown>): Question[] {
  const raw = Array.isArray(config.questions) ? config.questions : []
  return raw.map((q: Record<string, unknown>) => ({
    text: typeof q.text === 'string' ? q.text : '',
    choices: Array.isArray(q.choices) ? q.choices.map(String) : [],
    multiple: q.multiple === true,
    written: q.type === 'written',
    maxMarks: typeof q.max_marks === 'number' ? q.max_marks : 1,
  }))
}

export const percent = (score: number) => `${Math.round(score * 100)}%`

export const shortDate = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
  })

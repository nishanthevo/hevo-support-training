/** The quiz editor's pure edits on a question list. Each returns a new list;
 * the server's rules (what makes a quiz publishable) live in the backend. */

export interface QuizQuestion {
  text: string
  choices: string[]
  /** One correct choice (index) or several; absent until the author picks. */
  answer?: number | number[]
  explanation?: string
  /** 'written' = the learner types an answer and a person marks it. Absent = a choice question. */
  type?: 'written'
  /** Written only: what it is worth (1–10). */
  max_marks?: number
  /** Written only: what a good answer says. Only the people marking it see it, until the
   * learner has passed or used every try. */
  reference_answer?: string
  /** Written only: the points a good answer covers, one per line. Optional; markers only. */
  key_points?: string
}

export const isWritten = (q: QuizQuestion) => q.type === 'written'

export const isMultiple = (q: QuizQuestion) => !isWritten(q) && Array.isArray(q.answer)

export const newWrittenQuestion = (): QuizQuestion => ({
  type: 'written',
  text: '',
  choices: [],
  max_marks: 1,
  reference_answer: '',
})

/** Switch a question between a choice question and a written one, keeping its text and
 * explanation and dropping what only the other kind uses. */
export function setWritten(q: QuizQuestion, written: boolean): QuizQuestion {
  if (written === isWritten(q)) return q
  const keep = { text: q.text, ...(q.explanation ? { explanation: q.explanation } : {}) }
  return written
    ? { ...keep, type: 'written', choices: [], max_marks: 1, reference_answer: '' }
    : { ...keep, choices: ['', ''] }
}

export function correctChoices(q: QuizQuestion): number[] {
  if (Array.isArray(q.answer)) return q.answer
  return typeof q.answer === 'number' ? [q.answer] : []
}

export function readQuestions(config: Record<string, unknown>): QuizQuestion[] {
  return Array.isArray(config.questions) ? (config.questions as QuizQuestion[]) : []
}

export const newQuestion = (): QuizQuestion => ({ text: '', choices: ['', ''] })

export function replaceAt<T>(items: T[], index: number, next: T): T[] {
  return items.map((item, i) => (i === index ? next : item))
}

export function removeAt<T>(items: T[], index: number): T[] {
  return items.filter((_, i) => i !== index)
}

/** Mark/unmark a choice as correct. A single-answer question has exactly one. */
export function setCorrect(q: QuizQuestion, choice: number, on: boolean): QuizQuestion {
  if (!isMultiple(q)) return { ...q, answer: choice }
  const current = correctChoices(q)
  const next = on ? [...new Set([...current, choice])] : current.filter((c) => c !== choice)
  return { ...q, answer: next.sort((a, b) => a - b) }
}

/** Switch between "one correct answer" and "select all that apply", keeping
 * what the author already marked wherever possible. */
export function setMultiple(q: QuizQuestion, multiple: boolean): QuizQuestion {
  if (multiple === isMultiple(q)) return q
  const correct = correctChoices(q)
  if (multiple) return { ...q, answer: correct }
  const { answer: _dropped, ...rest } = q
  return correct.length > 0 ? { ...rest, answer: correct[0] } : rest
}

export const addChoice = (q: QuizQuestion): QuizQuestion => ({ ...q, choices: [...q.choices, ''] })

/** Remove a choice and keep the correct-answer indexes pointing at the same
 * choices. */
export function removeChoice(q: QuizQuestion, index: number): QuizQuestion {
  const remap = (c: number) => (c > index ? c - 1 : c)
  const choices = removeAt(q.choices, index)
  const correct = correctChoices(q)
    .filter((c) => c !== index)
    .map(remap)
  if (isMultiple(q)) return { ...q, choices, answer: correct }
  const { answer: _dropped, ...rest } = q
  return correct.length > 0 ? { ...rest, choices, answer: correct[0] } : { ...rest, choices }
}

/** Move an item, doing nothing if the target is off either end. */
export function moveItemSafe<T>(items: T[], from: number, to: number): T[] {
  if (to < 0 || to >= items.length) return items
  const next = items.slice()
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  return next
}

import type { IconName } from '../../components/Icon'
import type { ActivitySummary } from '../../lib/api'
import { lessonActivities, type Lesson } from './treeUtils'

/** Types that were never finished and can no longer be added. Rows already in published
 * versions still arrive from the server (inert), so they keep a name and an icon. */
const RETIRED_LABELS: Record<string, string> = {
  pdf: 'PDF',
  workshop: 'Workshop',
  external_course: 'External course',
}

export function isRetiredType(type: string): boolean {
  return type in RETIRED_LABELS
}

export const ACTIVITY_LABELS: Record<string, string> = {
  reading: 'Reading',
  video: 'Video',
  mcq: 'Quiz',
  manual_sim: 'Simulation',
  bot_sim: 'Practice ticket',
  ...RETIRED_LABELS,
}

const ICONS: Record<string, IconName> = {
  reading: 'book',
  pdf: 'file',
  video: 'play',
  mcq: 'quiz',
  workshop: 'tool',
  external_course: 'external',
  manual_sim: 'chat',
  bot_sim: 'chat',
}

export function activityIcon(type: string): IconName {
  return ICONS[type] ?? 'book'
}

/** The lesson's primary activity (its first), used for the muted type icon. */
export function primaryActivity(lesson: Lesson): ActivitySummary | null {
  return lessonActivities(lesson)[0] ?? null
}

// What a learner will *do*, grouped for the "Includes" fact.
const MIX_ORDER: { label: string; types: string[] }[] = [
  { label: 'readings', types: ['reading'] },
  { label: 'videos', types: ['video'] },
  { label: 'quizzes', types: ['mcq'] },
  { label: 'simulations', types: ['manual_sim', 'bot_sim'] },
]

/** e.g. ["readings", "videos", "quizzes", "simulations"] — only kinds present. */
export function activityMix(lessons: Lesson[]): string[] {
  const present = new Set(lessons.flatMap((l) => lessonActivities(l).map((a) => a.activity_type)))
  return MIX_ORDER.filter((g) => g.types.some((t) => present.has(t))).map((g) => g.label)
}

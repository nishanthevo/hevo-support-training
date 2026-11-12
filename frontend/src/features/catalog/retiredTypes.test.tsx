import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { ActivitySummary } from '../../lib/api'
import { ACTIVITY_LABELS, activityMix, isRetiredType } from './activityTypes'
import { ActivityRunner } from './activities/ActivityRunner'
import type { Lesson } from './treeUtils'

const activity = (type: string, title = 'Old row'): ActivitySummary => ({
  id: 1,
  activity_type: type,
  title,
  is_required: true,
  points: 0,
  order: 0,
  max_attempts: 3,
  config: {},
  completable: false,
})

describe('retired activity types (PDF, Workshop, External course)', () => {
  it.each(['pdf', 'workshop', 'external_course'])(
    'an old %s row still shows, by name, as no longer available',
    (type) => {
      render(<ActivityRunner activity={activity(type)} />)
      expect(screen.getByText('No longer available')).toBeInTheDocument()
      expect(screen.queryByText('Available soon')).not.toBeInTheDocument()
      expect(
        screen.getByText(new RegExp(`${ACTIVITY_LABELS[type]} · Required`)),
      ).toBeInTheDocument()
      expect(isRetiredType(type)).toBe(true)
    },
  )

  it('a type the server sends that we have never heard of is still a plain "Available soon"', () => {
    render(<ActivityRunner activity={activity('hologram')} />)
    expect(screen.getByText('Available soon')).toBeInTheDocument()
    expect(screen.getByText(/hologram · Required/)).toBeInTheDocument()
    expect(isRetiredType('hologram')).toBe(false)
  })

  it('the real types are not retired', () => {
    for (const type of ['reading', 'video', 'mcq', 'manual_sim', 'bot_sim']) {
      expect(isRetiredType(type)).toBe(false)
    }
  })

  it('the "Includes" summary never advertises a retired type', () => {
    const lesson = (type: string): Lesson =>
      ({
        node: { id: 1, activities: [activity(type)] },
        sections: [],
        moduleId: 1,
        moduleNumber: 1,
        position: 1,
      }) as unknown as Lesson
    expect(activityMix([lesson('reading'), lesson('workshop'), lesson('external_course')])).toEqual(
      ['readings'],
    )
  })
})

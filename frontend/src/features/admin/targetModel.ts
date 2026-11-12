export interface Target {
  kind: 'course' | 'program'
  id: string
}

/** The request body fragment for a target (`course_id` or `program_id`). */
export function targetBody(target: Target): { course_id?: number; program_id?: number } {
  const id = Number(target.id)
  return target.kind === 'course' ? { course_id: id } : { program_id: id }
}

export function hasTarget(target: Target): boolean {
  return target.id !== ''
}

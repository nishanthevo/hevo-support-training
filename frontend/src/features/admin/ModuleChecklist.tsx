import page from './enrollment.module.css'

export interface ModuleChoice {
  id: number
  title: string
}

interface Props {
  modules: ModuleChoice[]
  /** The ids that are ticked (the modules the learner gets). */
  ticked: number[]
  onChange: (ids: number[]) => void
  legend?: string
}

/** The request fragment for a choice of modules: nothing when every module is ticked (the
 * default), so an untouched checklist enrolls on the whole course. */
export function moduleBody(modules: ModuleChoice[], ticked: number[]): { module_ids?: number[] } {
  return ticked.length < modules.length ? { module_ids: ticked } : {}
}

/** Which modules of a course the learner gets. A single-module course has nothing to choose. */
export function ModuleChecklist({ modules, ticked, onChange, legend = 'Modules they get' }: Props) {
  if (modules.length < 2) return null
  const on = new Set(ticked)

  function toggle(id: number) {
    // Keep course order, whatever order they were ticked in.
    onChange(modules.filter((m) => (m.id === id ? !on.has(id) : on.has(m.id))).map((m) => m.id))
  }

  return (
    <fieldset className={page.modules}>
      <legend>
        {legend}{' '}
        <span className={page.muted}>
          · {ticked.length} of {modules.length}
        </span>
      </legend>
      <span className={page.moduleShortcuts}>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => onChange(modules.map((m) => m.id))}
        >
          All
        </button>
        <button type="button" className="btn btn-sm" onClick={() => onChange([])}>
          None
        </button>
      </span>
      <ul>
        {modules.map((m) => (
          <li key={m.id}>
            <label>
              <input type="checkbox" checked={on.has(m.id)} onChange={() => toggle(m.id)} />
              {m.title}
            </label>
          </li>
        ))}
      </ul>
      {ticked.length === 0 && (
        <p className={page.bad} role="alert">
          Tick at least one module.
        </p>
      )}
    </fieldset>
  )
}

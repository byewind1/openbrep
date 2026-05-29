import { useState } from 'react'
import type { AddParameterRequest, WorkbenchParameter } from '../api/types'

type AuthorableParameterType = AddParameterRequest['type_tag']

const PARAMETER_TYPES: AuthorableParameterType[] = ['Length', 'RealNum', 'Integer', 'Boolean', 'String']
const GDL_PARAMETER_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/

interface AddParameterInlineFormProps {
  parameters: WorkbenchParameter[]
  issues: string[]
  applying: boolean
  onAdd: (parameter: AddParameterRequest) => Promise<boolean>
  onValidate: () => void
}

export function AddParameterInlineForm({ parameters, issues, applying, onAdd, onValidate }: AddParameterInlineFormProps) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [typeTag, setTypeTag] = useState<AuthorableParameterType>('Length')
  const [defaultValue, setDefaultValue] = useState<unknown>('0')
  const [description, setDescription] = useState('')
  const [error, setError] = useState<string | null>(null)

  if (!open) {
    return (
      <div className="parameter-authoring collapsed">
        <button type="button" className="parameter-authoring-toggle" onClick={() => setOpen(true)}>
          + Parameter
        </button>
        <button type="button" className="parameter-authoring-validate" onClick={onValidate}>
          Validate
        </button>
        <span className={issues.length ? 'parameter-authoring-status warning' : 'parameter-authoring-status'}>
          {issues.length ? `${issues.length} issues` : 'ok'}
        </span>
      </div>
    )
  }

  const submit = async () => {
    const trimmedName = name.trim()
    if (!trimmedName) {
      setError('Name required')
      return
    }
    if (!GDL_PARAMETER_NAME.test(trimmedName)) {
      setError('Invalid name')
      return
    }
    if (!typeTag) {
      setError('Type required')
      return
    }
    if (parameters.some((parameter) => parameter.name === trimmedName)) {
      setError('Name exists')
      return
    }

    setError(null)
    const ok = await onAdd({
      name: trimmedName,
      type_tag: typeTag,
      value: normalizeDefaultValue(typeTag, defaultValue),
      description: description.trim(),
    })
    if (ok) {
      setName('')
      setTypeTag('Length')
      setDefaultValue('0')
      setDescription('')
      setOpen(false)
    }
  }

  return (
    <div className="parameter-authoring expanded">
      <div className="parameter-authoring-row compact-grid two-col">
        <input
          className="parameter-authoring-input"
          type="text"
          placeholder="name"
          value={name}
          onChange={(event) => setName(event.currentTarget.value)}
        />
        <select
          className="parameter-authoring-input"
          value={typeTag}
          onChange={(event) => {
            const nextType = event.currentTarget.value as AuthorableParameterType
            setTypeTag(nextType)
            setDefaultValue(defaultForType(nextType))
          }}
        >
          {PARAMETER_TYPES.map((type) => (
            <option key={type} value={type}>{type}</option>
          ))}
        </select>
      </div>
      <div className="parameter-authoring-row compact-grid two-col">
        {typeTag === 'Boolean' ? (
          <label className="parameter-authoring-boolean">
            <span>default</span>
            <input
              type="checkbox"
              checked={Boolean(defaultValue)}
              onChange={(event) => setDefaultValue(event.currentTarget.checked)}
            />
          </label>
        ) : (
          <input
            className="parameter-authoring-input"
            type={isNumericType(typeTag) ? 'number' : 'text'}
            step={typeTag === 'Integer' ? 1 : 0.01}
            placeholder="default"
            value={String(defaultValue)}
            onChange={(event) => setDefaultValue(event.currentTarget.value)}
          />
        )}
        <input
          className="parameter-authoring-input"
          type="text"
          placeholder="description"
          value={description}
          onChange={(event) => setDescription(event.currentTarget.value)}
        />
      </div>
      <div className="parameter-authoring-actions">
        {error ? <span className="parameter-authoring-error">{error}</span> : <span />}
        <button type="button" onClick={() => void submit()} disabled={applying}>
          {applying ? 'Adding' : 'Add'}
        </button>
        <button type="button" onClick={onValidate} disabled={applying}>
          Validate
        </button>
        <button type="button" onClick={() => { setOpen(false); setError(null) }} disabled={applying}>
          Cancel
        </button>
      </div>
      {issues.length ? (
        <div className="parameter-issues">
          {issues.slice(0, 3).map((issue) => (
            <div className="parameter-issue" key={issue}>{issue}</div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function defaultForType(typeTag: AuthorableParameterType): unknown {
  if (typeTag === 'Boolean') return false
  if (typeTag === 'String') return ''
  return '0'
}

function isNumericType(typeTag: AuthorableParameterType) {
  return ['Length', 'RealNum', 'Integer'].includes(typeTag)
}

function normalizeDefaultValue(typeTag: AuthorableParameterType, value: unknown): unknown {
  if (typeTag === 'Boolean') return Boolean(value)
  if (typeTag === 'Integer') return Number.parseInt(String(value || '0'), 10)
  if (typeTag === 'Length' || typeTag === 'RealNum') return Number.parseFloat(String(value || '0'))
  return String(value ?? '')
}

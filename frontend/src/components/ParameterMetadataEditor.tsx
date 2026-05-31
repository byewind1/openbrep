import { useEffect, useMemo, useState } from 'react'
import type { UpdateParameterRequest, WorkbenchParameter } from '../api/types'

type EditableParameterType = NonNullable<UpdateParameterRequest['type_tag']>

const EDITABLE_TYPES: EditableParameterType[] = ['Length', 'RealNum', 'Integer', 'Boolean', 'String']

interface ParameterMetadataEditorProps {
  parameters: WorkbenchParameter[]
  applying: boolean
  onUpdate: (parameter: UpdateParameterRequest) => Promise<boolean>
  onDelete: (name: string) => Promise<boolean>
}

export function ParameterMetadataEditor({
  parameters,
  applying,
  onUpdate,
  onDelete,
}: ParameterMetadataEditorProps) {
  const editableParameters = useMemo(() => parameters.filter((parameter) => parameter.type_tag !== 'Title'), [parameters])
  const [selectedName, setSelectedName] = useState('')
  const selected = editableParameters.find((parameter) => parameter.name === selectedName) ?? editableParameters[0]
  const [newName, setNewName] = useState('')
  const [typeTag, setTypeTag] = useState<EditableParameterType>('Length')
  const [value, setValue] = useState('')
  const [description, setDescription] = useState('')

  useEffect(() => {
    if (!selected) return
    setSelectedName(selected.name)
    setNewName(selected.name)
    setTypeTag(normalizeEditableType(selected.type_tag))
    setValue(selected.value)
    setDescription(selected.description)
  }, [selected?.name])

  if (!selected) {
    return null
  }

  const canRetag = !selected.is_fixed
  const canDelete = !selected.is_fixed

  async function submitUpdate() {
    if (!selected || !newName.trim()) return
    const ok = await onUpdate({
      name: selected.name,
      new_name: newName.trim(),
      type_tag: typeTag,
      value: normalizeValue(typeTag, value),
      description,
    })
    if (ok) setSelectedName(newName.trim())
  }

  async function submitDelete() {
    if (!selected || selected.is_fixed) return
    await onDelete(selected.name)
  }

  return (
    <section className="parameter-metadata-editor" aria-label="Edit parameter metadata">
      <div className="section-label">编辑参数</div>
      <div className="parameter-metadata-grid">
        <select value={selected.name} onChange={(event) => setSelectedName(event.currentTarget.value)}>
          {editableParameters.map((parameter) => (
            <option value={parameter.name} key={parameter.name}>
              {parameter.name}
            </option>
          ))}
        </select>
        <input
          type="text"
          value={newName}
          disabled={!canRetag}
          onChange={(event) => setNewName(event.currentTarget.value)}
          aria-label="Parameter name"
        />
        <select
          value={typeTag}
          disabled={!canRetag}
          onChange={(event) => setTypeTag(event.currentTarget.value as EditableParameterType)}
        >
          {EDITABLE_TYPES.map((type) => (
            <option value={type} key={type}>{type}</option>
          ))}
        </select>
        <input
          type={typeTag === 'String' ? 'text' : 'number'}
          step={typeTag === 'Integer' || typeTag === 'Boolean' ? 1 : 0.01}
          min={typeTag === 'Boolean' ? 0 : undefined}
          max={typeTag === 'Boolean' ? 1 : undefined}
          value={value}
          onChange={(event) => setValue(event.currentTarget.value)}
          aria-label="Parameter default value"
        />
      </div>
      <input
        className="parameter-metadata-description"
        type="text"
        value={description}
        onChange={(event) => setDescription(event.currentTarget.value)}
        aria-label="Parameter description"
        placeholder="description"
      />
      <div className="parameter-metadata-actions">
        <span>{selected.is_fixed ? 'fixed' : 'editable'}</span>
        <button type="button" disabled={applying || !newName.trim()} onClick={() => void submitUpdate()}>
          Save
        </button>
        <button type="button" disabled={applying || !canDelete} onClick={() => void submitDelete()}>
          Delete
        </button>
      </div>
    </section>
  )
}

function normalizeEditableType(typeTag: string): EditableParameterType {
  if (EDITABLE_TYPES.includes(typeTag as EditableParameterType)) return typeTag as EditableParameterType
  return 'String'
}

function normalizeValue(typeTag: EditableParameterType, value: string) {
  if (typeTag === 'Boolean') return value === '1' || value.toLowerCase() === 'true'
  if (typeTag === 'Integer') return Number.parseInt(value || '0', 10)
  if (typeTag === 'Length' || typeTag === 'RealNum') return Number.parseFloat(value || '0')
  return value
}

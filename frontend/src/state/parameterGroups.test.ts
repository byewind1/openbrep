import { groupParameters } from './parameterGroups'

test('groups dimensional parameters separately from property parameters', () => {
  const grouped = groupParameters([
    { name: 'A', type_tag: 'Length', description: 'Width', value: '1.0', is_fixed: true },
    { name: 'shelf_count', type_tag: 'Integer', description: 'Shelves', value: '4', is_fixed: false },
  ])

  expect(grouped.dimensions.map((p) => p.name)).toEqual(['A'])
  expect(grouped.properties.map((p) => p.name)).toEqual(['shelf_count'])
})

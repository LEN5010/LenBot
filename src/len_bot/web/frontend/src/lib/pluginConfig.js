// Plugin schemas own the fields; compound values remain explicit JSON.
export function configFields(schema) {
  const resolve = value => value?.$ref?.startsWith('#/$defs/')
    ? {...schema.$defs[value.$ref.slice(8)], ...value} : value
  return Object.entries(schema.properties || {}).map(([key, original]) => {
    let field = resolve(original)
    const nullable = field.anyOf?.some(item => item.type === 'null') || false
    if (nullable && field.anyOf.length === 2) field = {...resolve(field.anyOf.find(item => item.type !== 'null')), ...original}
    const json = ['object', 'array'].includes(field.type) || (!field.type && !field.enum && field.const === undefined)
    return {key, schema: field, nullable, json, required: (schema.required || []).includes(key)}
  })
}

// A schema may declare that a set of fields are alternative shapes of one
// choice — the workspace plugin's worker/gateway backends are exactly that.
// The form offers one choice and never submits two branches at once.
export function exclusiveGroups(schema) {
  return (schema['x-lenbot-exclusive'] || []).filter(group => (group.fields || []).length > 1)
}

export function groupFor(schema, key) {
  return exclusiveGroups(schema).find(group => group.fields.includes(key)) || null
}

export function selectedBranch(draft, group) {
  return group.fields.find(field => Object.hasOwn(draft, field) && draft[field] !== null) || null
}

export function chooseBranch(draft, group, key) {
  const next = {...draft}
  for (const field of group.fields) if (field !== key) delete next[field]
  if (key !== null && !Object.hasOwn(next, key)) next[key] = '{}'
  return next
}

export function configDraft(config, schema, secrets = []) {
  const draft = {}
  for (const field of configFields(schema)) {
    if (secrets.includes(field.key)) draft[field.key] = ''
    else if (Object.hasOwn(config, field.key) && config[field.key] !== null)
      draft[field.key] = field.json ? JSON.stringify(config[field.key], null, 2) : config[field.key]
  }
  return draft
}

// A first configuration starts from the schema, not from a shape the form
// invented.  Only fields the schema actually defaults are visible with that
// value; everything else is left unset, so saving does not write a null or an
// empty object over a field the operator never touched.  Two exclusive
// branches are never both seeded.
export function blankConfigDraft(schema, secrets = []) {
  const draft = {}
  for (const field of configFields(schema)) {
    if (secrets.includes(field.key)) { draft[field.key] = ''; continue }
    if (field.schema.const !== undefined) { draft[field.key] = field.schema.const; continue }
    if (field.schema.default !== undefined) {
      draft[field.key] = field.json ? JSON.stringify(field.schema.default, null, 2) : field.schema.default
    }
  }
  for (const group of exclusiveGroups(schema)) {
    for (const field of group.fields) delete draft[field]
  }
  return draft
}

export function configValue(draft, schema, {secrets = [], preserveSecrets = false} = {}) {
  const config = {}
  for (const field of configFields(schema)) {
    if (!Object.hasOwn(draft, field.key)) continue
    const value = draft[field.key]
    if (secrets.includes(field.key) && preserveSecrets && !value) continue
    if (field.json) {
      // An exclusive branch the operator has not filled in yet is not an
      // empty object to submit; it is an unfilled choice.
      if (value === '' && groupFor(schema, field.key)) continue
      try { config[field.key] = JSON.parse(value) }
      catch { throw new Error(`${field.schema.title || field.key} 需要合法 JSON`) }
    } else config[field.key] = value
  }
  return config
}

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

export function configDraft(config, schema, secrets = []) {
  const draft = {}
  for (const field of configFields(schema)) {
    if (secrets.includes(field.key)) draft[field.key] = ''
    else if (Object.hasOwn(config, field.key)) draft[field.key] = field.json ? JSON.stringify(config[field.key], null, 2) : config[field.key]
  }
  return draft
}

export function blankConfigDraft(schema, secrets = []) {
  const draft = {}
  for (const field of configFields(schema)) {
    draft[field.key] = secrets.includes(field.key) ? '' : field.schema.const !== undefined
      ? field.schema.const : field.json ? (field.schema.type === 'array' ? '[]' : '{}') : null
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
      try { config[field.key] = JSON.parse(value) }
      catch { throw new Error(`${field.schema.title || field.key} 需要合法 JSON`) }
    } else config[field.key] = value
  }
  return config
}

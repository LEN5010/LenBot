// Plugin schemas own the fields; compound values remain explicit JSON.
//
// A field is rendered from its own declaration, not from a list kept here:
// nested objects with declared properties become grouped subfields (the
// workspace worker/gateway backends are the case that exists today), a list of
// simple values becomes editable rows, and anything else compound stays an
// explicit JSON value.  There is deliberately no general schema editor.
const SIMPLE = ['string', 'number', 'integer', 'boolean']

export function configFields(schema, definitions) {
  const defs = definitions || (schema && schema.$defs) || {}
  const resolve = value => value?.$ref?.startsWith('#/$defs/')
    ? {...defs[value.$ref.slice(8)], ...value} : value
  return Object.entries((schema && schema.properties) || {}).map(([key, original]) => {
    let field = resolve(original)
    const nullable = field.anyOf?.some(item => item.type === 'null') || false
    if (nullable && field.anyOf.length === 2) field = {...resolve(field.anyOf.find(item => item.type !== 'null')), ...original}
    const nested = field.type === 'object' && !!field.properties
    const list = field.type === 'array' && SIMPLE.includes(field.items?.type)
    const json = !nested && !list && (['object', 'array'].includes(field.type) || (!field.type && !field.enum && field.const === undefined))
    return {key, schema: field, nullable, nested, list, json, required: (schema.required || []).includes(key)}
  })
}

export function fieldPath(prefix, key) {
  return prefix ? `${prefix}.${key}` : key
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

export function chooseBranch(draft, group, key, seed = '{}') {
  const next = {...draft}
  for (const field of group.fields) if (field !== key) delete next[field]
  if (key !== null && !Object.hasOwn(next, key)) next[key] = seed
  return next
}

export function configDraft(config, schema, secrets = [], prefix = '') {
  const draft = {}
  const source = config && typeof config === 'object' ? config : {}
  for (const field of configFields(schema)) {
    const path = fieldPath(prefix, field.key)
    if (field.nested) {
      const nested = source[field.key]
      // The stored object is kept so its siblings survive an edit; an absent
      // one stays absent instead of becoming an empty backend.
      if (nested && typeof nested === 'object') draft[field.key] = configDraft(nested, field.schema, secrets, path)
      continue
    }
    if (secrets.includes(path)) { draft[field.key] = ''; continue }
    if (!Object.hasOwn(source, field.key) || source[field.key] === null) continue
    draft[field.key] = field.list ? [...source[field.key]]
      : field.json ? JSON.stringify(source[field.key], null, 2) : source[field.key]
  }
  return draft
}

// A first configuration starts from the schema, not from a shape the form
// invented.  Only fields the schema actually defaults are visible with that
// value; everything else is left unset, so saving does not write a null or an
// empty object over a field the operator never touched.  Two exclusive
// branches are never both seeded.
export function blankConfigDraft(schema, secrets = [], prefix = '') {
  const draft = {}
  for (const field of configFields(schema)) {
    const path = fieldPath(prefix, field.key)
    if (field.nested) continue
    if (secrets.includes(path)) { draft[field.key] = ''; continue }
    if (field.schema.const !== undefined) { draft[field.key] = field.schema.const; continue }
    if (field.schema.default !== undefined) {
      draft[field.key] = field.list ? [...field.schema.default]
        : field.json ? JSON.stringify(field.schema.default, null, 2) : field.schema.default
    }
  }
  for (const group of exclusiveGroups(schema)) {
    for (const field of group.fields) delete draft[field]
  }
  return draft
}

export function configValue(draft, schema, {secrets = [], preserveSecrets = false} = {}, prefix = '') {
  const config = {}
  for (const field of configFields(schema)) {
    const path = fieldPath(prefix, field.key)
    if (field.nested) {
      const branch = draft[field.key]
      if (!branch || typeof branch !== 'object') continue
      const value = configValue(branch, field.schema, {secrets, preserveSecrets}, path)
      // An exclusive branch the operator has not filled in yet is not an empty
      // object to submit; it is an unfilled choice.
      if (!Object.keys(value).length && (groupFor(schema, field.key) || !Object.keys(branch).length)) continue
      config[field.key] = value
      continue
    }
    if (!Object.hasOwn(draft, field.key)) continue
    const value = draft[field.key]
    if (secrets.includes(path)) {
      // null is the only way a panel says "clear this credential"; an empty
      // value only means the operator did not touch a stored one.
      if (value === null) { config[field.key] = null; continue }
      if (value === '' && preserveSecrets) continue
      config[field.key] = value
      continue
    }
    if (value === null) { config[field.key] = null; continue }
    if (field.list) { config[field.key] = value; continue }
    if (field.json) {
      if (value === '' && groupFor(schema, field.key)) continue
      try { config[field.key] = JSON.parse(value) }
      catch { throw new Error(`${field.schema.title || path} 需要合法 JSON`) }
    } else config[field.key] = value
  }
  return config
}

// The local pre-submit check.  It only reports what can be decided from the
// draft itself — a missing required value or a compound value that is not
// valid JSON — so the server's model validation stays the real admission rule.
//
// A field the draft never mentions is normally left alone: the form does not
// write a value the operator never touched.  Inside a branch that is present
// (a chosen backend, a saved sub-object) that reasoning does not hold — the
// branch is being submitted — so its required fields are checked.
export function draftProblems(schema, draft, {secrets = [], configSet = {}, requirePresent = false} = {}, prefix = '') {
  const problems = []
  for (const field of configFields(schema)) {
    const path = fieldPath(prefix, field.key)
    const title = field.schema.title || field.key
    const present = Object.hasOwn(draft, field.key)
    if (field.nested) {
      const branch = draft[field.key]
      if (branch && typeof branch === 'object') {
        problems.push(...draftProblems(field.schema, branch, {secrets, configSet, requirePresent: true}, path))
      }
      continue
    }
    if (!present) {
      if (requirePresent && field.required) problems.push({key: path, message: `${title}：必填，尚未填写`})
      continue
    }
    const value = draft[field.key]
    const keptSecret = secrets.includes(path) && configSet[path]
    if (field.required && (value === '' || value === null || value === undefined) && !keptSecret) {
      problems.push({key: path, message: `${title}：必填，当前为空`})
      continue
    }
    if (field.json && typeof value === 'string' && value.trim() !== '') {
      try { JSON.parse(value) }
      catch { problems.push({key: path, message: `${title}：不是合法 JSON，请按字段下方说明的结构填写`}) }
    }
  }
  return problems
}

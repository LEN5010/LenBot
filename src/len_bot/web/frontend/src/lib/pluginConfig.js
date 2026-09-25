import { hasConfigDraftChanges, rebaseConfigDraft } from './configDraft.js'

// Plugin schemas own the fields; compound values remain explicit JSON.
//
// A field is rendered from its own declaration, not from a list kept here:
// nested objects with declared properties become grouped subfields (the
// workspace worker/gateway backends and the browser settings are the cases
// that exist today), a list of simple values becomes editable rows, and
// anything else compound stays an explicit JSON value.  There is deliberately
// no general schema editor.  A field that is only ever a JSON reference to
// another definition is resolved before it is classified, so `$ref` does not
// decide the widget.
const SIMPLE = ['string', 'number', 'integer', 'boolean']

function definitionsOf(schema) {
  return (schema && schema.$defs) || {}
}

export function resolveField(field, definitions) {
  let current = field
  const seen = new Set()
  while (current?.$ref?.startsWith('#/$defs/') && !seen.has(current.$ref)) {
    seen.add(current.$ref)
    const target = definitions[current.$ref.slice(8)]
    if (!target) break
    current = {
      ...target,
      ...Object.fromEntries(Object.entries(current).filter(([key]) => key !== '$ref'))
    }
  }
  return current
}

export function configFields(schema, definitions) {
  const defs = definitions || definitionsOf(schema)
  return Object.entries((schema && schema.properties) || {}).map(([key, original]) => {
    let field = resolveField(original, defs)
    const nullable = field.anyOf?.some(item => item.type === 'null') || false
    if (nullable && field.anyOf.length === 2) {
      field = {
        ...resolveField(field.anyOf.find(item => item.type !== 'null'), defs),
        ...original
      }
    }
    const nested = field.type === 'object' && !!field.properties
    const list = field.type === 'array' && SIMPLE.includes(field.items?.type)
    const json = !nested && !list && (['object', 'array'].includes(field.type) || (!field.type && !field.enum && field.const === undefined))
    return {key, schema: field, definitions: defs, nullable, nested, list, json, required: (schema.required || []).includes(key),
      choices: listChoices(schema, key)}
  })
}

// A closed list of values a schema can name in full (the link parser's
// platforms) is offered as checkboxes: the operator should not have to recall
// that the only accepted spelling today is `bilibili`.  The declaration lives
// beside the field, and an undeclared list stays free-form rows rather than
// being guessed at.
export function listChoices(schema, key) {
  return (schema && schema['x-lenbot-list-choices'] && schema['x-lenbot-list-choices'][key]) || null
}

// Enum values are stored as the schema's own strings; only what is shown is a
// name.  Labels come from the model that declares the values, so there is no
// second vocabulary to keep in step.
export function enumLabels(schema, key) {
  return (schema && schema['x-lenbot-enum-labels'] && schema['x-lenbot-enum-labels'][key]) || null
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

export function configDraft(config, schema, secrets = [], prefix = '', definitions) {
  const defs = definitions || definitionsOf(schema)
  const draft = {}
  const source = config && typeof config === 'object' ? config : {}
  for (const field of configFields(schema, defs)) {
    const path = fieldPath(prefix, field.key)
    if (field.nested) {
      const nested = source[field.key]
      // The stored object is kept so its siblings survive an edit; an absent
      // one stays absent instead of becoming an empty backend.
      if (nested && typeof nested === 'object') draft[field.key] = configDraft(nested, field.schema, secrets, path, defs)
      continue
    }
    if (secrets.includes(path)) {
      draft[field.key] = '';
      continue
    }
    if (!Object.hasOwn(source, field.key) || source[field.key] === null) continue
    draft[field.key] = field.list ? [...source[field.key]]
      : field.json ? JSON.stringify(source[field.key], null, 2) : source[field.key]
  }
  return draft
}

// Compare compound fields as their saved JSON values, not textarea formatting.
// Credential drafts remain their explicit keep/replace/clear values; this
// transform never reads a stored secret or submits a configuration.
function draftValues(draft, schema, toEditor, prefix = '', definitions) {
  if (draft === null) return null
  const defs = definitions || definitionsOf(schema), next = {...draft}
  for (const field of configFields(schema, defs)) {
    if (!Object.hasOwn(draft, field.key)) continue
    const value = draft[field.key], path = fieldPath(prefix, field.key)
    if (value === null) continue
    if (field.nested) next[field.key] = draftValues(value, field.schema, toEditor, path, defs)
    else if (field.json) {
      if (toEditor) next[field.key] = JSON.stringify(value, null, 2)
      else {
        try {
          next[field.key] = JSON.parse(value)
        }
        catch {
          throw new Error(`${field.schema.title || path} 需要合法 JSON；请先修正草稿，或选择采用现值。`)
        }
      }
    }
  }
  return next
}

function rebasePluginValues(original, draft, current, schema, definitions) {
  let next = rebaseConfigDraft(original, draft, current)
  if (next === null) return null
  const defs = definitions || definitionsOf(schema)
  for (const group of exclusiveGroups(schema)) {
    const choice = value => Object.fromEntries(group.fields.filter(key=>Object.hasOwn(value||{},key)).map(key=>[key,value[key]]))
    const before = choice(original), own = choice(draft), saved = choice(current)
    // A concurrently switched backend cannot leave both branches present and
    // let their Schema order silently select which one the operator will save.
    if (hasConfigDraftChanges(before, own) && selectedBranch(own, group) !== selectedBranch(saved, group)) {
      for (const key of group.fields) delete next[key]
      next = {...next, ...JSON.parse(JSON.stringify(own))}
    }
  }
  for (const field of configFields(schema, defs)) {
    if (field.nested && next[field.key] && original?.[field.key] && draft?.[field.key] && current?.[field.key]) {
      next[field.key] = rebasePluginValues(original[field.key], draft[field.key], current[field.key], field.schema, defs)
    }
  }
  return next
}

export function rebasePluginDraft(original, draft, current, schema) {
  return draftValues(rebasePluginValues(
    draftValues(original, schema, false), draftValues(draft, schema, false), draftValues(current, schema, false), schema,
  ), schema, true)
}

// A first configuration starts from the schema, not from a shape the form
// invented.  Only fields the schema actually defaults are visible with that
// value; everything else is left unset, so saving does not write a null or an
// empty object over a field the operator never touched.  Two exclusive
// branches are never both seeded.
// A one-of declaration carries `required: true` when the schema itself cannot
// exist without a choice: workspace has no valid configuration with neither
// backend, so a submit that leaves the choice unselected is refused here with
// the same reason the model would give, instead of being sent.
export function groupGaps(schema, draft) {
  return exclusiveGroups(schema).filter(group => group.required && !selectedBranch(draft, group))
    .map(group => ({key: group.fields[0], message: `${group.title}：必须选择其中一项`}))
}

export function blankConfigDraft(schema, secrets = [], prefix = '', definitions) {
  const defs = definitions || definitionsOf(schema)
  const draft = {}
  for (const field of configFields(schema, defs)) {
    const path = fieldPath(prefix, field.key)
    if (field.nested) {
      // A required sub-object is part of the first configuration, so it starts
      // from its own defaults; an optional one is left for the operator to
      // choose, and is never seeded as an empty object.
      if (field.required) draft[field.key] = blankConfigDraft(field.schema, secrets, path, defs)
      continue
    }
    if (secrets.includes(path)) {
      draft[field.key] = '';
      continue
    }
    if (field.schema.const !== undefined) {
      draft[field.key] = field.schema.const;
      continue
    }
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

export function configValue(draft, schema, {secrets = [], preserveSecrets = false} = {}, prefix = '', definitions) {
  const defs = definitions || definitionsOf(schema)
  const config = {}
  for (const field of configFields(schema, defs)) {
    const path = fieldPath(prefix, field.key)
    if (field.nested) {
      const branch = draft[field.key]
      if (!branch || typeof branch !== 'object') continue
      const value = configValue(branch, field.schema, {secrets, preserveSecrets}, path, defs)
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
      if (value === null) {
        config[field.key] = null;
        continue
      }
      if (value === '' && preserveSecrets) continue
      config[field.key] = value
      continue
    }
    if (value === null) {
      config[field.key] = null;
      continue
    }
    if (field.list) {
      config[field.key] = value;
      continue
    }
    if (field.json) {
      if (value === '' && groupFor(schema, field.key)) continue
      try {
        config[field.key] = JSON.parse(value)
      }
      catch {
        throw new Error(`${field.schema.title || path} 需要合法 JSON`)
      }
    } else config[field.key] = value
  }
  // Unselected exclusive branches are an explicit clear, not an omitted
  // field that merge_config would keep from the stored configuration.
  for (const group of exclusiveGroups(schema)) {
    const selected = selectedBranch(draft, group)
    if (!selected) continue
    for (const field of group.fields) {
      if (field !== selected) config[field] = null
    }
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
export function draftProblems(schema, draft, {secrets = [], configSet = {}, requirePresent = false} = {}, prefix = '', definitions) {
  const defs = definitions || definitionsOf(schema)
  const problems = []
  for (const gap of groupGaps(schema, draft)) problems.push({key: fieldPath(prefix, gap.key), message: gap.message})
  for (const field of configFields(schema, defs)) {
    const path = fieldPath(prefix, field.key)
    const title = field.schema.title || field.key
    const present = Object.hasOwn(draft, field.key)
    if (field.nested) {
      const branch = draft[field.key]
      if (branch && typeof branch === 'object') {
        problems.push(...draftProblems(field.schema, branch, {secrets, configSet, requirePresent: true}, path, defs))
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
      try {
        JSON.parse(value)
      }
      catch {
        problems.push({key: path, message: `${title}：不是合法 JSON，请按字段下方说明的结构填写`})
      }
    }
  }
  return problems
}

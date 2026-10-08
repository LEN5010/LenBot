export const normalizedUrl = value => (value || '').replace(/\/+$/, '')
export const credentialBinding = row => JSON.stringify([row.api, normalizedUrl(row.base_url), normalizedUrl(row.proxy)])

export function providerCandidate(row) {
  return { alias: row.originalAlias || null, provider: {
    api: row.api, base_url: normalizedUrl(row.base_url), api_key: row.api_key || null, proxy: normalizedUrl(row.proxy) || null,
  } }
}

export function providerRows(models) {
  return Object.entries(models.providers).map(([alias, value]) => ({
    id: alias, alias, originalAlias: alias, api: value.api, base_url: value.base_url, api_key: '', proxy: value.proxy || '',
    keyConfigured: value.api_key_configured, keyReusable: value.api_key_configured, keyBinding: credentialBinding(value),
  }))
}

export const modelChoices = models => models.map(item => ({ title: item.name === item.id ? item.id : `${item.name} · ${item.id}`, value: item.id }))

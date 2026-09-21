/** API wrappers — all calls use relative URLs so the Vite proxy / backend serve them. */

const BASE = '/api/v1'

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, options)
  const body = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
  if (!res.ok) {
    const err = new Error(body.detail || body.error || `HTTP ${res.status}`)
    err.status = res.status
    err.body = body
    throw err
  }
  return body
}

export async function listSamples() {
  return request('/samples')
}

export async function loadSample(key) {
  return request(`/samples/${key}/load`, { method: 'POST' })
}

export async function uploadDocument(file) {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${BASE}/documents`, { method: 'POST', body: form })
  const body = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
  if (!res.ok) {
    const err = new Error(body.detail || body.error || `HTTP ${res.status}`)
    err.status = res.status
    err.body = body
    throw err
  }
  return body
}

export async function extractDocument(docId) {
  return request(`/documents/${docId}/extract`, { method: 'POST' })
}

export async function patchExtraction(extractionId, fields) {
  return request(`/extractions/${extractionId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fields }),
  })
}

export async function analyzeExtraction(extractionId) {
  return request(`/extractions/${extractionId}/analyze`, { method: 'POST' })
}

export async function getAnalysis(analysisId) {
  return request(`/analyses/${analysisId}`)
}

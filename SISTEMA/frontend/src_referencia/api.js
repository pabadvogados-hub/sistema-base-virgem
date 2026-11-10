// Em producao, deixe VITE_API_URL="" para usar o mesmo dominio (nginx faz o proxy de /api).
const API_URL = import.meta.env.VITE_API_URL !== undefined
  ? import.meta.env.VITE_API_URL
  : 'http://localhost:8000'

function getToken() {
  return localStorage.getItem('esc_token') || ''
}

export async function api(path, { method = 'GET', body, form, auth = true } = {}) {
  const headers = {}
  if (auth && getToken()) headers['Authorization'] = `Bearer ${getToken()}`
  let payload
  if (form) {
    payload = form // FormData - browser define o content-type
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  const res = await fetch(`${API_URL}${path}`, { method, headers, body: payload })
  if (res.status === 401 && auth) {
    localStorage.removeItem('esc_token')
    if (!location.pathname.startsWith('/login')) location.href = '/login'
  }
  const text = await res.text()
  const data = text ? JSON.parse(text) : null
  if (!res.ok) {
    throw new Error(data?.detail || `Erro ${res.status}`)
  }
  return data
}

export function downloadUrl(path) {
  return `${API_URL}${path}`
}

export async function downloadFile(path, filename) {
  const res = await fetch(`${API_URL}${path}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  })
  if (!res.ok) throw new Error('Falha no download')
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

import { createContext, useContext, useEffect, useState } from 'react'
import { api } from './api'

const AuthCtx = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const token = localStorage.getItem('esc_token')
    if (!token) { setLoading(false); return }
    api('/api/auth/me')
      .then(setUser)
      .catch(() => localStorage.removeItem('esc_token'))
      .finally(() => setLoading(false))
  }, [])

  async function login(email, senha) {
    const data = await api('/api/auth/login', { method: 'POST', auth: false, body: { email, senha } })
    localStorage.setItem('esc_token', data.token)
    setUser(data.usuario)
    return data.usuario
  }

  function logout() {
    localStorage.removeItem('esc_token')
    setUser(null)
    location.href = '/login'
  }

  return (
    <AuthCtx.Provider value={{ user, setUser, loading, login, logout }}>
      {children}
    </AuthCtx.Provider>
  )
}

export const useAuth = () => useContext(AuthCtx)

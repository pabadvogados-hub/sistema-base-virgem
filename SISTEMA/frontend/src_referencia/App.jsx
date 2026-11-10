import { useEffect, Component } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuth } from './auth'

// Escudo: se qualquer tela quebrar, mostra o erro em vez de travar/branco.
class ErrorBoundary extends Component {
  constructor(p) { super(p); this.state = { erro: null } }
  static getDerivedStateFromError(e) { return { erro: e } }
  componentDidCatch(e, info) { console.error('APP CRASH:', e, info) }
  render() {
    if (this.state.erro) {
      return (
        <div style={{ padding: 24, fontFamily: 'monospace', color: '#fff', background: '#0a0f1c', minHeight: '100vh' }}>
          <h2 style={{ color: '#e9cf92' }}>Ops — algo quebrou nesta tela</h2>
          <pre style={{ whiteSpace: 'pre-wrap', color: '#ff8a8a' }}>{String(this.state.erro?.message || this.state.erro)}</pre>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 11, color: '#93a4bd', marginTop: 8 }}>{this.state.erro?.stack}</pre>
          <button onClick={() => location.reload()} style={{ marginTop: 14, padding: '8px 18px', background: '#3b82f6', border: 'none', borderRadius: 8, cursor: 'pointer', fontWeight: 700 }}>Recarregar</button>
        </div>
      )
    }
    return this.props.children
  }
}

// Recarrega sozinho quando uma versao nova e publicada (sem Ctrl+Shift+R).
function useAutoUpdate() {
  useEffect(() => {
    const atual = (document.querySelector('script[src*="assets/index-"]')?.src || '').match(/assets\/index-[\w-]+\.js/)?.[0]
    async function check() {
      try {
        const html = await fetch('/?ts=' + Date.now(), { cache: 'no-store' }).then((r) => r.text())
        const nova = html.match(/assets\/index-[\w-]+\.js/)?.[0]
        const el = document.activeElement
        const digitando = el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') && el.value
        if (atual && nova && nova !== atual && !digitando) location.reload()
      } catch { /* ignora falha de rede */ }
    }
    const t = setInterval(check, 60000)
    return () => clearInterval(t)
  }, [])
}
import Layout from './components/Layout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Intake from './pages/Intake'
import Atendimento from './pages/Atendimento'
import Operacional from './pages/Operacional'
import Campanhas from './pages/Campanhas'
import Usuarios from './pages/Usuarios'
import Financeiro from './pages/Financeiro'
import Processos from './pages/Processos'
import Contatos from './pages/Contatos'
import Controladoria from './pages/Controladoria'
import EmBreve from './pages/EmBreve'

function Protected({ children }) {
  const { user, loading } = useAuth()
  if (loading) return <div className="center-load">Carregando…</div>
  if (!user) return <Navigate to="/login" replace />
  return <Layout>{children}</Layout>
}

function AdminOnly({ children }) {
  const { user } = useAuth()
  if (user?.papel !== 'admin') return <Navigate to="/" replace />
  return children
}

export default function App() {
  useAutoUpdate()
  return (
    <ErrorBoundary>
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Protected><Dashboard /></Protected>} />
      <Route path="/chat" element={<Protected><Atendimento vista="chat" /></Protected>} />
      <Route path="/crm" element={<Protected><Atendimento vista="crm" /></Protected>} />
      <Route path="/contatos" element={<Protected><Contatos /></Protected>} />
      <Route path="/operacional" element={<Protected><Operacional /></Protected>} />
      <Route path="/processos" element={<Protected><Processos /></Protected>} />
      <Route path="/controladoria" element={<Protected><Controladoria /></Protected>} />
      <Route path="/campanhas" element={<Navigate to="/marketing" replace />} />
      <Route path="/usuarios" element={<Protected><AdminOnly><Usuarios /></AdminOnly></Protected>} />
      <Route path="/intake" element={<Protected><Intake /></Protected>} />
      <Route path="/assinatura" element={<Protected><EmBreve titulo="Assinatura Digital" /></Protected>} />
      <Route path="/marketing" element={<Protected><Campanhas /></Protected>} />
      <Route path="/financeiro" element={<Protected><Financeiro /></Protected>} />
      {/* rotas antigas -> redireciona */}
      <Route path="/whatsapp" element={<Navigate to="/crm" replace />} />
      <Route path="/pecas" element={<Navigate to="/operacional" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </ErrorBoundary>
  )
}

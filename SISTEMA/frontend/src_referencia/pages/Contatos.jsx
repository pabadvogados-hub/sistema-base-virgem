import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'

const PRATA = {
  background: 'linear-gradient(180deg,#fff,#c8d2e4 60%,#9aa6bd)',
  WebkitBackgroundClip: 'text', backgroundClip: 'text', WebkitTextFillColor: 'transparent',
}

// Formata o telefone brasileiro pra leitura (o Tribunal quer ver o número — POP da Flávia)
function fmtTel(t) {
  if (t && String(t).includes('@')) return '— (só WhatsApp)'
  const d = (t || '').replace(/\D/g, '')
  if (d.length === 13) return `+${d.slice(0, 2)} ${d.slice(2, 4)} ${d.slice(4, 9)}-${d.slice(9)}`
  if (d.length === 12) return `+${d.slice(0, 2)} ${d.slice(2, 4)} ${d.slice(4, 8)}-${d.slice(8)}`
  return t || '—'
}
const inicial = (n) => (n || '?').trim().charAt(0).toUpperCase()

export default function Contatos() {
  const nav = useNavigate()
  const { user } = useAuth()
  const ehAdmin = user?.papel === 'admin'
  const [q, setQ] = useState('')
  const [itens, setItens] = useState([])
  const [buscou, setBuscou] = useState(false)
  const [carregando, setCarregando] = useState(false)
  const [erro, setErro] = useState('')
  const [copiado, setCopiado] = useState('')

  async function buscar(e) {
    if (e) e.preventDefault()
    setCarregando(true); setErro('')
    try {
      const r = await api(`/api/atendimento/contatos?q=${encodeURIComponent(q.trim())}`)
      setItens(r.items || []); setBuscou(true)
    } catch (er) { setErro(er.message) }
    setCarregando(false)
  }
  function copiar(tel) {
    try { navigator.clipboard.writeText((tel || '').replace(/\D/g, '')) } catch (e) { /* ignore */ }
    setCopiado(tel); setTimeout(() => setCopiado(''), 1500)
  }
  async function excluir(c) {
    if (!window.confirm(`Excluir o contato "${c.contato_nome || c.contato_telefone}"?\n\nA conversa e TODAS as mensagens/áudios serão apagados. Isso NÃO pode ser desfeito.`)) return
    try {
      await api(`/api/atendimento/conversas/${c.id}`, { method: 'DELETE' })
      setItens((xs) => xs.filter((x) => x.id !== c.id))
    } catch (er) { setErro(er.message) }
  }

  return (
    <>
      <div style={{ marginBottom: 18 }}>
        <h2 style={{ fontSize: 22, fontWeight: 800, ...PRATA }}>Contatos</h2>
        <p className="muted" style={{ marginTop: 4 }}>
          Busque qualquer contato ou conversa por <b>nome</b> ou <b>número</b> — nada fica escondido.
        </p>
      </div>

      <form onSubmit={buscar} style={{ display: 'flex', gap: 10, maxWidth: 640, marginBottom: 22 }}>
        <input autoFocus value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Nome ou número (ex.: Hélio Paranhos, 1198…)"
          style={{ flex: 1, background: 'var(--chip)', border: '1px solid var(--border)', borderRadius: 11, padding: '11px 14px', color: 'var(--text)', outline: 'none', fontSize: 14 }} />
        <button className="btn btn-gold" disabled={carregando}>{carregando ? <span className="spin" /> : 'Buscar'}</button>
      </form>

      {erro && <div className="alert alert-error">{erro}</div>}
      {buscou && !carregando && itens.length === 0 &&
        <p className="muted">Nenhum contato encontrado para “{q}”.</p>}

      <div style={{ display: 'grid', gap: 10 }}>
        {itens.map((c) => (
          <div key={c.id} className="panel" style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 14, padding: '13px 16px' }}>
            <div style={{ width: 44, height: 44, borderRadius: '50%', flex: '0 0 auto', display: 'grid', placeItems: 'center', fontWeight: 800, color: '#0d1218', background: 'linear-gradient(150deg,var(--gold),#a97f34)' }}>
              {inicial(c.contato_nome)}
            </div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontSize: 15, fontWeight: 700 }}>{c.contato_nome || 'Sem nome'}</div>
              <div style={{ display: 'flex', gap: 9, alignItems: 'center', marginTop: 3, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 14, color: 'var(--gold)', fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{fmtTel(c.contato_telefone)}</span>
                {c.etapa && <span style={{ fontSize: 11, background: 'var(--chip)', color: 'var(--muted)', borderRadius: 6, padding: '2px 8px' }}>{c.etapa}</span>}
                {c.atendente_email && <span className="muted" style={{ fontSize: 11.5 }}>· {c.atendente_email.split('@')[0]}</span>}
              </div>
              {c.ultima_mensagem && <div className="muted" style={{ fontSize: 12.5, marginTop: 4, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.ultima_mensagem}</div>}
            </div>
            <div style={{ display: 'flex', gap: 8, flex: '0 0 auto' }}>
              {!String(c.contato_telefone || '').includes('@') &&
                <button onClick={() => copiar(c.contato_telefone)} className="btn btn-sm btn-ghost" title="Copiar número">
                  {copiado === c.contato_telefone ? '✓ copiado' : 'Copiar nº'}
                </button>}
              <button onClick={() => nav(`/chat?c=${c.id}`)} className="btn btn-sm btn-gold">Abrir no chat</button>
              {ehAdmin &&
                <button onClick={() => excluir(c)} className="btn btn-sm btn-ghost" title="Excluir contato e conversa (definitivo)"
                  style={{ color: '#e0705f', borderColor: 'rgba(224,112,95,.32)' }}>Excluir</button>}
            </div>
          </div>
        ))}
      </div>
    </>
  )
}

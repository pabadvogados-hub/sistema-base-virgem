import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'

function iniciais(nome, tel) {
  const base = (nome || tel || '?').trim()
  const p = base.split(/\s+/)
  return ((p[0]?.[0] || '') + (p[1]?.[0] || '')).toUpperCase() || '#'
}

function horaCurta(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    const hoje = new Date()
    return d.toDateString() === hoje.toDateString()
      ? d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
      : d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })
  } catch { return '' }
}

const EMOJIS = ['😀', '😁', '😂', '🤣', '😊', '😍', '😘', '😎', '🤔', '😅', '😉', '🙂', '😢', '😭', '😡', '🙏', '👍', '👏', '🙌', '💪', '👊', '🤝', '👋', '🔥', '✅', '❌', '⚠️', '💯', '❤️', '💛', '🎉', '💰', '📄', '📌', '⏰', '📞', '💬', '🚛', '⚖️', '😴']

// Nome em prata brilhante (gradiente)
const PRATA = {
  background: 'linear-gradient(180deg,#ffffff 0%,#e6ecf5 36%,#a9b6cb 60%,#f4f7fb 100%)',
  WebkitBackgroundClip: 'text', backgroundClip: 'text', WebkitTextFillColor: 'transparent',
  color: 'transparent', textShadow: '0 1px 10px rgba(255,255,255,0.10)',
}
// Icones de linha (SVG) — mais limpos que emoji
function MicIcon({ size = 19 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="2" width="6" height="12" rx="3" /><path d="M5 10a7 7 0 0 0 14 0" /><line x1="12" y1="17" x2="12" y2="22" /><line x1="8" y1="22" x2="16" y2="22" />
    </svg>
  )
}
function TrashIcon({ size = 17 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" /><path d="M10 11v6M14 11v6" /><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
  )
}
function SmileIcon({ size = 20 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" /><path d="M8 14s1.5 2 4 2 4-2 4-2" /><line x1="9" y1="9" x2="9.01" y2="9" /><line x1="15" y1="9" x2="15.01" y2="9" />
    </svg>
  )
}
function PlusIcon({ size = 20 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  )
}
function fmtTempo(s) {
  if (!isFinite(s) || s < 0) s = 0
  const m = Math.floor(s / 60), ss = Math.floor(s % 60)
  return `${m}:${String(ss).padStart(2, '0')}`
}
// Player de audio customizado — dourado, elegante (substitui o feio nativo).
// preload="none" + SEM truque de seek: nunca trava, mesmo com muitos audios.
const VELOCIDADES = [1, 1.5, 2]
function AudioPlayer({ src }) {
  const ref = useRef(null)
  const [tocando, setTocando] = useState(false)
  const [cur, setCur] = useState(0)
  const [dur, setDur] = useState(0)
  const [vel, setVel] = useState(1)
  function marcaDuracao() {
    const a = ref.current; if (!a) return
    if (isFinite(a.duration) && a.duration > 0) setDur(a.duration)  // so aceita duracao valida
  }
  function mudarVel(e) {
    e.stopPropagation()
    const prox = VELOCIDADES[(VELOCIDADES.indexOf(vel) + 1) % VELOCIDADES.length]
    setVel(prox)
    if (ref.current) ref.current.playbackRate = prox
  }
  function play() {
    const a = ref.current; if (!a) return
    if (a.paused) { a.playbackRate = vel; a.play().catch(() => {}); setTocando(true) } else { a.pause(); setTocando(false) }
  }
  function seek(e) {
    const a = ref.current; if (!a || !isFinite(dur) || !dur) return
    const r = e.currentTarget.getBoundingClientRect()
    a.currentTime = Math.min(dur, Math.max(0, (e.clientX - r.left) / r.width) * dur)
  }
  const pct = dur ? Math.min(100, (cur / dur) * 100) : 0
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 11, minWidth: 210, maxWidth: 250 }}>
      <audio ref={ref} src={src} preload="none"
        onLoadedMetadata={marcaDuracao} onDurationChange={marcaDuracao}
        onTimeUpdate={() => setCur(ref.current?.currentTime || 0)}
        onPause={() => setTocando(false)} onPlay={() => setTocando(true)}
        onEnded={() => { setTocando(false); setCur(0) }} />
      <button type="button" onClick={play} title={tocando ? 'Pausar' : 'Tocar'}
        style={{ flexShrink: 0, width: 38, height: 38, borderRadius: '50%', border: 'none', cursor: 'pointer',
          background: 'linear-gradient(135deg, #3b82f6, #b8934f)', color: '#1a1205', display: 'grid', placeItems: 'center',
          boxShadow: '0 2px 10px rgba(59,130,246,0.35)' }}>
        {tocando
          ? <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg>
          : <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" style={{ marginLeft: 2 }}><path d="M7 5v14l12-7z" /></svg>}
      </button>
      <div style={{ flex: 1 }}>
        <div onClick={seek} style={{ height: 6, borderRadius: 999, background: 'rgba(59,130,246,0.20)', cursor: 'pointer', position: 'relative' }}>
          <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${pct}%`, borderRadius: 999, background: 'linear-gradient(90deg, #b8934f, #e9cf92)' }} />
          <div style={{ position: 'absolute', left: `${pct}%`, top: '50%', transform: 'translate(-50%, -50%)', width: 12, height: 12, borderRadius: '50%', background: '#f0dca5', boxShadow: '0 0 7px rgba(59,130,246,0.8)' }} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginTop: 4 }}>
          <span style={{ fontSize: 10.5, color: 'var(--muted)', fontVariantNumeric: 'tabular-nums', letterSpacing: '.02em' }}>{fmtTempo(cur)} / {fmtTempo(dur)}</span>
          <button type="button" onClick={mudarVel} title="Velocidade de reprodução"
            style={{ border: '1px solid rgba(59,130,246,0.45)', background: vel > 1 ? 'rgba(59,130,246,0.18)' : 'transparent', color: 'var(--gold-2)',
              borderRadius: 7, padding: '1px 8px', fontSize: 10.5, fontWeight: 800, cursor: 'pointer', lineHeight: 1.5, flexShrink: 0 }}>
            {vel}×
          </button>
        </div>
      </div>
    </div>
  )
}
// Dropdown 100% customizado — a lista que abre tambem fica no tema (dourado/vidro, ✓ no selecionado)
function LuxSelect({ value, onChange, options, placeholder = 'Selecione', title, full = false, dropUp = false }) {
  const [aberto, setAberto] = useState(false)
  const atual = options.find((o) => o.value === value)
  const label = atual ? atual.label : placeholder
  return (
    <div style={{ position: 'relative', ...(full ? { width: '100%' } : {}) }} title={title}>
      <button type="button" onClick={() => setAberto((v) => !v)}
        style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'space-between', width: full ? '100%' : 'auto', minWidth: 150, maxWidth: full ? 'none' : 220,
          background: 'rgba(20,28,46,0.55)', border: `1.5px solid rgba(59,130,246,${aberto ? 0.7 : 0.35})`, borderRadius: 6,
          color: (value !== '' && value != null) ? 'var(--gold-2)' : 'var(--muted)', fontWeight: 600, fontSize: 13, padding: '8px 12px', cursor: 'pointer',
          boxShadow: aberto ? '0 0 0 3px rgba(59,130,246,0.14)' : 'none', transition: 'all .18s', backdropFilter: 'blur(6px)', outline: 'none' }}>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" style={{ transform: aberto ? 'rotate(180deg)' : 'none', transition: 'transform .18s', flexShrink: 0 }}><polyline points="6 9 12 15 18 9" /></svg>
      </button>
      {aberto && (
        <>
          <div onClick={() => setAberto(false)} style={{ position: 'fixed', inset: 0, zIndex: 40 }} />
          <div style={{ position: 'absolute', ...(dropUp ? { bottom: '112%' } : { top: '112%' }), left: 0, minWidth: '100%', width: 'max-content', maxWidth: 280, background: 'var(--surface-solid)', border: '1px solid rgba(59,130,246,0.28)', borderRadius: 7, boxShadow: 'var(--shadow-lg)', padding: 6, zIndex: 50, maxHeight: 300, overflowY: 'auto' }}>
            {options.map((o) => {
              const sel = o.value === value
              return (
                <button key={String(o.value)} type="button" onClick={() => { onChange(o.value); setAberto(false) }}
                  style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', textAlign: 'left', border: 'none',
                    background: sel ? 'linear-gradient(90deg, rgba(59,130,246,0.22), rgba(59,130,246,0.05))' : 'transparent',
                    color: sel ? 'var(--gold-2)' : 'var(--text)', padding: '9px 11px', borderRadius: 8, cursor: 'pointer', fontSize: 13, fontWeight: sel ? 700 : 500, transition: 'background .12s', outline: 'none' }}
                  onMouseEnter={(e) => { if (!sel) e.currentTarget.style.background = 'rgba(59,130,246,0.10)' }}
                  onMouseLeave={(e) => { if (!sel) e.currentTarget.style.background = 'transparent' }}>
                  <span style={{ width: 13, flexShrink: 0, color: 'var(--gold)', fontWeight: 800 }}>{sel ? '✓' : ''}</span>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{o.label}</span>
                </button>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
// Cor da tag: QUALIFICADO = verde, DESQUALIFICADO = vermelho, resto = neutro.
// (checa DESQUALIFIC primeiro — senao "DESQUALIFICADO" cairia no match de "QUALIFIC")
function estiloTag(tag) {
  const t = (tag || '').toUpperCase()
  if (t.includes('DESQUALIFIC')) return { background: 'rgba(248,113,113,0.16)', color: '#f87171', border: '1px solid rgba(248,113,113,0.4)' }
  if (t.includes('QUALIFIC')) return { background: 'rgba(52,211,153,0.16)', color: '#34d399', border: '1px solid rgba(52,211,153,0.4)' }
  return { background: 'var(--chip)', color: 'var(--text)', border: '1px solid transparent' }
}
function nomeExibicao(c) {
  const nome = (c?.contato_nome || '').trim()
  if (nome && !nome.includes('@lid')) return nome
  const tel = (c?.contato_telefone || '').trim()
  const soDig = tel.replace(/\D/g, '')
  // numero valido BR tem no max 13 digitos (55 + DDD + 9 digitos). Mais que isso = @lid disfarcado
  if (tel && !tel.includes('@') && soDig.length <= 13) return tel
  return 'Contato WhatsApp'
}
function donoLabel(resp) {
  if (!resp) return ''
  const r = resp.toLowerCase()
  if (r.includes('advogada') || r.includes('advogada')) return 'Advogada'
  if (r.includes('titular')) return 'Titular'
  return resp.split(' ')[0]
}
function statusConv(c) {
  if (c.etapa && /perdid|desqualific/i.test(c.etapa)) return { cor: '#f87171', txt: 'desqualificado' }
  if (c.ia_ativa) return { cor: '#34d399', txt: 'IA' }
  return { cor: '#3b82f6', txt: 'humano' }
}

function Avatar({ nome, tel, foto, size = 42 }) {
  const [erro, setErro] = useState(false)
  const base = { width: size, height: size, borderRadius: '50%', flexShrink: 0 }
  if (foto && !erro) {
    return <img src={foto} alt="" onError={() => setErro(true)} style={{ ...base, objectFit: 'cover' }} />
  }
  return (
    <div style={{ ...base, background: 'linear-gradient(145deg, var(--navy-700), var(--navy-600))', color: '#fff', display: 'grid', placeItems: 'center', fontSize: Math.round(size * 0.34), fontWeight: 700 }}>
      {iniciais(nome, tel)}
    </div>
  )
}

const inp = { width: '100%', padding: '9px 11px', border: '1.5px solid var(--border)', borderRadius: 8, fontSize: 14, fontFamily: 'inherit', background: 'rgba(255,255,255,0.04)', color: 'var(--text)' }
const lbl = { display: 'block', fontSize: 12.5, fontWeight: 600, marginBottom: 5, color: 'var(--text)' }
function Campo({ label, children, full }) {
  return <div style={full ? { gridColumn: '1 / -1' } : undefined}><label style={lbl}>{label}</label>{children}</div>
}

function ClienteModal({ inicial, numeros, pipelines, onFechar, onSalvarNovo, onSalvarFicha }) {
  const ehNovo = !inicial
  const [f, setF] = useState(() => ({
    numero_id: inicial?.numero_id || numeros[0]?.id || '',
    pipeline_id: inicial?.pipeline_id || pipelines[0]?.id || '',
    etapa: inicial?.etapa || '',
    contato_nome: inicial?.contato_nome || '', contato_telefone: inicial?.contato_telefone || '',
    tag: inicial?.tag || '', origem: inicial?.origem || '', responsavel: inicial?.responsavel || '',
    email: inicial?.email || '', handle: inicial?.handle || '', valor: inicial?.valor || 0, notas: inicial?.notas || '',
  }))
  const [salv, setSalv] = useState(false)
  const [err, setErr] = useState('')
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }))
  async function salvar() {
    setSalv(true); setErr('')
    try {
      if (ehNovo) await onSalvarNovo({ ...f, numero_id: Number(f.numero_id), pipeline_id: Number(f.pipeline_id) || null, valor: Number(f.valor) || 0 })
      else await onSalvarFicha(inicial.id, { contato_nome: f.contato_nome, tag: f.tag, origem: f.origem, responsavel: f.responsavel, email: f.email, handle: f.handle, valor: Number(f.valor) || 0, notas: f.notas })
      onFechar()
    } catch (e) { setErr(e.message); setSalv(false) }
  }
  const pipe = pipelines.find((p) => p.id === Number(f.pipeline_id))
  return (
    <div onClick={onFechar} style={{ position: 'fixed', inset: 0, background: 'rgba(1,18,38,.5)', display: 'grid', placeItems: 'center', zIndex: 100, padding: 20 }}>
      <div onClick={(e) => e.stopPropagation()} className="panel" style={{ width: 540, maxWidth: '100%', maxHeight: '90vh', overflowY: 'auto', margin: 0 }}>
        <h2>{ehNovo ? 'Novo cliente' : 'Ficha do cliente'}</h2>
        {err && <div className="alert alert-error" style={{ marginTop: 10 }}>{err}</div>}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 14 }}>
          <Campo label="Nome" full><input style={inp} value={f.contato_nome} onChange={set('contato_nome')} /></Campo>
          {ehNovo && <Campo label="Telefone (DDD+número)"><input style={inp} value={f.contato_telefone} onChange={set('contato_telefone')} placeholder="11999998888" /></Campo>}
          {ehNovo && <Campo label="Número (canal)"><select style={inp} value={f.numero_id} onChange={set('numero_id')}>{numeros.map((n) => <option key={n.id} value={n.id}>{n.nome}</option>)}</select></Campo>}
          {ehNovo && <Campo label="Pipeline"><select style={inp} value={f.pipeline_id} onChange={(e) => setF((x) => ({ ...x, pipeline_id: e.target.value, etapa: '' }))}>{pipelines.map((p) => <option key={p.id} value={p.id}>{p.nome}</option>)}</select></Campo>}
          {ehNovo && <Campo label="Coluna"><select style={inp} value={f.etapa} onChange={set('etapa')}><option value="">(primeira)</option>{(pipe?.stages || []).map((s) => <option key={s.id} value={s.nome}>{s.nome}</option>)}</select></Campo>}
          <Campo label="Tag"><input style={inp} value={f.tag} onChange={set('tag')} placeholder="Quente, Inbound…" /></Campo>
          <Campo label="Origem"><input style={inp} value={f.origem} onChange={set('origem')} placeholder="Campanha, Indicação…" /></Campo>
          <Campo label="Responsável"><input style={inp} value={f.responsavel} onChange={set('responsavel')} /></Campo>
          <Campo label="Valor (R$)"><input style={inp} type="number" value={f.valor} onChange={set('valor')} /></Campo>
          <Campo label="E-mail"><input style={inp} value={f.email} onChange={set('email')} /></Campo>
          <Campo label="@ / Handle"><input style={inp} value={f.handle} onChange={set('handle')} /></Campo>
          <Campo label="Notas" full><textarea style={{ ...inp, minHeight: 70, resize: 'vertical' }} value={f.notas} onChange={set('notas')} /></Campo>
        </div>
        <div style={{ display: 'flex', gap: 10, marginTop: 18, justifyContent: 'flex-end' }}>
          <button className="btn btn-ghost" onClick={onFechar}>Cancelar</button>
          <button className="btn btn-gold" disabled={salv || !f.contato_nome.trim()} onClick={salvar}>{salv ? <span className="spin" /> : 'Salvar'}</button>
        </div>
      </div>
    </div>
  )
}

export default function Atendimento({ vista }) {
  // vista fixa por rota: 'chat' -> conversas | 'crm' -> funil
  const [aba, setAba] = useState(vista === 'chat' ? 'conversas' : 'funil')
  const modo = vista === 'chat' ? 'conversas' : vista === 'crm' ? 'funil' : aba
  const navigate = useNavigate()
  const { user } = useAuth()
  const [sp] = useSearchParams()
  const [usuarios, setUsuarios] = useState([])
  const [numeros, setNumeros] = useState([])
  const [numeroId, setNumeroId] = useState(null)
  const [pipelines, setPipelines] = useState([])
  const [pipeAtivo, setPipeAtivo] = useState(null)
  const [conversas, setConversas] = useState([])
  const [ativaId, setAtivaId] = useState(null)
  const [detalhe, setDetalhe] = useState(null)
  const [texto, setTexto] = useState('')
  const [enviando, setEnviando] = useState(false)
  const [erro, setErro] = useState('')
  const [arrastando, setArrastando] = useState(null)
  const [modalCliente, setModalCliente] = useState(null)   // null | {novo:true} | {conv}
  const listaTimer = useRef(null)
  const chatTimer = useRef(null)
  const fimChat = useRef(null)

  useEffect(() => { carregarNumeros(); carregarPipelines(); carregarUsuarios() }, [])

  // abre conversa vinda do CRM (/chat?c=<id>)
  useEffect(() => {
    const c = sp.get('c')
    if (c) setAtivaId(Number(c))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sp])

  useEffect(() => {
    carregarConversas()
    clearInterval(listaTimer.current)
    listaTimer.current = setInterval(carregarConversas, 6000)
    return () => clearInterval(listaTimer.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [numeroId, modo, pipeAtivo])

  useEffect(() => {
    clearInterval(chatTimer.current)
    if (!ativaId) { setDetalhe(null); return }
    abrirConversa(ativaId)
    chatTimer.current = setInterval(() => abrirConversa(ativaId, true), 4000)
    return () => clearInterval(chatTimer.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ativaId])

  // (o scroll da conversa e controlado dentro do Inbox — gruda no fundo so quando faz sentido)

  async function carregarNumeros() {
    try { setNumeros(await api('/api/atendimento/numeros')) } catch (e) { setErro(e.message) }
  }
  async function carregarUsuarios() {
    try {
      const r = await api('/api/auth/usuarios')
      setUsuarios(Array.isArray(r) ? r : (r?.items || []))  // endpoint devolve {items:[...]}
    } catch (e) { /* nao-admin: segue sem lista */ }
  }
  // Distribuir a conversa pra um responsavel (aparece no CRM dele). Nao mexe na IA.
  async function distribuir(id, email) {
    try {
      await api(`/api/atendimento/conversas/${id}/atribuir`, { method: 'POST', body: { atendente_email: email, assumir_ia: false } })
      await abrirConversa(id, true); carregarConversas()
    } catch (e) { setErro(e.message) }
  }
  // Aceitar a conversa (triagem): sai da fila "Nao lidas".
  async function aceitarConversa(id) {
    try {
      await api(`/api/atendimento/conversas/${id}/aceitar`, { method: 'POST', body: { aceita: true } })
      await abrirConversa(id, true); carregarConversas()
    } catch (e) { setErro(e.message) }
  }
  // Recusar a conversa (triagem): sai da fila "Nao lidas", marcada como recusada.
  async function recusarConversa(id) {
    try {
      await api(`/api/atendimento/conversas/${id}/recusar`, { method: 'POST', body: { aceita: true } })
      await abrirConversa(id, true); carregarConversas()
    } catch (e) { setErro(e.message) }
  }
  // Excluir conversa + contato + mensagens (destrutivo, so admin).
  async function excluirConversa(id) {
    if (!window.confirm('Excluir esta conversa e o contato?\n\nTodas as mensagens e áudios serão apagados. Isso NÃO pode ser desfeito.')) return
    try {
      await api(`/api/atendimento/conversas/${id}`, { method: 'DELETE' })
      if (ativaId === id) { setAtivaId(null); setDetalhe(null) }
      setConversas((cs) => cs.filter((c) => c.id !== id))
      carregarConversas()
    } catch (e) { setErro(e.message) }
  }
  async function carregarPipelines() {
    try {
      const pls = await api('/api/atendimento/pipelines')
      setPipelines(pls)
      setPipeAtivo((cur) => cur ?? (pls[0]?.id ?? null))
    } catch (e) { setErro(e.message) }
  }
  async function carregarConversas() {
    try {
      const q = new URLSearchParams({ status: 'aberto' })
      if (numeroId) q.set('numero_id', numeroId)
      if (modo === 'funil' && pipeAtivo) q.set('pipeline_id', pipeAtivo)
      setConversas(await api(`/api/atendimento/conversas?${q.toString()}`))
    } catch (e) { setErro(e.message) }
  }
  async function abrirConversa(id, silencioso = false) {
    try {
      const d = await api(`/api/atendimento/conversas/${id}/mensagens`)
      setDetalhe(d)
      if (!silencioso) setConversas((cs) => cs.map((c) => (c.id === id ? { ...c, nao_lidas: 0 } : c)))
    } catch (e) { setErro(e.message) }
  }
  async function responder(e) {
    e.preventDefault()
    if (!texto.trim() || !ativaId) return
    setEnviando(true); setErro('')
    try {
      await api(`/api/atendimento/conversas/${ativaId}/responder`, { method: 'POST', body: { texto: texto.trim() } })
      setTexto(''); await abrirConversa(ativaId, true); carregarConversas()
    } catch (e) { setErro(e.message) }
    setEnviando(false)
  }
  async function mover(id, etapa, pipeline_id) {
    try {
      await api(`/api/atendimento/conversas/${id}/etapa`, { method: 'POST', body: { etapa, pipeline_id } })
      carregarConversas()
      if (id === ativaId) abrirConversa(id, true)
    } catch (e) { setErro(e.message) }
  }
  async function toggleIA(id, ativa) {
    try {
      await api(`/api/atendimento/conversas/${id}/ia`, { method: 'POST', body: { ativa } })
      await abrirConversa(id, true); carregarConversas()
    } catch (e) { setErro(e.message) }
  }
  // Disparar IA de atendimento: SEMPRE liga + faz responder agora (1 clique, sem toggle).
  // A resposta vem em background — recarrega algumas vezes pra ela aparecer sozinha.
  async function dispararIA(id) {
    try {
      await api(`/api/atendimento/conversas/${id}/ia`, { method: 'POST', body: { ativa: true } })
      await abrirConversa(id, true); carregarConversas()
      for (const t of [1500, 3500, 6500]) setTimeout(() => { abrirConversa(id, true); carregarConversas() }, t)
    } catch (e) { setErro(e.message) }
  }
  async function renomearPipeline(p) {
    const nome = prompt('Renomear pipeline:', p.nome)
    if (!nome?.trim() || nome.trim() === p.nome) return
    try { await api(`/api/atendimento/pipelines/${p.id}`, { method: 'PATCH', body: { nome: nome.trim() } }); carregarPipelines() }
    catch (e) { setErro(e.message) }
  }
  async function salvarFicha(id, dados) {
    await api(`/api/atendimento/conversas/${id}/dados`, { method: 'PATCH', body: dados })
    await abrirConversa(id, true); carregarConversas()
  }
  async function criarCliente(dados) {
    const r = await api('/api/atendimento/conversas', { method: 'POST', body: dados })
    carregarConversas()
    if (r && r.reusado) {
      alert('Esse número já estava cadastrado — abri a conversa que já existe. Nada foi duplicado. ✅')
      if (r.id) setAtivaId(r.id)
    }
    return r
  }
  async function enviarNota(id, texto) {
    if (!texto?.trim()) return
    await api(`/api/atendimento/conversas/${id}/nota`, { method: 'POST', body: { texto: texto.trim() } })
    await abrirConversa(id, true)
  }
  async function enviarAudio(id, blob) {
    const form = new FormData()
    form.append('arquivo', blob, 'audio.webm')
    await api(`/api/atendimento/conversas/${id}/audio`, { method: 'POST', form })
    await abrirConversa(id, true); carregarConversas()
  }
  async function enviarMidia(id, file, caption = '') {
    const form = new FormData()
    form.append('arquivo', file)
    if (caption) form.append('caption', caption)
    await api(`/api/atendimento/conversas/${id}/midia`, { method: 'POST', form })
    await abrirConversa(id, true); carregarConversas()
  }
  async function enviarFigurinha(id, figurinhaId) {
    await api(`/api/atendimento/conversas/${id}/figurinha`, { method: 'POST', body: { figurinha_id: figurinhaId } })
    await abrirConversa(id, true); carregarConversas()
  }
  async function novaPipeline() {
    const nome = prompt('Nome do novo pipeline:')
    if (!nome?.trim()) return
    const pessoal = window.confirm('Esse pipeline é PESSOAL (só você vê)?\n\nOK = pessoal (privado)\nCancelar = compartilhado com a equipe')
    try { await api('/api/atendimento/pipelines', { method: 'POST', body: { nome: nome.trim(), pessoal } }); carregarPipelines() }
    catch (e) { setErro(e.message) }
  }
  async function novaColuna(pipeline_id) {
    const nome = prompt('Nome da nova coluna:')
    if (!nome?.trim()) return
    try { await api(`/api/atendimento/pipelines/${pipeline_id}/stages`, { method: 'POST', body: { nome: nome.trim() } }); carregarPipelines() }
    catch (e) { setErro(e.message) }
  }
  async function renomearColuna(stage) {
    const nome = prompt('Renomear coluna:', stage.nome)
    if (!nome?.trim() || nome.trim() === stage.nome) return
    try { await api(`/api/atendimento/stages/${stage.id}`, { method: 'PATCH', body: { nome: nome.trim() } }); carregarPipelines(); carregarConversas() }
    catch (e) { setErro(e.message) }
  }
  async function excluirColuna(stage) {
    if (!confirm(`Excluir a coluna "${stage.nome}"? (precisa estar vazia)`)) return
    try { await api(`/api/atendimento/stages/${stage.id}`, { method: 'DELETE' }); carregarPipelines() }
    catch (e) { setErro(e.message) }
  }

  const pipe = pipelines.find((p) => p.id === pipeAtivo)
  const conv = detalhe?.conversa
  const nomeNumero = (id) => numeros.find((n) => n.id === id)?.nome || ''

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      {/* Abas + filtro numero */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap', flexShrink: 0 }}>
        {!vista && (
          <div style={{ display: 'inline-flex', background: 'var(--chip)', borderRadius: 6, padding: 4 }}>
            {[['funil', 'Funil (CRM)'], ['conversas', 'Conversas']].map(([k, label]) => (
              <button key={k} onClick={() => setAba(k)}
                style={{ border: 'none', borderRadius: 8, padding: '8px 18px', fontWeight: 600, fontSize: 14,
                  background: aba === k ? '#fff' : 'transparent', color: aba === k ? 'var(--navy-800)' : 'var(--muted)',
                  boxShadow: aba === k ? 'var(--shadow)' : 'none' }}>{label}</button>
            ))}
          </div>
        )}
        {modo !== 'conversas' && <div style={{ fontWeight: 700, fontSize: 18 }}>CRM</div>}
        <button className="btn btn-gold btn-sm" onClick={() => setModalCliente({ novo: true })}>+ Novo cliente</button>
      </div>

      {erro && <div className="alert alert-error">{erro}</div>}

      <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
      {modo === 'funil' ? (
        <div style={{ flex: 1, display: 'flex', gap: 14, alignItems: 'stretch', minHeight: 0, minWidth: 0 }}>
          {/* SIDEBAR de pipelines */}
          <div className="panel" style={{ flex: '0 0 220px', padding: 12, margin: 0, overflowY: 'auto' }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.04em', padding: '4px 6px 10px' }}>Pipelines</div>
            {pipelines.map((p) => (
              <div key={p.id} style={{ display: 'flex', alignItems: 'center', borderRadius: 6, marginBottom: 4,
                background: p.id === pipeAtivo ? 'linear-gradient(145deg, rgba(59,130,246,0.18), rgba(59,130,246,0.05))' : 'transparent',
                boxShadow: p.id === pipeAtivo ? 'inset 0 0 0 1px rgba(59,130,246,0.28)' : 'none' }}>
                <button onClick={() => setPipeAtivo(p.id)}
                  style={{ flex: 1, textAlign: 'left', border: 'none', background: 'transparent', padding: '10px 12px', display: 'flex', alignItems: 'center', gap: 6,
                    fontSize: 13.5, fontWeight: 600, cursor: 'pointer', color: p.id === pipeAtivo ? 'var(--gold-2)' : 'var(--text)',
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {p.pessoal && (
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, opacity: 0.7 }} title="Pessoal (só você vê)"><rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></svg>
                  )}
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.nome}</span>
                </button>
                <button onClick={() => renomearPipeline(p)} title="Renomear pipeline"
                  style={{ border: 'none', background: 'transparent', color: p.id === pipeAtivo ? 'var(--gold)' : 'var(--muted)', padding: '0 10px', fontSize: 13, cursor: 'pointer', opacity: 0.75 }}>✎</button>
              </div>
            ))}
            <button onClick={novaPipeline} className="btn btn-ghost btn-sm btn-block" style={{ marginTop: 8 }}>+ Novo pipeline</button>
          </div>

          {/* KANBAN do pipeline ativo */}
          <div style={{ flex: 1, minWidth: 0, overflow: 'auto' }}>
            {!pipe && <p className="muted">Selecione um pipeline.</p>}
            {pipe && (
              <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', height: '100%' }}>
                {pipe.stages.map((st) => {
                  const cards = conversas.filter((c) => c.etapa === st.nome)
                  return (
                    <div key={st.id}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={() => { if (arrastando != null) { mover(arrastando, st.nome, pipe.id); setArrastando(null) } }}
                      style={{ flex: '0 0 250px', background: 'var(--col-bg)', borderRadius: 7, padding: 10, minHeight: 200 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 6px 12px' }}>
                        <span style={{ width: 9, height: 9, borderRadius: '50%', background: st.cor }} />
                        <span onDoubleClick={() => renomearColuna(st)} title="2 cliques p/ renomear"
                          style={{ fontWeight: 700, fontSize: 13, cursor: 'text', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{st.nome}</span>
                        <span style={{ background: 'var(--surface-solid)', color: 'var(--muted)', borderRadius: 7, fontSize: 12, fontWeight: 600, padding: '1px 8px' }}>{cards.length}</span>
                        <span style={{ marginLeft: 'auto', display: 'flex', gap: 2 }}>
                          <button onClick={() => renomearColuna(st)} title="Renomear coluna"
                            style={{ border: 'none', background: 'transparent', color: 'var(--muted)', fontSize: 13, padding: 2, lineHeight: 1 }}>✎</button>
                          <button onClick={() => excluirColuna(st)} title="Excluir coluna"
                            style={{ border: 'none', background: 'transparent', color: 'var(--muted)', fontSize: 14, padding: 2, lineHeight: 1 }}>×</button>
                        </span>
                      </div>
                      {st.descricao && (
                        <div style={{ fontSize: 11, color: 'var(--muted)', padding: '0 6px 12px', marginTop: -6, lineHeight: 1.4, borderLeft: `2px solid ${st.cor}`, paddingLeft: 8, marginLeft: 4 }}>{st.descricao}</div>
                      )}
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {cards.map((c) => (
                          <div key={c.id} draggable
                            onDragStart={() => setArrastando(c.id)} onDragEnd={() => setArrastando(null)}
                            onClick={() => navigate(`/chat?c=${c.id}`)}
                            style={{ background: 'var(--surface-solid)', borderRadius: 6, padding: 12, boxShadow: 'var(--shadow)', cursor: 'grab', borderLeft: `3px solid ${st.cor}` }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                              <Avatar nome={nomeExibicao(c)} tel="" foto={c.foto_url} size={30} />
                              <div style={{ minWidth: 0, flex: 1 }}>
                                <div style={{ fontWeight: 600, fontSize: 13.5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{nomeExibicao(c)}</div>
                                <div className="muted" style={{ fontSize: 11 }}>{horaCurta(c.atualizado_em)}</div>
                              </div>
                              {c.e_reclamante && <span title="Reclamante">🚛</span>}
                            </div>
                            <div className="muted" style={{ fontSize: 12.5, marginTop: 8, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{c.ultima_mensagem || '—'}</div>
                            {(c.tag || c.origem || c.valor > 0) && (
                              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 6 }}>
                                {c.tag && <span style={{ ...estiloTag(c.tag), fontSize: 10, borderRadius: 6, padding: '1px 7px', fontWeight: 700 }}>{c.tag}</span>}
                                {c.origem && <span style={{ fontSize: 10, background: 'rgba(139,92,246,0.16)', color: '#6b21a8', borderRadius: 6, padding: '1px 6px', fontWeight: 600 }}>{c.origem}</span>}
                                {c.valor > 0 && <span style={{ fontSize: 10, background: 'rgba(52,211,153,0.14)', color: 'var(--success)', borderRadius: 6, padding: '1px 6px', fontWeight: 700 }}>R$ {c.valor}</span>}
                              </div>
                            )}
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8 }}>
                              <span className="muted" style={{ fontSize: 11 }}>{nomeNumero(c.numero_id)}</span>
                              {c.ia_ativa
                                ? <span style={{ marginLeft: 'auto', fontSize: 10.5, color: 'var(--success)', fontWeight: 600 }}>● IA</span>
                                : <span style={{ marginLeft: 'auto', fontSize: 10.5, color: 'var(--muted)', fontWeight: 600 }}>● humano</span>}
                              {c.nao_lidas > 0 && <span style={{ background: 'var(--navy-800)', color: '#fff', borderRadius: 6, fontSize: 10.5, fontWeight: 700, padding: '1px 7px' }}>{c.nao_lidas}</span>}
                            </div>
                          </div>
                        ))}
                        {cards.length === 0 && <div style={{ color: '#aab4c5', fontSize: 12.5, textAlign: 'center', padding: '14px 0' }}>vazio</div>}
                      </div>
                    </div>
                  )
                })}
                <button onClick={() => novaColuna(pipe.id)} className="btn btn-ghost btn-sm" style={{ flex: '0 0 auto', height: 40 }}>+ Coluna</button>
              </div>
            )}
          </div>
        </div>
      ) : (
        <Inbox
          conversas={conversas} ativaId={ativaId} setAtivaId={setAtivaId}
          conv={conv} detalhe={detalhe} nomeNumero={nomeNumero} numeros={numeros}
          texto={texto} setTexto={setTexto} enviando={enviando} responder={responder}
          pipelines={pipelines} mover={mover} toggleIA={toggleIA} dispararIA={dispararIA} fimChat={fimChat}
          usuarios={usuarios} user={user} distribuir={distribuir} excluirConversa={excluirConversa} aceitarConversa={aceitarConversa} recusarConversa={recusarConversa}
          onEditarFicha={(c) => setModalCliente({ conv: c })} enviarNota={enviarNota} enviarAudio={enviarAudio} enviarMidia={enviarMidia} enviarFigurinha={enviarFigurinha}
        />
      )}
      </div>

      {modalCliente && (
        <ClienteModal
          inicial={modalCliente.conv || null}
          numeros={numeros} pipelines={pipelines}
          onFechar={() => setModalCliente(null)}
          onSalvarNovo={criarCliente} onSalvarFicha={salvarFicha}
        />
      )}
    </div>
  )
}

/* ---------- INBOX / CHAT ---------- */
function Inbox({ conversas, ativaId, setAtivaId, conv, detalhe, nomeNumero, numeros, texto, setTexto, enviando, responder, pipelines, mover, toggleIA, dispararIA, usuarios = [], user, distribuir, excluirConversa, aceitarConversa, recusarConversa, onEditarFicha, enviarNota, enviarAudio, enviarMidia, enviarFigurinha, fimChat }) {
  const [nota, setNota] = useState(false)
  const [menuConv, setMenuConv] = useState(false)
  const [busca, setBusca] = useState('')
  const [globais, setGlobais] = useState([])
  // busca GLOBAL de contatos (acha qualquer um, mesmo fora da lista atual) — puxa pro chat
  useEffect(() => {
    const q = busca.trim()
    if (q.length < 2) { setGlobais([]); return }
    let vivo = true
    const t = setTimeout(async () => {
      try { const r = await api(`/api/atendimento/contatos?q=${encodeURIComponent(q)}`); if (vivo) setGlobais(r.items || []) }
      catch (e) { /* ignore */ }
    }, 300)
    return () => { vivo = false; clearTimeout(t) }
  }, [busca])
  const [filtro, setFiltro] = useState('todas')  // todas | nao_lidas (= fila de triagem)
  const chaveResp = 'esc_resp_' + (user?.email || '')
  const [respFiltro, setRespFiltro] = useState(() => localStorage.getItem(chaveResp) || '')  // '' = todos
  function mudarResp(v) { setRespFiltro(v); localStorage.setItem(chaveResp, v) }
  const nomeResp = (email) => usuarios.find((u) => u.email === email)?.nome || email
  // "Nao lidas" = ainda na coluna inicial do pipeline (nao triada). So sai quando muda o status no CRM.
  function etapaInicial(pipelineId) {
    const p = pipelines.find((x) => x.id === pipelineId)
    if (!p || !p.stages || !p.stages.length) return null
    return [...p.stages].sort((a, b) => (a.ordem || 0) - (b.ordem || 0))[0].nome
  }
  function naoTriada(c) {
    if (c.aceita || c.recusada) return false  // aceita ou recusada -> saiu da fila "Nao lidas"
    const ini = etapaInicial(c.pipeline_id)
    if (!ini) return (c.nao_lidas || 0) > 0  // fallback se nao souber o pipeline
    return c.etapa === ini
  }
  const porResp = respFiltro ? conversas.filter((c) => (c.atendente_email || '') === respFiltro) : conversas
  const listaNaoTriada = porResp.filter(naoTriada)
  const naoLidasTotal = listaNaoTriada.length
  // busca por nome OU numero
  const bq = busca.trim().toLowerCase()
  const bdig = bq.replace(/\D/g, '')
  const conversasFiltradas = (filtro === 'nao_lidas' ? listaNaoTriada : porResp).filter((c) =>
    !bq || nomeExibicao(c).toLowerCase().includes(bq) || (bdig && (c.contato_telefone || '').includes(bdig)))
  // resultados da busca global que NAO estao na lista atual (evita repetir)
  const idsLocais = new Set(conversasFiltradas.map((c) => c.id))
  const extras = bq ? globais.filter((g) => !idsLocais.has(g.id)) : []
  const [gravando, setGravando] = useState(false)
  const [enviandoAudio, setEnviandoAudio] = useState(false)
  const [showEmoji, setShowEmoji] = useState(false)
  const [enviandoMidia, setEnviandoMidia] = useState(false)
  const fileRef = useRef(null)
  const mediaRec = useRef(null)
  const chunks = useRef([])
  const cancelado = useRef(false)
  async function anexar(e) {
    const f = e.target.files?.[0]; e.target.value = ''
    if (!f) return
    setEnviandoMidia(true)
    try { await enviarMidia(conv.id, f) } catch (er) { alert('Falha ao enviar o anexo.') }
    setEnviandoMidia(false)
  }
  // ---- Figurinhas (galeria + envio) ----
  const [showFig, setShowFig] = useState(false)
  const [figs, setFigs] = useState([])
  const [enviandoFig, setEnviandoFig] = useState(false)
  const figFileRef = useRef(null)
  async function carregarFigs() {
    try { const r = await api('/api/atendimento/figurinhas'); setFigs(r.items || []) } catch {}
  }
  function abrirFigs() { setShowFig((v) => { const nv = !v; if (nv) carregarFigs(); return nv }) }
  async function mandarFig(figId) {
    if (enviandoFig) return
    setEnviandoFig(true)
    try { await enviarFigurinha(conv.id, figId); setShowFig(false) }
    catch (er) { alert('Falha ao enviar a figurinha.') }
    setEnviandoFig(false)
  }
  async function subirFig(e) {
    const f = e.target.files?.[0]; e.target.value = ''
    if (!f) return
    setEnviandoFig(true)
    try {
      const form = new FormData(); form.append('arquivo', f)
      await api('/api/atendimento/figurinhas', { method: 'POST', form })
      await carregarFigs()
    } catch (er) { alert('Falha ao subir a figurinha.') }
    setEnviandoFig(false)
  }
  async function apagarFig(figId) {
    if (!window.confirm('Remover essa figurinha da galeria?')) return
    try { await api(`/api/atendimento/figurinhas/${figId}`, { method: 'DELETE' }); await carregarFigs() } catch {}
  }
  const [salvandoFig, setSalvandoFig] = useState('')
  async function salvarRecebida(url) {
    if (salvandoFig) return
    setSalvandoFig(url)
    try { await api('/api/atendimento/figurinhas/salvar', { method: 'POST', body: { url } }); await carregarFigs() }
    catch (er) { alert('Falha ao salvar a figurinha.') }
    setSalvandoFig('')
  }
  // Sobe um arquivo que o lead mandou direto pra pasta dele no Google Drive.
  const [subindoDrive, setSubindoDrive] = useState(0)
  const [subidos, setSubidos] = useState({})
  async function subirDrive(m) {
    if (subindoDrive) return
    setSubindoDrive(m.id)
    try {
      const r = await api(`/api/atendimento/conversas/${conv.id}/subir-drive`, { method: 'POST', body: { message_ids: [m.id] } })
      const it = (r.itens || []).find((x) => x.id === m.id)
      if (it && it.ok) setSubidos((s) => ({ ...s, [m.id]: it.url || r.pasta_url }))
      else alert((it && it.erro) || r.erro || 'Não consegui subir esse arquivo.')
    } catch (er) { alert('Falha ao subir para o Drive.') }
    setSubindoDrive(0)
  }
  async function gravar() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      chunks.current = []
      cancelado.current = false
      mr.ondataavailable = (e) => { if (e.data.size) chunks.current.push(e.data) }
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        if (cancelado.current) { cancelado.current = false; return }  // descartou: nao envia
        const blob = new Blob(chunks.current, { type: mr.mimeType || 'audio/webm' })
        setEnviandoAudio(true)
        try { await enviarAudio(conv.id, blob) } catch (e) { alert('Falha ao enviar o áudio.') }
        setEnviandoAudio(false)
      }
      mr.start(); mediaRec.current = mr; setGravando(true)
    } catch (e) { alert('Não consegui acessar o microfone. Permita o acesso no navegador.') }
  }
  function pararGravar() { try { mediaRec.current?.stop() } catch (e) { /* noop */ } setGravando(false) }
  function cancelarGravar() { cancelado.current = true; try { mediaRec.current?.stop() } catch (e) { /* noop */ } setGravando(false) }
  const scrollRef = useRef(null)
  const grudarFundo = useRef(true)
  // ao abrir/trocar de conversa, começa grudado no fim (mostra as últimas)
  useEffect(() => { grudarFundo.current = true }, [conv?.id])
  // quando muda o nº de mensagens: só desce se você já estava no fim
  useEffect(() => {
    const el = scrollRef.current
    if (el && grudarFundo.current) el.scrollTop = el.scrollHeight
  }, [detalhe?.mensagens?.length, conv?.id])
  function aoRolar() {
    const el = scrollRef.current
    if (el) grudarFundo.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120
  }
  const pipeConv = pipelines.find((p) => p.id === conv?.pipeline_id)
  const agenteNome = numeros.find((n) => n.id === conv?.numero_id)?.agente === 'alicia' ? 'Alícia' : 'IA de atendimento'
  return (
    <div style={{ display: 'flex', gap: 16, alignItems: 'stretch', flex: 1, minHeight: 0, width: '100%' }}>
      <div className="panel" style={{ flex: '0 0 320px', padding: 0, margin: 0, overflowY: 'auto' }}>
        <div style={{ position: 'sticky', top: 0, zIndex: 5, padding: '15px 16px 12px', borderBottom: '1px solid var(--border)', background: 'linear-gradient(180deg, rgba(59,130,246,0.12), rgba(10,15,28,0.85))', backdropFilter: 'blur(8px)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
            <span style={{ fontSize: 15.5, fontWeight: 800, letterSpacing: '.01em', ...PRATA }}>Chat ao Vivo</span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 10, fontWeight: 800, letterSpacing: '.06em', color: '#4ade80', background: 'rgba(74,222,128,0.12)', border: '1px solid rgba(74,222,128,0.32)', borderRadius: 7, padding: '3px 9px' }}>
              <span className="live-dot" /> AO VIVO
            </span>
          </div>
          <div style={{ position: 'relative', marginTop: 10 }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" strokeWidth="2" strokeLinecap="round"
              style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }}>
              <circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input value={busca} onChange={(e) => setBusca(e.target.value)} placeholder="Buscar por nome ou número…"
              style={{ width: '100%', padding: '8px 30px 8px 32px', borderRadius: 6, fontSize: 12.5,
                border: '1px solid var(--border)', background: 'var(--surface-2)', color: 'var(--text)', outline: 'none' }} />
            {busca && (
              <button type="button" onClick={() => setBusca('')} title="Limpar"
                style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', border: 'none', background: 'transparent', color: 'var(--muted)', cursor: 'pointer', fontSize: 15, lineHeight: 1, padding: 2 }}>×</button>
            )}
          </div>
          <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
            {[['todas', 'Todas'], ['nao_lidas', 'Não lidas']].map(([k, label]) => (
              <button key={k} type="button" onClick={() => setFiltro(k)}
                style={{ flex: 1, fontSize: 12, fontWeight: 700, padding: '7px 0', borderRadius: 6, cursor: 'pointer',
                  border: filtro === k ? '1px solid rgba(59,130,246,0.55)' : '1px solid var(--border)',
                  background: filtro === k ? 'linear-gradient(180deg, rgba(59,130,246,0.24), rgba(59,130,246,0.07))' : 'transparent',
                  color: filtro === k ? 'var(--gold-2)' : 'var(--muted)', transition: 'all .15s' }}>
                {label}{k === 'nao_lidas' && naoLidasTotal > 0 ? ` · ${naoLidasTotal}` : ''}
              </button>
            ))}
          </div>
          {usuarios.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <LuxSelect full title="Ver o CRM de quem" value={respFiltro} placeholder="Todos os responsáveis"
                options={[{ value: '', label: 'Todos os responsáveis' }, ...usuarios.map((u) => ({ value: u.email, label: u.nome }))]}
                onChange={(val) => mudarResp(val)} />
            </div>
          )}
        </div>
        {conversasFiltradas.length === 0 && extras.length === 0 && (
          <p className="muted" style={{ padding: 24 }}>
            {bq ? 'Nenhum contato encontrado.' : filtro === 'nao_lidas' ? 'Fila zerada — tudo triado. 🎉' : 'Nenhuma conversa.'}
          </p>
        )}
        {conversasFiltradas.map((c) => (
          <button key={c.id} onClick={() => setAtivaId(c.id)}
            style={{ display: 'flex', gap: 12, width: '100%', textAlign: 'left', background: c.id === ativaId ? 'var(--active)' : 'transparent',
              border: 'none', borderBottom: '1px solid var(--border)', borderLeft: c.id === ativaId ? '3px solid var(--navy-600)' : '3px solid transparent', padding: '14px 16px', alignItems: 'center' }}>
            <Avatar nome={nomeExibicao(c)} tel="" foto={c.foto_url} size={42} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ fontWeight: 700, fontSize: 14, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', ...PRATA }}>{nomeExibicao(c)}</span>
                <span className="muted" style={{ fontSize: 11, flexShrink: 0 }}>{horaCurta(c.atualizado_em)}</span>
              </div>
              <div style={{ fontSize: 12.5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', marginTop: 2, color: '#f4f7fc' }}>{c.ultima_mensagem || '—'}</div>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 6 }}>
                <span style={{ fontSize: 10.5, color: 'var(--gold-2)', background: 'var(--chip)', borderRadius: 7, padding: '1px 8px', fontWeight: 600 }}>{donoLabel(c.responsavel) || c.etapa}</span>
                {c.atendente_email && <span title={`Distribuída para ${nomeResp(c.atendente_email)}`} style={{ fontSize: 10, color: '#7dd3fc', fontWeight: 700 }}>↪ {(nomeResp(c.atendente_email) || '').split(' ')[0]}</span>}
                {c.e_reclamante && <span title="Reclamante">🚛</span>}
                {(() => { const s = statusConv(c); return <span style={{ fontSize: 10.5, color: s.cor, fontWeight: 600 }}>● {s.txt}</span> })()}
                {c.nao_lidas > 0 && <span style={{ marginLeft: 'auto', background: 'var(--gold-soft)', color: '#ffffff', borderRadius: 6, fontSize: 11, fontWeight: 700, padding: '1px 7px' }}>{c.nao_lidas}</span>}
              </div>
            </div>
          </button>
        ))}
        {extras.length > 0 && (
          <>
            <div style={{ padding: '10px 16px 5px', fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.06em', color: 'var(--muted)', borderTop: '1px solid var(--border)' }}>
              Outros contatos · {extras.length}
            </div>
            {extras.map((c) => (
              <button key={'g' + c.id} onClick={() => setAtivaId(c.id)}
                style={{ display: 'flex', gap: 12, width: '100%', textAlign: 'left', background: 'transparent',
                  border: 'none', borderBottom: '1px solid var(--border)', borderLeft: '3px solid transparent', padding: '12px 16px', alignItems: 'center' }}>
                <Avatar nome={nomeExibicao(c)} tel="" foto={c.foto_url} size={38} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 700, fontSize: 13.5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', ...PRATA }}>{nomeExibicao(c)}</div>
                  <div className="muted" style={{ fontSize: 11.5, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.contato_telefone}{c.etapa ? ` · ${c.etapa}` : ''}</div>
                </div>
                <span style={{ fontSize: 11, color: 'var(--gold-2)', flexShrink: 0, fontWeight: 700 }}>abrir →</span>
              </button>
            ))}
          </>
        )}
      </div>

      <div className="panel" style={{ flex: 1, padding: 0, margin: 0, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        {!conv && (
          <div style={{ margin: 'auto', color: 'var(--muted)', textAlign: 'center', padding: 24 }}>
            <div style={{ width: 96, height: 96, margin: '0 auto 18px', borderRadius: '50%', display: 'grid', placeItems: 'center', fontSize: 42, background: 'radial-gradient(circle at 50% 45%, rgba(59,130,246,0.20), rgba(59,130,246,0) 70%)', border: '1px solid rgba(59,130,246,0.28)', boxShadow: '0 0 40px rgba(59,130,246,0.10) inset' }}>💬</div>
            <div style={{ fontSize: 16, fontWeight: 800, ...PRATA }}>Selecione uma conversa</div>
            <div style={{ fontSize: 12.5, marginTop: 6, maxWidth: 280, marginLeft: 'auto', marginRight: 'auto', lineHeight: 1.5 }}>a IA atendem em tempo real. Assuma quando quiser.</div>
          </div>
        )}
        {conv && (
          <>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <Avatar nome={nomeExibicao(conv)} tel="" foto={conv.foto_url} size={40} />
              <div style={{ flex: '1 1 210px', minWidth: 165 }}>
                <div style={{ fontWeight: 800, fontSize: 15.5, lineHeight: 1.2, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden', wordBreak: 'break-word', ...PRATA }}>{nomeExibicao(conv)}</div>
                <div className="muted" style={{ fontSize: 12, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', marginTop: 2 }}>{conv.contato_telefone}</div>
              </div>
              {conv.contato_telefone && !String(conv.contato_telefone).includes('@') && (
                <button onClick={() => window.dispatchEvent(new CustomEvent('esc:ligar', { detail: { numero: conv.contato_telefone, nome: nomeExibicao(conv), conversaId: conv.id } }))}
                  className="btn btn-sm" title="Ligar para o lead pelo sistema (softphone)"
                  style={{ background: 'var(--success)', color: '#08130d', border: 'none', fontWeight: 700, whiteSpace: 'nowrap', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.6a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.5-1.2a2 2 0 0 1 2.1-.5c.8.3 1.7.6 2.6.7a2 2 0 0 1 1.7 2z" /></svg>
                  Ligar</button>
              )}
              <LuxSelect title="Pipeline / funil" value={conv.pipeline_id || ''}
                options={pipelines.map((p) => ({ value: p.id, label: p.nome }))}
                onChange={(val) => { const p = pipelines.find((x) => x.id === val); mover(conv.id, p?.stages?.[0]?.nome, p?.id) }} />
              <LuxSelect title="Etapa do CRM" value={conv.etapa}
                options={(pipeConv?.stages || []).map((s) => ({ value: s.nome, label: s.nome }))}
                onChange={(val) => mover(conv.id, val, conv.pipeline_id)} />
              {conv.ia_ativa ? (
                <>
                  <button onClick={() => dispararIA(conv.id)} className="btn btn-sm" title={`Fazer ${agenteNome} responder agora`}
                    style={{ background: 'var(--chip)', color: 'var(--gold)', border: '1.5px solid rgba(59,130,246,0.45)', whiteSpace: 'nowrap' }}>↻ Responder agora</button>
                  <button onClick={() => toggleIA(conv.id, false)} className="btn btn-sm" title={`Desligar ${agenteNome}`}
                    style={{ background: 'var(--success)', color: '#08130d', border: 'none', whiteSpace: 'nowrap' }}>● {agenteNome} ligado</button>
                </>
              ) : (
                <button onClick={() => dispararIA(conv.id)} className="btn btn-sm" title={`Ligar e disparar ${agenteNome}`}
                  style={{ background: 'transparent', color: 'var(--gold)', border: '1.5px solid rgba(59,130,246,0.4)', whiteSpace: 'nowrap' }}>▶ Ligar {agenteNome}</button>
              )}
              {usuarios.length > 0 && (
                <LuxSelect title="Distribuir esta conversa para o CRM de alguém" value={conv.atendente_email || ''} placeholder="Distribuir para…"
                  options={[{ value: '', label: 'Distribuir para…' }, ...usuarios.map((u) => ({ value: u.email, label: u.nome }))]}
                  onChange={(val) => distribuir(conv.id, val)} />
              )}
              <button onClick={() => onEditarFicha(conv)} className="btn btn-sm btn-ghost" title="Editar ficha do cliente">Ficha</button>
              {user?.papel === 'admin' && (
                <div style={{ position: 'relative' }}>
                  <button onClick={() => setMenuConv((v) => !v)} className="btn btn-sm btn-ghost" title="Mais ações" style={{ padding: '6px 11px', fontSize: 18, lineHeight: 1 }}>⋮</button>
                  {menuConv && (
                    <>
                      <div onClick={() => setMenuConv(false)} style={{ position: 'fixed', inset: 0, zIndex: 20 }} />
                      <div style={{ position: 'absolute', top: '112%', right: 0, background: 'var(--surface-solid)', border: '1px solid var(--border)', borderRadius: 7, boxShadow: 'var(--shadow-lg)', padding: 6, zIndex: 30, minWidth: 220 }}>
                        <button onClick={() => { setMenuConv(false); excluirConversa(conv.id) }}
                          style={{ display: 'flex', gap: 9, width: '100%', textAlign: 'left', alignItems: 'center', border: 'none', background: 'transparent', color: 'var(--danger)', padding: '10px 12px', borderRadius: 8, cursor: 'pointer', fontWeight: 600, fontSize: 13 }}
                          onMouseEnter={(e) => e.currentTarget.style.background = 'rgba(248,113,113,0.12)'} onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}>
                          <TrashIcon size={15} /> Excluir conversa e contato
                        </button>
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
            {(conv.tag || conv.origem || conv.responsavel || conv.valor > 0) && (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: '8px 20px', borderBottom: '1px solid var(--border)', fontSize: 12 }}>
                {conv.tag && <span style={{ ...estiloTag(conv.tag), borderRadius: 6, padding: '2px 9px', fontWeight: 700 }}>{conv.tag}</span>}
                {conv.origem && <span style={{ background: 'rgba(139,92,246,0.16)', color: '#6b21a8', borderRadius: 6, padding: '2px 8px', fontWeight: 600 }}>origem: {conv.origem}</span>}
                {conv.responsavel && <span style={{ color: 'var(--muted)' }}>resp.: {conv.responsavel}</span>}
                {conv.valor > 0 && <span style={{ color: 'var(--success)', fontWeight: 700 }}>R$ {conv.valor}</span>}
              </div>
            )}
            <div ref={scrollRef} onScroll={aoRolar} style={{ flex: 1, overflowY: 'auto', padding: 20, background: 'var(--chat-bg)', display: 'flex', flexDirection: 'column', gap: 8 }}>
              {detalhe.mensagens.map((m) => {
                if (m.origem === 'nota') {
                  return (
                    <div key={m.id} style={{ alignSelf: 'center', maxWidth: '85%', background: 'var(--nota-bg)', border: '1px solid #f0e0a8', borderRadius: 6, padding: '8px 12px', color: '#7a5c00' }}>
                      <div style={{ fontWeight: 700, fontSize: 10.5, marginBottom: 3, textTransform: 'uppercase', letterSpacing: '.03em' }}>🗒 Nota interna · {m.autor || 'equipe'} · {horaCurta(m.criado_em)}</div>
                      <div style={{ fontSize: 13.5, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{m.conteudo}</div>
                    </div>
                  )
                }
                const meu = m.origem !== 'contato'
                const ehFig = m.tipo === 'figurinha'
                const corFundo = m.origem === 'ia' ? 'var(--bubble-us)' : m.origem === 'atendente' ? 'var(--navy-700)' : 'var(--bubble-them)'
                const corTexto = m.origem === 'atendente' ? '#fff' : 'var(--text)'
                return (
                  <div key={m.id} style={{ alignSelf: meu ? 'flex-end' : 'flex-start', maxWidth: '74%' }}>
                    <div style={ehFig ? { padding: 0 } : { background: corFundo, color: corTexto, border: m.origem === 'contato' ? '1px solid var(--border)' : 'none', borderRadius: 7, padding: '9px 13px', fontSize: 14, lineHeight: 1.5, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      {ehFig ? (
                        m.midia_url
                          ? <div>
                              <img src={m.midia_url} alt="figurinha" style={{ width: 120, height: 120, objectFit: 'contain', display: 'block' }} />
                              {!meu && (
                                <button type="button" onClick={() => salvarRecebida(m.midia_url)} disabled={salvandoFig === m.midia_url}
                                  title="Salvar na minha galeria"
                                  style={{ marginTop: 4, border: '1px solid var(--border)', background: 'var(--surface-2)', color: 'var(--gold)', borderRadius: 8, padding: '2px 8px', fontSize: 11, cursor: 'pointer' }}>
                                  {salvandoFig === m.midia_url ? 'salvando…' : '＋ salvar'}
                                </button>
                              )}
                            </div>
                          : <span className="muted">[figurinha]</span>
                      ) : m.tipo === 'audio' ? (
                        <div>
                          {m.midia_url
                            ? <AudioPlayer src={m.midia_url} />
                            : <span className="muted">[áudio]</span>}
                          {m.conteudo
                            ? <div style={{ marginTop: 6, fontStyle: 'italic', fontSize: 13.5 }}>“{m.conteudo}”</div>
                            : <span className="muted" style={{ fontSize: 12 }}>transcrevendo…</span>}
                        </div>
                      ) : m.tipo === 'imagem' ? (
                        <div>
                          {m.midia_url && <a href={m.midia_url} target="_blank" rel="noreferrer"><img src={m.midia_url} alt="" style={{ maxWidth: 220, maxHeight: 240, borderRadius: 8, display: 'block' }} /></a>}
                          {m.conteudo && <div style={{ marginTop: 5 }}>{m.conteudo}</div>}
                        </div>
                      ) : m.tipo === 'doc' ? (
                        <div>
                          {m.midia_url && <a href={m.midia_url} target="_blank" rel="noreferrer" style={{ textDecoration: 'underline' }}>📎 abrir documento</a>}
                          {m.conteudo && <div style={{ marginTop: 4 }}>{m.conteudo}</div>}
                        </div>
                      ) : (m.conteudo || <span className="muted">[{m.tipo}]</span>)}
                    </div>
                    <div style={{ fontSize: 10.5, color: 'var(--muted)', marginTop: 3, textAlign: meu ? 'right' : 'left' }}>
                      {m.origem === 'ia' ? 'IA' : m.origem === 'atendente' ? (m.autor || 'você') : ''} {horaCurta(m.criado_em)}
                    </div>
                    {!meu && m.midia_url && ['imagem', 'doc', 'audio', 'video'].includes(m.tipo) && (() => {
                      const link = m.midia_drive || subidos[m.id]
                      return link ? (
                        <a href={link} target="_blank" rel="noreferrer" title="Ver no Google Drive"
                          style={{ display: 'inline-flex', alignItems: 'center', gap: 4, marginTop: 4, fontSize: 11, color: 'var(--gold)', textDecoration: 'none' }}>
                          ✓ no Drive
                        </a>
                      ) : (
                        <button type="button" onClick={() => subirDrive(m)} disabled={subindoDrive === m.id}
                          title="Salvar este arquivo na pasta do lead no Google Drive"
                          style={{ marginTop: 4, border: '1px solid var(--border)', background: 'var(--surface-2)', color: 'var(--gold)', borderRadius: 8, padding: '2px 9px', fontSize: 11, cursor: 'pointer' }}>
                          {subindoDrive === m.id ? 'subindo…' : '⬆ Drive'}
                        </button>
                      )
                    })()}
                  </div>
                )
              })}
              <div ref={fimChat} />
            </div>
            <div style={{ padding: 14, borderTop: '1px solid var(--border)' }}>
              <div style={{ display: 'flex', gap: 6, marginBottom: 8, alignItems: 'center' }}>
                <button type="button" onClick={() => setNota(false)} className="btn btn-sm"
                  style={{ background: !nota ? 'var(--navy-700)' : 'transparent', color: !nota ? '#fff' : 'var(--muted)', border: !nota ? 'none' : '1.5px solid var(--border)' }}>Mensagem</button>
                <button type="button" onClick={() => { setNota(true); setShowEmoji(false) }} className="btn btn-sm"
                  style={{ background: nota ? '#b8860b' : 'transparent', color: nota ? '#fff' : 'var(--muted)', border: nota ? 'none' : '1.5px solid var(--border)' }}>🗒 Nota interna</button>
                {/* triagem: aceitar OU recusar (tira de "Nao lidas") */}
                {!conv.aceita && !conv.recusada && (
                  <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
                    <button type="button" onClick={() => aceitarConversa(conv.id)} className="btn btn-sm" title="Aceitar o lead — sai de Não lidas"
                      style={{ background: 'transparent', color: 'var(--success)', border: '1.5px solid rgba(52,211,153,0.5)', fontWeight: 700, whiteSpace: 'nowrap' }}>✓ Aceitar</button>
                    <button type="button" onClick={() => recusarConversa(conv.id)} className="btn btn-sm" title="Recusar o lead — sai de Não lidas"
                      style={{ background: 'transparent', color: 'var(--danger)', border: '1.5px solid rgba(248,113,113,0.5)', fontWeight: 700, whiteSpace: 'nowrap' }}>✕ Recusar</button>
                  </div>
                )}
              </div>
              <form onSubmit={(e) => { e.preventDefault(); if (!texto.trim()) return; if (nota) enviarNota(conv.id, texto).then(() => setTexto('')); else responder(e) }} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <input type="file" ref={fileRef} style={{ display: 'none' }} accept="image/*,application/pdf,.doc,.docx,.xls,.xlsx,.zip,.txt" onChange={anexar} />
                {!nota && !gravando && (
                  <button type="button" onClick={() => fileRef.current?.click()} disabled={enviandoMidia}
                    title="Anexar imagem ou arquivo"
                    style={{ border: 'none', background: 'var(--chip)', color: 'var(--text)', width: 40, height: 40, borderRadius: '50%', cursor: 'pointer', flexShrink: 0, display: 'grid', placeItems: 'center' }}>
                    {enviandoMidia ? <span className="spin" style={{ borderTopColor: 'var(--navy-700)', borderColor: 'rgba(0,0,0,.2)', borderTopWidth: 2.5 }} /> : <PlusIcon />}
                  </button>
                )}
                {!nota && !gravando && (
                  <div style={{ position: 'relative', flexShrink: 0 }}>
                    <input type="file" ref={figFileRef} style={{ display: 'none' }} accept="image/*" onChange={subirFig} />
                    <button type="button" onClick={abrirFigs} disabled={enviandoFig} title="Figurinhas"
                      style={{ border: '1.5px solid rgba(59,130,246,0.35)', background: 'var(--chip)', color: showFig ? 'var(--gold)' : 'var(--text)', width: 40, height: 40, borderRadius: '50%', cursor: 'pointer', display: 'grid', placeItems: 'center', fontSize: 19 }}>
                      {enviandoFig ? <span className="spin" style={{ borderTopColor: 'var(--gold)', borderColor: 'rgba(59,130,246,0.25)', borderTopWidth: 2.5 }} /> : '😺'}
                    </button>
                    {showFig && (
                      <>
                        <div onClick={() => setShowFig(false)} style={{ position: 'fixed', inset: 0, zIndex: 20 }} />
                        <div style={{ position: 'absolute', bottom: '120%', left: 0, background: 'var(--surface-solid)', border: '1px solid var(--border)', borderRadius: 7, boxShadow: 'var(--shadow-lg)', padding: 10, width: 292, zIndex: 30 }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                            <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--muted)' }}>Figurinhas</span>
                            <button type="button" onClick={() => figFileRef.current?.click()} className="btn btn-sm"
                              style={{ background: 'var(--chip)', color: 'var(--gold)', border: '1.5px solid rgba(59,130,246,0.35)', padding: '3px 10px', fontSize: 12 }}>+ Adicionar</button>
                          </div>
                          {figs.length === 0 ? (
                            <div className="muted" style={{ fontSize: 12.5, padding: '14px 4px', textAlign: 'center' }}>
                              Nenhuma figurinha ainda.<br />Clique em <b>+ Adicionar</b> pra subir um .webp/.png.
                            </div>
                          ) : (
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 6, maxHeight: 240, overflowY: 'auto' }}>
                              {figs.map((f) => (
                                <div key={f.id} style={{ position: 'relative' }}>
                                  <button type="button" onClick={() => mandarFig(f.id)} disabled={enviandoFig} title="Enviar figurinha"
                                    style={{ border: '1px solid var(--border)', background: 'var(--surface-2)', borderRadius: 8, padding: 4, cursor: 'pointer', width: '100%', aspectRatio: '1', display: 'grid', placeItems: 'center' }}>
                                    <img src={f.url} alt={f.nome} style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }} />
                                  </button>
                                  <button type="button" onClick={() => apagarFig(f.id)} title="Remover"
                                    style={{ position: 'absolute', top: -6, right: -6, width: 18, height: 18, borderRadius: '50%', border: 'none', background: 'var(--danger)', color: '#fff', fontSize: 11, lineHeight: 1, cursor: 'pointer', display: 'grid', placeItems: 'center' }}>×</button>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                )}
                {gravando ? (
                  <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 10, padding: '11px 14px', border: '1.5px solid var(--danger)', borderRadius: 24, background: 'rgba(248,113,113,0.12)', color: 'var(--danger)', fontWeight: 600 }}>
                    <span style={{ width: 10, height: 10, borderRadius: '50%', background: 'var(--danger)' }} /> Gravando áudio…
                  </div>
                ) : (
                  <div style={{ flex: 1, position: 'relative', display: 'flex', alignItems: 'center', gap: 4, border: nota ? '1.5px solid var(--nota-border)' : '1.5px solid var(--border)', background: nota ? 'var(--nota-bg)' : 'var(--surface-2)', borderRadius: 24, padding: '3px 6px 3px 16px' }}>
                    <input value={texto} onChange={(e) => setTexto(e.target.value)}
                      placeholder={nota ? 'Nota interna — só a equipe vê…' : (conv.ia_ativa ? 'Responder assume a conversa (a IA para)…' : 'Digite uma mensagem')}
                      style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', fontSize: 14, fontFamily: 'inherit', padding: '8px 0' }} />
                    {!nota && (
                      <button type="button" onClick={() => setShowEmoji((v) => !v)} title="Emoji"
                        style={{ border: 'none', background: 'transparent', color: showEmoji ? 'var(--gold)' : 'var(--muted)', cursor: 'pointer', padding: 4, lineHeight: 1, display: 'grid', placeItems: 'center' }}><SmileIcon /></button>
                    )}
                    {!nota && showEmoji && (
                      <>
                        <div onClick={() => setShowEmoji(false)} style={{ position: 'fixed', inset: 0, zIndex: 20 }} />
                        <div style={{ position: 'absolute', bottom: '120%', right: 0, background: 'var(--surface-solid)', border: '1px solid var(--border)', borderRadius: 7, boxShadow: 'var(--shadow-lg)', padding: 8, display: 'grid', gridTemplateColumns: 'repeat(8, 1fr)', gap: 2, width: 300, zIndex: 30 }}>
                          {EMOJIS.map((em) => (
                            <button key={em} type="button" onClick={() => setTexto((t) => t + em)}
                              style={{ border: 'none', background: 'transparent', fontSize: 20, cursor: 'pointer', padding: 4, borderRadius: 6 }}>{em}</button>
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                )}
                {!nota && gravando && (
                  <>
                    <button type="button" onClick={cancelarGravar} title="Cancelar (descartar áudio)" style={{ border: '1.5px solid var(--border)', background: 'var(--chip)', color: 'var(--muted)', width: 40, height: 40, borderRadius: '50%', cursor: 'pointer', flexShrink: 0, display: 'grid', placeItems: 'center' }}><TrashIcon /></button>
                    <button type="button" onClick={pararGravar} title="Parar e enviar" style={{ border: 'none', background: 'var(--danger)', color: '#fff', width: 40, height: 40, borderRadius: '50%', fontSize: 18, cursor: 'pointer', flexShrink: 0, display: 'grid', placeItems: 'center' }}>➤</button>
                  </>
                )}
                {!nota && !gravando && (
                  <button type="button" onClick={gravar} disabled={enviandoAudio} title="Gravar áudio" style={{ border: '1.5px solid rgba(59,130,246,0.35)', background: 'var(--chip)', color: 'var(--gold)', width: 40, height: 40, borderRadius: '50%', cursor: 'pointer', flexShrink: 0, display: 'grid', placeItems: 'center' }}>{enviandoAudio ? <span className="spin" style={{ borderTopColor: 'var(--gold)', borderColor: 'rgba(59,130,246,0.25)', borderTopWidth: 2.5 }} /> : <MicIcon />}</button>
                )}
                {!gravando && (texto.trim() || nota) && <button className={nota ? 'btn' : 'btn btn-gold'} disabled={enviando || !texto.trim()} style={nota ? { background: '#b8860b' } : undefined}>{enviando ? <span className="spin" /> : (nota ? 'Salvar nota' : 'Enviar')}</button>}
              </form>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

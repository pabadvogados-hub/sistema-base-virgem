import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

// Softphone WebRTC (JsSIP) — registra o ramal do usuario no Asterisk e liga pelo navegador.
// Duas formas de ligar:
//   1) Evento window 'esc:ligar' {numero, nome, conversaId} disparado pelo botao Ligar de um contato.
//   2) Teclado de discagem embutido (clicar na tarja de status abre o discador).
// JsSIP vem como global window.JsSIP (carregado via <script> no index.html).

function soDigitos(s) { return String(s || '').replace(/\D/g, '') }
function fmtTempo(seg) { const m = Math.floor(seg / 60), s = seg % 60; return `${m}:${String(s).padStart(2, '0')}` }
function iniciais(nome) {
  const p = String(nome || '').trim().split(/\s+/).filter(Boolean)
  return ((p[0]?.[0] || '') + (p[1]?.[0] || '')).toUpperCase() || '?'
}

export default function Softphone() {
  const [reg, setReg] = useState('conectando')   // conectando | ok | falha | off
  const [teste, setTeste] = useState(false)
  const [chamada, setChamada] = useState(null)    // {estado, nome, numero} | null
  const [mudo, setMudo] = useState(false)
  const [seg, setSeg] = useState(0)
  const [discador, setDiscador] = useState(false) // painel de teclado aberto
  const [digitado, setDigitado] = useState('')    // numero sendo digitado
  const [micBloq, setMicBloq] = useState(false)    // permissao de microfone negada
  const uaRef = useRef(null)
  const sessRef = useRef(null)
  const audioRef = useRef(null)
  const timerRef = useRef(null)
  const regRef = useRef('conectando')             // espelho do reg pra ler dentro de async

  function setRegistro(v) { regRef.current = v; setReg(v) }

  // Pre-autoriza o microfone ao abrir (a permissao fica lembrada e a 1a ligacao nao falha).
  useEffect(() => {
    if (!navigator.mediaDevices?.getUserMedia) return
    navigator.mediaDevices.getUserMedia({ audio: true })
      .then((s) => { s.getTracks().forEach((t) => t.stop()); setMicBloq(false) })
      .catch(() => setMicBloq(true))
  }, [])

  useEffect(() => {
    let vivo = true
    async function init() {
      if (!window.JsSIP) { setRegistro('falha'); return }
      let cred
      try { cred = await api('/api/voz/credenciais') } catch { setRegistro('off'); return }
      if (!vivo) return
      setTeste(!!cred.modo_teste)
      try {
        const socket = new window.JsSIP.WebSocketInterface(cred.wss)
        const ua = new window.JsSIP.UA({ sockets: [socket], uri: cred.uri, password: cred.senha, session_timers: false, register: true })
        ua.on('registered', () => setRegistro('ok'))
        ua.on('unregistered', () => setRegistro('falha'))
        ua.on('registrationFailed', () => setRegistro('falha'))
        ua.on('disconnected', () => setRegistro('falha'))
        ua.on('newRTCSession', (e) => {
          if (e.originator === 'remote') anexarSessao(e.session, { estado: 'recebendo', nome: e.request?.from?.display_name || 'Desconhecido', numero: '' })
        })
        ua.start()
        uaRef.current = ua
      } catch { setRegistro('falha') }
    }
    init()
    return () => { vivo = false; try { uaRef.current && uaRef.current.stop() } catch {} }
  }, [])

  function iniciarTimer() { setSeg(0); clearInterval(timerRef.current); timerRef.current = setInterval(() => setSeg((s) => s + 1), 1000) }
  function pararTimer() { clearInterval(timerRef.current) }

  function ligarAudio(session) {
    try {
      const pc = session.connection
      if (!pc) return
      const ms = new MediaStream()
      pc.getReceivers().forEach((r) => { if (r.track && r.track.kind === 'audio') ms.addTrack(r.track) })
      if (audioRef.current && ms.getTracks().length) { audioRef.current.srcObject = ms; audioRef.current.play().catch(() => {}) }
    } catch {}
  }

  function anexarSessao(session, info) {
    sessRef.current = session
    setChamada(info); setMudo(false)
    session.on('peerconnection', (e) => {
      e.peerconnection.addEventListener('track', (ev) => {
        if (audioRef.current) { audioRef.current.srcObject = ev.streams[0]; audioRef.current.play().catch(() => {}) }
      })
    })
    session.on('progress', () => setChamada((c) => c && { ...c, estado: 'chamando' }))
    session.on('accepted', () => { setChamada((c) => c && { ...c, estado: 'em_ligacao' }); ligarAudio(session); iniciarTimer() })
    session.on('confirmed', () => { setChamada((c) => c && { ...c, estado: 'em_ligacao' }); ligarAudio(session); iniciarTimer() })
    session.on('ended', () => { pararTimer(); setChamada(null); sessRef.current = null })
    session.on('failed', (e) => { pararTimer(); setChamada({ estado: 'falhou', motivo: traduzErro(e && e.cause) }); sessRef.current = null; setTimeout(() => setChamada(null), 2800) })
  }

  // Discagem unificada (usada pelo teclado e pelo evento esc:ligar).
  // Espera o ramal registrar (ate ~8s) em vez de falhar na hora.
  async function discar(numero, nome) {
    const dig = soDigitos(numero)
    if (!dig) { alert('Digite um número para ligar.'); return }
    if (sessRef.current) { alert('Já existe uma ligação em andamento.'); return }
    let ua = uaRef.current
    for (let i = 0; i < 40 && (!ua || regRef.current !== 'ok'); i++) {
      await new Promise((r) => setTimeout(r, 200)); ua = uaRef.current
    }
    if (!ua || regRef.current !== 'ok') {
      alert(regRef.current === 'off' ? 'Seu usuário não tem ramal de voz configurado.' : 'O telefone não conectou. Recarregue a página e tente de novo.')
      return
    }
    try {
      const session = ua.call(`sip:${dig}@sistema.escritorio.adv.br`, { mediaConstraints: { audio: true, video: false } })
      anexarSessao(session, { estado: 'chamando', nome: nome || dig, numero: dig })
      setDiscador(false); setDigitado('')
    } catch (e) { alert('Falha ao iniciar a ligação: ' + e.message) }
  }

  useEffect(() => {
    function onLigar(ev) { const { numero, nome } = ev.detail || {}; discar(numero, nome) }
    window.addEventListener('esc:ligar', onLigar)
    return () => window.removeEventListener('esc:ligar', onLigar)
  }, [])

  function desligar() { try { sessRef.current && sessRef.current.terminate() } catch {} }
  function atender() { try { sessRef.current && sessRef.current.answer({ mediaConstraints: { audio: true, video: false } }) } catch {} }
  function toggleMudo() {
    const s = sessRef.current; if (!s) return
    if (mudo) { s.unmute({ audio: true }); setMudo(false) } else { s.mute({ audio: true }); setMudo(true) }
  }
  function teclar(t) { setDigitado((d) => (d + t).slice(0, 20)) }
  function apagar() { setDigitado((d) => d.slice(0, -1)) }

  const emLig = chamada?.estado === 'em_ligacao'
  const chamando = chamada?.estado === 'chamando'
  const recebendo = chamada?.estado === 'recebendo'
  const falhou = chamada?.estado === 'falhou'
  const corReg = reg === 'ok' ? '#34d399' : reg === 'conectando' ? '#e0c072' : '#f0736b'
  const labelReg = reg === 'ok' ? (teste ? 'Telefone pronto (teste)' : 'Telefone pronto') : reg === 'conectando' ? 'Conectando telefone…' : reg === 'off' ? 'Sem ramal de voz' : 'Telefone offline'

  return (
    <>
      <audio ref={audioRef} autoPlay />
      <style>{`@keyframes slpulse{0%{box-shadow:0 0 0 0 rgba(59,130,246,.45)}70%{box-shadow:0 0 0 16px rgba(59,130,246,0)}100%{box-shadow:0 0 0 0 rgba(59,130,246,0)}}`}</style>

      {/* Discador (teclado) */}
      {discador && !chamada && (
        <div style={{ position: 'fixed', bottom: 66, right: 16, zIndex: 65, width: 258,
          background: 'linear-gradient(160deg,#1a2735 0%,#131d28 100%)', border: '1px solid rgba(184,134,11,.28)',
          borderRadius: 18, boxShadow: '0 24px 70px rgba(0,0,0,.6)', padding: 16, color: '#eef3f9' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <span style={{ fontSize: 11, letterSpacing: '.08em', textTransform: 'uppercase', fontWeight: 700, color: '#b8860b' }}>Discar</span>
            <button onClick={() => setDiscador(false)} title="Fechar" style={{ background: 'none', border: 'none', color: '#9fb0c2', cursor: 'pointer', fontSize: 16, lineHeight: 1 }}>✕</button>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <input value={digitado} onChange={(e) => setDigitado(soDigitos(e.target.value).slice(0, 20))}
              onKeyDown={(e) => { if (e.key === 'Enter') discar(digitado) }}
              placeholder="DDD + número" inputMode="numeric"
              style={{ flex: 1, minWidth: 0, background: 'rgba(0,0,0,.28)', border: '1px solid rgba(255,255,255,.1)', borderRadius: 10,
                color: '#eef3f9', fontSize: 18, padding: '9px 12px', fontVariantNumeric: 'tabular-nums', letterSpacing: '.03em' }} />
            {digitado && <button onClick={apagar} title="Apagar" style={{ background: 'none', border: 'none', color: '#9fb0c2', cursor: 'pointer', fontSize: 20 }}>⌫</button>}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 8 }}>
            {['1', '2', '3', '4', '5', '6', '7', '8', '9', '*', '0', '#'].map((t) => (
              <button key={t} onClick={() => teclar(t)}
                style={{ padding: '12px 0', borderRadius: 12, border: '1px solid rgba(255,255,255,.08)', cursor: 'pointer',
                  background: 'rgba(255,255,255,.04)', color: '#eef3f9', fontSize: 19, fontWeight: 600 }}
                onMouseDown={(e) => (e.currentTarget.style.background = 'rgba(184,134,11,.22)')}
                onMouseUp={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,.04)')}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,.04)')}>{t}</button>
            ))}
          </div>
          <button onClick={() => discar(digitado)} disabled={!digitado}
            style={{ width: '100%', marginTop: 12, padding: '11px 0', borderRadius: 12, border: 'none',
              cursor: digitado ? 'pointer' : 'not-allowed', background: digitado ? '#1f8e5a' : 'rgba(255,255,255,.06)',
              color: '#fff', fontSize: 14.5, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
            <IcPhone size={18} /> Ligar
          </button>
          {micBloq && <div style={{ fontSize: 11.5, color: '#f0a0a0', marginTop: 10, lineHeight: 1.4 }}>Microfone bloqueado. Libere no cadeado ao lado do endereço e recarregue.</div>}
        </div>
      )}

      {!chamada && (
        <div onClick={() => setDiscador((v) => !v)} title="Abrir discador"
          style={{ position: 'fixed', bottom: 16, right: 16, zIndex: 60, display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer',
          background: 'rgba(13,20,30,.92)', backdropFilter: 'blur(6px)', border: '1px solid rgba(255,255,255,.08)', borderRadius: 999,
          padding: '7px 14px', fontSize: 12.5, color: '#aeb9c7', boxShadow: '0 6px 20px rgba(0,0,0,.35)' }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: corReg, boxShadow: `0 0 8px ${corReg}` }} /> {labelReg}
          <span style={{ marginLeft: 4, color: '#b8860b', display: 'grid', placeItems: 'center' }}><IcPhone size={15} /></span>
        </div>
      )}

      {chamada && (
        <div style={{ position: 'fixed', bottom: 22, right: 22, zIndex: 70, width: 328,
          background: 'linear-gradient(160deg,#1a2735 0%,#131d28 100%)', border: '1px solid rgba(59,130,246,.28)',
          borderRadius: 20, boxShadow: '0 24px 70px rgba(0,0,0,.6)', padding: '22px 22px 20px', color: '#eef3f9',
          fontFamily: 'inherit' }}>

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
            <span style={{ fontSize: 11.5, letterSpacing: '.08em', textTransform: 'uppercase', fontWeight: 700,
              color: emLig ? '#7ee0a8' : chamando ? '#e0c072' : recebendo ? '#7ee0a8' : '#f0a0a0' }}>
              {emLig ? '● Em ligação' : chamando ? 'Chamando…' : recebendo ? 'Ligação recebida' : 'Ligação falhou'}
            </span>
            {teste && !falhou && (
              <span title="Sem linha telefônica ainda — você ouve o eco (sua voz)"
                style={{ fontSize: 10, fontWeight: 700, color: '#e0c072', border: '1px solid rgba(59,130,246,.4)', borderRadius: 999, padding: '2px 8px' }}>TESTE · ECO</span>
            )}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <div style={{ width: 54, height: 54, borderRadius: '50%', flexShrink: 0, display: 'grid', placeItems: 'center',
              background: 'linear-gradient(135deg,#3b82f6,#b8860b)', color: '#1a1206', fontWeight: 800, fontSize: 19,
              animation: chamando ? 'slpulse 1.4s infinite' : 'none' }}>{iniciais(chamada.nome)}</div>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 17, fontWeight: 700, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{chamada.nome || chamada.numero}</div>
              {chamada.numero && <div style={{ fontSize: 12.5, color: '#9fb0c2' }}>{chamada.numero}</div>}
              {emLig && <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: '#3b82f6', fontVariantNumeric: 'tabular-nums' }}>{fmtTempo(seg)}</div>}
            </div>
          </div>

          {falhou && <div style={{ fontSize: 12.5, color: '#f0a0a0', marginTop: 12 }}>{chamada.motivo}</div>}

          {!falhou && (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 26, marginTop: 20 }}>
              {recebendo && (
                <BotaoRedondo cor="#1f8e5a" label="Atender" onClick={atender} icone={<IcPhone size={25} />} />
              )}
              {emLig && (
                <BotaoRedondo cor={mudo ? '#b8860b' : 'rgba(255,255,255,.09)'} borda={!mudo} label={mudo ? 'No mudo' : 'Mutar'} onClick={toggleMudo} icone={mudo ? <IcMicOff size={23} /> : <IcMic size={23} />} />
              )}
              <BotaoRedondo cor="#e23b30" label="Desligar" onClick={desligar} icone={<IcHangup size={26} />} grande />
            </div>
          )}
        </div>
      )}
    </>
  )
}

function BotaoRedondo({ cor, label, onClick, icone, grande, borda }) {
  const d = grande ? 60 : 54
  return (
    <div style={{ textAlign: 'center' }}>
      <button onClick={onClick} title={label} style={{ width: d, height: d, borderRadius: '50%',
        border: borda ? '1px solid rgba(255,255,255,.16)' : 'none', cursor: 'pointer',
        background: cor, color: '#fff', display: 'grid', placeItems: 'center',
        boxShadow: '0 8px 20px rgba(0,0,0,.4)', transition: 'transform .12s, filter .12s' }}
        onMouseDown={(e) => (e.currentTarget.style.transform = 'scale(.92)')}
        onMouseUp={(e) => (e.currentTarget.style.transform = 'scale(1)')}
        onMouseLeave={(e) => (e.currentTarget.style.transform = 'scale(1)')}>{icone}</button>
      <div style={{ fontSize: 11, color: '#9fb0c2', marginTop: 7, fontWeight: 500 }}>{label}</div>
    </div>
  )
}

// ---- Icones SVG (estilo softphone moderno, sem emoji) ----
const svgBase = { fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' }
function IcMic({ size = 22 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...svgBase}>
      <rect x="9" y="2" width="6" height="12" rx="3" /><path d="M5 10v2a7 7 0 0 0 14 0v-2" /><line x1="12" y1="19" x2="12" y2="22" />
    </svg>
  )
}
function IcMicOff({ size = 22 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...svgBase}>
      <line x1="2" y1="2" x2="22" y2="22" /><path d="M9 9v3a3 3 0 0 0 5.12 2.12" /><path d="M15 9.34V5a3 3 0 0 0-5.94-.6" />
      <path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2a6.9 6.9 0 0 1-.12 1.28" /><line x1="12" y1="19" x2="12" y2="22" />
    </svg>
  )
}
function IcPhone({ size = 24 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...svgBase}>
      <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.6a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.5-1.2a2 2 0 0 1 2.1-.5c.8.3 1.7.6 2.6.7a2 2 0 0 1 1.7 2z" />
    </svg>
  )
}
function IcHangup({ size = 24 }) {
  return (
    <span style={{ display: 'grid', placeItems: 'center', transform: 'rotate(135deg)' }}><IcPhone size={size} /></span>
  )
}

function traduzErro(cause) {
  const m = { 'Rejected': 'Chamada rejeitada', 'Busy': 'Ocupado', 'Unavailable': 'Indisponível',
    'Not Found': 'Número não encontrado', 'Canceled': 'Cancelada', 'No Answer': 'Não atendeu',
    'Bye': 'Encerrada', 'WebRTC Error': 'Erro de áudio/microfone', 'User Denied Media Access': 'Microfone bloqueado' }
  return m[cause] || cause || 'Falhou'
}

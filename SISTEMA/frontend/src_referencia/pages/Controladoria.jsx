import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'

/* ---------- ícones de linha (sem emoji) ---------- */
function Ico({ d, size = 18, sw = 1.7 }) {
  return (<svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>{d}</svg>)
}
const IC_GRID = <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /></>
const IC_PROC = <><path d="M6 2h9l5 5v15H6z" /><path d="M15 2v5h5M9 13h6M9 17h6" /></>
const IC_INTI = <><path d="M4 4h16v12H4z" /><path d="M4 12h4l2 3h4l2-3h4" /></>
const IC_PRAZO = <><circle cx="12" cy="13" r="8" /><path d="M12 9v4l2.5 2.5M9 2h6" /></>
const IC_AUD = <><rect x="3" y="4" width="18" height="17" rx="2" /><path d="M16 2v4M8 2v4M3 10h18" /></>
const IC_REFRESH = <><path d="M23 4v6h-6M1 20v-6h6" /><path d="M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15" /></>
const IC_RADAR = <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="4.5" /><circle cx="12" cy="12" r="1" /></>
const IC_FOLDER = <><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" /></>
const IC_INBOX = <><path d="M22 12h-6l-2 3h-4l-2-3H2" /><path d="M5.5 5.5 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.5A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.5z" /></>
const IC_PLUS = <><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></>
const IC_SEARCH = <><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></>
const IC_MOV = <><path d="M12 2v4M12 18v4M2 12h4M18 12h4" /><circle cx="12" cy="12" r="4" /></>
const IC_DOC = <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6M8 13h8M8 17h6" /></>
const IC_PARTES = <><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /></>
const IC_PUB = <><path d="M4 4h16v12H4z" /><path d="M8 8h8M8 12h5" /><path d="M2 20h20" /></>
const IC_CLOSE = <><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></>
const IC_INFO = <><circle cx="12" cy="12" r="9" /><path d="M12 8h.01M11 12h1v4h1" /></>
const IC_CAL = <><rect x="3" y="4" width="18" height="17" rx="2" /><path d="M16 2v4M8 2v4M3 10h18" /><path d="M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01" /></>
const IC_CHEV_L = <path d="M15 18l-6-6 6-6" />
const IC_CHEV_R = <path d="M9 18l6-6-6-6" />

const ACCENT = 'var(--gold)'
const cats = {
  proc: { c: '#3b82f6', bg: 'rgba(59,130,246,.13)' },
  inti: { c: '#38bdf8', bg: 'rgba(56,189,248,.13)' },
  prazo: { c: '#f6b73c', bg: 'rgba(246,183,60,.14)' },
  aud: { c: '#a78bfa', bg: 'rgba(167,139,250,.14)' },
}
const ocultoNome = (s) => { const t = String(s || ''); return !t || t.startsWith('[Sigiloso') || t.toLowerCase().includes('ocultada') }
const limpoPolo = (s) => (ocultoNome(s) ? 'Sob sigilo' : s)

export default function Controladoria() {
  const { user } = useAuth()
  const ehAdmin = user?.papel === 'admin'
  const [dash, setDash] = useState(null)
  const [mons, setMons] = useState([])
  const [oab, setOab] = useState('')
  const [msg, setMsg] = useState('')
  const [salvando, setSalvando] = useState(false)
  const [carregando, setCarregando] = useState(true)
  const [atualizando, setAtualizando] = useState(false)
  const [sec, setSec] = useState('painel')
  const [buscaOab, setBuscaOab] = useState('')
  const [resultados, setResultados] = useState(null)
  const [buscando, setBuscando] = useState(false)
  const [reg, setReg] = useState({})
  const [procAberto, setProcAberto] = useState(null)
  const [intis, setIntis] = useState(null)
  const [agenda, setAgenda] = useState(null)
  const [cart2, setCart2] = useState(null)
  const [partes, setPartes] = useState(null)
  const [fProc, setFProc] = useState('')
  const [fParte, setFParte] = useState('')
  const [parteAberta, setParteAberta] = useState(null)

  const carregar = useCallback(async (silencioso) => {
    if (silencioso) setAtualizando(true); else setCarregando(true)
    try {
      const [d, m, ii, cc, pt, ag] = await Promise.all([
        api('/api/controladoria/dashboard'),
        api('/api/controladoria/monitores').catch(() => ({ monitores: [] })),
        api('/api/controladoria/intimacoes?status=pendentes').catch(() => ({ total: 0, intimacoes: [] })),
        api('/api/controladoria/processos').catch(() => ({ processos: [], total: 0 })),
        api('/api/controladoria/partes').catch(() => ({ entidades: [], total: 0 })),
        api('/api/controladoria/agenda').catch(() => ({ eventos: [] })),
      ])
      setDash(d); setMons(m?.monitores || []); setIntis(ii); setCart2(cc); setPartes(pt); setAgenda(ag)
    } catch (e) { setDash({ conectado: false, erro: e.message }) }
    setCarregando(false); setAtualizando(false)
  }, [])

  useEffect(() => { carregar(); const t = setInterval(() => carregar(true), 60000); return () => clearInterval(t) }, [carregar])
  useEffect(() => { if (!buscaOab && mons[0]?.valor) setBuscaOab(mons[0].valor) }, [mons]) // eslint-disable-line

  async function fazerBusca(e) {
    e?.preventDefault()
    const v = buscaOab.trim().toUpperCase().replace(/\s+/g, '')
    if (!v) return
    setBuscando(true); setResultados(null); setReg({})
    try { setResultados(await api('/api/controladoria/busca', { method: 'POST', body: { tipo: 'oab', valor: v } })) }
    catch (er) { setResultados({ erro: er.message || 'falha na busca' }) }
    setBuscando(false)
  }
  async function carregarMaisBusca() {
    const cur = resultados?.cursor
    const v = buscaOab.trim().toUpperCase().replace(/\s+/g, '')
    if (!cur || !v) return
    setBuscando(true)
    try {
      const r = await api('/api/controladoria/busca', { method: 'POST', body: { tipo: 'oab', valor: v, cursor: cur } })
      setResultados((prev) => ({ ...r, resultados: [...(prev?.resultados || []), ...(r.resultados || [])] }))
    } catch { /* mantém o que já tem */ }
    setBuscando(false)
  }
  async function registrar(numero) {
    setReg((s) => ({ ...s, [numero]: 'loading' }))
    try { await api('/api/controladoria/registrar', { method: 'POST', body: { numero } }); setReg((s) => ({ ...s, [numero]: 'ok' })) }
    catch (er) { setReg((s) => ({ ...s, [numero]: 'erro' })) }
  }
  async function registrarTodos() {
    const lista = resultados?.resultados || []
    if (!lista.length) return
    if (!window.confirm(`Registrar ${lista.length} processos? Cada um entra na carteira e conta no plano do SYNC.`)) return
    for (const p of lista) { if (reg[p.numero] !== 'ok') await registrar(p.numero) }
  }
  async function ligarMonitor(e) {
    e.preventDefault()
    const v = oab.trim().toUpperCase().replace(/\s+/g, '')
    if (!v) return
    setSalvando(true); setMsg('')
    try {
      await api('/api/controladoria/monitores', { method: 'POST', body: { tipo: 'oab', valor: v } })
      setMsg(`OK · monitor ligado para ${v}. Os processos aparecem aqui assim que o SYNC indexar.`)
      setOab(''); await carregar(true)
    } catch (er) { setMsg('Erro: ' + (er.message || 'não foi possível ligar o monitor')) }
    setSalvando(false)
  }

  if (carregando && !dash) {
    return (<div className="center-load" style={{ minHeight: '50vh', gap: 12 }}><span className="spin" /><span>Carregando painel…</span></div>)
  }

  const conectado = dash?.conectado
  const cart = dash?.carteira || {}
  const inti = dash?.intimacoes_pendentes || {}
  const intiList = intis?.intimacoes || []
  const intiTotal = intis?.total ?? inti.total ?? 0
  const intiUrgentes = intiList.filter((x) => x.data_fatal || x.requer_manifestacao).length
  const aud = dash?.audiencias || {}
  const ativ = dash?.atividade || []
  const maxAtiv = Math.max(1, ...ativ.map((a) => a.total || 0))
  const indexando = conectado && mons.length > 0 && mons.some((m) => !m.ultima_checagem)

  const cartList = cart2?.processos || []
  const cartFilt = cartList.filter((p) => !fProc.trim() || `${p.numero} ${p.classe} ${p.assunto} ${p.polo_ativo} ${p.polo_passivo}`.toLowerCase().includes(fProc.toLowerCase()))
  const parteList = partes?.entidades || []
  const parteFilt = parteList.filter((e) => !fParte.trim() || `${e.nome} ${e.cpf_cnpj || ''}`.toLowerCase().includes(fParte.toLowerCase()))

  const tiles = [
    { k: 'proc', t: 'Processos na carteira', v: cart.processos_carteira ?? 0, ic: IC_PROC, sub: `${cart.processos_monitorados ?? 0} monitorados · ${cart.processos_baixados ?? 0} baixados` },
    { k: 'inti', t: 'Intimações pendentes', v: intiTotal, ic: IC_INTI, sub: intiTotal ? `${intiUrgentes} com prazo · ${Math.max(0, intiTotal - intiUrgentes)} ciência` : 'nada na fila' },
    { k: 'prazo', t: 'Prazos · próximos 7 dias', v: dash?.prazos_7d ?? 0, ic: IC_PRAZO, sub: `${dash?.prazos_15d ?? 0} em 15 dias · ${dash?.prazos_total ?? 0} no total` },
    { k: 'aud', t: 'Audiências · 30 dias', v: aud.audiencias_30d ?? 0, ic: IC_AUD, sub: aud.audiencias_30d ? 'agendadas' : 'nenhuma marcada' },
  ]

  const NAV = [
    { k: 'painel', t: 'Painel', ic: IC_GRID },
    { k: 'agenda', t: 'Agenda', ic: IC_CAL, n: (agenda?.eventos || []).length },
    { k: 'busca', t: 'Busca', ic: IC_SEARCH },
    { k: 'processos', t: 'Processos', ic: IC_FOLDER, n: cart.processos_carteira },
    { k: 'intimacoes', t: 'Intimações', ic: IC_INTI, n: intiTotal },
    { k: 'partes', t: 'Partes', ic: IC_PARTES, n: partes?.total },
    { k: 'monitores', t: 'Monitores', ic: IC_RADAR, n: mons.length },
  ]

  return (
    <>
      <style>{`
        @keyframes ctrlShimmer { 0%{background-position:-420px 0} 100%{background-position:420px 0} }
        @keyframes ctrlPulse { 0%{box-shadow:0 0 0 0 rgba(56,189,248,.5)} 70%{box-shadow:0 0 0 6px rgba(56,189,248,0)} 100%{box-shadow:0 0 0 0 rgba(56,189,248,0)} }
        @keyframes ctrlSweep { 0%{transform:rotate(0)} 100%{transform:rotate(360deg)} }
        .ctrl-card { position:relative; overflow:hidden; transition:border-color .18s, transform .12s, box-shadow .18s; }
        .ctrl-card:hover { transform:translateY(-2px); border-color:rgba(59,130,246,.34); box-shadow:0 16px 40px rgba(0,0,0,.4); }
        .ctrl-card .top { position:absolute; top:0; left:0; right:0; height:3px; }
        .ctrl-idx { background-image:linear-gradient(90deg,transparent,rgba(56,189,248,.18),transparent); background-size:420px 100%; animation:ctrlShimmer 1.6s linear infinite; }
        .ctrl-row { padding:12px 4px; border-bottom:1px solid var(--border-soft); }
        .ctrl-row:last-child { border-bottom:none; }
        .ctrl-row.clic { cursor:pointer; border-radius:7px; transition:background .12s; }
        .ctrl-row.clic:hover { background:rgba(255,255,255,.03); }
        .ctrl-empty { display:flex; flex-direction:column; align-items:center; gap:9px; text-align:center; padding:26px 12px; color:var(--muted); }
        .ctrl-empty .ring { width:42px; height:42px; border-radius:11px; display:grid; place-items:center; background:rgba(255,255,255,.03); border:1px dashed var(--border); color:var(--muted); }
        .ctrl-tabs { display:flex; gap:2px; border-bottom:1px solid var(--border); margin-bottom:18px; overflow-x:auto; }
        .ctrl-tab { display:inline-flex; align-items:center; gap:7px; padding:10px 14px; font-size:13px; font-weight:600; color:var(--muted); border:none; background:none; border-bottom:2px solid transparent; margin-bottom:-1px; cursor:pointer; white-space:nowrap; }
        .ctrl-tab:hover { color:var(--text); }
        .ctrl-tab.on { color:var(--gold-2); border-bottom-color:var(--gold); }
        .ctrl-tab .cnt { font-size:10.5px; background:var(--chip); border-radius:5px; padding:1px 6px; font-weight:700; }
        .ctrl-tab.on .cnt { background:rgba(59,130,246,.16); color:var(--gold-2); }
        .ctrl-shell { display:flex; gap:18px; align-items:flex-start; }
        .ctrl-rail { flex:0 0 194px; display:flex; flex-direction:column; gap:3px; position:sticky; top:12px; }
        .ctrl-nav { display:flex; align-items:center; gap:11px; padding:11px 13px; border-radius:8px; font-size:13.5px; font-weight:600; color:var(--muted); cursor:pointer; border:none; background:none; text-align:left; width:100%; transition:background .12s,color .12s; }
        .ctrl-nav:hover { background:rgba(255,255,255,.04); color:var(--text); }
        .ctrl-nav.on { background:linear-gradient(145deg, rgba(59,130,246,.16), rgba(59,130,246,.05)); color:var(--gold-2); box-shadow:inset 0 0 0 1px rgba(59,130,246,.22); }
        .ctrl-nav .cnt2 { margin-left:auto; font-size:10.5px; background:var(--chip); border-radius:5px; padding:1px 7px; font-weight:700; }
        .ctrl-nav.on .cnt2 { background:rgba(59,130,246,.18); color:var(--gold-2); }
        .ctrl-main { flex:1; min-width:0; }
        .ctrl-inp { width:100%; background:var(--chip); border:1px solid var(--border); border-radius:7px; padding:10px 12px; color:var(--text); outline:none; font-size:13.5px; }
        @media (max-width:860px){ .ctrl-shell{flex-direction:column} .ctrl-rail{flex-direction:row;overflow-x:auto;position:static;flex:none;width:100%} .ctrl-nav .cnt2{display:none} }
      `}</style>

      {/* cabeçalho */}
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', marginBottom: 18 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ width: 34, height: 34, borderRadius: 9, display: 'grid', placeItems: 'center', background: cats.proc.bg, color: cats.proc.c }}><Ico d={IC_RADAR} size={19} /></span>
            <h2 style={{ fontSize: 23, fontWeight: 800, letterSpacing: '-.01em' }}>Controladoria</h2>
          </div>
          <p className="muted" style={{ marginTop: 6, fontSize: 13.5 }}>Prazos, intimações, audiências e carteira — monitorados pelo <b style={{ color: 'var(--text)' }}>SYNC</b> em tempo real.</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 12.5, fontWeight: 700, padding: '7px 13px', borderRadius: 8, background: conectado ? 'rgba(52,211,153,.1)' : 'rgba(248,113,113,.1)', border: `1px solid ${conectado ? 'rgba(52,211,153,.28)' : 'rgba(248,113,113,.28)'}`, color: conectado ? '#6ee7b7' : '#fca5a5' }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: conectado ? '#34d399' : '#f87171', animation: conectado ? 'ctrlPulse 2s infinite' : 'none' }} />
            {conectado ? 'SYNC conectado' : 'SYNC offline'}
          </span>
          <button className="btn btn-ghost btn-sm" onClick={() => carregar(true)} disabled={atualizando} title="Atualizar">
            <span style={{ display: 'inline-flex', animation: atualizando ? 'ctrlSweep .8s linear infinite' : 'none' }}><Ico d={IC_REFRESH} size={15} /></span>
            Atualizar
          </button>
        </div>
      </div>

      {!conectado && (
        <div className="panel" style={{ margin: 0, borderColor: 'rgba(248,113,113,.3)' }}>
          <b>SYNC não configurado.</b> <span className="muted">{dash?.erro || 'Falta o token do SYNC no servidor.'}</span>
        </div>
      )}

      {conectado && (
        <div className="ctrl-shell">
          {/* menu lateral próprio */}
          <nav className="ctrl-rail">
            {NAV.map((it) => (
              <button key={it.k} className={`ctrl-nav ${sec === it.k ? 'on' : ''}`} onClick={() => setSec(it.k)}>
                <Ico d={it.ic} size={17} />{it.t}
                {(it.n !== undefined && it.n !== null && it.n !== 0) ? <span className="cnt2">{it.n}</span> : null}
              </button>
            ))}
          </nav>

          <div className="ctrl-main">
            {/* ===================== PAINEL ===================== */}
            {sec === 'painel' && (<>
              {indexando && (
                <div className="ctrl-idx" style={{ borderRadius: 8, border: '1px solid rgba(56,189,248,.28)', padding: '13px 16px', marginBottom: 18, display: 'flex', alignItems: 'center', gap: 12 }}>
                  <span style={{ width: 30, height: 30, borderRadius: 8, display: 'grid', placeItems: 'center', background: cats.inti.bg, color: cats.inti.c, flex: '0 0 auto', animation: 'ctrlSweep 2.2s linear infinite' }}><Ico d={IC_RADAR} size={17} /></span>
                  <div style={{ fontSize: 13 }}><b>Sincronizando a base…</b> <span className="muted">o SYNC está indexando os processos das OABs monitoradas. Os números se preenchem sozinhos.</span></div>
                </div>
              )}

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(215px, 1fr))', gap: 14, marginBottom: 18 }}>
                {tiles.map((tl) => {
                  const c = cats[tl.k]
                  return (
                    <div key={tl.k} className="panel ctrl-card" style={{ margin: 0, padding: 20 }}>
                      <div className="top" style={{ background: `linear-gradient(90deg, ${c.c}, transparent)` }} />
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <span style={{ color: 'var(--muted)', fontSize: 11, textTransform: 'uppercase', letterSpacing: '.06em', fontWeight: 700 }}>{tl.t}</span>
                        <span style={{ width: 34, height: 34, borderRadius: 9, display: 'grid', placeItems: 'center', background: c.bg, color: c.c }}><Ico d={tl.ic} size={17} /></span>
                      </div>
                      <div style={{ fontSize: 32, fontWeight: 800, marginTop: 12, fontVariantNumeric: 'tabular-nums', lineHeight: 1 }}>{tl.v}</div>
                      <div className="muted" style={{ fontSize: 11.5, marginTop: 7 }}>{tl.sub}</div>
                    </div>
                  )
                })}
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 16, alignItems: 'start' }}>
                <div style={{ display: 'grid', gap: 16 }}>
                  <Bloco icone={IC_INTI} cor={cats.inti} titulo="Intimações pendentes" contagem={intiTotal}>
                    {intiList.length === 0
                      ? <Vazio ico={IC_INBOX} texto={indexando ? 'Aguardando a primeira varredura do SYNC.' : 'Nenhuma intimação pendente.'} dica="As intimações chegam sozinhas pelos processos acompanhados no SYNC." />
                      : intiList.slice(0, 6).map((it) => (<IntimacaoRow key={it.id} it={it} onVer={it.processo ? () => setProcAberto(it.processo) : undefined} />))}
                  </Bloco>
                  <Bloco icone={IC_PRAZO} cor={cats.prazo} titulo="Prazos da semana" contagem={dash?.prazos_7d}>
                    {(!dash?.prazos || dash.prazos.length === 0)
                      ? <Vazio ico={IC_PRAZO} texto={indexando ? 'Os prazos aparecem após a indexação.' : 'Nenhum prazo nos próximos dias.'} />
                      : dash.prazos.slice(0, 8).map((p, i) => (<Linha key={p.id || i} titulo={p.descricao || p.tipo || 'Prazo'} tag={p.data || p.prazo} tagCor={cats.prazo} sub={p.processo || p.numero} onVer={(p.processo || p.numero) ? () => setProcAberto(p.processo || p.numero) : undefined} />))}
                  </Bloco>
                </div>
                <div style={{ display: 'grid', gap: 16 }}>
                  <div className="panel" style={{ margin: 0 }}>
                    <Cabecalho icone={IC_AUD} cor={cats.aud} titulo="Audiências próximas" contagem={aud.audiencias_30d} />
                    {(!aud.proximas || aud.proximas.length === 0)
                      ? <Vazio ico={IC_AUD} texto="Nenhuma audiência marcada." />
                      : aud.proximas.slice(0, 5).map((a, i) => (<Linha key={a.id || i} titulo={a.tipo || 'Audiência'} tag={a.data} tagCor={cats.aud} sub={a.processo || a.numero} onVer={(a.processo || a.numero) ? () => setProcAberto(a.processo || a.numero) : undefined} />))}
                  </div>
                  <div className="panel" style={{ margin: 0 }}>
                    <Cabecalho icone={IC_FOLDER} cor={cats.proc} titulo="Atividade" contagem={`${ativ.length}d`} />
                    {ativ.length === 0 ? <Vazio ico={IC_FOLDER} texto="Sem atividade registrada." />
                      : (<div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 64, marginTop: 4 }}>
                        {ativ.map((a, i) => (<div key={i} title={`${a.dia}: ${a.total}`} style={{ flex: 1, background: a.total ? ACCENT : 'var(--chip)', height: `${a.total ? Math.max(10, (a.total / maxAtiv) * 100) : 8}%`, borderRadius: 2, minHeight: 4, transition: 'height .2s' }} />))}
                      </div>)}
                  </div>
                </div>
              </div>
            </>)}

            {/* ===================== AGENDA ===================== */}
            {sec === 'agenda' && <AgendaCal eventos={agenda?.eventos || []} onAbrir={setProcAberto} indexando={indexando} />}

            {/* ===================== BUSCA ===================== */}
            {sec === 'busca' && (
              <div className="panel" style={{ margin: 0 }}>
                <Cabecalho icone={IC_SEARCH} cor={cats.proc} titulo="Buscar processos por OAB" />
                <p className="muted" style={{ fontSize: 12.5, marginTop: -6, marginBottom: 14 }}>Busca em tempo real no PDPJ. Clique no número (ou <b>Ver</b>) pra abrir o processo, ou <b>Registrar</b> pra puxar pra carteira.</p>
                <form onSubmit={fazerBusca} style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                  <input value={buscaOab} onChange={(e) => setBuscaOab(e.target.value)} placeholder="OAB (ex.: SP490295)" className="ctrl-inp" style={{ maxWidth: 260 }} />
                  <button className="btn btn-gold btn-sm" disabled={buscando} style={{ flex: '0 0 auto' }}>{buscando ? <span className="spin" /> : <Ico d={IC_SEARCH} size={15} />}Buscar</button>
                </form>
                {resultados?.erro && <div style={{ color: '#fca5a5', fontSize: 13 }}>Erro: {resultados.erro}</div>}
                {buscando && !resultados && <div className="muted" style={{ fontSize: 13 }}>Buscando no PDPJ…</div>}
                {resultados?.resultados && (resultados.resultados.length === 0
                  ? <Vazio ico={IC_INBOX} texto="Nenhum processo encontrado para essa OAB." />
                  : (<>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
                      <span className="muted" style={{ fontSize: 12.5 }}><b style={{ color: 'var(--text)' }}>{resultados.resultados.length}</b> processo(s){resultados.fonte ? ` — no ${String(resultados.fonte).toUpperCase()}` : ''}{resultados.cursor ? ' · há mais' : ''}</span>
                      <button className="btn btn-ghost btn-sm" onClick={registrarTodos}><Ico d={IC_PLUS} size={14} />Registrar todos</button>
                    </div>
                    <div style={{ overflowX: 'auto', border: '1px solid var(--border)', borderRadius: 8 }}>
                      <table>
                        <thead><tr><th>Processo</th><th>Tribunal</th><th style={{ textAlign: 'right' }}>Ação</th></tr></thead>
                        <tbody>
                          {resultados.resultados.map((p, i) => (
                            <tr key={p.numero || i}>
                              <td><button onClick={() => setProcAberto(p.numero)} style={{ background: 'none', border: 'none', padding: 0, color: 'var(--gold-2)', fontFamily: 'ui-monospace, monospace', fontSize: 13, cursor: 'pointer', fontWeight: 600 }}>{p.numero}</button></td>
                              <td>{p.tribunal || '—'}</td>
                              <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                <button className="btn btn-ghost btn-sm" onClick={() => setProcAberto(p.numero)} style={{ marginRight: 6 }}>Ver</button>
                                {reg[p.numero] === 'ok' ? <span className="pill done">Registrado</span>
                                  : reg[p.numero] === 'loading' ? <span className="spin" />
                                    : <button className="btn btn-gold btn-sm" onClick={() => registrar(p.numero)}>{reg[p.numero] === 'erro' ? 'Tentar de novo' : 'Registrar'}</button>}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {resultados.cursor && (
                      <div style={{ textAlign: 'center', marginTop: 14 }}>
                        <button className="btn btn-ghost btn-sm" onClick={carregarMaisBusca} disabled={buscando}>
                          {buscando ? <span className="spin" /> : <Ico d={IC_PLUS} size={14} />}Carregar mais processos
                        </button>
                      </div>
                    )}
                  </>))}
              </div>
            )}

            {/* ===================== PROCESSOS (carteira) ===================== */}
            {sec === 'processos' && (
              <div className="panel" style={{ margin: 0 }}>
                <Cabecalho icone={IC_FOLDER} cor={cats.proc} titulo="Processos na carteira" contagem={cartList.length} />
                <input value={fProc} onChange={(e) => setFProc(e.target.value)} placeholder="Filtrar por número, classe, assunto ou parte…" className="ctrl-inp" style={{ marginBottom: 14 }} />
                {cartFilt.length === 0
                  ? <Vazio ico={IC_FOLDER} texto={cartList.length ? 'Nenhum processo com esse filtro.' : (indexando ? 'Aguardando o SYNC indexar os processos.' : 'Nenhum processo na carteira ainda.')} dica={cartList.length ? undefined : 'Registre processos pela aba Busca.'} />
                  : cartFilt.map((p) => (
                    <div key={p.numero} className="ctrl-row clic" onClick={() => setProcAberto(p.numero)}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 13, fontWeight: 600, color: 'var(--gold-2)' }}>{p.numero}</span>
                        {p.tribunal && <span style={{ fontSize: 10, fontWeight: 700, color: cats.proc.c, background: cats.proc.bg, borderRadius: 5, padding: '2px 7px' }}>{p.tribunal}</span>}
                        {p.baixado ? <span style={{ fontSize: 10, fontWeight: 700, color: '#6ee7b7', background: 'rgba(52,211,153,.12)', borderRadius: 5, padding: '2px 7px' }}>Autos baixados</span>
                          : p.baixando_documentos ? <span style={{ fontSize: 10, fontWeight: 700, color: cats.inti.c, background: cats.inti.bg, borderRadius: 5, padding: '2px 7px' }}>Baixando…</span> : null}
                      </div>
                      <div style={{ fontSize: 12.5, marginTop: 4 }}>{p.classe || '—'}{p.assunto ? ` · ${p.assunto}` : ''}</div>
                      <div className="muted" style={{ fontSize: 11.5, marginTop: 3 }}>{limpoPolo(p.polo_ativo)} × {limpoPolo(p.polo_passivo)}{p.ultimo_andamento ? ` · andamento ${fmtData(p.ultimo_andamento)}` : ''}</div>
                    </div>
                  ))}
              </div>
            )}

            {/* ===================== INTIMAÇÕES ===================== */}
            {sec === 'intimacoes' && (
              <div className="panel" style={{ margin: 0 }}>
                <Cabecalho icone={IC_INTI} cor={cats.inti} titulo="Intimações pendentes" contagem={intiTotal} />
                {intiTotal > 0 && <p className="muted" style={{ fontSize: 12, marginTop: -6, marginBottom: 12 }}><b style={{ color: cats.prazo.c }}>{intiUrgentes}</b> exigem manifestação/prazo · {Math.max(0, intiTotal - intiUrgentes)} são só ciência.</p>}
                {intiList.length === 0
                  ? <Vazio ico={IC_INBOX} texto={indexando ? 'Aguardando a primeira varredura do SYNC.' : 'Nenhuma intimação pendente.'} dica="As intimações chegam sozinhas pelos processos acompanhados no SYNC." />
                  : intiList.map((it) => (<IntimacaoRow key={it.id} it={it} onVer={it.processo ? () => setProcAberto(it.processo) : undefined} />))}
              </div>
            )}

            {/* ===================== PARTES ===================== */}
            {sec === 'partes' && (
              <div className="panel" style={{ margin: 0 }}>
                <Cabecalho icone={IC_PARTES} cor={cats.aud} titulo="Partes" contagem={partes?.total} />
                <input value={fParte} onChange={(e) => setFParte(e.target.value)} placeholder="Filtrar por nome ou CPF/CNPJ…" className="ctrl-inp" style={{ marginBottom: 14 }} />
                {parteFilt.length === 0
                  ? <Vazio ico={IC_PARTES} texto={parteList.length ? 'Nenhuma parte com esse filtro.' : 'Nenhuma parte ainda.'} />
                  : parteFilt.map((e, i) => {
                    const aberto = parteAberta === (e.cpf_cnpj || e.nome || i)
                    const chave = e.cpf_cnpj || e.nome || i
                    return (
                      <div key={chave} className="ctrl-row">
                        <div className="clic" onClick={() => setParteAberta(aberto ? null : chave)} style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                          <b style={{ fontSize: 13, textTransform: 'capitalize' }}>{ocultoNome(e.nome) ? 'Sob sigilo' : e.nome}</b>
                          {e.sigiloso && <span style={{ fontSize: 9.5, fontWeight: 700, color: cats.prazo.c, background: cats.prazo.bg, borderRadius: 4, padding: '1px 6px', textTransform: 'uppercase', letterSpacing: '.04em' }}>sigiloso</span>}
                          {e.tipo_pessoa && <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase' }}>{e.tipo_pessoa}</span>}
                          <span style={{ marginLeft: 'auto', fontSize: 11, fontWeight: 700, color: cats.proc.c, background: cats.proc.bg, borderRadius: 5, padding: '2px 8px' }}>{(e.processos || []).length} processo(s)</span>
                          <span style={{ color: 'var(--gold-2)', fontWeight: 700, fontSize: 11.5 }}>{aberto ? 'ocultar' : 'ver ›'}</span>
                        </div>
                        {e.cpf_cnpj && <div className="muted" style={{ fontSize: 11.5, marginTop: 2, fontFamily: 'ui-monospace, monospace' }}>{e.cpf_cnpj}</div>}
                        {aberto && (
                          <div style={{ marginTop: 8, paddingLeft: 10, borderLeft: `2px solid ${cats.proc.bg}` }}>
                            {(e.processos || []).map((pr, j) => (
                              <div key={j} className="clic" onClick={() => setProcAberto(pr.numero)} style={{ cursor: 'pointer', padding: '7px 6px', borderRadius: 6 }}>
                                <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12.5, color: 'var(--gold-2)', fontWeight: 600 }}>{pr.numero}</span>
                                <span className="muted" style={{ fontSize: 11, marginLeft: 8 }}>{pr.tribunal}{pr.polo ? ` · ${pr.polo}` : ''}{pr.classe ? ` · ${pr.classe}` : ''}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
              </div>
            )}

            {/* ===================== MONITORES ===================== */}
            {sec === 'monitores' && (
              <div className="panel" style={{ margin: 0 }}>
                <Cabecalho icone={IC_RADAR} cor={cats.proc} titulo="Monitores ativos" contagem={mons.length} />
                <p className="muted" style={{ fontSize: 12.5, marginTop: -6, marginBottom: 14 }}>Cada OAB monitorada é acompanhada sozinha pelo SYNC — os processos, intimações e prazos entram automaticamente.</p>
                {mons.length === 0
                  ? <Vazio ico={IC_RADAR} texto="Nenhuma OAB monitorada ainda." />
                  : mons.map((m) => (
                    <div key={m.id} className="ctrl-row" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12.5, fontWeight: 700, letterSpacing: '.5px', padding: '4px 9px', borderRadius: 6, background: 'var(--chip)', border: '1px solid var(--border)' }}>{m.valor}</span>
                      <div style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 7 }}>
                        <span style={{ width: 7, height: 7, borderRadius: '50%', background: m.ultima_checagem ? '#34d399' : '#38bdf8', animation: m.ultima_checagem ? 'none' : 'ctrlPulse 2s infinite' }} />
                        <span className="muted" style={{ fontSize: 11.5 }}>{m.ultima_checagem ? 'Monitorando' : 'Sincronizando…'}</span>
                      </div>
                    </div>
                  ))}
                {ehAdmin && (
                  <form onSubmit={ligarMonitor} style={{ display: 'flex', gap: 8, marginTop: 14 }}>
                    <input value={oab} onChange={(e) => setOab(e.target.value)} placeholder="Ligar OAB (ex.: SP490295)" className="ctrl-inp" style={{ flex: 1 }} />
                    <button className="btn btn-gold btn-sm" disabled={salvando} style={{ flex: '0 0 auto' }}>{salvando ? <span className="spin" /> : <Ico d={IC_PLUS} size={15} />}Ligar</button>
                  </form>
                )}
                {msg && <div style={{ fontSize: 12, marginTop: 9, color: msg.startsWith('Erro') ? '#fca5a5' : '#6ee7b7' }}>{msg}</div>}
              </div>
            )}
          </div>
        </div>
      )}

      {procAberto && (
        <ProcessoDetalhe numero={procAberto} onClose={() => setProcAberto(null)} onRegistrar={() => registrar(procAberto)} regStatus={reg[procAberto]} />
      )}
    </>
  )
}

/* ---------- subcomponentes ---------- */
function Cabecalho({ icone, cor, titulo, contagem }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 12 }}>
      <span style={{ width: 28, height: 28, borderRadius: 8, display: 'grid', placeItems: 'center', background: cor.bg, color: cor.c }}><Ico d={icone} size={16} /></span>
      <span style={{ fontWeight: 700, fontSize: 14 }}>{titulo}</span>
      {(contagem !== undefined && contagem !== null && contagem !== '') && (
        <span style={{ marginLeft: 'auto', fontSize: 11.5, fontWeight: 700, color: cor.c, background: cor.bg, borderRadius: 6, padding: '2px 8px' }}>{contagem}</span>
      )}
    </div>
  )
}
function Bloco({ icone, cor, titulo, contagem, children }) {
  return (
    <div className="panel" style={{ margin: 0 }}>
      <Cabecalho icone={icone} cor={cor} titulo={titulo} contagem={contagem} />
      {children}
    </div>
  )
}
function Linha({ titulo, tag, tagCor, sub, onVer }) {
  return (
    <div className={`ctrl-row ${onVer ? 'clic' : ''}`} onClick={onVer}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <b style={{ fontSize: 13 }}>{titulo}</b>
        {tag && <span style={{ fontSize: 10.5, background: tagCor.bg, color: tagCor.c, borderRadius: 5, padding: '2px 8px', fontWeight: 700 }}>{tag}</span>}
      </div>
      {sub && (
        <div className="muted" style={{ fontSize: 11.5, marginTop: 3, fontFamily: 'ui-monospace, monospace' }}>
          {sub}{onVer && <span style={{ marginLeft: 8, color: 'var(--gold-2)', fontWeight: 700, fontFamily: 'Montserrat, sans-serif' }}>ver ›</span>}
        </div>
      )}
    </div>
  )
}
function fmtData(s) {
  if (!s) return ''
  const d = String(s).slice(0, 10).split('-')
  return d.length === 3 ? `${d[2]}/${d[1]}/${d[0]}` : String(s)
}
const TRI_CAT = {
  manifestacao: { l: 'Manifestação', c: '#f87171', bg: 'rgba(248,113,113,.14)' },
  recurso: { l: 'Cabe recurso', c: '#f87171', bg: 'rgba(248,113,113,.14)' },
  sentenca: { l: 'Sentença', c: '#a78bfa', bg: 'rgba(167,139,250,.14)' },
  audiencia: { l: 'Audiência', c: '#a78bfa', bg: 'rgba(167,139,250,.14)' },
  despacho: { l: 'Despacho', c: '#38bdf8', bg: 'rgba(56,189,248,.13)' },
  ciencia: { l: 'Só ciência', c: '#6ee7b7', bg: 'rgba(52,211,153,.12)' },
  outro: { l: 'Info', c: '#8695ad', bg: 'rgba(255,255,255,.06)' },
}
function IntimacaoRow({ it, onVer }) {
  const prazo = it.data_fatal || it.prazo_ato
  const t = it.triagem
  const cat = t ? (TRI_CAT[t.categoria] || TRI_CAT.outro) : null
  return (
    <div className={`ctrl-row ${onVer ? 'clic' : ''}`} onClick={onVer}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        {cat
          ? <span style={{ fontSize: 10, fontWeight: 800, color: cat.c, background: cat.bg, borderRadius: 5, padding: '2px 8px', textTransform: 'uppercase', letterSpacing: '.03em' }}>{cat.l}</span>
          : it.requer_manifestacao && <span style={{ fontSize: 10, fontWeight: 700, background: 'rgba(248,113,113,.14)', color: '#fca5a5', borderRadius: 5, padding: '2px 8px' }}>Manifestação</span>}
        <b style={{ fontSize: 13 }}>{it.tipo_comunicacao || 'Intimação'}{it.tipo_ato ? ` · ${it.tipo_ato}` : ''}</b>
        {prazo && <span style={{ fontSize: 10, fontWeight: 700, background: cats.prazo.bg, color: cats.prazo.c, borderRadius: 5, padding: '2px 8px' }}>Prazo {fmtData(prazo)}{it.prazo_dias ? ` · ${it.prazo_dias}d` : ''}</span>}
        {!t && <span style={{ fontSize: 9.5, color: 'var(--muted)', fontStyle: 'italic' }}>analisando…</span>}
      </div>
      {t?.acao && <div style={{ fontSize: 12, marginTop: 5, lineHeight: 1.45 }}><span style={{ color: cat.c, fontWeight: 800 }}>Ação: </span><span style={{ color: 'var(--text)' }}>{t.acao}</span></div>}
      {it.resumo && <div className="muted" style={{ fontSize: 11.5, marginTop: 4, lineHeight: 1.5, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{it.resumo}</div>}
      <div className="muted" style={{ fontSize: 11, marginTop: 4, fontFamily: 'ui-monospace, monospace' }}>
        {it.processo}{it.orgao ? ` · ${it.orgao}` : ''}{onVer && <span style={{ marginLeft: 8, color: 'var(--gold-2)', fontWeight: 700, fontFamily: 'Montserrat, sans-serif' }}>ver ›</span>}
      </div>
    </div>
  )
}
function Vazio({ ico, texto, dica }) {
  return (
    <div className="ctrl-empty">
      <span className="ring"><Ico d={ico} size={19} /></span>
      <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{texto}</span>
      {dica && <span style={{ fontSize: 11.5, maxWidth: 320, lineHeight: 1.5 }}>{dica}</span>}
    </div>
  )
}

function AgendaCal({ eventos, onAbrir, indexando }) {
  const hoje = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  const fmt = (a, m, d) => `${a}-${pad(m + 1)}-${pad(d)}`
  const hojeStr = fmt(hoje.getFullYear(), hoje.getMonth(), hoje.getDate())
  const [ref, setRef] = useState({ a: hoje.getFullYear(), m: hoje.getMonth() })
  const [sel, setSel] = useState(null)
  const [vista, setVista] = useState('mes')       // 'mes' | 'lista'
  const [filtro, setFiltro] = useState('todos')   // todos | vencidos | hoje | 7dias | audiencias
  const MES = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
  const DOW = ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb']
  const diasAte = (ds) => Math.round((new Date(ds + 'T00:00:00') - new Date(hojeStr + 'T00:00:00')) / 86400000)
  const brData = (s) => (s || '').split('-').reverse().join('/')
  const urg = (e) => {
    if (e.tipo === 'audiencia') return { c: cats.aud.c, bg: cats.aud.bg }
    const n = diasAte(e.data)
    if (n < 0) return { c: '#f87171', bg: 'rgba(248,113,113,.16)' }   // vencido
    if (n === 0) return { c: '#f87171', bg: 'rgba(248,113,113,.16)' } // hoje
    if (n <= 3) return { c: '#f6b73c', bg: 'rgba(246,183,60,.18)' }   // urgente
    if (n <= 7) return { c: '#f6b73c', bg: 'rgba(246,183,60,.10)' }   // breve
    return { c: cats.proc.c, bg: cats.proc.bg }                       // futuro
  }
  const porDia = {}
  eventos.forEach((e) => { if (e.data) (porDia[e.data] = porDia[e.data] || []).push(e) })

  // resumo
  const nVenc = eventos.filter((e) => e.tipo === 'prazo' && diasAte(e.data) < 0).length
  const nHoje = eventos.filter((e) => diasAte(e.data) === 0).length
  const n7 = eventos.filter((e) => { const n = diasAte(e.data); return n >= 0 && n <= 7 }).length
  const nAud = eventos.filter((e) => e.tipo === 'audiencia' && diasAte(e.data) >= 0).length
  const chips = [
    { k: 'vencidos', t: 'Vencidos', n: nVenc, c: '#f87171', bg: 'rgba(248,113,113,.14)' },
    { k: 'hoje', t: 'Vence hoje', n: nHoje, c: '#f87171', bg: 'rgba(248,113,113,.14)' },
    { k: '7dias', t: 'Próx. 7 dias', n: n7, c: cats.prazo.c, bg: cats.prazo.bg },
    { k: 'audiencias', t: 'Audiências', n: nAud, c: cats.aud.c, bg: cats.aud.bg },
  ]

  // lista conforme vista/filtro/dia
  let itens = []
  let titulo = 'Próximos'
  if (vista === 'mes' && sel) {
    itens = porDia[sel] || []
    titulo = brData(sel)
  } else if (filtro === 'vencidos') {
    itens = eventos.filter((e) => e.tipo === 'prazo' && diasAte(e.data) < 0).sort((a, b) => b.data.localeCompare(a.data))
    titulo = 'Vencidos'
  } else if (filtro === 'hoje') {
    itens = eventos.filter((e) => diasAte(e.data) === 0); titulo = 'Vence hoje'
  } else if (filtro === '7dias') {
    itens = eventos.filter((e) => { const n = diasAte(e.data); return n >= 0 && n <= 7 }); titulo = 'Próximos 7 dias'
  } else if (filtro === 'audiencias') {
    itens = eventos.filter((e) => e.tipo === 'audiencia' && diasAte(e.data) >= 0); titulo = 'Audiências'
  } else {
    itens = eventos.filter((e) => diasAte(e.data) >= 0).slice(0, 80); titulo = 'Próximos'
  }
  // agrupa por dia
  const grupos = {}
  itens.forEach((e) => (grupos[e.data] = grupos[e.data] || []).push(e))
  const datasOrd = Object.keys(grupos).sort((a, b) => (filtro === 'vencidos' ? b.localeCompare(a) : a.localeCompare(b)))

  const navMes = (delta) => { setSel(null); setRef((r) => { const nm = r.m + delta; return { a: r.a + Math.floor(nm / 12), m: ((nm % 12) + 12) % 12 } }) }
  const primeiroDow = new Date(ref.a, ref.m, 1).getDay()
  const diasNoMes = new Date(ref.a, ref.m + 1, 0).getDate()
  const celulas = []
  for (let i = 0; i < primeiroDow; i++) celulas.push(null)
  for (let d = 1; d <= diasNoMes; d++) celulas.push(d)

  const Evento = ({ e, semData }) => {
    const u = urg(e)
    const n = e.tipo === 'prazo' ? diasAte(e.data) : null
    const selo = e.tipo === 'audiencia' ? 'audiência' : (n < 0 ? `vencido ${-n}d` : n === 0 ? 'hoje' : `em ${n}d`)
    const ativoOc = ocultoNome(e.polo_ativo)
    const passivoOc = ocultoNome(e.polo_passivo)
    const ambosOc = ativoOc && passivoOc
    const temPartes = (e.polo_ativo || e.polo_passivo) && !ambosOc
    return (
      <div className={`ctrl-row ${e.processo ? 'clic' : ''}`} onClick={e.processo ? () => onAbrir(e.processo) : undefined}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {!semData && <span style={{ fontFamily: 'ui-monospace,monospace', fontSize: 11.5, color: u.c, fontWeight: 700 }}>{brData(e.data)}{e.hora ? ` ${e.hora}` : ''}</span>}
          <span style={{ fontSize: 10, fontWeight: 700, color: u.c, background: u.bg, borderRadius: 5, padding: '1px 7px', textTransform: 'uppercase', letterSpacing: '.03em' }}>{selo}</span>
          {temPartes
            ? <b style={{ fontSize: 12.5, textTransform: 'capitalize' }}>{limpoPolo(e.polo_ativo)} <span style={{ color: 'var(--muted)', fontWeight: 400 }}>×</span> {limpoPolo(e.polo_passivo)}</b>
            : <b style={{ fontSize: 12.5, textTransform: 'capitalize' }}>{e.classe || e.titulo}</b>}
          {ambosOc && <span style={{ fontSize: 9.5, fontWeight: 700, color: 'var(--muted)', background: 'var(--chip)', border: '1px solid var(--border)', borderRadius: 4, padding: '1px 6px' }}>sob sigilo</span>}
          {e.manifestacao && <span style={{ fontSize: 9.5, fontWeight: 700, color: '#fca5a5', background: 'rgba(248,113,113,.14)', borderRadius: 4, padding: '1px 6px' }}>manifestação</span>}
        </div>
        <div className="muted" style={{ fontSize: 11, marginTop: 3, fontFamily: 'ui-monospace,monospace' }}>
          {temPartes && <span style={{ textTransform: 'capitalize', fontFamily: 'Montserrat,sans-serif' }}>{e.titulo} · </span>}
          {e.processo}{e.orgao ? ` · ${e.orgao}` : ''}{e.processo && <span style={{ marginLeft: 8, color: 'var(--gold-2)', fontWeight: 700, fontFamily: 'Montserrat,sans-serif' }}>ver ›</span>}
        </div>
        {e.resumo && <div className="muted" style={{ fontSize: 11, marginTop: 3, lineHeight: 1.5, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{e.resumo}</div>}
      </div>
    )
  }

  const curto = (s) => { const t = limpoPolo(s); if (t === 'Parte sigilosa') return 'Sigiloso'; const w = String(t).split(' ').filter(Boolean); return w.slice(0, 2).join(' ') }

  return (
    <div style={{ display: 'grid', gap: 18 }}>
      {/* chips resumo */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 14 }}>
        {chips.map((ch) => {
          const on = filtro === ch.k && vista === 'lista'
          return (
            <button key={ch.k} onClick={() => { setFiltro(ch.k); setVista('lista'); setSel(null) }} className="panel ctrl-card" style={{
              margin: 0, padding: '18px 20px', cursor: 'pointer', textAlign: 'left',
              border: `1px solid ${on ? ch.c : 'var(--border)'}`, display: 'flex', alignItems: 'center', gap: 14,
              boxShadow: on ? `0 0 0 1px ${ch.c}, 0 10px 28px rgba(0,0,0,.35)` : undefined,
            }}>
              <div className="top" style={{ background: `linear-gradient(90deg, ${ch.c}, transparent)` }} />
              <span style={{ width: 44, height: 44, borderRadius: 12, display: 'grid', placeItems: 'center', background: ch.bg, color: ch.c, flex: '0 0 auto' }}>
                <Ico d={ch.k === 'audiencias' ? IC_AUD : IC_PRAZO} size={22} />
              </span>
              <div>
                <div style={{ fontSize: 30, fontWeight: 800, lineHeight: 1, color: ch.n ? ch.c : 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>{ch.n}</div>
                <div className="muted" style={{ fontSize: 11.5, marginTop: 5, textTransform: 'uppercase', letterSpacing: '.05em', fontWeight: 700 }}>{ch.t}</div>
              </div>
            </button>
          )
        })}
      </div>

      <div className="panel" style={{ margin: 0, padding: 24 }}>
        {/* header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 18, flexWrap: 'wrap' }}>
          <span style={{ width: 40, height: 40, borderRadius: 12, display: 'grid', placeItems: 'center', background: 'linear-gradient(145deg, rgba(246,183,60,.22), rgba(246,183,60,.06))', color: cats.prazo.c, boxShadow: '0 4px 14px rgba(246,183,60,.14)' }}><Ico d={IC_CAL} size={22} /></span>
          <div>
            <div style={{ fontWeight: 800, fontSize: 22, letterSpacing: '-.01em', lineHeight: 1 }}>{vista === 'mes' ? `${MES[ref.m]} ${ref.a}` : 'Agenda'}</div>
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{eventos.length} eventos · prazos e audiências</div>
          </div>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
            <div style={{ display: 'flex', background: 'var(--chip)', borderRadius: 9, padding: 3, gap: 2 }}>
              <button className="btn btn-sm" style={{ padding: '7px 16px', background: vista === 'mes' ? 'var(--gold)' : 'transparent', color: vista === 'mes' ? '#fff' : 'var(--muted)', boxShadow: 'none' }} onClick={() => setVista('mes')}>Mês</button>
              <button className="btn btn-sm" style={{ padding: '7px 16px', background: vista === 'lista' ? 'var(--gold)' : 'transparent', color: vista === 'lista' ? '#fff' : 'var(--muted)', boxShadow: 'none' }} onClick={() => { setVista('lista'); setFiltro('todos'); setSel(null) }}>Lista</button>
            </div>
            {vista === 'mes' && <div style={{ display: 'flex', gap: 6, marginLeft: 4 }}>
              <button className="btn btn-ghost btn-sm" onClick={() => navMes(-1)} title="Mês anterior"><Ico d={IC_CHEV_L} size={16} /></button>
              <button className="btn btn-ghost btn-sm" onClick={() => { setSel(null); setRef({ a: hoje.getFullYear(), m: hoje.getMonth() }) }}>Hoje</button>
              <button className="btn btn-ghost btn-sm" onClick={() => navMes(1)} title="Próximo mês"><Ico d={IC_CHEV_R} size={16} /></button>
            </div>}
          </div>
        </div>

        {/* legenda */}
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center', margin: '0 0 16px', padding: '10px 14px', background: 'var(--chip)', borderRadius: 9, border: '1px solid var(--border)' }}>
          <span style={{ fontSize: 10.5, fontWeight: 800, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.06em' }}>Legenda</span>
          {[['#f87171', 'Vencido / vence hoje'], ['#f6b73c', 'Prazo a vencer (≤7 dias)'], [cats.proc.c, 'Prazo distante (+7 dias)'], [cats.aud.c, 'Audiência']].map(([c, l]) => (
            <span key={l} style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 12 }}>
              <span style={{ width: 12, height: 12, borderRadius: 3, background: c, borderLeft: `3px solid ${c}`, display: 'inline-block' }} />{l}
            </span>
          ))}
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 12 }}>
            <span style={{ fontSize: 9, fontWeight: 700, color: '#fca5a5', background: 'rgba(248,113,113,.14)', borderRadius: 4, padding: '1px 6px' }}>manifestação</span> exige resposta
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 12, color: 'var(--muted)' }}>
            <b style={{ color: 'var(--text)' }}>Cliente</b> × <b style={{ color: 'var(--text)' }}>Parte contrária</b>
          </span>
        </div>

        {/* calendário */}
        {vista === 'mes' && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7,1fr)', gap: 6 }}>
            {DOW.map((d, i) => <div key={'h' + i} style={{ textAlign: 'center', fontSize: 11, fontWeight: 800, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.08em', padding: '4px 0 8px' }}>{d}</div>)}
            {celulas.map((d, i) => {
              if (!d) return <div key={'e' + i} />
              const ds = fmt(ref.a, ref.m, d)
              const evs = porDia[ds] || []
              const ehHoje = ds === hojeStr
              const selecionado = ds === sel
              const dow = new Date(ref.a, ref.m, d).getDay()
              const fds = dow === 0 || dow === 6
              const critico = evs.some((e) => urg(e).c === '#f87171')
              return (
                <button key={i} onClick={() => setSel(selecionado ? null : ds)} style={{
                  minHeight: 108, borderRadius: 12,
                  border: `1px solid ${selecionado ? 'var(--gold)' : (critico ? 'rgba(248,113,113,.35)' : 'var(--border)')}`,
                  background: selecionado ? 'rgba(59,130,246,.08)' : (fds ? 'rgba(255,255,255,.015)' : 'var(--surface)'),
                  padding: 8, cursor: 'pointer', textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 4, color: 'var(--text)',
                  transition: 'transform .1s, border-color .15s, box-shadow .15s',
                  boxShadow: selecionado ? '0 8px 22px rgba(0,0,0,.35)' : 'none',
                }}>
                  <span style={{
                    fontSize: 13, fontWeight: 700, alignSelf: 'flex-start', minWidth: 24, height: 24, lineHeight: '24px', textAlign: 'center', borderRadius: '50%',
                    background: ehHoje ? 'var(--gold)' : 'transparent', color: ehHoje ? '#fff' : (fds ? 'var(--muted)' : 'var(--text)'),
                  }}>{d}</span>
                  {evs.slice(0, 3).map((e, j) => {
                    const u = urg(e)
                    return (
                      <span key={j} title={(e.polo_ativo || e.polo_passivo) ? `${limpoPolo(e.polo_ativo)} × ${limpoPolo(e.polo_passivo)} — ${e.titulo}` : e.titulo} style={{
                        fontSize: 10, fontWeight: 700, color: u.c, background: u.bg, borderLeft: `3px solid ${u.c}`, borderRadius: 4,
                        padding: '2px 5px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                      }}>{e.tipo === 'audiencia' ? 'Audiência' : (e.polo_ativo ? curto(e.polo_ativo) : (e.prazo_dias ? `Prazo ${e.prazo_dias}d` : 'Prazo'))}</span>
                    )
                  })}
                  {evs.length > 3 && <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', paddingLeft: 2 }}>+{evs.length - 3} mais</span>}
                </button>
              )
            })}
          </div>
        )}

        {/* lista agrupada por dia */}
        <div style={{ marginTop: vista === 'mes' ? 20 : 0, borderTop: vista === 'mes' ? '1px solid var(--border)' : 'none', paddingTop: vista === 'mes' ? 16 : 0 }}>
          <div style={{ fontWeight: 800, fontSize: 15, marginBottom: 12 }}>{titulo} <span className="muted" style={{ fontWeight: 400, fontSize: 13 }}>· {itens.length}</span></div>
          {itens.length === 0
            ? <Vazio ico={IC_CAL} texto={indexando ? 'Os prazos aparecem após a indexação.' : (sel ? 'Nada nesse dia.' : 'Nada aqui.')} />
            : datasOrd.map((dt) => (
              <div key={dt} style={{ marginBottom: 14 }}>
                {!(vista === 'mes' && sel) && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 2px 8px' }}>
                    <span style={{ fontSize: 12.5, fontWeight: 800, color: diasAte(dt) < 0 ? '#f87171' : diasAte(dt) === 0 ? cats.prazo.c : 'var(--text)' }}>{brData(dt)}</span>
                    <span style={{ fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.04em', color: 'var(--muted)' }}>{diasAte(dt) === 0 ? 'hoje' : diasAte(dt) < 0 ? `vencido há ${-diasAte(dt)}d` : `em ${diasAte(dt)}d`}</span>
                    <span style={{ flex: 1, height: 1, background: 'var(--border)' }} />
                  </div>
                )}
                {grupos[dt].map((e, i) => <Evento key={i} e={e} semData />)}
              </div>
            ))}
        </div>
      </div>
    </div>
  )
}

function Badge({ cor, children }) {
  return <span style={{ fontSize: 10.5, fontWeight: 700, color: cor.c, background: cor.bg, borderRadius: 6, padding: '3px 9px' }}>{children}</span>
}
function Mini({ l, v }) {
  if (!v && v !== 0) return null
  return (<div><div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.05em', fontWeight: 700 }}>{l}</div><div style={{ fontSize: 13, marginTop: 2 }}>{v}</div></div>)
}
function Polo({ titulo, partes, advs, cor }) {
  const lista = Array.isArray(partes) ? partes : (partes ? [partes] : [])
  const advL = Array.isArray(advs) ? advs : (advs ? [advs] : [])
  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12 }}>
      <div style={{ fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.05em', color: cor.c, marginBottom: 6 }}>{titulo}</div>
      {lista.length === 0 ? <span className="muted" style={{ fontSize: 12 }}>—</span>
        : lista.map((p, i) => <div key={i} style={{ fontSize: 12.5, fontWeight: 600 }}>{typeof p === 'string' ? limpoPolo(p) : (p.nome || '—')}</div>)}
      {advL.length > 0 && <div className="muted" style={{ fontSize: 11, marginTop: 5 }}>Adv.: {advL.map((a) => (typeof a === 'string' ? a : a.nome)).join(', ')}</div>}
    </div>
  )
}

// Painel lateral (drawer) com o processo completo do SYNC
function ProcessoDetalhe({ numero, onClose, onRegistrar, regStatus }) {
  const [d, setD] = useState(null)
  const [det, setDet] = useState(null)
  const [erro, setErro] = useState('')
  const [sub, setSub] = useState('movimentos')
  const [autos, setAutos] = useState(null)
  const [teor, setTeor] = useState({})
  const [abertoId, setAbertoId] = useState(null)

  useEffect(() => {
    let vivo = true
    setD(null); setDet(null); setErro(''); setAutos(null); setTeor({}); setAbertoId(null)
    api(`/api/controladoria/preview/${encodeURIComponent(numero)}`).then((r) => vivo && setD(r)).catch((e) => vivo && setErro(e.message || 'não foi possível carregar o processo'))
    api(`/api/controladoria/processo/${encodeURIComponent(numero)}`).then((r) => vivo && setDet(r)).catch(() => {})
    api(`/api/controladoria/autos/${encodeURIComponent(numero)}`).then((r) => vivo && setAutos(r)).catch(() => {})
    return () => { vivo = false }
  }, [numero])

  async function verTeor(it) {
    const id = it.id
    if (abertoId === id) { setAbertoId(null); return }
    setAbertoId(id)
    if (!teor[id]) {
      setTeor((s) => ({ ...s, [id]: { loading: true } }))
      try {
        if (it.tipo === 'documento') {
          const r = await api(`/api/controladoria/documento/${id}/markdown`)
          setTeor((s) => ({ ...s, [id]: { texto: r.markdown || r.texto || r.conteudo || '(sem teor disponível)' } }))
        } else {
          const r = await api(`/api/controladoria/intimacao/${id}`)
          setTeor((s) => ({ ...s, [id]: { texto: r.texto || r.resumo || '(sem teor disponível)', link: r.link } }))
        }
      } catch { setTeor((s) => ({ ...s, [id]: { erro: 'não foi possível carregar o teor' } })) }
    }
  }
  async function baixarPdf(it) {
    try {
      const r = await api(`/api/controladoria/documento/${it.id}/arquivo`)
      if (r && r.url) window.open(r.url, '_blank', 'noopener')
      else window.alert('Arquivo ainda não disponível (o SYNC pode estar baixando).')
    } catch { window.alert('Não foi possível abrir o PDF (pode ainda estar em download no tribunal).') }
  }

  const brl = (v) => (v || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
  const timeline = autos?.autos || (d?.movimentos || []).map((m) => ({ tipo: 'movimento', data: m.data, descricao: m.descricao }))
  const docs = (autos?.autos || []).filter((a) => a.tipo === 'documento' && a.id)
  const atos = (autos?.autos || []).filter((a) => a.id && a.tipo !== 'documento')
  const SUBS = [
    { k: 'movimentos', t: 'Movimentações', ic: IC_MOV, n: timeline.length },
    { k: 'pecas', t: 'Peças e atos', ic: IC_DOC, n: (docs.length + atos.length) || d?.documentos?.length },
    { k: 'partes', t: 'Partes', ic: IC_PARTES, n: d?.partes?.length },
    { k: 'dados', t: 'Dados', ic: IC_INFO },
  ]

  const PecaRow = ({ a, tipo }) => {
    const podeLer = tipo === 'ato' || !!a.tem_markdown
    const podePdf = !!a.tem_arquivo
    const baixando = a.conteudo_status && String(a.conteudo_status).startsWith('aguardando')
    return (
      <div className="ctrl-row">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ color: cats.proc.c, flex: '0 0 auto' }}><Ico d={IC_DOC} size={15} /></span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600, textTransform: 'capitalize', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.nome || a.tipo_documento || a.tipo_ato || a.tipo || 'Documento'}</div>
            <div className="muted" style={{ fontSize: 11 }}>{a.tipo_documento || a.tipo_ato || a.tipo}{a.data ? ` · ${fmtData(a.data)}` : ''}{baixando ? ' · baixando…' : ''}</div>
          </div>
          {podeLer && <button className="btn btn-ghost btn-sm" onClick={() => verTeor(a)}>{abertoId === a.id ? 'ocultar' : 'ler'}</button>}
          {podePdf && <button className="btn btn-gold btn-sm" onClick={() => baixarPdf(a)}>PDF</button>}
        </div>
        {abertoId === a.id && (
          <div style={{ marginTop: 10, padding: 12, background: 'var(--chat-bg)', border: '1px solid var(--border)', borderRadius: 8 }}>
            {teor[a.id]?.loading && <span className="muted" style={{ fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 8 }}><span className="spin" /> carregando teor…</span>}
            {teor[a.id]?.erro && <span style={{ color: '#fca5a5', fontSize: 12 }}>{teor[a.id].erro}</span>}
            {teor[a.id]?.texto && <div style={{ fontSize: 12, lineHeight: 1.6, whiteSpace: 'pre-wrap', maxHeight: 360, overflowY: 'auto' }}>{teor[a.id].texto}</div>}
            {teor[a.id]?.link && <a href={teor[a.id].link} target="_blank" rel="noreferrer" style={{ display: 'inline-block', marginTop: 8, fontSize: 11.5, color: 'var(--gold-2)', fontWeight: 700 }}>Abrir no PJe ›</a>}
          </div>
        )}
      </div>
    )
  }

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(3,8,18,.66)', backdropFilter: 'blur(3px)', zIndex: 120, display: 'flex', justifyContent: 'flex-end' }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 'min(760px, 100%)', height: '100%', background: 'var(--surface-solid)', borderLeft: '1px solid var(--border)', boxShadow: '-20px 0 60px rgba(0,0,0,.5)', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '18px 22px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'flex-start', gap: 12 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontFamily: 'ui-monospace, monospace', fontSize: 17, fontWeight: 700 }}>{numero}</div>
            <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginTop: 8 }}>
              {d?.tribunal && <Badge cor={cats.proc}>{d.tribunal}</Badge>}
              {d?.grau && <Badge cor={cats.inti}>{d.grau}º grau</Badge>}
              {d?.classe && <Badge cor={cats.aud}>{d.classe}</Badge>}
              {d?.segredo && <Badge cor={cats.prazo}>Segredo de justiça</Badge>}
            </div>
          </div>
          <button onClick={onClose} className="btn btn-ghost btn-sm" title="Fechar"><Ico d={IC_CLOSE} size={15} /></button>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: 22 }}>
          {erro && <div className="ctrl-empty"><span className="ring"><Ico d={IC_INFO} size={19} /></span><span style={{ fontSize: 13, fontWeight: 600 }}>{erro}</span></div>}
          {!d && !erro && <div className="center-load" style={{ minHeight: 200, gap: 10 }}><span className="spin" />Carregando o processo…</div>}
          {d && (<>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginBottom: 16, flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
                {d.orgao_julgador && <Mini l="Órgão julgador" v={d.orgao_julgador} />}
                {d.assunto && <Mini l="Assunto" v={d.assunto} />}
                {d.valor_causa ? <Mini l="Valor da causa" v={brl(d.valor_causa)} /> : null}
                {d.data_distribuicao && <Mini l="Distribuído" v={d.data_distribuicao} />}
              </div>
              {regStatus === 'ok' ? <span className="pill done">Na carteira</span>
                : <button className="btn btn-gold btn-sm" onClick={onRegistrar}><Ico d={IC_PLUS} size={14} />Registrar processo</button>}
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12, marginBottom: 18 }}>
              <Polo titulo="Polo ativo" partes={det?.polo_ativo || d.polo_ativo} advs={det?.advogados_ativo || d.advogados_ativo} cor={cats.inti} />
              <Polo titulo="Polo passivo" partes={det?.polo_passivo || d.polo_passivo} advs={det?.advogados_passivo || d.advogados_passivo} cor={cats.prazo} />
            </div>

            <div className="ctrl-tabs">
              {SUBS.map((s) => (
                <button key={s.k} className={`ctrl-tab ${sub === s.k ? 'on' : ''}`} onClick={() => setSub(s.k)}>
                  <Ico d={s.ic} size={14} />{s.t}{s.n ? <span className="cnt">{s.n}</span> : null}
                </button>
              ))}
            </div>

            {sub === 'movimentos' && (() => {
              const tl = timeline.filter((m) => (m.descricao || '').trim() || m.tipo === 'audiencia')
              return tl.length === 0
                ? <Vazio ico={IC_MOV} texto="Sem movimentações." />
                : tl.map((m, i) => (
                <div key={i} className="ctrl-row" style={{ display: 'flex', gap: 12 }}>
                  <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11.5, color: m.tipo === 'audiencia' ? cats.aud.c : 'var(--gold-2)', whiteSpace: 'nowrap', paddingTop: 1 }}>{fmtData(m.data)}</span>
                  <span style={{ fontSize: 12.5, lineHeight: 1.5 }}>{m.descricao}{m.tipo === 'audiencia' ? ' · audiência' : ''}</span>
                </div>
              ))
            })()}

            {sub === 'pecas' && (<>
              <div style={{ fontSize: 11.5, lineHeight: 1.5, padding: '10px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'rgba(56,189,248,.06)', marginBottom: 12 }} className="muted">
                <b style={{ color: 'var(--text)' }}>Ler</b> abre o teor aqui · <b style={{ color: 'var(--text)' }}>PDF</b> abre o arquivo original do tribunal (via certificado). Documentos ainda em download aparecem como "baixando…".
              </div>
              {(docs.length === 0 && atos.length === 0)
                ? <Vazio ico={IC_DOC} texto={regStatus === 'ok' ? 'Aguardando o SYNC baixar os autos.' : 'Registre o processo pra abrir os autos.'} />
                : (<>
                  {docs.length > 0 && <div className="muted" style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.04em', margin: '4px 0 2px' }}>Documentos ({docs.length})</div>}
                  {docs.map((a) => <PecaRow key={a.id} a={a} tipo="doc" />)}
                  {atos.length > 0 && <div className="muted" style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.04em', margin: '14px 0 2px' }}>Atos e intimações ({atos.length})</div>}
                  {atos.map((a) => <PecaRow key={a.id} a={a} tipo="ato" />)}
                </>)}
            </>)}

            {sub === 'partes' && ((d.partes || []).length === 0
              ? <Vazio ico={IC_PARTES} texto="Sem partes." />
              : d.partes.map((p, i) => (
                <div key={i} className="ctrl-row">
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <b style={{ fontSize: 13 }}>{typeof p.nome === 'string' ? limpoPolo(p.nome) : p.nome}</b>
                    {p.polo && <span style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.05em', color: 'var(--muted)' }}>{p.polo}</span>}
                  </div>
                  {p.cpf_cnpj && <div className="muted" style={{ fontSize: 11.5, fontFamily: 'ui-monospace, monospace' }}>{p.cpf_cnpj}</div>}
                  {(p.advogados || []).length > 0 && <div className="muted" style={{ fontSize: 11.5, marginTop: 3 }}>Adv.: {(p.advogados || []).map((a) => (typeof a === 'string' ? a : a.nome)).join(', ')}</div>}
                </div>
              )))}

            {sub === 'dados' && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 14 }}>
                <Mini l="Número" v={d.numero} />
                <Mini l="Tribunal" v={d.tribunal} />
                <Mini l="Classe" v={d.classe} />
                <Mini l="Assunto" v={d.assunto} />
                <Mini l="Órgão julgador" v={d.orgao_julgador} />
                <Mini l="Grau" v={d.grau} />
                <Mini l="Valor da causa" v={d.valor_causa ? brl(d.valor_causa) : null} />
                <Mini l="Distribuição" v={d.data_distribuicao} />
                <Mini l="Segredo" v={d.segredo ? 'Sim' : 'Não'} />
              </div>
            )}
          </>)}
        </div>
      </div>
    </div>
  )
}

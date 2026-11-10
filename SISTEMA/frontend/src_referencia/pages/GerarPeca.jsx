import { useEffect, useRef, useState } from 'react'
import { api, downloadFile } from '../api'

const TIPOS = [
  'PETICAO INICIAL', 'CONTESTACAO', 'REPLICA', 'RAZOES FINAIS',
  'RECURSO ORDINARIO', 'RECURSO DE REVISTA', 'CONTRARRAZOES',
  'EMBARGOS DE DECLARACAO', 'AGRAVO DE INSTRUMENTO', 'MANIFESTACAO',
]

export default function GerarPeca() {
  const [tipo, setTipo] = useState(TIPOS[0])
  const [cliente, setCliente] = useState('')
  const [clienteId, setClienteId] = useState('')
  const [driveLink, setDriveLink] = useState('')
  const [dados, setDados] = useState('')
  const [erro, setErro] = useState('')
  const [job, setJob] = useState(null)
  const [gerando, setGerando] = useState(false)
  const [historico, setHistorico] = useState([])

  // autocomplete
  const [sugestoes, setSugestoes] = useState([])
  const [buscando, setBuscando] = useState(false)
  const buscaTimer = useRef(null)
  const pollTimer = useRef(null)

  useEffect(() => { carregarHistorico() }, [])
  useEffect(() => () => clearInterval(pollTimer.current), [])

  async function carregarHistorico() {
    try { setHistorico((await api('/api/peca/historico')).items) } catch {}
  }

  function onClienteChange(v) {
    setCliente(v); setClienteId('')
    clearTimeout(buscaTimer.current)
    if (v.trim().length < 2) { setSugestoes([]); return }
    buscaTimer.current = setTimeout(async () => {
      setBuscando(true)
      try {
        const r = await api(`/api/clientes/buscar?q=${encodeURIComponent(v)}`)
        setSugestoes(r.items || [])
      } catch { setSugestoes([]) } finally { setBuscando(false) }
    }, 400)
  }

  function escolherCliente(c) {
    setCliente(c.nome); setClienteId(String(c.id || '')); setSugestoes([])
  }

  async function gerar(e) {
    e.preventDefault()
    setErro('')
    if (!cliente.trim() || (!driveLink.trim() && !dados.trim())) { setErro('Preencha o cliente e o link da pasta do Drive (ou os dados do caso).'); return }
    setGerando(true); setJob(null)
    try {
      const r = await api('/api/peca/gerar', {
        method: 'POST',
        body: { tipo_peca: tipo, cliente_nome: cliente, cliente_advbox_id: clienteId, drive_link: driveLink, dados_caso: dados },
      })
      poll(r.job_id)
    } catch (err) {
      setErro(err.message); setGerando(false)
    }
  }

  function poll(jobId) {
    clearInterval(pollTimer.current)
    pollTimer.current = setInterval(async () => {
      try {
        const j = await api(`/api/peca/jobs/${jobId}`)
        setJob(j)
        if (j.status === 'done' || j.status === 'error') {
          clearInterval(pollTimer.current); setGerando(false); carregarHistorico()
        }
      } catch (err) {
        clearInterval(pollTimer.current); setGerando(false); setErro(err.message)
      }
    }, 2500)
  }

  async function baixar(jobId, nome) {
    try { await downloadFile(`/api/peca/jobs/${jobId}/download`, `${nome}.docx`) }
    catch (err) { setErro(err.message) }
  }

  return (
    <div className="row" style={{ alignItems: 'flex-start' }}>
      <div style={{ flex: 2, minWidth: 320 }}>
        <form className="panel" onSubmit={gerar}>
          <h2>Nova peça</h2>
          <p className="p-sub">A IA produz no padrão da Dra. Advogada e entrega no papel timbrado do Escritório Modelo.</p>

          {erro && <div className="alert alert-error">{erro}</div>}

          <div className="row">
            <div className="field">
              <label>Tipo de peça</label>
              <select value={tipo} onChange={(e) => setTipo(e.target.value)}>
                {TIPOS.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div className="field autocomplete">
              <label>Cliente (busca no ADVBOX)</label>
              <input value={cliente} onChange={(e) => onClienteChange(e.target.value)} placeholder="Digite o nome do cliente" autoComplete="off" />
              {sugestoes.length > 0 && (
                <div className="ac-list">
                  {sugestoes.map((c) => (
                    <div key={c.id} className="ac-item" onClick={() => escolherCliente(c)}>
                      {c.nome}<br /><small>{c.cpf || 'sem CPF'} · {c.telefone || ''}</small>
                    </div>
                  ))}
                </div>
              )}
              {buscando && <small className="muted">buscando…</small>}
              {clienteId && <small className="muted">✓ vinculado ao ADVBOX (#{clienteId})</small>}
            </div>
          </div>

          <div className="field">
            <label>Link da pasta do Google Drive (documentos do cliente)</label>
            <input value={driveLink} onChange={(e) => setDriveLink(e.target.value)} placeholder="Cole aqui o link da pasta do Drive com todos os documentos do cliente" autoComplete="off" />
          </div>

          <div className="field">
            <label>Dados do caso / instruções</label>
            <textarea value={dados} onChange={(e) => setDados(e.target.value)} placeholder="Descreva os fatos, pedidos, período do contrato, função, o que precisa constar na peça…" />
          </div>

          <button className="btn btn-gold btn-block" disabled={gerando}>
            {gerando ? <><span className="spin" /> Gerando…</> : 'Gerar peça no timbrado'}
          </button>
        </form>

        {job && (
          <div className="panel">
            <h2>Andamento</h2>
            <div style={{ marginTop: 8 }}>
              <span className={`pill ${job.status}`}>{job.status}</span>{' '}
              <span className="muted">{job.progresso_msg}</span>
            </div>
            {job.status === 'error' && <div className="alert alert-error" style={{ marginTop: 14 }}>{job.erro}</div>}
            {job.status === 'done' && (
              <div className="alert alert-ok" style={{ marginTop: 14 }}>
                <div style={{ marginBottom: 10 }}>
                  Peça pronta para revisão da Dra. Advogada.
                  {job.drive_url && <><br />Salva na pasta do cliente no Drive (PEÇAS SISTEMA OPERACIONAL).</>}
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <button className="btn btn-sm" onClick={() => baixar(job.id, `${job.cliente_nome} - ${job.tipo_peca}`)}>Baixar .docx</button>
                  {job.drive_url && <a className="btn btn-ghost btn-sm" href={job.drive_url} target="_blank" rel="noreferrer">Abrir no Drive</a>}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <div style={{ flex: 1, minWidth: 260 }}>
        <div className="panel">
          <h2>Histórico</h2>
          <p className="p-sub">Suas últimas peças.</p>
          {historico.length === 0 && <p className="muted">Nenhuma peça ainda.</p>}
          {historico.map((h) => (
            <div key={h.id} style={{ padding: '12px 0', borderBottom: '1px solid var(--border)' }}>
              <div style={{ fontWeight: 600, fontSize: 14 }}>{h.cliente_nome}</div>
              <div className="muted" style={{ fontSize: 13 }}>{h.tipo_peca}</div>
              <div style={{ marginTop: 6, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <span className={`pill ${h.status}`}>{h.status}</span>
                {h.tem_docx && <button className="btn btn-ghost btn-sm" onClick={() => baixar(h.id, `${h.cliente_nome} - ${h.tipo_peca}`)}>Baixar</button>}
                {h.drive_url && <a className="btn btn-ghost btn-sm" href={h.drive_url} target="_blank" rel="noreferrer">Drive</a>}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

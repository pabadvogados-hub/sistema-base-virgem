import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

export default function Intake() {
  const [transcricao, setTranscricao] = useState(null)
  const [extras, setExtras] = useState([])
  const [erro, setErro] = useState('')
  const [job, setJob] = useState(null)
  const [modoTeste, setModoTeste] = useState(false)
  const [rodando, setRodando] = useState(false)
  const [historico, setHistorico] = useState([])
  const pollTimer = useRef(null)

  useEffect(() => { carregarHistorico() }, [])
  useEffect(() => () => clearInterval(pollTimer.current), [])

  async function carregarHistorico() {
    try { setHistorico((await api('/api/intake/historico')).items) } catch {}
  }

  async function subirArquivo(file) {
    const form = new FormData()
    form.append('arquivo', file)
    return await api('/api/intake/upload', { method: 'POST', form })
  }

  async function iniciar(e) {
    e.preventDefault()
    setErro('')
    if (!transcricao) { setErro('Selecione a transcrição da reunião (PDF).'); return }
    setRodando(true); setJob(null)
    try {
      const t = await subirArquivo(transcricao)
      const extrasUp = []
      for (const f of extras) extrasUp.push((await subirArquivo(f)).path)
      const r = await api('/api/intake/iniciar', {
        method: 'POST',
        body: { transcricao_path: t.path, transcricao_nome: t.nome, docs_extras: extrasUp, modo_teste: modoTeste },
      })
      poll(r.job_id)
    } catch (err) {
      setErro(err.message); setRodando(false)
    }
  }

  function poll(jobId) {
    clearInterval(pollTimer.current)
    pollTimer.current = setInterval(async () => {
      try {
        const j = await api(`/api/intake/jobs/${jobId}`)
        setJob(j)
        if (j.status === 'done' || j.status === 'error') {
          clearInterval(pollTimer.current); setRodando(false); carregarHistorico()
        }
      } catch (err) {
        clearInterval(pollTimer.current); setRodando(false); setErro(err.message)
      }
    }, 3000)
  }

  return (
    <div className="row" style={{ alignItems: 'flex-start' }}>
      <div style={{ flex: 2, minWidth: 340 }}>
        <form className="panel" onSubmit={iniciar}>
          <h2>Nova contratação</h2>
          <p className="p-sub">Sobe a transcrição da reunião e os documentos. O sistema roda o intake completo: IVA analisa, cria a pasta no Drive, gera Ficha/Contrato/Procuração/Declaração, envia pro ZapSign (Dra. Advogada assina via API), cadastra no ADVBOX, cria a tarefa da Flávia e inscreve na cobrança.</p>

          {erro && <div className="alert alert-error">{erro}</div>}

          <div className="field">
            <label>Transcrição da reunião (PDF, Word, TXT, imagem…)</label>
            <input type="file" accept=".pdf,.doc,.docx,.txt,.md,.rtf,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff" onChange={(e) => setTranscricao(e.target.files[0])} />
          </div>

          <div className="field">
            <label>Documentos do cliente (CNH, RG, CTPS, fotos/prints… — pode selecionar vários)</label>
            <input type="file" multiple accept=".pdf,.txt,.md,.rtf,.doc,.docx,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff" onChange={(e) => setExtras([...e.target.files])} />
            {extras.length > 0 && <small className="muted">{extras.length} arquivo(s) selecionado(s)</small>}
          </div>

          <div className="alert" style={{ background: '#fff8e6', color: '#8a6d00', border: '1px solid #f0e0a8', fontSize: 13 }}>
            ⚠️ Operação real: cria registros no Drive/ADVBOX e envia mensagens ao cliente. Use com um cliente de verdade.
          </div>

          <label style={{ display: 'flex', gap: 9, alignItems: 'flex-start', padding: '12px 14px', margin: '4px 0 14px', borderRadius: 10, cursor: 'pointer', border: `1.5px solid ${modoTeste ? 'rgba(59,130,246,0.5)' : 'var(--border)'}`, background: modoTeste ? 'rgba(59,130,246,0.08)' : 'transparent' }}>
            <input type="checkbox" checked={modoTeste} onChange={(e) => setModoTeste(e.target.checked)} style={{ marginTop: 2 }} />
            <span style={{ fontSize: 13.5 }}>
              <b style={{ color: modoTeste ? 'var(--gold-2)' : 'var(--text)' }}>🧪 Modo teste</b>
              <span className="muted" style={{ display: 'block', fontSize: 12.5, marginTop: 2 }}>
                Gera só os documentos no Drive (Ficha, Contrato, Procuração, Declaração). <b>NÃO</b> envia pro ZapSign, <b>NÃO</b> cadastra no ADVBOX e <b>NÃO</b> manda WhatsApp ao cliente.
              </span>
            </span>
          </label>

          <button className={`btn btn-block ${modoTeste ? '' : 'btn-gold'}`} disabled={rodando} style={modoTeste ? { background: '#334155', color: '#fff' } : undefined}>
            {rodando ? <><span className="spin" /> Processando…</> : (modoTeste ? '🧪 Rodar em modo teste' : 'Iniciar contratação')}
          </button>
        </form>

        {job && (
          <div className="panel">
            <h2>Andamento</h2>
            <div style={{ marginTop: 8, marginBottom: 12 }}>
              <span className={`pill ${job.status}`}>{job.status}</span>{' '}
              <span className="muted">{job.progresso_msg}</span>
              {job.cliente_nome && <div style={{ marginTop: 6, fontWeight: 600 }}>Cliente: {job.cliente_nome}</div>}
            </div>
            {job.log && (
              <pre style={{ background: '#0b1a33', color: '#d6e2f5', padding: 16, borderRadius: 10, fontSize: 12.5, lineHeight: 1.55, overflowX: 'auto', whiteSpace: 'pre-wrap', maxHeight: 460, overflowY: 'auto' }}>{job.log}</pre>
            )}
          </div>
        )}
      </div>

      <div style={{ flex: 1, minWidth: 260 }}>
        <div className="panel">
          <h2>Histórico</h2>
          <p className="p-sub">Últimas contratações.</p>
          {historico.length === 0 && <p className="muted">Nenhuma ainda.</p>}
          {historico.map((h) => (
            <div key={h.id} style={{ padding: '12px 0', borderBottom: '1px solid var(--border)' }}>
              <div style={{ fontWeight: 600, fontSize: 14 }}>{h.cliente_nome || h.transcricao_nome || '—'}</div>
              <div style={{ marginTop: 6 }}><span className={`pill ${h.status}`}>{h.status}</span></div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

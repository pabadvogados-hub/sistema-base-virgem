import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

const PERIODOS = [
  { k: 'today', label: 'Hoje' },
  { k: 'yesterday', label: 'Ontem' },
  { k: 'last_7d', label: '7 dias' },
  { k: 'last_14d', label: '14 dias' },
  { k: 'last_30d', label: '30 dias' },
]

const brl = (v) => (Number(v) || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
const num = (v) => (Number(v) || 0).toLocaleString('pt-BR')

function Tile({ label, valor, sub, destaque }) {
  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 14,
      padding: '18px 20px', boxShadow: 'var(--shadow)',
    }}>
      <div style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: '.05em', color: 'var(--muted)', fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 800, marginTop: 6, color: destaque ? 'var(--gold-2)' : 'var(--text)' }}>{valor}</div>
      {sub && <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 3 }}>{sub}</div>}
    </div>
  )
}

export default function Campanhas() {
  const [periodo, setPeriodo] = useState('today')
  const [dados, setDados] = useState(null)
  const [serie, setSerie] = useState([])
  const [erro, setErro] = useState('')
  const [carregando, setCarregando] = useState(false)
  const [atualizadoEm, setAtualizadoEm] = useState(null)
  const timer = useRef(null)

  async function carregar(p = periodo, silencioso = false) {
    if (!silencioso) setCarregando(true)
    setErro('')
    try {
      const d = await api(`/api/campanhas/dashboard?periodo=${p}`)
      if (d.erro) setErro(d.erro)
      setDados(d)
      setAtualizadoEm(new Date())
      const s = await api('/api/campanhas/serie?dias=14')
      setSerie(s.serie || [])
    } catch (e) {
      setErro(e.message)
    } finally {
      setCarregando(false)
    }
  }

  useEffect(() => { carregar(periodo) }, [periodo])

  // Ao vivo: atualiza sozinho a cada 60s
  useEffect(() => {
    clearInterval(timer.current)
    timer.current = setInterval(() => carregar(periodo, true), 60000)
    return () => clearInterval(timer.current)
  }, [periodo])

  const total = dados?.total || {}
  const camps = dados?.campanhas || []
  const maxGasto = Math.max(1, ...serie.map((s) => s.gasto))

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12, marginBottom: 22 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <h2 style={{ fontSize: 22, fontWeight: 800 }}>Campanhas</h2>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 700, color: 'var(--success)', background: 'rgba(52,211,153,0.12)', border: '1px solid rgba(52,211,153,0.3)', padding: '4px 10px', borderRadius: 20 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--success)', display: 'inline-block' }} /> AO VIVO
          </span>
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          {PERIODOS.map((p) => (
            <button key={p.k} className={`btn btn-sm ${periodo === p.k ? 'btn-gold' : 'btn-ghost'}`} onClick={() => setPeriodo(p.k)}>{p.label}</button>
          ))}
          <button className="btn btn-sm btn-ghost" onClick={() => carregar(periodo)} disabled={carregando}>{carregando ? '…' : '↻'}</button>
        </div>
      </div>

      {erro && <div className="alert alert-error">{erro}</div>}

      {/* KPIs */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 14, marginBottom: 22 }}>
        <Tile label="Investido" valor={brl(total.gasto)} destaque />
        <Tile label="Leads (conversas)" valor={num(total.conversas)} destaque />
        <Tile label="Custo por lead" valor={brl(total.custo_por_conversa)} />
        <Tile label="Cliques" valor={num(total.cliques)} />
        <Tile label="Impressões" valor={num(total.impressoes)} />
        <Tile label="Alcance" valor={num(total.alcance)} />
        <Tile label="CTR" valor={`${num(total.ctr)}%`} />
        <Tile label="CPC" valor={brl(total.cpc)} />
      </div>

      {/* Gráfico últimos 14 dias */}
      <div className="panel">
        <h2>Investimento diário — últimos 14 dias</h2>
        <p className="p-sub">Barra = investido no dia · número acima = leads.</p>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 6, height: 160, marginTop: 10 }}>
          {serie.length === 0 && <span className="muted">Sem dados no período.</span>}
          {serie.map((s) => (
            <div key={s.data} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, minWidth: 0 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--gold-2)' }}>{s.conversas || ''}</div>
              <div title={`${s.data}: ${brl(s.gasto)} · ${s.conversas} leads`}
                style={{
                  width: '100%', maxWidth: 34,
                  height: `${Math.round((s.gasto / maxGasto) * 120) + 4}px`,
                  background: 'linear-gradient(180deg, var(--gold-2), var(--gold-soft))',
                  borderRadius: '6px 6px 0 0',
                }} />
              <div style={{ fontSize: 10, color: 'var(--muted)' }}>{(s.data || '').slice(8, 10)}/{(s.data || '').slice(5, 7)}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Tabela de campanhas */}
      <div className="panel" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ padding: '20px 24px' }}>
          <h2>Campanhas ativas</h2>
          <p className="p-sub">{atualizadoEm ? `Atualizado às ${atualizadoEm.toLocaleTimeString('pt-BR')}` : 'Carregando…'}</p>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table>
            <thead>
              <tr>
                <th>Campanha</th><th>Investido</th><th>Leads</th><th>Custo/lead</th><th>Cliques</th><th>CTR</th><th>CPC</th>
              </tr>
            </thead>
            <tbody>
              {camps.length === 0 && <tr><td colSpan={7} className="muted" style={{ padding: 20 }}>Nenhuma campanha ativa no período.</td></tr>}
              {camps.map((c) => (
                <tr key={c.id}>
                  <td style={{ maxWidth: 320 }}>
                    <div style={{ fontWeight: 600 }}>{c.nome}</div>
                    <div className="muted" style={{ fontSize: 12 }}>{c.status}</div>
                  </td>
                  <td>{brl(c.gasto)}</td>
                  <td style={{ fontWeight: 700, color: 'var(--gold-2)' }}>{num(c.conversas)}</td>
                  <td>{brl(c.custo_por_conversa)}</td>
                  <td>{num(c.cliques)}</td>
                  <td>{num(c.ctr)}%</td>
                  <td>{brl(c.cpc)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}

import { useState, useEffect } from 'react'
import { api } from '../api'

const LIMIT = 50
const PRATA = {
  background: 'linear-gradient(180deg,#fff,#c8d2e4 60%,#9aa6bd)',
  WebkitBackgroundClip: 'text', backgroundClip: 'text', WebkitTextFillColor: 'transparent',
}
const BRL = (v) => (v || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
const ehContraria = (papel) => (papel || '').toUpperCase().includes('CONTR')

function Campo({ label, valor }) {
  if (!valor) return null
  return (
    <div>
      <div style={{ fontSize: 10.5, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.05em', fontWeight: 700 }}>{label}</div>
      <div style={{ fontSize: 13.5, marginTop: 2 }}>{valor}</div>
    </div>
  )
}

function FichaProcesso({ id, onFechar }) {
  const [d, setD] = useState(null)
  const [erro, setErro] = useState('')
  useEffect(() => {
    let vivo = true
    api(`/api/processos/${id}`).then((r) => vivo && setD(r)).catch((e) => vivo && setErro(e.message))
    return () => { vivo = false }
  }, [id])

  return (
    <div onClick={onFechar} style={{ position: 'fixed', inset: 0, background: 'rgba(3,8,18,.66)', backdropFilter: 'blur(3px)', display: 'grid', placeItems: 'center', zIndex: 100, padding: 20 }}>
      <div onClick={(e) => e.stopPropagation()} className="panel" style={{ width: 720, maxWidth: '100%', maxHeight: '88vh', overflowY: 'auto', margin: 0 }}>
        {erro && <div className="alert alert-error">{erro}</div>}
        {!d && !erro && <p className="muted">Carregando ficha…</p>}
        {d && (
          <>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 19, fontWeight: 800, ...PRATA }}>{d.cliente || '—'}</div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 7, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--gold-2)', background: 'var(--chip)', borderRadius: 20, padding: '2px 10px' }}>{d.area || '—'}</span>
                  {d.encerrado
                    ? <span style={{ fontSize: 11.5, color: 'var(--muted)', fontWeight: 700 }}>● Encerrado</span>
                    : <span style={{ fontSize: 11.5, color: 'var(--success)', fontWeight: 700 }}>● Ativo</span>}
                  <span className="muted" style={{ fontSize: 12 }}>{d.numero || d.protocolo || 'sem número'}</span>
                </div>
              </div>
              <button onClick={onFechar} className="btn btn-sm btn-ghost">Fechar</button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 14, marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--border)' }}>
              <Campo label="Tipo" valor={d.tipo} />
              <Campo label="Fase" valor={d.fase} />
              <Campo label="Etapa" valor={d.etapa} />
              <Campo label="Responsável" valor={d.responsavel} />
              <Campo label="Distribuído em" valor={d.data_processo} />
              <Campo label="Pasta" valor={d.pasta} />
              <Campo label="Expectativa" valor={d.valor_expectativa ? BRL(d.valor_expectativa) : null} />
              <Campo label="Honorários" valor={d.valor_honorarios ? BRL(d.valor_honorarios) : null} />
              <Campo label="Contingência" valor={d.contingencia ? BRL(d.contingencia) : null} />
            </div>

            <div style={{ marginTop: 22 }}>
              <div style={{ fontSize: 11, fontWeight: 800, letterSpacing: '.14em', textTransform: 'uppercase', color: 'var(--gold)' }}>
                Partes ({(d.partes || []).length})
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 10 }}>
                {(d.partes || []).map((pt) => {
                  const contra = ehContraria(pt.papel)
                  const f = pt.ficha || {}
                  return (
                    <div key={pt.id} style={{ border: `1px solid ${contra ? 'rgba(248,113,113,0.3)' : 'rgba(52,211,153,0.3)'}`, borderRadius: 12, padding: '12px 14px', background: 'var(--panel-2, rgba(255,255,255,0.03))' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 9, flexWrap: 'wrap' }}>
                        <span style={{ fontWeight: 700, fontSize: 14 }}>{pt.nome || '—'}</span>
                        <span style={{ fontSize: 10, fontWeight: 800, letterSpacing: '.05em', borderRadius: 20, padding: '2px 8px',
                          color: contra ? '#f87171' : '#34d399', background: contra ? 'rgba(248,113,113,0.12)' : 'rgba(52,211,153,0.12)' }}>
                          {contra ? 'PARTE CONTRÁRIA' : (pt.papel || 'CLIENTE')}
                        </span>
                      </div>
                      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 7, fontSize: 12.3, color: 'var(--muted)' }}>
                        {(pt.documento || f.documento) && <span>Doc: <b style={{ color: 'var(--text)' }}>{pt.documento || f.documento}</b></span>}
                        {f.telefone && <span>Tel: <b style={{ color: 'var(--text)' }}>{f.telefone}</b></span>}
                        {f.email && <span>{f.email}</span>}
                        {(f.cidade || f.estado) && <span>{[f.cidade, f.estado].filter(Boolean).join(' / ')}</span>}
                        {f.profissao && <span>{f.profissao}</span>}
                      </div>
                    </div>
                  )
                })}
                {(d.partes || []).length === 0 && <p className="muted" style={{ fontSize: 13 }}>Sem partes cadastradas.</p>}
              </div>
            </div>

            {d.notas && (
              <div style={{ marginTop: 22 }}>
                <div style={{ fontSize: 11, fontWeight: 800, letterSpacing: '.14em', textTransform: 'uppercase', color: 'var(--gold)' }}>Notas</div>
                <div style={{ fontSize: 13.3, marginTop: 8, whiteSpace: 'pre-wrap', color: '#d7deec', lineHeight: 1.6 }}>{d.notas}</div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

export default function Processos() {
  const [q, setQ] = useState('')
  const [busca, setBusca] = useState('')
  const [grupo, setGrupo] = useState('')
  const [dados, setDados] = useState(null)
  const [erro, setErro] = useState('')
  const [carregando, setCarregando] = useState(true)
  const [pagina, setPagina] = useState(0)
  const [arquivados, setArquivados] = useState(false)
  const [fichaId, setFichaId] = useState(null)

  async function carregar() {
    setCarregando(true); setErro('')
    try {
      const p = new URLSearchParams({ q: busca, grupo, ativos: arquivados ? 'false' : 'true', limit: LIMIT, offset: pagina * LIMIT })
      setDados(await api(`/api/processos?${p.toString()}`))
    } catch (e) { setErro(e.message) }
    setCarregando(false)
  }
  useEffect(() => { carregar() }, [busca, grupo, pagina, arquivados])

  function aplicarBusca(e) { e.preventDefault(); setPagina(0); setBusca(q.trim()) }
  const total = dados?.total || 0
  const totalPag = Math.max(1, Math.ceil(total / LIMIT))
  const itens = dados?.itens || []

  const inp = { padding: '9px 12px', border: '1.5px solid var(--border)', borderRadius: 10, fontSize: 14, fontFamily: 'inherit', background: 'var(--surface-2)', color: 'var(--text)' }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 800 }}>Processos</h1>
        <span className="muted" style={{ fontSize: 13 }}>
          {dados ? `${total} processo${total !== 1 ? 's' : ''} · ${dados.ativos_total} ativos da Advogada` : 'ADVBOX · Advogada'}
        </span>
      </div>

      <form onSubmit={aplicarBusca} style={{ display: 'flex', gap: 8, marginTop: 16, flexWrap: 'wrap', alignItems: 'center' }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar por cliente, número, tipo…"
          style={{ ...inp, flex: 1, minWidth: 240 }} />
        <select value={grupo} onChange={(e) => { setPagina(0); setGrupo(e.target.value) }} style={{ ...inp, maxWidth: 260 }}>
          <option value="">Todas as áreas</option>
          {(dados?.grupos || []).map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
        <button className="btn btn-gold btn-sm" type="submit">Buscar</button>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5, color: 'var(--muted)', cursor: 'pointer', whiteSpace: 'nowrap' }}>
          <input type="checkbox" checked={arquivados} onChange={(e) => { setPagina(0); setArquivados(e.target.checked) }} />
          incluir arquivados
        </label>
      </form>

      {erro && <div className="alert alert-error" style={{ marginTop: 14 }}>{erro}</div>}

      <div className="panel" style={{ marginTop: 16, padding: 0, overflow: 'hidden' }}>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13.5 }}>
            <thead>
              <tr style={{ color: 'var(--muted)', fontSize: 11.5, textTransform: 'uppercase', letterSpacing: '.04em' }}>
                <th style={{ textAlign: 'left', padding: '11px 16px' }}>Cliente</th>
                <th style={{ textAlign: 'left', padding: '11px 10px' }}>Área / Núcleo</th>
                <th style={{ textAlign: 'left', padding: '11px 10px' }}>Tipo</th>
                <th style={{ textAlign: 'left', padding: '11px 10px' }}>Nº / Protocolo</th>
                <th style={{ textAlign: 'left', padding: '11px 16px' }}>Fase / Etapa</th>
              </tr>
            </thead>
            <tbody>
              {carregando && <tr><td colSpan={5} style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>Carregando…</td></tr>}
              {!carregando && itens.map((p) => (
                <tr key={p.id} onClick={() => setFichaId(p.id)} title="Ver ficha completa"
                  style={{ borderTop: '1px solid var(--border)', cursor: 'pointer' }}
                  onMouseEnter={(e) => e.currentTarget.style.background = 'rgba(59,130,246,0.06)'}
                  onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}>
                  <td style={{ padding: '11px 16px', fontWeight: 600, maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {p.cliente || '—'}
                    {p.partes?.length > 1 && <span style={{ marginLeft: 6, fontSize: 10.5, color: 'var(--muted)', fontWeight: 600 }}>+{p.partes.length - 1}</span>}
                  </td>
                  <td style={{ padding: '11px 10px', whiteSpace: 'nowrap' }}><span style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--gold-2)', background: 'var(--chip)', borderRadius: 20, padding: '2px 9px' }}>{p.grupo || '—'}</span></td>
                  <td style={{ padding: '11px 10px', color: 'var(--muted)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.tipo || '—'}</td>
                  <td style={{ padding: '11px 10px', color: 'var(--muted)', whiteSpace: 'nowrap' }}>{p.numero || '—'}</td>
                  <td style={{ padding: '11px 16px', fontSize: 12.5 }}>
                    <span style={{ color: 'var(--gold-2)', fontWeight: 600 }}>{p.fase || '—'}</span>
                    {p.etapa ? <span style={{ color: 'var(--muted)' }}> · {p.etapa}</span> : null}
                  </td>
                </tr>
              ))}
              {!carregando && itens.length === 0 && <tr><td colSpan={5} style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>Nenhum processo encontrado.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {total > LIMIT && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 14, marginTop: 16 }}>
          <button className="btn btn-sm btn-ghost" disabled={pagina === 0} onClick={() => setPagina((p) => Math.max(0, p - 1))}>‹ Anterior</button>
          <span className="muted" style={{ fontSize: 13 }}>Página {pagina + 1} de {totalPag}</span>
          <button className="btn btn-sm btn-ghost" disabled={pagina + 1 >= totalPag} onClick={() => setPagina((p) => p + 1)}>Próxima ›</button>
        </div>
      )}

      {fichaId && <FichaProcesso id={fichaId} onFechar={() => setFichaId(null)} />}
    </div>
  )
}

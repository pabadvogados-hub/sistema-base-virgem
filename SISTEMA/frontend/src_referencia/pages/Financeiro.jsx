import { useState, useEffect } from 'react'
import { api } from '../api'

const BRL = (v) => (v || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
const dataBR = (s) => { if (!s) return '—'; const [y, m, d] = s.split('-'); return `${d}/${m}/${y}` }

const TABS = [
  { k: 'cobrancas', label: 'Cobranças (Asaas)', pronto: true },
  { k: 'receber', label: 'Contas a Receber', pronto: true },
  { k: 'pagar', label: 'Contas a Pagar', pronto: true },
  { k: 'fechamento', label: 'Fechamento / DRE', pronto: false },
  { k: 'comissoes', label: 'Comissões', pronto: false },
]

function Kpi({ titulo, qtd, total, cor }) {
  return (
    <div style={{ flex: 1, minWidth: 190, background: 'var(--panel)', border: `1px solid ${cor}44`, borderRadius: 14, padding: '16px 18px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ width: 9, height: 9, borderRadius: '50%', background: cor, boxShadow: `0 0 10px ${cor}` }} />
        <span style={{ fontSize: 12.5, color: 'var(--muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.04em' }}>{titulo}</span>
      </div>
      <div style={{ fontSize: 24, fontWeight: 800, marginTop: 8, color: cor, fontVariantNumeric: 'tabular-nums' }}>{BRL(total)}</div>
      <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 2 }}>{qtd} lançamento{qtd !== 1 ? 's' : ''}</div>
    </div>
  )
}

// Painel generico: KPIs + sub-abas + filtros (cliente/data) + tabela. enviarBoleto opcional.
function Painel({ endpoint, kpis, colunas, enviarBoleto }) {
  const [dados, setDados] = useState(null)
  const [erro, setErro] = useState('')
  const [carregando, setCarregando] = useState(true)
  const [aba, setAba] = useState('vencidas')
  const [fCli, setFCli] = useState('')
  const [fDe, setFDe] = useState('')
  const [fAte, setFAte] = useState('')
  const [enviando, setEnviando] = useState(null)
  const [enviados, setEnviados] = useState({})

  async function carregar() {
    setCarregando(true); setErro('')
    try { setDados(await api(endpoint)) } catch (e) { setErro(e.message) }
    setCarregando(false)
  }
  useEffect(() => { carregar() }, [endpoint])

  const r = dados?.resumo
  let lista = dados ? (dados[aba] || []) : []
  const cli = fCli.trim().toLowerCase()
  lista = lista.filter((x) =>
    (!cli || (x.cliente || '').toLowerCase().includes(cli) || (x.descricao || '').toLowerCase().includes(cli))
    && (!fDe || (x.vencimento || '') >= fDe)
    && (!fAte || (x.vencimento || '') <= fAte))

  async function fazerEnvio(id, nome) {
    if (!window.confirm(`Enviar o boleto para ${nome} pelo WhatsApp do escritório?`)) return
    setEnviando(id)
    try { await enviarBoleto(id); setEnviados((e) => ({ ...e, [id]: true })) }
    catch (e) { alert('Falha ao enviar: ' + e.message) }
    setEnviando(null)
  }
  const cols = enviarBoleto ? [...colunas, {
    h: 'Boleto', align: 'center', cell: (x) => enviados[x.id]
      ? <span style={{ color: 'var(--success)', fontWeight: 700, fontSize: 12 }}>✓ Enviado</span>
      : <button onClick={() => fazerEnvio(x.id, x.cliente)} disabled={enviando === x.id} className="btn btn-sm"
          style={{ background: 'var(--success)', color: '#08130d', border: 'none', fontWeight: 700, fontSize: 12, padding: '5px 11px', whiteSpace: 'nowrap' }}>
          {enviando === x.id ? '…' : 'Enviar boleto'}</button>,
  }] : colunas

  const inp = { padding: '8px 11px', border: '1.5px solid var(--border)', borderRadius: 9, fontSize: 13, fontFamily: 'inherit', background: 'var(--surface-2)', color: 'var(--text)' }

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: -34, marginBottom: 6 }}>
        <button onClick={carregar} className="btn btn-sm btn-ghost">↻ Atualizar</button>
      </div>
      {erro && <div className="alert alert-error" style={{ marginTop: 8 }}>{erro}</div>}
      {carregando && <p className="muted" style={{ marginTop: 16 }}>Carregando…</p>}
      {dados && (
        <>
          <div style={{ display: 'flex', gap: 12, marginTop: 12, flexWrap: 'wrap' }}>
            {kpis.map((k) => <Kpi key={k.rkey} titulo={k.titulo} qtd={r[k.rkey].qtd} total={r[k.rkey].total} cor={k.cor} />)}
          </div>
          {dados.ocultos_nao_cobrar > 0 && (
            <div style={{ marginTop: 10, fontSize: 12.5, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 7 }}>
              <span style={{ color: 'var(--gold-2)', fontWeight: 700 }}>{dados.ocultos_nao_cobrar}</span>
              cobranças ocultas — clientes da lista "não cobrar" (continuam no Asaas, só não aparecem aqui).
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, marginTop: 20, flexWrap: 'wrap', alignItems: 'center' }}>
            {kpis.map((k) => (
              <button key={k.aba} onClick={() => setAba(k.aba)}
                style={{ border: `1px solid ${aba === k.aba ? k.cor : 'var(--border)'}`, background: aba === k.aba ? `${k.cor}22` : 'transparent',
                  color: aba === k.aba ? k.cor : 'var(--muted)', borderRadius: 9, padding: '8px 14px', fontSize: 13, fontWeight: 700, cursor: 'pointer' }}>
                {k.curto} · {r[k.rkey].qtd}
              </button>
            ))}
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <input value={fCli} onChange={(e) => setFCli(e.target.value)} placeholder="Filtrar cliente…" style={{ ...inp, width: 180 }} />
              <span style={{ fontSize: 12, color: 'var(--muted)' }}>venc.</span>
              <input type="date" value={fDe} onChange={(e) => setFDe(e.target.value)} style={inp} title="De" />
              <input type="date" value={fAte} onChange={(e) => setFAte(e.target.value)} style={inp} title="Até" />
              {(fCli || fDe || fAte) && <button onClick={() => { setFCli(''); setFDe(''); setFAte('') }} className="btn btn-sm btn-ghost">limpar</button>}
            </div>
          </div>

          <div className="panel" style={{ marginTop: 14, padding: 0, overflow: 'hidden' }}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13.5 }}>
                <thead>
                  <tr style={{ color: 'var(--muted)', fontSize: 11.5, textTransform: 'uppercase', letterSpacing: '.04em' }}>
                    {cols.map((c, i) => <th key={i} style={{ textAlign: c.align || 'left', padding: '11px 14px' }}>{c.h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {lista.map((row) => (
                    <tr key={row.id} style={{ borderTop: '1px solid var(--border)' }}>
                      {cols.map((c, i) => <td key={i} style={{ padding: '11px 14px', textAlign: c.align || 'left', ...(c.tdStyle ? c.tdStyle(row, aba) : {}) }}>{c.cell(row, aba)}</td>)}
                    </tr>
                  ))}
                  {lista.length === 0 && <tr><td colSpan={cols.length} style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>Nada aqui.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </>
  )
}

const truncar = { maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }
const num = { fontVariantNumeric: 'tabular-nums' }

const KPIS_RECEBER = [
  { rkey: 'vencidas', aba: 'vencidas', curto: 'Vencidas', titulo: 'Vencidas (inadimplentes)', cor: '#f87171' },
  { rkey: 'a_vencer', aba: 'a_vencer', curto: 'A vencer', titulo: 'A vencer', cor: '#3b82f6' },
  { rkey: 'pagas_mes', aba: 'pagas', curto: 'Pagas', titulo: 'Pagas no mês', cor: '#34d399' },
]
const COLS_RECEBER = [
  { h: 'Cliente', cell: (x) => x.cliente, tdStyle: () => ({ fontWeight: 600, ...truncar }) },
  { h: 'Descrição', cell: (x) => x.descricao || '—', tdStyle: () => ({ color: 'var(--muted)', ...truncar }) },
  { h: 'Vencimento', align: 'center', cell: (x) => dataBR(x.vencimento), tdStyle: (x, aba) => ({ ...num, color: aba === 'vencidas' ? '#f87171' : 'var(--text)' }) },
  { h: 'Valor', align: 'right', cell: (x) => BRL(x.valor), tdStyle: () => ({ fontWeight: 700, ...num }) },
  { h: 'Boleto/PIX', align: 'center', cell: (x) => x.link ? <a href={x.link} target="_blank" rel="noreferrer" style={{ color: 'var(--gold-2)', textDecoration: 'underline', fontSize: 12.5 }}>abrir</a> : '—' },
]

const KPIS_PAGAR = [
  { rkey: 'vencidas', aba: 'vencidas', curto: 'Vencidas', titulo: 'Vencidas (a pagar)', cor: '#f87171' },
  { rkey: 'a_vencer', aba: 'a_vencer', curto: 'A vencer', titulo: 'A vencer', cor: '#3b82f6' },
  { rkey: 'pagas', aba: 'pagas', curto: 'Pagas', titulo: 'Pagas (período)', cor: '#34d399' },
]
const COLS_PAGAR = [
  { h: 'Descrição', cell: (x) => x.descricao, tdStyle: () => ({ fontWeight: 600, ...truncar }) },
  { h: 'Categoria', cell: (x) => x.categoria || '—', tdStyle: () => ({ color: 'var(--muted)', ...truncar }) },
  { h: 'Vencimento', align: 'center', cell: (x) => dataBR(x.vencimento), tdStyle: (x, aba) => ({ ...num, color: aba === 'vencidas' ? '#f87171' : 'var(--text)' }) },
  { h: 'Valor', align: 'right', cell: (x) => BRL(x.valor), tdStyle: () => ({ fontWeight: 700, ...num }) },
  { h: 'Banco', align: 'center', cell: (x) => <span style={{ fontSize: 12, color: 'var(--muted)' }}>{(x.banco || '—').split(' - ')[0]}</span> },
]

const KPIS_RECEBER_ADV = [
  { rkey: 'vencidas', aba: 'vencidas', curto: 'Vencidas', titulo: 'Vencidas (a receber)', cor: '#f87171' },
  { rkey: 'a_vencer', aba: 'a_vencer', curto: 'A vencer', titulo: 'A vencer', cor: '#3b82f6' },
  { rkey: 'pagas', aba: 'pagas', curto: 'Recebidas', titulo: 'Recebidas (período)', cor: '#34d399' },
]
const COLS_RECEBER_ADV = [
  { h: 'Cliente', cell: (x) => x.cliente || '—', tdStyle: () => ({ fontWeight: 600, ...truncar }) },
  { h: 'Descrição', cell: (x) => x.descricao || '—', tdStyle: () => ({ color: 'var(--muted)', ...truncar }) },
  { h: 'Vencimento', align: 'center', cell: (x) => dataBR(x.vencimento), tdStyle: (x, aba) => ({ ...num, color: aba === 'vencidas' ? '#f87171' : 'var(--text)' }) },
  { h: 'Valor', align: 'right', cell: (x) => BRL(x.valor), tdStyle: () => ({ fontWeight: 700, ...num }) },
  { h: 'Origem', align: 'center', cell: (x) => <span style={{ fontSize: 12, color: 'var(--muted)' }}>{(x.banco || '—').split(' - ')[0]}</span> },
]

export default function Financeiro() {
  const [tab, setTab] = useState('cobrancas')
  async function enviarBoleto(id) {
    await api('/api/financeiro/enviar-boleto', { method: 'POST', body: { payment_id: id } })
  }
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 800 }}>Financeiro</h1>
        <span className="muted" style={{ fontSize: 13 }}>Asaas · ADVBOX</span>
      </div>

      <div style={{ display: 'flex', gap: 6, marginTop: 16, flexWrap: 'wrap', borderBottom: '1px solid var(--border)' }}>
        {TABS.map((t) => (
          <button key={t.k} onClick={() => t.pronto && setTab(t.k)}
            style={{ border: 'none', background: 'transparent', cursor: t.pronto ? 'pointer' : 'default', padding: '10px 14px', fontSize: 14, fontWeight: 700,
              color: tab === t.k ? 'var(--gold-2)' : 'var(--muted)', borderBottom: tab === t.k ? '2px solid var(--gold)' : '2px solid transparent', marginBottom: -1, opacity: t.pronto ? 1 : 0.55 }}>
            {t.label}{!t.pronto && <span style={{ fontSize: 10, marginLeft: 5 }}>• em breve</span>}
          </button>
        ))}
      </div>

      <div style={{ marginTop: 20 }}>
        {tab === 'cobrancas' && <Painel endpoint="/api/financeiro/cobrancas" kpis={KPIS_RECEBER} colunas={COLS_RECEBER} enviarBoleto={enviarBoleto} />}
        {tab === 'receber' && <Painel endpoint="/api/financeiro/contas-receber" kpis={KPIS_RECEBER_ADV} colunas={COLS_RECEBER_ADV} />}
        {tab === 'pagar' && <Painel endpoint="/api/financeiro/contas-pagar" kpis={KPIS_PAGAR} colunas={COLS_PAGAR} />}
        {(tab === 'fechamento' || tab === 'comissoes') && (
          <div className="panel" style={{ textAlign: 'center', padding: 40, color: 'var(--muted)' }}>
            <div style={{ fontSize: 15, fontWeight: 700 }}>Em construção</div>
            <div style={{ fontSize: 13, marginTop: 6 }}>Essa aba entra na próxima etapa.</div>
          </div>
        )}
      </div>
    </div>
  )
}

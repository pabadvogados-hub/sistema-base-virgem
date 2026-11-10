import { useState } from 'react'
import GerarPeca from './GerarPeca'

// Operacional agrupa as ferramentas operacionais. Por enquanto: Gerar Peça.
const SECOES = [
  { id: 'peca', nome: 'Gerar Peça' },
  { id: 'tarefas', nome: 'Tarefas / Prazos', embreve: true },
  { id: 'processos', nome: 'Processos', embreve: true },
]

export default function Operacional() {
  const [sec, setSec] = useState('peca')
  return (
    <div>
      <div style={{ display: 'inline-flex', background: '#eef2fa', borderRadius: 10, padding: 4, marginBottom: 18, flexWrap: 'wrap' }}>
        {SECOES.map((s) => (
          <button key={s.id} onClick={() => !s.embreve && setSec(s.id)}
            title={s.embreve ? 'Em breve' : ''}
            style={{ border: 'none', borderRadius: 8, padding: '8px 16px', fontWeight: 600, fontSize: 14,
              cursor: s.embreve ? 'not-allowed' : 'pointer', opacity: s.embreve ? 0.5 : 1,
              background: sec === s.id ? '#fff' : 'transparent', color: sec === s.id ? 'var(--navy-800)' : 'var(--muted)',
              boxShadow: sec === s.id ? 'var(--shadow)' : 'none' }}>
            {s.nome}{s.embreve ? ' · em breve' : ''}
          </button>
        ))}
      </div>
      {sec === 'peca' && <GerarPeca />}
    </div>
  )
}

import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'
import { MODULOS, podeAcessar } from '../modules'

export default function Dashboard() {
  const { user } = useAuth()
  const nav = useNavigate()
  const mods = MODULOS.filter((m) => podeAcessar(user, m))
  const primeiroNome = (user?.nome || '').split(' ')[0]

  return (
    <>
      <div style={{ marginBottom: 28 }}>
        <h2 style={{ fontSize: 24, fontWeight: 800 }}>Olá, {primeiroNome} 👋</h2>
        <p className="muted" style={{ marginTop: 6 }}>Escolha um módulo para começar.</p>
      </div>

      <div className="grid grid-mods">
        {mods.map((m) => (
          <div
            key={m.id}
            className={`mod-card ${!m.pronto ? 'locked' : ''}`}
            onClick={() => m.pronto && nav(m.rota)}
            style={{ cursor: m.pronto ? 'pointer' : 'default' }}
          >
            <h3>{m.nome}</h3>
            <p>{m.desc}</p>
            {!m.pronto && <span className="tag-lock">Em breve</span>}
            {m.acesso === 'financeiro' && <span className="tag-lock">Acesso restrito</span>}
          </div>
        ))}
      </div>
    </>
  )
}

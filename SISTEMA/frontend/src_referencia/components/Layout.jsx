import { useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { useAuth } from '../auth'
import { MODULOS, podeAcessar } from '../modules'
import Softphone from './Softphone'

// Ícones de linha (estilo ADONE) por rota
const ICONES = {
  '/': <><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><path d="M9 22V12h6v10" /></>,
  '/chat': <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />,
  '/crm': <><rect x="3" y="4" width="18" height="16" rx="1" /><line x1="9" y1="4" x2="9" y2="20" /><line x1="15" y1="4" x2="15" y2="20" /></>,
  '/intake': <><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" /><rect x="8" y="2" width="8" height="4" rx="1" /></>,
  '/operacional': <><rect x="2" y="7" width="20" height="14" rx="2" /><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" /></>,
  '/processos': <><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" /></>,
  '/assinatura': <><path d="M12 20h9" /><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z" /></>,
  '/marketing': <><path d="M11 5L6 9H2v6h4l5 4V5z" /><path d="M15.5 8.5a5 5 0 0 1 0 7" /></>,
  '/financeiro': <><line x1="12" y1="1" x2="12" y2="23" /><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" /></>,
  '/usuarios': <><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></>,
}

function Icone({ rota }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
      {ICONES[rota] || ICONES['/']}
    </svg>
  )
}

const IcoGear = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </svg>
)
const IcoSair = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" y1="12" x2="9" y2="12" />
  </svg>
)

// Selo institucional prateado (balança da justiça) — rodapé da sidebar
function Selo() {
  return (
    <div className="selo" title="Escritório Modelo Advogados — Sistema Jurídico">
      <svg viewBox="0 0 100 100" role="img" aria-label="Selo Escritório Modelo Advogados">
        <defs>
          <linearGradient id="prata" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#f6f9ff" />
            <stop offset=".5" stopColor="#c3cdde" />
            <stop offset="1" stopColor="#8791a6" />
          </linearGradient>
          <path id="seloTopo" d="M 13,50 A 37,37 0 0 1 87,50" />
          <path id="seloBase" d="M 13,50 A 37,37 0 0 0 87,50" />
        </defs>
        <circle cx="50" cy="50" r="47" fill="none" stroke="url(#prata)" strokeWidth="1.4" />
        <circle cx="50" cy="50" r="40.5" fill="none" stroke="url(#prata)" strokeWidth=".7" opacity=".55" />
        <text fill="url(#prata)" fontSize="8" fontWeight="700" letterSpacing="1.7" fontFamily="Montserrat, sans-serif">
          <textPath href="#seloTopo" startOffset="50%" textAnchor="middle">ESCRITÓRIO MODELO ADVOGADOS</textPath>
        </text>
        <text fill="url(#prata)" fontSize="6.6" fontWeight="600" letterSpacing="2" fontFamily="Montserrat, sans-serif">
          <textPath href="#seloBase" startOffset="50%" textAnchor="middle">SISTEMA JURÍDICO</textPath>
        </text>
        <circle cx="9.5" cy="50" r="1.2" fill="url(#prata)" />
        <circle cx="90.5" cy="50" r="1.2" fill="url(#prata)" />
        <g transform="translate(50,50)" stroke="url(#prata)" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round">
          <path d="M0,-14 V12" />
          <path d="M-12,-10 H12" />
          <circle cx="0" cy="-14" r="1.8" fill="url(#prata)" stroke="none" />
          <path d="M-12,-10 V-6" /><path d="M12,-10 V-6" />
          <path d="M-16,-6 a4,4 0 0 0 8,0" />
          <path d="M8,-6 a4,4 0 0 0 8,0" />
          <path d="M-7,12 H7" />
        </g>
      </svg>
      <span className="selo-cap">Sistema Jurídico Escritório Modelo</span>
    </div>
  )
}

export default function Layout({ children }) {
  const { user, logout } = useAuth()
  const [open, setOpen] = useState(false)
  const [colapsado, setColapsado] = useState(() => localStorage.getItem('esc_sb') === '1')
  function toggleColapso() { setColapsado((v) => { localStorage.setItem('esc_sb', v ? '0' : '1'); return !v }) }
  const loc = useLocation()
  const mods = MODULOS.filter((m) => podeAcessar(user, m))
  const titulo = MODULOS.find((m) => m.rota === loc.pathname)?.nome || 'Central'
  const hoje = new Date().toLocaleDateString('pt-BR', { weekday: 'long', day: '2-digit', month: 'long', year: 'numeric' })
  // Chat e CRM usam a tela toda (sem o limite de 1100px) — igual ao ADONE
  const larguraTotal = ['/chat', '/crm'].includes(loc.pathname)

  return (
    <div className="shell">
      <aside className={`sidebar ${open ? 'open' : ''} ${colapsado ? 'collapsed' : ''}`}>
        <button className="sidebar-toggle" onClick={toggleColapso} title={colapsado ? 'Expandir menu' : 'Recolher menu'}>{colapsado ? '»' : '«'}</button>
        <div className="ws">
          <img src="/logo-esc-simbolo.png" alt="Escritório Modelo" className="ws-mark" />
          {!colapsado && (
            <div className="ws-info">
              <b>Escritório Modelo Advogados</b>
              <span>{user?.papel === 'admin' ? 'Administrador' : user?.papel === 'advogado' ? 'Advogado(a)' : 'Equipe'}</span>
            </div>
          )}
        </div>

        <NavLink to="/" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`} onClick={() => setOpen(false)} title="Início">
          <span className="ico"><Icone rota="/" /></span>
          {!colapsado && 'Início'}
        </NavLink>

        <div className="nav-sep">Módulos</div>
        {mods.filter((m) => m.id !== 'usuarios').map((m) => (
          <NavLink key={m.id} to={m.rota} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`} onClick={() => setOpen(false)} title={m.nome}>
            <span className="ico"><Icone rota={m.rota} /></span>
            {!colapsado && m.nome}
          </NavLink>
        ))}

        {user?.papel === 'admin' && (
          <>
            <div className="nav-sep">Administração</div>
            <NavLink to="/usuarios" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`} onClick={() => setOpen(false)} title="Usuários">
              <span className="ico"><Icone rota="/usuarios" /></span>
              {!colapsado && 'Usuários'}
            </NavLink>
          </>
        )}

        <div className="side-user">
          <div className="su-av">{(user?.nome || '?').trim().charAt(0).toUpperCase()}</div>
          {!colapsado && (
            <div className="su-info">
              <b>{user?.nome}</b>
              <span>{user?.email}</span>
            </div>
          )}
          <div className="su-actions">
            {!colapsado && user?.papel === 'admin' && (
              <NavLink to="/usuarios" className="su-out" title="Administração"><IcoGear /></NavLink>
            )}
            <button className="su-out" onClick={logout} title="Sair"><IcoSair /></button>
          </div>
        </div>

        <Selo />
      </aside>

      <Softphone />
      <div className="main">
        <div className="topbar">
          <div className="tb-left">
            <h1>{titulo}</h1>
            <span className="tb-sub">{hoje}</span>
          </div>
          <span className="badge">{user?.papel === 'admin' ? 'Administrador' : user?.papel === 'advogado' ? 'Advogado(a)' : 'Equipe'}</span>
        </div>
        <div className="content" style={larguraTotal ? { maxWidth: 'none', padding: '16px 24px', height: 'calc(100vh - 61px)', display: 'flex', flexDirection: 'column', overflow: 'hidden' } : undefined}>{children}</div>
      </div>
    </div>
  )
}

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'
import CarouselHero from '../components/CarouselHero'

export default function Login() {
  const { login, user } = useAuth()
  const nav = useNavigate()
  const [email, setEmail] = useState('')
  const [senha, setSenha] = useState('')
  const [erro, setErro] = useState('')
  const [carregando, setCarregando] = useState(false)

  if (user) { nav('/'); return null }

  async function submit(e) {
    e.preventDefault()
    setErro(''); setCarregando(true)
    try {
      await login(email, senha)
      nav('/')
    } catch (err) {
      setErro(err.message || 'Não foi possível entrar')
    } finally {
      setCarregando(false)
    }
  }

  return (
    <div className="login-wrap">
      <div className="login-hero">
        <img src="/logo-esc-branco.png" alt="Escritório Modelo Advogados " className="hero-logo-top" />
        <CarouselHero />
      </div>

      <div className="login-form-side">
        <form className="login-card" onSubmit={submit}>
          <img src="/logo-esc-simbolo.png" alt="Escritório Modelo" className="form-logo" />
          <h2>Bem-vindo de volta</h2>
          <p className="sub">Entre com seu e-mail e senha do escritório.</p>

          {erro && <div className="alert alert-error">{erro}</div>}

          <div className="field">
            <label>E-mail</label>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="seu e-mail de acesso" required autoFocus />
          </div>
          <div className="field">
            <label>Senha</label>
            <input type="password" value={senha} onChange={(e) => setSenha(e.target.value)} placeholder="••••••••" required />
          </div>

          <button className="btn btn-block" disabled={carregando}>
            {carregando ? <span className="spin" /> : 'Entrar'}
          </button>
        </form>
      </div>
    </div>
  )
}

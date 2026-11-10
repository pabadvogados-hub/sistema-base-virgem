import { useEffect, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'

const vazio = { nome: '', email: '', senha: '', papel: 'usuario', acesso_financeiro: false }

export default function Usuarios() {
  const { user: eu } = useAuth()
  const [lista, setLista] = useState([])
  const [form, setForm] = useState(vazio)
  const [erro, setErro] = useState('')
  const [ok, setOk] = useState('')
  const [salvando, setSalvando] = useState(false)

  useEffect(() => { carregar() }, [])

  async function carregar() {
    try { setLista((await api('/api/auth/usuarios')).items) } catch (e) { setErro(e.message) }
  }

  async function criar(e) {
    e.preventDefault()
    setErro(''); setOk(''); setSalvando(true)
    try {
      await api('/api/auth/usuarios', { method: 'POST', body: form })
      setOk(`Usuário ${form.nome} criado.`)
      setForm(vazio)
      carregar()
    } catch (err) { setErro(err.message) } finally { setSalvando(false) }
  }

  async function alternar(u, campo) {
    try {
      await api(`/api/auth/usuarios/${u.id}`, { method: 'PATCH', body: { [campo]: !u[campo] } })
      carregar()
    } catch (e) { setErro(e.message) }
  }

  async function mudarPapel(u, papel) {
    try {
      await api(`/api/auth/usuarios/${u.id}`, { method: 'PATCH', body: { papel } })
      carregar()
    } catch (e) { setErro(e.message) }
  }

  async function resetar(u) {
    const nova = prompt(`Nova senha para ${u.nome}:`)
    if (!nova) return
    try {
      await api(`/api/auth/usuarios/${u.id}/reset-senha`, { method: 'POST', body: { nova_senha: nova } })
      setOk(`Senha de ${u.nome} redefinida.`)
    } catch (e) { setErro(e.message) }
  }

  return (
    <div className="row" style={{ alignItems: 'flex-start' }}>
      <div style={{ flex: 2, minWidth: 340 }}>
        <div className="panel" style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{ padding: '20px 24px' }}>
            <h2>Equipe</h2>
            <p className="p-sub">Papéis e acesso ao financeiro.</p>
          </div>
          {erro && <div className="alert alert-error" style={{ margin: '0 24px 16px' }}>{erro}</div>}
          {ok && <div className="alert alert-ok" style={{ margin: '0 24px 16px' }}>{ok}</div>}
          <table>
            <thead>
              <tr><th>Nome</th><th>Papel</th><th>Financeiro</th><th>Ativo</th><th></th></tr>
            </thead>
            <tbody>
              {lista.map((u) => {
                const souEu = eu && u.id === eu.id
                return (
                <tr key={u.id}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{u.nome}{souEu && <span className="muted" style={{ fontWeight: 400 }}> (você)</span>}</div>
                    <div className="muted" style={{ fontSize: 12 }}>{u.email}</div>
                  </td>
                  <td>
                    <select value={u.papel} onChange={(e) => mudarPapel(u, e.target.value)} disabled={souEu} style={{ padding: '6px 8px', borderRadius: 8, border: '1.5px solid var(--border)', fontSize: 13 }}>
                      <option value="usuario">Equipe</option>
                      <option value="advogado">Advogado(a)</option>
                      <option value="admin">Admin</option>
                    </select>
                  </td>
                  <td>
                    <button className={`btn btn-sm ${u.acesso_financeiro ? 'btn-gold' : 'btn-ghost'}`} onClick={() => alternar(u, 'acesso_financeiro')} disabled={u.papel === 'admin'}>
                      {u.acesso_financeiro ? '✓ Sim' : 'Não'}
                    </button>
                  </td>
                  <td>
                    <button className={`btn btn-sm ${u.ativo ? 'btn-ghost' : 'btn-danger'}`} onClick={() => alternar(u, 'ativo')} disabled={souEu} title={souEu ? 'Você não pode desativar seu próprio usuário' : ''}>
                      {u.ativo ? 'Ativo' : 'Inativo'}
                    </button>
                  </td>
                  <td><button className="btn btn-ghost btn-sm" onClick={() => resetar(u)}>Resetar senha</button></td>
                </tr>
              )})}
            </tbody>
          </table>
        </div>
      </div>

      <div style={{ flex: 1, minWidth: 280 }}>
        <form className="panel" onSubmit={criar}>
          <h2>Novo usuário</h2>
          <p className="p-sub">Cadastre um membro da equipe.</p>
          <div className="field">
            <label>Nome completo</label>
            <input value={form.nome} onChange={(e) => setForm({ ...form, nome: e.target.value })} required />
          </div>
          <div className="field">
            <label>E-mail</label>
            <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
          </div>
          <div className="field">
            <label>Senha provisória</label>
            <input type="text" value={form.senha} onChange={(e) => setForm({ ...form, senha: e.target.value })} placeholder="mín. 6 caracteres" required />
          </div>
          <div className="field">
            <label>Papel</label>
            <select value={form.papel} onChange={(e) => setForm({ ...form, papel: e.target.value })}>
              <option value="usuario">Equipe</option>
              <option value="advogado">Advogado(a)</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          <div className="field" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <input type="checkbox" id="fin" checked={form.acesso_financeiro} onChange={(e) => setForm({ ...form, acesso_financeiro: e.target.checked })} style={{ width: 'auto' }} />
            <label htmlFor="fin" style={{ margin: 0 }}>Acesso ao Financeiro / Asaas</label>
          </div>
          <button className="btn btn-block" disabled={salvando}>{salvando ? 'Salvando…' : 'Criar usuário'}</button>
        </form>
      </div>
    </div>
  )
}

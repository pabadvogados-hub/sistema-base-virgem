// Registro central de modulos do sistema.
// `acesso`: 'todos' | 'financeiro' | 'admin'
// `pronto`: se ja esta implementado (senao mostra "Em breve")
export const MODULOS = [
  { id: 'chat', nome: 'Chat ao Vivo', desc: 'Atendimento de WhatsApp ao vivo: a IA atendem, você assume quando quiser.', ico: '💬', rota: '/chat', acesso: 'todos', pronto: true },
  { id: 'crm', nome: 'CRM', desc: 'Funil de leads por pipeline (Reclamantes, Empresas, Palestra, Cobrança…).', ico: '🗂️', rota: '/crm', acesso: 'todos', pronto: true },
  { id: 'contatos', nome: 'Contatos', desc: 'Busque qualquer contato ou conversa por nome ou número — nada fica escondido.', ico: '🔎', rota: '/contatos', acesso: 'todos', pronto: true },
  { id: 'intake', nome: 'Intake / Contratação', desc: 'Ficha, contrato, procuração e declaração do novo cliente.', ico: '📋', rota: '/intake', acesso: 'todos', pronto: true },
  { id: 'operacional', nome: 'Operacional', desc: 'Geração de peças, tarefas, prazos e processos.', ico: '⚖️', rota: '/operacional', acesso: 'todos', pronto: true },
  { id: 'processos', nome: 'Processos', desc: 'Consulta de todos os processos do escritório (ADVBOX).', ico: '📁', rota: '/processos', acesso: 'todos', pronto: true },
  { id: 'controladoria', nome: 'Controladoria', desc: 'Prazos, intimações e auditoria — integrado ao SYNC.', ico: '🛡️', rota: '/controladoria', acesso: 'todos', pronto: true },
  { id: 'assinatura', nome: 'Assinatura Digital', desc: 'Envio e acompanhamento de documentos no ZapSign.', ico: '✍️', rota: '/assinatura', acesso: 'todos', pronto: false },
  { id: 'marketing', nome: 'Marketing', desc: 'Dashboard ao vivo das campanhas de tráfego (Meta Ads).', ico: '📊', rota: '/marketing', acesso: 'todos', pronto: true },
  { id: 'financeiro', nome: 'Financeiro / Asaas', desc: 'Cobranças, conciliação e fechamento. Acesso restrito.', ico: '💰', rota: '/financeiro', acesso: 'financeiro', pronto: true },
  { id: 'usuarios', nome: 'Usuários', desc: 'Cadastro de equipe, papéis e permissões.', ico: '👥', rota: '/usuarios', acesso: 'admin', pronto: true },
]

export function podeAcessar(user, mod) {
  if (!user) return false
  if (user.papel === 'admin') return true
  if (mod.acesso === 'todos') return true
  if (mod.acesso === 'financeiro') return !!user.acesso_financeiro
  if (mod.acesso === 'admin') return user.papel === 'admin'
  return false
}

/* Configuracao do escritorio para a tela (carregada antes de tudo no index.html).
   Edite aqui quem e admin e quem e da equipe comercial; nada disso fica fixo no codigo.
   - admins: e-mails de login de quem ve tudo (CRM de todos, video gravado em teste etc.)
   - vendedores: equipe comercial. "nome" aparece nas metas e comissoes, "apelido" na saudacao,
     "email" e o login no sistema, "ramal" e o usuario do ramal na Telefonia (se houver). */
window.ESC_CONFIG = {
  escritorio: "Escritório Modelo Advogados",
  cidade: "Cidade/UF",
  admins: [],
  vendedores: [
    /* { nome: "Nome do vendedor", apelido: "Apelido", email: "login@dominio.com.br", ramal: "usuario_do_ramal" } */
  ],
  ramais_admin: ["titular"],
  video_gravado_teste: []   /* vazio = vale para os admins */
};

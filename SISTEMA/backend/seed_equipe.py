"""Seed OPCIONAL da equipe inicial do escritorio (Dr. Titular e Dra. Advogada).

Nenhuma senha fica no codigo: cada uma e gerada na hora e impressa UMA vez.
Os e-mails de login vem do config/.env (EQUIPE_EMAIL_TITULAR / EQUIPE_EMAIL_ADVOGADA)
ou dos argumentos:

    python seed_equipe.py
    python seed_equipe.py titular@dominio advogada@dominio
"""
import asyncio
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv  # noqa: E402

from app.config import ENV_CENTRAL, ENV_LOCAL  # noqa: E402
from seed_admin import criar_ou_atualizar  # noqa: E402

for _env in (ENV_CENTRAL, ENV_LOCAL):
    if _env.exists():
        load_dotenv(_env, override=False)


def main():
    email_titular = sys.argv[1] if len(sys.argv) > 1 else os.getenv('EQUIPE_EMAIL_TITULAR', '')
    email_vit = sys.argv[2] if len(sys.argv) > 2 else os.getenv('EQUIPE_EMAIL_ADVOGADA', '')
    if not email_titular or not email_vit:
        print('Informe os e-mails (argumentos ou EQUIPE_EMAIL_TITULAR/EQUIPE_EMAIL_ADVOGADA no config/.env).')
        sys.exit(1)
    equipe = [
        ('Titular do Escritório', email_titular, 'admin', True),
        ('Advogada Responsável', email_vit, 'advogado', True),
        # usuario da automacao: o motor de tarefas assina como ele (app/juridico/equipe.json, id 3).
        # Rode este seed com o banco VAZIO para os ids sairem 1 = Titular, 2 = Advogada, 3 = automacao.
        ('Assistente IA (automacao)', os.getenv('EQUIPE_EMAIL_AUTOMACAO', 'automacao@sistema.local'), 'usuario', False),
    ]
    print('Guarde as senhas abaixo. Elas NAO ficam salvas em lugar nenhum.\n')
    for nome, email, papel, fin in equipe:
        senha = secrets.token_urlsafe(12)
        acao = asyncio.run(criar_ou_atualizar(nome, email, senha, papel=papel, financeiro=fin))
        print(f'{acao:10} {papel:9} {email:40} senha: {senha}')


if __name__ == '__main__':
    main()

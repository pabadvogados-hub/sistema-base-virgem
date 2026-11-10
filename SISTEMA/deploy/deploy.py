#!/usr/bin/env python
"""Deploy do Sistema Escritório Modelo Advogados para a VPS do escritorio, SEM apagar trabalho de ninguem.

A VPS e a copia OFICIAL. Quem ajusta o sistema direto la deixa o computador de quem roda
este script atras. Por isso o deploy NUNCA empacota a pasta app/ inteira: sobe so o que voce
mudou, e trava se a VPS tiver mudado no mesmo arquivo.

Uso:
  python SISTEMA/deploy/deploy.py --status    # compara seu computador x VPS (nao muda nada)
  python SISTEMA/deploy/deploy.py --puxar     # traz a versao da VPS pro seu computador (com backup)
  python SISTEMA/deploy/deploy.py --simular   # mostra o que o deploy mandaria, sem mandar
  python SISTEMA/deploy/deploy.py             # backend: manda SO o que voce mudou + reinicia
  python SISTEMA/deploy/deploy.py --restart   # so reinicia o servico

Fluxo certo: --puxar  ->  faz a sua mudanca  ->  --simular  ->  deploy.

Trava do backend:
  - sem --puxar nesta maquina, o deploy nao roda;
  - arquivo que mudou na VPS depois do seu --puxar e voce tambem mudou = CONFLITO: o deploy
    inteiro para e lista quais (rode --puxar e refaca a mudanca em cima da versao nova);
  - sobe so os arquivos que voce alterou, nunca a pasta inteira, e nunca apaga nada na VPS;
  - antes de sobrescrever, guarda copia na VPS em backend/_deploy_backups/<data>/;
  - se o backend nao importar depois do envio, desfaz sozinho e aborta (producao intacta).

Frontend (--frontend / --all) fica BLOQUEADO aqui: a tela no ar e o bundle de SISTEMA/frontend/dist
mais os blocos .js separados. Mudanca de tela entra como bloco novo e separado, conferido antes.

Tudo vem do config/.env:
  VPS_HOST, VPS_USER (padrao root), VPS_PORT (22), VPS_SSH_KEY ou VPS_PASS,
  VPS_DIR (padrao /opt/escritorio), VPS_SERVICO (padrao esc-sistema.service),
  ESCRITORIO_DOMINIO (so para mostrar o link no fim).
"""
import hashlib
import io
import json
import os
import posixpath
import shlex
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]            # raiz do projeto
BACKEND = RAIZ / 'SISTEMA' / 'backend'
BACKEND_APP = BACKEND / 'app'
DEPLOY_DIR = RAIZ / 'SISTEMA' / 'deploy'
BASE_ARQ = DEPLOY_DIR / '.base_vps.json'              # "foto" da VPS no ultimo --puxar (por maquina)
BACKUP_LOCAL = DEPLOY_DIR / '_backup_local'

def _env_cedo() -> dict:
    envp = RAIZ / 'config' / '.env'
    d = {}
    if envp.exists():
        for ln in envp.read_text(encoding='utf-8', errors='replace').splitlines():
            ln = ln.strip()
            if ln and not ln.startswith('#') and '=' in ln:
                k, v = ln.split('=', 1)
                d[k.strip()] = v.strip().strip('"').strip("'")
    return d


_ENV = _env_cedo()
REMOTO_RAIZ = _ENV.get('VPS_DIR', '/opt/escritorio').rstrip('/')
REMOTO_BACKEND = REMOTO_RAIZ + '/SISTEMA/backend'
REMOTO_BACKUPS = REMOTO_BACKEND + '/_deploy_backups'
REMOTO_PY = REMOTO_RAIZ + '/venv/bin/python'
SERVICO = _ENV.get('VPS_SERVICO', 'esc-sistema.service')
DOMINIO = _ENV.get('ESCRITORIO_DOMINIO', '')

# extensoes comparadas ignorando CRLF x LF (Windows x Linux), senao tudo parece diferente
TEXTO = ('.py', '.txt', '.json', '.md', '.html', '.js', '.css', '.sql', '.cfg', '.ini', '.yaml', '.yml', '.toml')

MSG_FRONT = """BLOQUEADO: deploy de frontend desligado neste script.

A tela no ar e o bundle de SISTEMA/frontend/dist com os blocos .js separados. Um build local
novo apagaria os ajustes feitos direto no bundle. Mudanca de tela: bloco novo e separado,
enviado a mao com backup do arquivo antigo na VPS."""


# ---------------------------------------------------------------------------
# conexao
# ---------------------------------------------------------------------------
def carregar_env() -> dict:
    envp = RAIZ / 'config' / '.env'
    dados = {}
    if envp.exists():
        for ln in envp.read_text(encoding='utf-8', errors='replace').splitlines():
            ln = ln.strip()
            if ln and not ln.startswith('#') and '=' in ln:
                k, v = ln.split('=', 1)
                dados[k.strip()] = v.strip().strip('"').strip("'")
    return dados


def conectar(env):
    import paramiko
    host = env.get('VPS_HOST')
    pw = env.get('VPS_PASS')
    chave = env.get('VPS_SSH_KEY')
    user, port = env.get('VPS_USER', 'root'), int(env.get('VPS_PORT', '22'))
    if not host or not (pw or chave):
        sys.exit('ERRO: defina VPS_HOST e VPS_PASS ou VPS_SSH_KEY no config/.env (ver docs/ONBOARDING.md).')
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    # A VPS aceita so publickey (senha desativada) — tenta a chave primeiro,
    # cai pra senha se a chave nao existir ou a VPS mudar de politica.
    if chave and os.path.exists(chave):
        try:
            cli.connect(host, port=port, username=user, key_filename=chave, timeout=30,
                        banner_timeout=30, auth_timeout=30)
            return cli
        except Exception as e:
            if not pw:
                sys.exit(f'ERRO: falha com VPS_SSH_KEY e nao ha VPS_PASS como fallback:\n{e}')
    cli.connect(host, port=port, username=user, password=pw, timeout=30,
                banner_timeout=30, auth_timeout=30)
    return cli


def run(cli, cmd, timeout=300):
    _in, out, err = cli.exec_command(cmd, timeout=timeout)
    o = out.read().decode('utf-8', 'replace')
    e = err.read().decode('utf-8', 'replace')
    return out.channel.recv_exit_status(), o, e


# ---------------------------------------------------------------------------
# comparacao computador x VPS
# ---------------------------------------------------------------------------
def ignorar(rel: str) -> bool:
    partes = rel.split('/')
    nome = partes[-1]
    return ('__pycache__' in partes or nome.endswith('.pyc') or '.bak' in nome
            or nome.endswith('~') or nome.startswith('.#'))


def normalizar(rel: str, dados: bytes) -> bytes:
    if rel.lower().endswith(TEXTO):
        return dados.replace(b'\r\n', b'\n')
    return dados


def hashes_locais() -> dict:
    out = {}
    if not BACKEND_APP.exists():
        return out
    for p in BACKEND_APP.rglob('*'):
        if p.is_file():
            rel = 'app/' + p.relative_to(BACKEND_APP).as_posix()
            if not ignorar(rel):
                out[rel] = hashlib.md5(normalizar(rel, p.read_bytes())).hexdigest()
    return out


_HASH_REMOTO = r'''
import hashlib, json, os, sys
raiz, texto = sys.argv[1], tuple(sys.argv[2].split(','))
out = {}
for d, subdirs, arqs in os.walk(os.path.join(raiz, 'app')):
    subdirs[:] = [s for s in subdirs if s != '__pycache__']
    for a in arqs:
        if a.endswith('.pyc') or '.bak' in a or a.endswith('~') or a.startswith('.#'):
            continue
        rel = os.path.relpath(os.path.join(d, a), raiz).replace(os.sep, '/')
        b = open(os.path.join(raiz, rel), 'rb').read()
        if a.lower().endswith(texto):
            b = b.replace(b'\r\n', b'\n')
        out[rel] = hashlib.md5(b).hexdigest()
print(json.dumps(out))
'''


def hashes_remotos(cli) -> dict:
    sftp = cli.open_sftp()
    sftp.putfo(io.BytesIO(_HASH_REMOTO.encode('utf-8')), '/tmp/esc_deploy_hash.py')
    sftp.close()
    rc, o, e = run(cli, f"{REMOTO_PY} /tmp/esc_deploy_hash.py {REMOTO_BACKEND} {','.join(TEXTO)}")
    if rc != 0:
        sys.exit(f'ERRO ao ler os arquivos da VPS:\n{o}{e}')
    return json.loads(o.strip().splitlines()[-1])


def carregar_base() -> dict:
    try:
        return json.loads(BASE_ARQ.read_text(encoding='utf-8')).get('arquivos', {})
    except Exception:
        return {}


def salvar_base(arquivos: dict):
    BASE_ARQ.write_text(json.dumps({'quando': time.strftime('%Y-%m-%d %H:%M:%S'), 'arquivos': arquivos},
                                   ensure_ascii=False, indent=1), encoding='utf-8')


def classificar(local: dict, remoto: dict, base: dict) -> dict:
    r = {'iguais': [], 'para_subir': [], 'novos_aqui': [], 'mudou_na_vps': [], 'conflitos': [], 'so_na_vps': []}
    for rel in sorted(set(local) | set(remoto)):
        loc, vps, bas = local.get(rel), remoto.get(rel), base.get(rel)
        if loc is not None and vps is not None:
            if loc == vps:
                r['iguais'].append(rel)
            elif bas is not None and vps == bas:
                r['para_subir'].append(rel)       # VPS igual ao ultimo --puxar: a mudanca e sua
            elif bas is not None and loc == bas:
                r['mudou_na_vps'].append(rel)     # voce nao mexeu; a VPS andou (--puxar traz)
            else:
                r['conflitos'].append(rel)        # os dois mudaram, ou nunca sincronizou
        elif loc is not None:
            if bas is None:
                r['novos_aqui'].append(rel)       # arquivo novo seu
            else:
                r['conflitos'].append(rel)        # existia na VPS no --puxar e sumiu de la
        else:
            r['so_na_vps'].append(rel)            # so existe na VPS: nunca e apagado
    return r


def _lista(titulo, itens, limite=25):
    if not itens:
        return
    print(f'\n{titulo} ({len(itens)}):')
    for rel in itens[:limite]:
        print('   ' + rel)
    if len(itens) > limite:
        print(f'   ... e mais {len(itens) - limite}')


# ---------------------------------------------------------------------------
# comandos
# ---------------------------------------------------------------------------
def status(cli):
    base = carregar_base()
    r = classificar(hashes_locais(), hashes_remotos(cli), base)
    print('==> Seu computador x VPS (backend/app)')
    if not base:
        print('\nATENCAO: esta maquina nunca rodou --puxar. Tudo que esta diferente conta como CONFLITO.')
    print(f"\niguais: {len(r['iguais'])}")
    _lista('VOCE mudou e a VPS nao mexeu (o deploy sobe)', r['para_subir'])
    _lista('arquivos novos seus (o deploy sobe)', r['novos_aqui'])
    _lista('a VPS mudou e voce nao (o deploy NAO mexe; --puxar traz)', r['mudou_na_vps'])
    _lista('so existem na VPS (nunca sao apagados)', r['so_na_vps'])
    _lista('CONFLITO: diferentes e sem como saber quem e mais novo (o deploy PARA)', r['conflitos'])


def puxar(cli):
    base = carregar_base()
    local, remoto = hashes_locais(), hashes_remotos(cli)
    ts = time.strftime('%Y%m%d_%H%M%S')
    sftp = cli.open_sftp()
    baixados, preservados, guardados, seus_sobrescritos = [], [], [], []
    try:
        for rel, vps in sorted(remoto.items()):
            loc, bas = local.get(rel), base.get(rel)
            if loc == vps:
                continue
            if loc is not None and bas is not None and loc != bas and vps == bas:
                preservados.append(rel)          # mudanca sua ainda nao publicada e a VPS nao mexeu: fica a sua
                continue
            destino = BACKEND / rel
            if destino.exists():
                bk = BACKUP_LOCAL / ts / rel
                bk.parent.mkdir(parents=True, exist_ok=True)
                bk.write_bytes(destino.read_bytes())
                guardados.append(rel)
                if bas is not None and loc != bas:
                    seus_sobrescritos.append(rel)
            destino.parent.mkdir(parents=True, exist_ok=True)
            with sftp.open(posixpath.join(REMOTO_BACKEND, rel), 'rb') as f:
                destino.write_bytes(f.read())
            baixados.append(rel)
    finally:
        sftp.close()
    salvar_base(remoto)
    so_aqui = sorted(rel for rel in local if rel not in remoto)
    print('==> Versao da VPS trazida para este computador')
    _lista('atualizados com a versao da VPS', baixados)
    _lista('suas mudancas nao publicadas, mantidas (a VPS nao mexeu nelas)', preservados)
    _lista('ATENCAO: voce tinha mudado e a VPS tambem; ficou a da VPS (a sua esta no backup)', seus_sobrescritos)
    _lista('so existem aqui (nao foram tocados)', so_aqui)
    if guardados:
        print(f'\nBackup do que havia aqui: {BACKUP_LOCAL / ts}')
    print('\nPronto. Agora faca a sua mudanca e rode --simular antes do deploy.')


def smoke_test(cli) -> bool:
    print('==> Smoke test: import app.main_vps na VPS ...')
    rc, o, e = run(cli, f"cd {REMOTO_BACKEND} && {REMOTO_PY} -c 'import app.main_vps' 2>&1")
    if rc != 0:
        print(f'    NAO importou:\n{o}{e}')
        return False
    print('    importou OK')
    return True


def reiniciar(cli):
    print('==> Reiniciando backend ...')
    rc, o, e = run(cli, f'systemctl restart {SERVICO}; sleep 3; systemctl is-active {SERVICO}')
    estado = (o or e).strip()
    print(f'    servico: {estado}')
    if 'active' not in estado:
        sys.exit('ERRO: servico nao voltou a ficar ativo! Verifique: journalctl -u ' + SERVICO)


def deploy_backend(cli, simular: bool) -> bool:
    base = carregar_base()
    if not base:
        sys.exit('TRAVA: esta maquina nunca rodou --puxar.\n'
                 'Rode primeiro:  python SISTEMA/deploy/deploy.py --puxar\n'
                 '(traz a versao da VPS, com backup do que voce tem aqui).')
    local, remoto = hashes_locais(), hashes_remotos(cli)
    r = classificar(local, remoto, base)
    if r['conflitos']:
        _lista('TRAVA — estes arquivos mudaram na VPS depois do seu ultimo --puxar e voce tambem mexeu', r['conflitos'])
        sys.exit('\nDeploy CANCELADO para nao apagar o trabalho de ninguem. Nada foi enviado.\n'
                 'Rode --puxar (sua versao vai para o backup) e refaca a mudanca em cima da versao da VPS.')
    enviar = r['para_subir'] + r['novos_aqui']
    if r['mudou_na_vps']:
        _lista('aviso: a VPS mudou nestes (nao serao tocados; --puxar traz)', r['mudou_na_vps'])
    if not enviar:
        print('\nNada para subir: nenhum arquivo seu diferente da VPS.')
        return False
    _lista('vai subir (alterados por voce)', r['para_subir'])
    _lista('vai subir (arquivos novos)', r['novos_aqui'])
    if simular:
        print('\n--simular: nada foi enviado.')
        return False

    ts = time.strftime('%Y%m%d_%H%M%S')
    dir_bk = f'{REMOTO_BACKUPS}/{ts}'
    print(f'\n==> Backup na VPS: {dir_bk}')
    for rel in r['para_subir']:
        alvo = shlex.quote(f'{dir_bk}/{rel}')
        rc, o, e = run(cli, f"mkdir -p $(dirname {alvo}) && cp -p {shlex.quote(REMOTO_BACKEND + '/' + rel)} {alvo}")
        if rc != 0:
            sys.exit(f'ERRO no backup de {rel} — nada foi enviado:\n{o}{e}')

    print('==> Enviando so os arquivos alterados ...')
    sftp = cli.open_sftp()
    try:
        for rel in enviar:
            remoto_p = f'{REMOTO_BACKEND}/{rel}'
            run(cli, f'mkdir -p {shlex.quote(posixpath.dirname(remoto_p))}')
            dados = normalizar(rel, (BACKEND / rel).read_bytes())
            sftp.putfo(io.BytesIO(dados), remoto_p)
            print('    ' + rel)
    finally:
        sftp.close()

    if not smoke_test(cli):
        print('==> Desfazendo o envio ...')
        for rel in r['para_subir']:
            run(cli, f"cp -p {shlex.quote(dir_bk + '/' + rel)} {shlex.quote(REMOTO_BACKEND + '/' + rel)}")
        for rel in r['novos_aqui']:
            run(cli, f"rm -f {shlex.quote(REMOTO_BACKEND + '/' + rel)}")
        sys.exit('Deploy desfeito: a VPS voltou a ficar como estava. Producao intacta.')

    for rel in enviar:
        base[rel] = local[rel]
    salvar_base(base)
    reiniciar(cli)
    return True


def main():
    args = set(sys.argv[1:])
    if '--frontend' in args or '--all' in args:
        sys.exit(MSG_FRONT)

    cli = conectar(carregar_env())
    try:
        if '--restart' in args:
            reiniciar(cli)
        elif '--status' in args:
            status(cli)
        elif '--puxar' in args:
            puxar(cli)
        elif deploy_backend(cli, simular='--simular' in args):
            print('\nPronto!' + (f' https://{DOMINIO}' if DOMINIO else ''))
    finally:
        cli.close()


if __name__ == '__main__':
    main()

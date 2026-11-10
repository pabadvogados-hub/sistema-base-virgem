#!/usr/bin/env python
"""Watcher: grava ligacao (MixMonitor .wav em /opt/escritorio/ligacoes_raw) -> converte pra
ogg (nome imprevisivel em /media) -> transcreve (Whisper) -> anexa na conversa do lead
no CRM. Cron 1/min. Audio bruto fica FORA da web (so o ogg transcrito e servido)."""
import os, re, glob, sqlite3, subprocess, secrets, sys, time
from datetime import datetime, timezone
sys.path.insert(0,'/opt/escritorio/SISTEMA/backend')
from app.whatsapp import transcricao
RAW='/opt/escritorio/ligacoes_raw'; DONE=RAW+'/done'; MEDIA='/opt/escritorio/media'
DB='/opt/escritorio/SISTEMA/backend/sistema_esc.db'; LOG='/var/log/escritorio-ligacoes.log'
def log(m):
    open(LOG,'a').write(f'{datetime.now().isoformat()[:19]} {m}\n')
def achar_conversa(con,num):
    cands=[num]+([num[2:]] if num.startswith('55') else [])
    for c in cands:
        r=con.execute('select id from wa_conversas where contato_telefone=?',(c,)).fetchone()
        if r: return r[0]
    # fallback mais estrito: numero nacional sem o 55 (DDD+numero), evita colisao
    if len(num)>=12:
        r=con.execute('select id from wa_conversas where contato_telefone like ? order by atualizado_em desc limit 1',('%'+num[-11:],)).fetchone()
        if r: return r[0]
    return None
def processar(path, base):
    m=re.match(r'^(\d+)__([^_]+)__.+\.wav$', base)
    num=m.group(1) if m else ''; ramal=m.group(2) if m else '?'
    if os.path.getsize(path) < 8000:
        os.rename(path, os.path.join(DONE,base+'.vazia')); log(f'{base}: vazia/nao atendida'); return
    token=secrets.token_hex(8); ogg=f'{MEDIA}/lig_{token}.ogg'
    subprocess.run(['/usr/bin/ffmpeg','-y','-i',path,'-ac','1','-c:a','libopus','-b:a','24k',ogg],capture_output=True,timeout=180)
    texto=''
    try:
        with open(ogg,'rb') as f: texto=(transcricao.transcrever_bytes(f.read()) or '').strip()
    except Exception as e: log(f'{base}: transcricao falhou: {str(e)[:80]}')
    con=sqlite3.connect(DB,timeout=30); con.execute('PRAGMA busy_timeout=30000')
    cid=achar_conversa(con,num) if num else None
    now=datetime.now(timezone.utc).isoformat(sep=' ')[:19]
    conteudo=('[Ligacao] '+texto) if texto else '[Ligacao - sem transcricao]'
    url=f'https://sistema.escritorio.adv.br/media/lig_{token}.ogg'
    if cid:
        con.execute('insert into wa_mensagens(conversa_id,origem,autor,tipo,conteudo,midia_url,criado_em,remote_id) values(?,?,?,?,?,?,?,?)',(cid,'ligacao',ramal,'audio',conteudo,url,now,''))
        con.execute('update wa_conversas set ultima_mensagem=?,atualizado_em=? where id=?',(conteudo[:300],now,cid)); con.commit()
        log(f'{base}: anexado cid {cid} ({len(texto)} chars)')
    else:
        log(f'{base}: conversa nao encontrada p/ {num}; transcricao: {texto[:120]}')
    con.close(); os.chmod(ogg,0o644); os.rename(path, os.path.join(DONE,base))
def main():
    os.makedirs(DONE,exist_ok=True)
    for wav in sorted(glob.glob(RAW+'/*.wav')):
        if time.time()-os.path.getmtime(wav) < 60: continue   # ligacao em andamento
        lock=wav+'.processing'
        try: os.rename(wav,lock)
        except OSError: continue
        try: processar(lock, os.path.basename(wav))
        except Exception as e:
            log(f'{os.path.basename(wav)}: ERRO {str(e)[:100]}')
            try: os.rename(lock, os.path.join(DONE,os.path.basename(wav)+'.erro'))
            except: pass
if __name__=='__main__': main()

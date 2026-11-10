"""Ponte de ligacao: WhatsApp (D-API, stream PCM 16 kHz) <-> agente de voz ElevenLabs.

Uso: python ligar_ia.py <telefone com DDI> <agent_id> [segundos_max] [nome]

- Liga pela linha DAPI_NUMERO_ID do SISTEMA (padrao wa_numeros id 1). Se a linha estiver em ligacao, desiste.
- So abre a conversa com a IA depois do "accepted" (cliente atendeu).
- Audio para o cliente sai em quadros de 20 ms (640 bytes) no ritmo real; silencio quando a IA nao fala.
- Transcricao em /opt/escritorio/voz_ia/logs/<callId>.txt
"""
import array
import asyncio
import base64
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

import requests
import websockets

DAPI = "https://api.d-api.cloud/api/v1"
DB = "/opt/escritorio/SISTEMA/backend/sistema_esc.db"
LOGS = "/opt/escritorio/voz_ia/logs"
FRAME = 640  # 20 ms @ 16 kHz s16le mono
PREBUF = FRAME * 15  # 300 ms
GANHO = 0.7  # ~-3 dB na voz da IA
UA = {"User-Agent": "Mozilla/5.0 esc-voz-ia"}


def credencial():
    con = sqlite3.connect(DB, timeout=30)
    nid = int(os.getenv("DAPI_NUMERO_ID") or 1)      # linha que liga (padrao: a linha de Reclamantes, id 1)
    inst, chave = con.execute("select zapi_instance, zapi_token from wa_numeros where id=?", (nid,)).fetchone()
    return inst.split(":", 1)[1], chave


def chave_eleven():
    return [l.split("=", 1)[1].strip() for l in open("/opt/escritorio/config/.env") if l.startswith("ELEVENLABS_API_KEY=")][0]


class Log:
    def __init__(self):
        os.makedirs(LOGS, exist_ok=True)
        self.f = None
        self.t0 = time.time()

    def abrir(self, call_id):
        self.f = open(os.path.join(LOGS, f"{call_id}.txt"), "a", encoding="utf-8")

    def __call__(self, quem, texto):
        linha = f"[{time.time() - self.t0:6.1f}s] {quem}: {texto}"
        print(linha, flush=True)
        if self.f:
            self.f.write(linha + "\n")
            self.f.flush()


async def main(tel, agent_id, maxdur, nome):
    log = Log()
    sess, chave = credencial()
    hd = {"Authorization": chave, "Content-Type": "application/json", **UA}
    est = requests.post(f"{DAPI}/calls/state", json={"sessionId": sess}, headers=hd, timeout=15).json()
    if (est.get("data") or {}).get("active"):
        log("SISTEMA", f"linha 1 em ligacao ({est['data'].get('state')}); desisto para nao atrapalhar a equipe")
        return
    r = requests.post(f"{DAPI}/calls/stream", json={"sessionId": sess, "phone": tel}, headers=hd, timeout=30)
    j = r.json()
    if not j.get("wsUrl"):
        log("SISTEMA", f"D-API recusou: {r.status_code} {str(j)[:300]}")
        return
    call_id = j["callId"]
    log.abrir(call_id)
    log("SISTEMA", f"{datetime.now():%d/%m/%Y %H:%M} ligando para +{tel} (callId {call_id}, sessao {sess})")
    signed = requests.get("https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
                          params={"agent_id": agent_id}, headers={"xi-api-key": chave_eleven()}, timeout=20).json()
    if not signed.get("signed_url"):
        log("SISTEMA", f"ElevenLabs sem signed_url: {signed}")
        return

    try:
        async with websockets.connect(j["wsUrl"], max_size=None, additional_headers=UA) as dws:
            # 1) espera o cliente atender (a D-API desiste sozinha em ~30 s)
            limite = time.time() + 50
            while True:
                m = await asyncio.wait_for(dws.recv(), timeout=max(1, limite - time.time()))
                if isinstance(m, bytes):
                    continue
                ev = json.loads(m)
                tipo = ev.get("type")
                log("D-API", tipo + (f" ({ev.get('reason') or ev.get('msg') or ''})" if tipo in ("ended", "error") else ""))
                if tipo in ("accepted", "connected"):
                    break
                if tipo in ("ended", "error"):
                    return

            # 2) liga a IA
            async with websockets.connect(signed["signed_url"], max_size=None) as ews:
                await ews.send(json.dumps({"type": "conversation_initiation_client_data",
                                           "dynamic_variables": {"nome": nome}}))
                fila = bytearray()
                fim = asyncio.Event()
                silencio = b"\0" * FRAME

                async def para_cliente():
                    # a ElevenLabs manda o inicio de cada fala mais devagar que o tempo real: segura
                    # ~300 ms antes de comecar a tocar (ou 400 ms de espera) para nao picotar
                    prox = time.monotonic()
                    tocando, desde = False, None
                    while not fim.is_set():
                        if not tocando and fila:
                            desde = desde or time.monotonic()
                            if len(fila) >= PREBUF or time.monotonic() - desde > 0.4:
                                tocando, desde = True, None
                        if not tocando:
                            q = silencio
                        elif len(fila) >= FRAME:
                            q = bytes(fila[:FRAME]); del fila[:FRAME]
                        elif fila:
                            q = bytes(fila) + b"\0" * (FRAME - len(fila)); fila.clear()
                            tocando = False
                        else:
                            q = silencio
                            tocando = False
                        await dws.send(q)
                        prox += 0.02
                        espera = prox - time.monotonic()
                        if espera < -0.1:
                            prox = time.monotonic()
                        else:
                            await asyncio.sleep(max(0, espera))

                async def do_cliente():
                    buf = bytearray()
                    async for m in dws:
                        if isinstance(m, bytes):
                            buf += m
                            if len(buf) >= 3200:  # ~100 ms por pacote para a IA
                                await ews.send(json.dumps({"user_audio_chunk": base64.b64encode(bytes(buf)).decode()}))
                                buf.clear()
                            continue
                        ev = json.loads(m)
                        tipo = ev.get("type")
                        if tipo == "recording":
                            log("D-API", f"gravacao: {ev.get('url')}")
                        elif tipo in ("ended", "error"):
                            log("D-API", f"{tipo} ({ev.get('reason') or ev.get('msg') or ''})")
                            break
                    fim.set()

                async def da_ia():
                    async for m in ews:
                        d = json.loads(m)
                        t = d.get("type")
                        if t == "audio":
                            pcm = array.array("h", base64.b64decode(d["audio_event"]["audio_base_64"]))
                            for i in range(len(pcm)):  # -3 dB: a ElevenLabs entrega com pico ~30000 e o WhatsApp distorce
                                pcm[i] = int(pcm[i] * GANHO)
                            fila.extend(pcm.tobytes())
                        elif t == "interruption":
                            fila.clear()
                        elif t == "ping":
                            await ews.send(json.dumps({"type": "pong", "event_id": d["ping_event"]["event_id"]}))
                        elif t == "agent_response":
                            log("IA", d["agent_response_event"]["agent_response"])
                        elif t == "user_transcript":
                            log("CLIENTE", d["user_transcription_event"]["user_transcript"])
                        elif t == "conversation_initiation_metadata":
                            m2 = d.get("conversation_initiation_metadata_event", {})
                            log("SISTEMA", f"IA conectada (conversa {m2.get('conversation_id')}, "
                                           f"saida {m2.get('agent_output_audio_format')}, entrada {m2.get('user_input_audio_format')})")
                    log("SISTEMA", "IA encerrou a conversa")
                    fim.set()

                tarefas = [asyncio.create_task(x()) for x in (para_cliente, do_cliente, da_ia)]
                try:
                    await asyncio.wait_for(fim.wait(), timeout=maxdur)
                except asyncio.TimeoutError:
                    log("SISTEMA", f"tempo maximo de {maxdur}s")
                # deixa a ultima fala da IA terminar de sair
                for _ in range(100):
                    if not fila:
                        break
                    await asyncio.sleep(0.05)
                fim.set()
                for t in tarefas:
                    t.cancel()
    finally:
        try:
            requests.post(f"{DAPI}/calls/end", json={"sessionId": sess, "callId": call_id}, headers=hd, timeout=15)
        except Exception:
            pass
        log("SISTEMA", "ligacao encerrada")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 240,
                     sys.argv[4] if len(sys.argv) > 4 else "tudo bem"))

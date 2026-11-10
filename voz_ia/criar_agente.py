"""Cria (ou reaproveita) o agente de voz do Escritório Modelo na ElevenLabs. Imprime o agent_id.

Configuracao (config/.env), sem valor padrao para a voz:
  ELEVENLABS_API_KEY        chave da conta ElevenLabs DO ESCRITORIO
  ELEVENLABS_VOZ_ID         id da voz (da biblioteca ou clonada com autorizacao da pessoa dona da voz)
  ELEVENLABS_AGENTE_NOME    nome do agente na conta (padrao "Escritório Modelo Ligacao IA")
  VOZ_IA_QUEM_FALA          em nome de quem a IA fala (padrao "do escritorio Escritório Modelo Advogados")

A ligacao continua sendo pelo WhatsApp (ligar_ia.py), gravada e transcrita, uma por vez por numero.
"""
import os
import sys

import requests

ENV = os.getenv("ESC_CONFIG_ENV", "/opt/escritorio/config/.env")


def _env(chave, padrao=""):
    v = os.environ.get(chave)
    if v:
        return v.strip()
    try:
        for linha in open(ENV, encoding="utf-8"):
            if linha.startswith(chave + "="):
                return linha.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return padrao


NOME = _env("ELEVENLABS_AGENTE_NOME", "Escritório Modelo Ligacao IA")
VOZ = _env("ELEVENLABS_VOZ_ID")
QUEM = _env("VOZ_IA_QUEM_FALA", "do escritorio Escritório Modelo Advogados")

# {{nome}} vem do ligar_ia.py (dynamic_variables) a cada ligacao
PROMPT = f"""Voce e o assistente virtual {QUEM}, escritorio de Cidade/UF que defende trabalhadores na Justica do
Trabalho (rescisao indireta, acidente de trabalho, trabalhadores de fast-food e supermercado, trabalhista em geral),
com a Dra. Advogada como advogada responsavel. Voce e uma inteligencia artificial e diz isso se perguntarem.

Esta e uma LIGACAO DE TESTE para {{{{nome}}}}, da equipe do escritorio, para mostrar que o escritorio agora tem um
assistente que liga e atende pelo WhatsApp.

Inicio da ligacao: espere a pessoa dizer "alo". So entao cumprimente: "Oi, {{{{nome}}}}! Aqui e o assistente
virtual do Escritório Modelo Advogados. Estou fazendo um teste rapidinho. Tudo bem?"

Como falar:
- Portugues do Brasil, jeito simples, educado e direto, como uma pessoa ao telefone.
- Frases curtas. Uma ideia por vez. Nunca leia listas.
- Pergunte como a pessoa esta, conte que e um teste e pergunte o que ela achou da voz e da demora para
  responder. Converse um pouco se ela quiser.
- Nao invente dados de clientes ou processos. Se pedirem algo assim, diga que nesta fase de teste voce
  ainda nao consulta o sistema.
- Barulho e conversas ao fundo sao comuns. Se ouvir frases que nao fazem sentido para a conversa, ignore e
  siga de onde parou. Se a pessoa disser so "alo?", ela nao ouviu: repita a ultima frase, mais curta.
- Quando a conversa acabar, agradeca e se despeca.
"""
NATURAL = """
Jeito de falar (para nao soar robotico):
- Fale como gente no telefone, nao como texto escrito: "to", "pra", "ne", "olha", "entao", "ah, que bom".
- Varie as frases. Nao repita sempre a mesma estrutura nem a mesma pergunta.
- Reaja ao que a pessoa disse antes de seguir ("ah, entendi", "que bom ouvir isso").
- Respostas curtas, de uma ou duas frases. Nada de explicacao longa.
- Se ja perguntou algo e a pessoa respondeu, nao pergunte de novo.
"""

PRIMEIRA = ""  # espera o "alo"


def main():
    key = _env("ELEVENLABS_API_KEY")
    if not key or not VOZ:
        print("Configure ELEVENLABS_API_KEY e ELEVENLABS_VOZ_ID no config/.env antes de criar o agente.",
              file=sys.stderr)
        sys.exit(1)
    h = {"xi-api-key": key, "Content-Type": "application/json"}
    lista = requests.get("https://api.elevenlabs.io/v1/convai/agents", headers=h, timeout=20).json()
    for a in lista.get("agents", []):
        if a.get("name") == NOME:
            print(a["agent_id"])
            return
    corpo = {
        "name": NOME,
        "conversation_config": {
            "agent": {"first_message": PRIMEIRA, "language": "pt", "prompt": {"prompt": PROMPT + NATURAL}},
            "tts": {"voice_id": VOZ, "model_id": "eleven_multilingual_v2", "speed": 1.1, "stability": 0.50,
                    "similarity_boost": 0.90, "agent_output_audio_format": "pcm_16000"},
            "asr": {"user_input_audio_format": "pcm_16000"},
            "conversation": {"max_duration_seconds": 300},
        },
    }
    r = requests.post("https://api.elevenlabs.io/v1/convai/agents/create", headers=h, json=corpo, timeout=30)
    if r.status_code not in (200, 201):
        print("ERRO", r.status_code, r.text[:800], file=sys.stderr)
        sys.exit(1)
    print(r.json()["agent_id"])


if __name__ == "__main__":
    main()

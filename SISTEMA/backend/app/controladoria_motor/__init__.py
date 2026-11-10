"""Motor juridico da Controladoria (handoff Advogada -> Sistema Escritório Modelo).

- feriados: calendario forense (nacional + movel + estadual/comarca + suspensao)
- motor_prazos: autoridade de datas (publicacao/inicio/vencimento/fatal interno)
- catalogo: atos, prazos legais e roteamento PECA/MEIO/CIENCIA (JSON editavel)
- classificador: 5 decisoes por intimacao (titularidade, ato, dias, trilha, prazos)

Principio: julgamento juridico mora nas regras; trabalho deterministico mora no
codigo. A IA le e propoe; NUNCA calcula data.
"""

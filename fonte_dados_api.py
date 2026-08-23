"""
Substitui o leitor_pdf.py como fonte de dados do robô: em vez de extrair
respostas de um PDF (regex sobre texto solto, sujeito a erro de layout),
busca as respostas já estruturadas na API do projeto "ds160-rascunho"
(o formulário web que o cliente preenche) e monta o mesmo
`dados_cliente.json` que o robo.py já consome.

Uso:
    python fonte_dados_api.py <cpf-ou-id-do-cliente>
    python fonte_dados_api.py            (pede o CPF interativamente)

Configuração (variáveis de ambiente, ou arquivo .env na mesma pasta):
    DS160_RASCUNHO_API_URL   ex.: https://ds160.2ntravel.com.br
    FLOW_API_URL              ex.: https://flow.2ntravel.com.br
    ROBO_API_SECRET           segredo compartilhado (mesmo valor
                              configurado em ROBO_API_SECRET nos dois
                              projetos Next.js)
"""
import json
import os
import sys

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

DS160_RASCUNHO_API_URL = os.environ.get("DS160_RASCUNHO_API_URL", "https://ds160.2ntravel.com.br")
ROBO_API_SECRET = os.environ.get("ROBO_API_SECRET", "")


# ------------------------------------------------------------------
# Acesso à API
# ------------------------------------------------------------------

def buscar_clientes_por_nome(busca):
    """Procura clientes por nome (ou CPF parcial) e devolve a lista de
    candidatos (id, nome, cpf, status, flowClienteId), ou None se der erro
    de conexão. Usado quando o operador não sabe o CPF de cor."""
    if not ROBO_API_SECRET:
        print("❌ Erro: variável de ambiente ROBO_API_SECRET não configurada (veja .env.example).")
        return None
    url = f"{DS160_RASCUNHO_API_URL}/api/robo-integracao/clientes"
    try:
        resp = requests.get(
            url, params={"q": busca}, headers={"Authorization": f"Bearer {ROBO_API_SECRET}"}, timeout=15
        )
    except requests.RequestException as e:
        print(f"❌ Erro de conexão com {DS160_RASCUNHO_API_URL}: {e}")
        return None
    if resp.status_code != 200:
        print(f"❌ Erro {resp.status_code} ao buscar clientes: {resp.text}")
        return None
    return resp.json().get("clientes", [])


def escolher_cliente_interativo(busca):
    """Busca por nome/CPF parcial e, se achar mais de um, deixa o operador
    escolher pelo número. Devolve o id do cliente escolhido, ou None."""
    candidatos = buscar_clientes_por_nome(busca)
    if candidatos is None:
        return None
    if not candidatos:
        print(f"❌ Nenhum cliente encontrado com '{busca}'.")
        return None
    if len(candidatos) == 1:
        return candidatos[0]["id"]

    print(f"\nEncontrei {len(candidatos)} clientes com '{busca}':")
    for i, c in enumerate(candidatos, start=1):
        vinculo = "vinculado ao Flow" if c.get("flowClienteId") else "sem vínculo com o Flow"
        print(f"  {i}. {c['nome']} — CPF {c.get('cpf') or 'sem CPF'} — {c['status']} — {vinculo}")
    escolha = input("Digite o número do cliente desejado: ").strip()
    try:
        return candidatos[int(escolha) - 1]["id"]
    except (ValueError, IndexError):
        print("❌ Escolha inválida.")
        return None


def buscar_cliente_api(identificador):
    """Busca o cliente (por id do ds160-rascunho ou por CPF) na API.
    Devolve o JSON cru da resposta, ou None se não encontrar/der erro."""
    if not ROBO_API_SECRET:
        print("❌ Erro: variável de ambiente ROBO_API_SECRET não configurada (veja .env.example).")
        return None

    url = f"{DS160_RASCUNHO_API_URL}/api/robo-integracao/clientes/{identificador}"
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {ROBO_API_SECRET}"}, timeout=15)
    except requests.RequestException as e:
        print(f"❌ Erro de conexão com {DS160_RASCUNHO_API_URL}: {e}")
        return None

    if resp.status_code == 404:
        print(f"❌ Nenhum cliente encontrado com '{identificador}' no ds160-rascunho.")
        return None
    if resp.status_code != 200:
        print(f"❌ Erro {resp.status_code} ao buscar cliente: {resp.text}")
        return None

    return resp.json()


# ------------------------------------------------------------------
# Helpers de leitura das respostas (respostas = { "<id>": {label, tipo, valor} })
# ------------------------------------------------------------------

def _bruto(respostas, campo_id):
    entrada = respostas.get(str(campo_id))
    return entrada["valor"] if entrada else None


def texto(respostas, campo_id, default="a confirmar"):
    v = _bruto(respostas, campo_id)
    return v.strip() if isinstance(v, str) and v.strip() else default


def texto_opcional(respostas, campo_id, default=""):
    v = _bruto(respostas, campo_id)
    return v.strip() if isinstance(v, str) and v.strip() else default


def somente_digitos(respostas, campo_id):
    v = _bruto(respostas, campo_id)
    return "".join(ch for ch in v if ch.isdigit()) if isinstance(v, str) else ""


def booleano(respostas, campo_id):
    """True somente quando a resposta for exatamente 'Sim' — qualquer outra
    coisa (incluindo campo não respondido) volta False, igual ao
    comportamento do leitor_pdf.py antigo."""
    v = _bruto(respostas, campo_id)
    return isinstance(v, str) and v.strip().upper() == "SIM"


def data_ddmmaaaa(respostas, campo_id):
    """Devolve a data já normalizada pela API como 'DD/MM/AAAA', ou '' se
    o campo não foi respondido."""
    v = _bruto(respostas, campo_id)
    return v if isinstance(v, str) and v else ""


def endereco(respostas, campo_id):
    """Devolve o dict {label_subcampo: valor} de um campo tipo 'address',
    ou {} se vazio."""
    v = _bruto(respostas, campo_id)
    return v if isinstance(v, dict) else {}


def primeiro_nao_vazio(respostas, *campo_ids, default=""):
    """Tenta vários ids de campo em ordem (útil quando o schema tem
    versões duplicadas da mesma pergunta, ex.: campo 'number' e campo
    'phone' pro mesmo telefone) e devolve o primeiro valor preenchido."""
    for cid in campo_ids:
        v = texto_opcional(respostas, cid)
        if v:
            return v
    return default


def traduzir_estado_civil(status_pt):
    status = status_pt.upper()
    if "CASADO" in status:
        return "MARRIED"
    if "SOLTEIRO" in status:
        return "SINGLE"
    if "DIVORCIADO" in status:
        return "DIVORCED"
    if "VIÚVO" in status or "VIUVO" in status:
        return "WIDOWED"
    return "OTHER/NON-APPLICABLE"


# ------------------------------------------------------------------
# Mapeamento por seção (mesmas chaves que leitor_pdf.py produzia)
# ------------------------------------------------------------------

def mapear_pagina_1(respostas):
    dados = {
        "tem_telecode": False,
        "outro_sobrenome": "",
        "outro_nome": "",
        "dob_dia": "1", "dob_mes": "JAN", "dob_ano": "2000",
        "cidade_nascimento": "a confirmar",
        "estado_nascimento": "a confirmar",
        "estado_nascimento_na": True,
        "pais_nascimento": "BRAZIL",
        "cpf": "",
        "outra_nacionalidade": False,
        "residente_permanente": False,
        "travel_dia": "1", "travel_mes": "1", "travel_ano": "2026", "travel_tempo": "1",
        "quem_paga": "S",
    }

    nome_completo = texto(respostas, 22, default="a confirmar a confirmar").split()
    dados["nome"] = nome_completo[0] if nome_completo else "a confirmar"
    dados["sobrenome"] = " ".join(nome_completo[1:]) if len(nome_completo) > 1 else "a confirmar"

    if dados["nome"] != "a confirmar" and dados["sobrenome"] != "a confirmar":
        dados["nome_nativo_na"] = False
        dados["nome_nativo"] = f"{dados['nome']} {dados['sobrenome']}"
    else:
        dados["nome_nativo_na"] = True

    dados["usou_outros_nomes"] = booleano(respostas, 6)
    if dados["usou_outros_nomes"]:
        partes_outro = texto(respostas, 7).split()
        dados["outro_nome"] = partes_outro[0] if partes_outro else "a confirmar"
        dados["outro_sobrenome"] = " ".join(partes_outro[1:]) if len(partes_outro) > 1 else "a confirmar"

    dados["sexo"] = "MALE" if texto(respostas, 24, "") == "Masculino" else "FEMALE"

    match_civil = texto_opcional(respostas, 30)
    if match_civil:
        dados["estado_civil"] = traduzir_estado_civil(match_civil)

    data_nasc = data_ddmmaaaa(respostas, 25)
    if data_nasc:
        dia, mes, ano = data_nasc.split("/")
        dados["dob_dia"] = dia.lstrip("0") or "0"
        meses_num_abrev = ["", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
        dados["dob_mes"] = meses_num_abrev[int(mes)]
        dados["dob_ano"] = ano

    dados["cidade_nascimento"] = texto(respostas, 26)
    estado_nasc = texto_opcional(respostas, 27)
    if estado_nasc:
        dados["estado_nascimento"] = estado_nasc
        dados["estado_nascimento_na"] = False

    pais_nasc = texto_opcional(respostas, 28)
    if pais_nasc:
        dados["pais_nascimento"] = "BRAZIL" if pais_nasc.upper() in ("BRASIL", "BRAZIL") else pais_nasc.upper()

    dados["cpf"] = somente_digitos(respostas, 276)

    dados["outra_nacionalidade"] = booleano(respostas, 36)
    if dados["outra_nacionalidade"]:
        dados["outra_nacionalidade_pais"] = texto(respostas, 37)
        dados["outra_nacionalidade_tem_passaporte"] = booleano(respostas, 39)
        if dados["outra_nacionalidade_tem_passaporte"]:
            dados["outra_nacionalidade_passaporte_numero"] = texto(respostas, 275)

    dados["residente_permanente"] = booleano(respostas, 42)

    data_viagem = data_ddmmaaaa(respostas, 50)
    if data_viagem:
        dia, mes, ano = data_viagem.split("/")
        dados["travel_dia"] = dia.lstrip("0") or "0"
        dados["travel_mes"] = mes.lstrip("0") or "0"
        dados["travel_ano"] = ano

    dados["travel_tempo"] = "".join(ch for ch in texto_opcional(respostas, 51, "1") if ch.isdigit()) or "1"
    dados["categoria_visto"] = texto(respostas, 46)
    dados["onde_ficara"] = texto(respostas, 53)
    dados["cidade_destino_eua"] = texto(respostas, 334)

    texto_pagador = texto_opcional(respostas, 281).upper()
    if "OUTRA EMPRESA" in texto_pagador or "EMPREGADOR" in texto_pagador:
        dados["quem_paga"] = "C"
    elif "OUTRA PESSOA" in texto_pagador:
        dados["quem_paga"] = "O"
    elif texto_pagador:
        dados["quem_paga"] = "S"

    if dados["quem_paga"] == "C":
        dados["pagador_empresa_nome"] = texto(respostas, 291)
        dados["pagador_empresa_telefone"] = texto_opcional(respostas, 293)
        dados["pagador_empresa_relacionamento"] = texto(respostas, 294)
        end = endereco(respostas, 295)
        dados["pagador_empresa_endereco_linha1"] = end.get("Rua") or "a confirmar"
        dados["pagador_empresa_endereco_cidade"] = end.get("Cidade", "a confirmar")
        dados["pagador_empresa_endereco_estado"] = end.get("Estado", "")
        dados["pagador_empresa_endereco_cep"] = end.get("Código postal", "")
        dados["pagador_empresa_endereco_pais"] = end.get("País", "BRASIL")

    if dados["quem_paga"] == "O":
        nome_completo_pagador = texto(respostas, 283)
        partes_pagador = nome_completo_pagador.split()
        dados["pagador_pessoa_nome"] = partes_pagador[0] if partes_pagador else "a confirmar"
        dados["pagador_pessoa_sobrenome"] = " ".join(partes_pagador[1:]) if len(partes_pagador) > 1 else "a confirmar"
        dados["pagador_pessoa_telefone"] = texto_opcional(respostas, 286)
        dados["pagador_pessoa_email"] = texto_opcional(respostas, 287).lower()
        dados["pagador_pessoa_relacionamento"] = texto(respostas, 284)

        end = endereco(respostas, 288)
        mesmo_endereco_texto = texto_opcional(respostas, 289)
        dados["pagador_pessoa_endereco_mesmo_aplicante"] = (
            "Mesmo endereço do aplicante" in mesmo_endereco_texto or not end
        )
        if end:
            dados["pagador_pessoa_endereco_linha1"] = end.get("Rua") or "a confirmar"
            dados["pagador_pessoa_endereco_cidade"] = end.get("Cidade", "a confirmar")
            dados["pagador_pessoa_endereco_estado"] = end.get("Estado", "")
            dados["pagador_pessoa_endereco_cep"] = end.get("Código postal", "")
            dados["pagador_pessoa_endereco_pais"] = end.get("País", "BRASIL")

    return dados


def mapear_pagina_2(respostas):
    dados = {}

    dados["viaja_com_alguem"] = booleano(respostas, 79)
    if dados["viaja_com_alguem"]:
        companheiros = []
        for id_nome, id_parentesco in [(63, 73), (71, 74), (70, 75), (69, 76), (68, 77)]:
            nome = texto_opcional(respostas, id_nome)
            if not nome:
                continue
            companheiros.append({"nome": nome, "parentesco": texto(respostas, id_parentesco)})
        dados["companheiros_viagem"] = companheiros or [{"nome": "a confirmar", "parentesco": "a confirmar"}]
    else:
        dados["companheiros_viagem"] = []

    dados["ja_esteve_eua"] = booleano(respostas, 81)

    visitas_anteriores = []
    for id_entrada, id_duracao, id_periodo in [(82, 83, 84), (86, 89, 95), (326, 90, 93), (87, 91, 94), (85, 92, 96)]:
        data_entrada = data_ddmmaaaa(respostas, id_entrada)
        if not data_entrada:
            continue
        visitas_anteriores.append({
            "data_entrada": data_entrada,
            "duracao": texto_opcional(respostas, id_duracao),
            "periodo": texto_opcional(respostas, id_periodo),
        })
    dados["visitas_anteriores_eua"] = visitas_anteriores

    dados["ja_teve_visto_eua"] = booleano(respostas, 104)
    dados["visto_anterior_data_emissao"] = data_ddmmaaaa(respostas, 105)
    dados["visto_anterior_numero"] = texto_opcional(respostas, 106)
    dados["visto_mesmo_tipo"] = booleano(respostas, 107)
    dados["visto_perdido_roubado"] = booleano(respostas, 108)
    dados["visto_cancelado_revogado"] = booleano(respostas, 114)
    dados["visto_recusado"] = booleano(respostas, 340)
    dados["peticao_imigrante"] = booleano(respostas, 116)

    dados["carteira_habilitacao_eua"] = booleano(respostas, 338)
    if dados["carteira_habilitacao_eua"]:
        dados["habilitacao_numero"] = texto(respostas, 339)

    return dados


def mapear_endereco_contato(respostas):
    dados = {}

    dados["endereco_rua"] = texto(respostas, 119)
    dados["endereco_cidade"] = texto(respostas, 120)
    dados["endereco_estado"] = texto(respostas, 121)
    dados["endereco_cep"] = somente_digitos(respostas, 122) or "a confirmar"
    dados["endereco_pais"] = texto(respostas, 123, "BRASIL")

    # Cada telefone tem 2 campos duplicados no schema (um "number" solto,
    # um "phone" com máscara) — usa o que estiver preenchido.
    dados["telefone_principal"] = primeiro_nao_vazio(respostas, 132, 335)
    dados["telefone_secundario"] = primeiro_nao_vazio(respostas, 133, 333)
    dados["telefone_comercial"] = primeiro_nao_vazio(respostas, 279, 336)

    dados["email"] = texto(respostas, 135).lower()

    midias_sociais = []
    for id_plataforma, id_handle in [(137, 138), (343, 344)]:
        plataforma = texto_opcional(respostas, id_plataforma)
        if plataforma and "NENHUMA" not in plataforma.upper():
            midias_sociais.append({"plataforma": plataforma, "handle": texto_opcional(respostas, id_handle)})
    dados["tem_midia_social"] = bool(midias_sociais)
    dados["midias_sociais"] = midias_sociais
    dados["midia_social_plataforma"] = midias_sociais[0]["plataforma"] if midias_sociais else ""
    dados["midia_social_handle"] = midias_sociais[0]["handle"] if midias_sociais else ""

    dados["passaporte_numero"] = texto(respostas, 145)
    dados["passaporte_pais_emissor"] = texto(respostas, 146, "BRASIL")
    dados["passaporte_cidade_emissora"] = texto(respostas, 148)
    dados["passaporte_estado_emissor"] = texto(respostas, 149)
    dados["passaporte_data_emissao"] = data_ddmmaaaa(respostas, 150)
    dados["passaporte_data_validade"] = data_ddmmaaaa(respostas, 151)
    dados["passaporte_perdido_roubado"] = booleano(respostas, 152)
    if dados["passaporte_perdido_roubado"]:
        dados["passaporte_perdido_numero"] = texto(respostas, 153)
        pais_perdido = texto_opcional(respostas, 154, "BRASIL")
        dados["passaporte_perdido_pais"] = "BRAZIL" if pais_perdido.upper() in ("BRASIL", "BRAZIL") else pais_perdido.upper()
        dados["passaporte_perdido_explicacao"] = texto_opcional(respostas, 155)

    dados["tem_contato_eua"] = texto_opcional(respostas, 158).upper().startswith("SIM")
    if dados["tem_contato_eua"]:
        dados["contato_eua_nome"] = texto(respostas, 304)
        # Campo 347 é texto livre no rascunho web — cliente pode digitar
        # espaço/traço/+ (ex.: "+52 15148 81511223"), e o CEAC rejeita
        # preencher.fill() quando o valor tem espaço no meio (o campo
        # some/trava sem dar erro claro). Só dígitos, igual telefone_*.
        dados["contato_eua_telefone"] = "".join(ch for ch in texto_opcional(respostas, 347) if ch.isdigit())
        dados["contato_eua_email"] = texto_opcional(respostas, 345).lower()

        end = endereco(respostas, 303)
        dados["contato_eua_endereco_linha1"] = end.get("Rua") or "a confirmar"
        dados["contato_eua_endereco_linha2"] = end.get("Bairro e Complemento", "")
        dados["contato_eua_endereco_cidade"] = end.get("Cidade", "a confirmar")
        dados["contato_eua_endereco_estado"] = end.get("Estado", "")
        dados["contato_eua_endereco_cep"] = end.get("Código postal", "")
        dados["contato_eua_endereco_pais"] = end.get("País", "a confirmar")

        dados["contato_eua_relacionamento"] = texto(respostas, 161)

    return dados


def mapear_familia(respostas):
    dados = {}

    dados["pai_nome"] = texto(respostas, 164)
    dados["pai_data_nascimento"] = data_ddmmaaaa(respostas, 165)
    dados["pai_esta_eua"] = booleano(respostas, 168)

    dados["mae_nome"] = texto(respostas, 166)
    dados["mae_data_nascimento"] = data_ddmmaaaa(respostas, 167)
    dados["mae_esta_eua"] = booleano(respostas, 169)

    dados["parente_1_grau_eua"] = booleano(respostas, 171)
    if dados["parente_1_grau_eua"]:
        dados["parente_1_grau_nome"] = texto(respostas, 173)
        dados["parente_1_grau_relacionamento"] = texto(respostas, 172)
        dados["parente_1_grau_status"] = texto(respostas, 313)

    dados["outro_parente_eua"] = booleano(respostas, 315)

    # Cônjuge: só o caso "casado com cônjuge atual" é mapeado aqui (mesmo
    # escopo que o leitor_pdf.py antigo já cobria) — Ex-Parceiro/Falecido
    # (outras opções do campo 176) ficam pra conferência manual.
    situacao_conjugal = texto_opcional(respostas, 176)
    if situacao_conjugal == "Cônjuge":
        dados["conjuge_nome"] = texto(respostas, 177)
        dados["conjuge_data_nascimento"] = data_ddmmaaaa(respostas, 179)
        dados["conjuge_nacionalidade"] = texto(respostas, 180, "BRASIL")
        end_nasc = endereco(respostas, 178)
        dados["conjuge_local_nascimento"] = end_nasc.get("Cidade") or "a confirmar"
        end_conjuge = endereco(respostas, 182)
        dados["conjuge_endereco_mesmo_aplicante"] = not end_conjuge
        # Endereço completo do cônjuge (quando diferente do aplicante) —
        # antes só pegava a Rua e ignorava cidade/estado/cep/país,
        # deixando o robô sem dado nenhum pra preencher no CEAC.
        dados["conjuge_endereco_linha1"] = end_conjuge.get("Rua", "")
        dados["conjuge_endereco_linha2"] = end_conjuge.get("Bairro e Complemento", "")
        dados["conjuge_endereco_cidade"] = end_conjuge.get("Cidade", "")
        dados["conjuge_endereco_estado"] = end_conjuge.get("Estado", "")
        dados["conjuge_endereco_cep"] = end_conjuge.get("Código postal", "")
        pais_conjuge = end_conjuge.get("País", "BRASIL")
        dados["conjuge_endereco_pais"] = "BRAZIL" if pais_conjuge.upper() in ("BRASIL", "BRAZIL") else pais_conjuge.upper()
        dados["conjuge_endereco_texto"] = end_conjuge.get("Rua", "")
    else:
        dados["conjuge_nome"] = ""
        dados["conjuge_data_nascimento"] = ""
        dados["conjuge_nacionalidade"] = ""
        dados["conjuge_local_nascimento"] = ""
        dados["conjuge_endereco_mesmo_aplicante"] = True
        dados["conjuge_endereco_linha1"] = ""
        dados["conjuge_endereco_linha2"] = ""
        dados["conjuge_endereco_cidade"] = ""
        dados["conjuge_endereco_estado"] = ""
        dados["conjuge_endereco_cep"] = ""
        dados["conjuge_endereco_pais"] = ""
        dados["conjuge_endereco_texto"] = ""

    return dados


def mapear_trabalho_educacao(respostas):
    dados = {}

    dados["ocupacao_atual"] = texto(respostas, 198)
    dados["cargo"] = texto(respostas, 317)
    dados["trabalho_data_inicio"] = data_ddmmaaaa(respostas, 203)
    dados["trabalho_funcoes"] = texto(respostas, 200)
    dados["trabalho_empresa_nome"] = texto(respostas, 316)
    dados["trabalho_telefone"] = texto_opcional(respostas, 202)

    end = endereco(respostas, 201)
    dados["trabalho_endereco_linha1"] = end.get("Rua") or "a confirmar"
    dados["trabalho_endereco_bairro"] = end.get("Bairro e Complemento", "a confirmar")
    dados["trabalho_endereco_cidade"] = end.get("Cidade", "a confirmar")
    dados["trabalho_endereco_estado"] = end.get("Estado", "")
    dados["trabalho_endereco_cep"] = end.get("Código postal", "")
    dados["trabalho_endereco_pais"] = end.get("País", "BRASIL")

    dados["trabalhou_outra_empresa_5anos"] = booleano(respostas, 207)
    if dados["trabalhou_outra_empresa_5anos"]:
        dados["trabalho_anterior_empresa_nome"] = texto(respostas, 208)
        dados["trabalho_anterior_cargo"] = texto(respostas, 321)
        dados["trabalho_anterior_telefone"] = texto_opcional(respostas, 351)

        end_ant = endereco(respostas, 209)
        dados["trabalho_anterior_endereco_linha1"] = end_ant.get("Rua") or "a confirmar"
        dados["trabalho_anterior_endereco_cidade"] = end_ant.get("Cidade", "a confirmar")
        dados["trabalho_anterior_endereco_estado"] = end_ant.get("Estado", "")
        dados["trabalho_anterior_endereco_cep"] = end_ant.get("Código postal", "")
        dados["trabalho_anterior_endereco_pais"] = end_ant.get("País", "BRASIL")

        dados["trabalho_anterior_data_inicio"] = data_ddmmaaaa(respostas, 212)
        dados["trabalho_anterior_data_fim"] = data_ddmmaaaa(respostas, 213)
        dados["trabalho_anterior_funcoes"] = texto(respostas, 214)

    dados["estudou_nivel_medio_superior"] = booleano(respostas, 217)
    dados["instituicao_nome"] = texto_opcional(respostas, 218)

    end_inst = endereco(respostas, 219)
    dados["instituicao_endereco_linha1"] = end_inst.get("Rua") or "a confirmar"
    dados["instituicao_endereco_cidade"] = end_inst.get("Cidade", "a confirmar")
    dados["instituicao_endereco_estado"] = end_inst.get("Estado", "")
    dados["instituicao_endereco_cep"] = end_inst.get("Código postal", "")
    dados["instituicao_endereco_pais"] = end_inst.get("País", "BRASIL")

    dados["curso_nome"] = texto_opcional(respostas, 220)
    dados["curso_data_inicio"] = data_ddmmaaaa(respostas, 221)
    dados["curso_data_termino"] = data_ddmmaaaa(respostas, 222)

    dados["idiomas"] = texto_opcional(respostas, 227)
    dados["viajou_ultimos_5_anos"] = booleano(respostas, 230)
    dados["paises_visitados_5_anos"] = texto_opcional(respostas, 231)

    dados["treinamento_arma"] = booleano(respostas, 234)
    dados["treinamento_arma_detalhe"] = texto_opcional(respostas, 236)
    dados["serviu_exercito"] = booleano(respostas, 235)
    if dados["serviu_exercito"]:
        pais_militar = endereco(respostas, 237).get("País", "BRASIL")
        dados["servico_militar_pais"] = "BRAZIL" if pais_militar.upper() in ("BRASIL", "BRAZIL") else pais_militar.upper()
        dados["servico_militar_ramo"] = texto(respostas, 238)
        dados["servico_militar_posicao"] = texto(respostas, 239)
        dados["servico_militar_especialidade"] = texto(respostas, 240)
        dados["servico_militar_data_inicio"] = data_ddmmaaaa(respostas, 241)
        dados["servico_militar_data_fim"] = data_ddmmaaaa(respostas, 242)

    return dados


# chave semântica -> id do campo (radio Sim/Não) no FormularioSchema
SEGURANCA_CAMPO_ID = {
    "doenca_transmissivel": 248,
    "disturbio_mental_fisico": 249,
    "usuario_drogas": 261,
    "preso_condenado": 250,
    "violou_lei_substancias_controladas": 254,
    "prostituicao": 255,
    "lavagem_dinheiro": 256,
    "trafico_pessoas": 257,
    "auxiliou_trafico_pessoas": 327,
    "conjuge_beneficiario_trafico": 328,
    "espionagem_sabotagem": 329,
    "atividades_terroristas": 258,
    "apoio_financeiro_terrorismo": 259,
    "membro_organizacao_terrorista": 260,
    "genocidio": 264,
    "tortura": 265,
    "execucoes_extrajudiciais": 266,
    "violacoes_liberdade_religiosa": 267,
    "audiencia_deportacao": 268,
    "fraude_visto": 269,
    "ultrapassou_prazo_visto": 270,
    "custodia_crianca_eua": 271,
    "votou_ilegalmente_eua": 272,
    "renunciou_cidadania_evitar_imposto": 273,
    "escola_publica_sem_reembolso": 274,
}


def explicacao(respostas, campo_id):
    """Texto que o próprio cliente escreveu no campo 'Explique' condicional
    a essa pergunta (existe desde 2026-08-23) — o robô NUNCA decide isso
    sozinho, só repassa o que um humano já escreveu no rascunho web."""
    entrada = respostas.get(str(campo_id))
    if not entrada:
        return ""
    texto = entrada.get("explicacao", "")
    return texto.strip() if isinstance(texto, str) else ""


def mapear_seguranca(respostas):
    """Igual ao leitor_pdf.py: cada pergunta vira True apenas se a resposta
    for exatamente 'Sim'. Uma pergunta NÃO respondida (campo ausente de
    `respostas`) também vira False aqui — a rede de segurança real contra
    isso é a revisão humana obrigatória em `revisar_seguranca()` no
    robo.py, que lista de novo cada pergunta pro operador conferir antes
    de enviar. Nunca confie só nesse dict pra decidir uma resposta de
    segurança.

    `seguranca_explicacoes` guarda o texto que o cliente já escreveu (via
    campo 'Explique' no rascunho web) pra cada pergunta respondida 'Sim' —
    ainda não usado pelo robo.py (Security Parts 2-5 não mapeadas), mas já
    disponível pra quando isso for implementado."""
    return {
        "seguranca": {chave: booleano(respostas, cid) for chave, cid in SEGURANCA_CAMPO_ID.items()},
        "seguranca_explicacoes": {
            chave: explicacao(respostas, cid)
            for chave, cid in SEGURANCA_CAMPO_ID.items()
            if explicacao(respostas, cid)
        },
    }


def montar_dados_cliente(cliente_api):
    respostas = cliente_api.get("respostas", {})

    dados = {}
    dados.update(mapear_pagina_1(respostas))
    dados.update(mapear_pagina_2(respostas))
    dados.update(mapear_endereco_contato(respostas))
    dados.update(mapear_familia(respostas))
    dados.update(mapear_trabalho_educacao(respostas))
    dados.update(mapear_seguranca(respostas))

    # Guardado à parte (não existia no leitor_pdf.py): id do cliente no
    # Flow, pra robo.py conseguir gravar o Application ID de volta lá no
    # final, sem precisar digitar manualmente.
    dados["_flow_cliente_id"] = cliente_api.get("flowClienteId")
    dados["_ds160_rascunho_cliente_id"] = cliente_api.get("id")

    return dados


def salvar_json(dados, nome_arquivo="dados_cliente.json"):
    with open(nome_arquivo, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=4)
    print(f"✅ Arquivo '{nome_arquivo}' gerado com sucesso!")


if __name__ == "__main__":
    entrada = sys.argv[1] if len(sys.argv) > 1 else input(
        "Nome ou CPF do cliente no ds160-rascunho: "
    ).strip()
    if not entrada:
        sys.exit(1)

    # CPF = só dígitos (com ou sem pontuação). Id do Prisma (cuid) = string
    # alfanumérica longa começando com 'c', sem espaço. Qualquer outra
    # coisa (nome, mesmo que uma palavra só) cai na busca por nome.
    so_digitos = entrada.replace(".", "").replace("-", "").isdigit()
    parece_id_prisma = len(entrada) > 20 and entrada.isalnum() and entrada.startswith("c")
    identificador = entrada if (so_digitos or parece_id_prisma) else escolher_cliente_interativo(entrada)
    if not identificador:
        sys.exit(1)

    cliente_api = buscar_cliente_api(identificador)
    if not cliente_api:
        sys.exit(1)

    print(f"ℹ️ Cliente encontrado: {cliente_api.get('nome')} (status: {cliente_api.get('status')})")
    if cliente_api.get("status") != "CONCLUIDO":
        print("⚠️ Esse rascunho ainda não foi marcado como concluído pelo cliente — confira com "
              "cuidado se todos os dados já estão preenchidos antes de rodar o robô.")

    dados = montar_dados_cliente(cliente_api)
    salvar_json(dados)
    print("\nResumo dos dados extraídos do cliente:")
    print(json.dumps(dados, indent=2, ensure_ascii=False))

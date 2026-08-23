import json
import os
import re
import time
import unicodedata

import requests
from playwright.sync_api import sync_playwright

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

FLOW_API_URL = os.environ.get("FLOW_API_URL", "https://flow.2ntravel.com.br")
ROBO_API_SECRET = os.environ.get("ROBO_API_SECRET", "")

# ==============================================================================
# AVISO IMPORTANTE
# As telas "Personal Information 1" e "Personal Information 2" já foram
# testadas e validadas contra o site real (ceac.state.gov).
# As telas a partir de "Travel Information" foram escritas seguindo a
# convenção de nomes de campo que o site do DS-160 costuma usar (mesmo padrão
# tbxAPP_*, ddlAPP_*, rblAPP_*, cbxAPP_*_NA já usado nas telas 1 e 2), mas
# AINDA NÃO FORAM CONFERIDAS no site ao vivo. Rode o robô devagar, tela por
# tela, e se algum seletor não encontrar o campo, abra o DevTools do
# navegador (F12 -> Elements), pegue o id real do campo e ajuste a linha
# correspondente aqui.
#
# Todo campo é preenchido através das funções preencher_texto/selecionar_dropdown/
# marcar_checkbox abaixo: elas NUNCA derrubam a execução — se um seletor não
# funcionar, avisam no terminal e o robô segue para o próximo campo, em vez de
# abortar a tela inteira (era isso que travava tudo antes).
# ==============================================================================

MESES_ABREV = {
    "1": "JAN", "2": "FEB", "3": "MAR", "4": "APR",
    "5": "MAY", "6": "JUN", "7": "JUL", "8": "AUG",
    "9": "SEP", "10": "OCT", "11": "NOV", "12": "DEC"
}

PERIODO_PT_EN = {
    "DIA": "Day(s)", "DIAS": "Day(s)",
    "SEMANA": "Week(s)", "SEMANAS": "Week(s)",
    "MES": "Month(s)", "MESES": "Month(s)",
    "ANO": "Year(s)", "ANOS": "Year(s)",
}

# Opções reais do campo 73 (Grau de Parentesco, tela Travel Companions) no
# formulário ds160-rascunho — conferidas contra o FormularioSchema em
# 2026-08-23. As chaves precisam bater exatamente com o texto da opção
# (incluindo o "*" de "Amigo(a)*" e os espaços em "Mãe / Pai"), senão o
# select_option falha silenciosamente e o campo fica sem preencher.
RELACIONAMENTO_EN = {
    "Cônjuge": "SPOUSE",
    "Filho(a)": "CHILD",
    "Mãe / Pai": "PARENT",
    "Irmão(a)": "OTHER RELATIVE",
    "Tio(a)": "OTHER RELATIVE",
    "Avô / Avó": "OTHER RELATIVE",
    "Primo(a)": "OTHER RELATIVE",
    "Amigo(a)*": "FRIEND",
    "Namorado(a)": "OTHER",
    "Parceiro de trabalho / Negócios": "BUSINESS ASSOCIATE",
    # Grafias antigas que existiam antes de padronizar os 5 campos de
    # parentesco no ds160-rascunho em 2026-08-23 (cada vaga de
    # acompanhante tinha opções digitadas ligeiramente diferentes) —
    # mantidas aqui como rede de segurança extra.
    "Filho/Filha": "CHILD",
    "Avô/Avó": "OTHER RELATIVE",
}

# Relacionamento com o contato nos EUA (opções reais do campo 161 no
# ds160-rascunho, conferidas em 2026-08-23 — nota: "Conjugê" é erro de
# digitação no próprio formulário, não aqui; a chave tem que bater com o
# que o cliente realmente vê e escolhe).
POC_RELACIONAMENTO_EN = {
    "Parente": "RELATIVE",
    "Conjugê": "SPOUSE",
    "Amigo": "FRIEND",
    "Parceiro Comercial": "BUSINESS ASSOCIATE",
    "Empregador": "EMPLOYER",
    "Escola Oficial": "SCHOOL OFFICIAL",
    "Outro": "OTHER",
}

# Nomes de estado americano escritos em português -> nome oficial em inglês
ESTADOS_EUA_PT_EN = {
    "NOVA IORQUE": "NEW YORK",
    "NOVA YORK": "NEW YORK",
    "CALIFORNIA": "CALIFORNIA",
    "FLORIDA": "FLORIDA",
    "HAVAI": "HAWAII",
    "LUISIANA": "LOUISIANA",
    "CAROLINA DO NORTE": "NORTH CAROLINA",
    "CAROLINA DO SUL": "SOUTH CAROLINA",
    "DAKOTA DO NORTE": "NORTH DAKOTA",
    "DAKOTA DO SUL": "SOUTH DAKOTA",
    "VIRGINIA": "VIRGINIA",
    "VIRGINIA OCIDENTAL": "WEST VIRGINIA",
    "NOVA JERSEY": "NEW JERSEY",
    "NOVO MEXICO": "NEW MEXICO",
    "PENSILVANIA": "PENNSYLVANIA",
    "GEORGIA": "GEORGIA",
}

# Grau de parentesco do parente de 1º grau nos EUA (opções reais do campo
# 172 no ds160-rascunho, conferidas em 2026-08-23 — "Noivo(a)" e
# "Filho/Filha" sem espaço ao redor da barra nunca bateram com o texto
# real da opção, "Noivo / Noiva" e "Filho / Filha", então esse campo
# provavelmente sempre falhava silenciosamente antes desta correção).
PARENTESCO_US_REL_EN = {
    "Cônjuge": "SPOUSE",
    "Noivo / Noiva": "FIANCÉ/FIANCÉE",
    "Filho / Filha": "CHILD",
    "Irmão / Irmã": "SIBLING",
}


# Opções reais dos campos 307/308/313 (Situação do Pai/Mãe/Parente nos
# EUA) no ds160-rascunho, conferidas em 2026-08-23. Antes esta função
# comparava por substring ("GREEN CARD" in status, "CIDAD" in status),
# o que classificava ERRADO a própria opção "Não Imigrante (não tem
# green card e não é cidadão)" — ela contém as substrings "GREEN CARD" e
# "CIDAD" dentro da negação, então caía sempre no primeiro "if" (U.S.
# CITIZEN) em vez de NONIMMIGRANT. Trocado pra comparação exata.
STATUS_PARENTE_EUA_EN = {
    "Cidadão Americano": "U.S. CITIZEN",
    "Residente permanente legal dos EUA (Green Card)": "U.S. LEGAL PERMANENT RESIDENT (LPR)",
    "Não Imigrante (não tem green card e não é cidadão)": "NONIMMIGRANT",
    "Outro": "OTHER/I DON'T KNOW",
}


def traduzir_status_parente_eua(status_pt):
    resultado = STATUS_PARENTE_EUA_EN.get(status_pt.strip())
    if resultado is None:
        print(f"⚠️ Situação de parente nos EUA não reconhecida: '{status_pt}' — selecione manualmente.")
        return "OTHER/I DON'T KNOW"
    return resultado

# Nomes de país em português (sem acento) -> texto exato da opção no site (em inglês)
PAISES_PT_EN = {
    "INGLATERRA": "UNITED KINGDOM",
    "REINO UNIDO": "UNITED KINGDOM",
    "ESPANHA": "SPAIN",
    "PORTUGAL": "PORTUGAL",
    "URUGUAI": "URUGUAY",
    "ARGENTINA": "ARGENTINA",
    "PARAGUAI": "PARAGUAY",
    "ALEMANHA": "GERMANY",
    "FRANCA": "FRANCE",
    "ITALIA": "ITALY",
    "ESTADOS UNIDOS": "UNITED STATES OF AMERICA",
    "CHILE": "CHILE",
    "MEXICO": "MEXICO",
    "CANADA": "CANADA",
    "HOLANDA": "NETHERLANDS",
    "PAISES BAIXOS": "NETHERLANDS",
    "SUICA": "SWITZERLAND",
    "JAPAO": "JAPAN",
    "CHINA": "CHINA",
    "COLOMBIA": "COLOMBIA",
    "PERU": "PERU",
    "BOLIVIA": "BOLIVIA",
}


def extrair_lista_paises(texto):
    """Separa uma lista de países em português tipo 'A, B e C' e traduz cada um
    para o texto de opção usado no site. Não traduzidos voltam em maiúsculas
    (aparece um aviso pra conferir manualmente)."""
    if not texto:
        return []
    texto_normalizado = texto.replace(" e ", ", ").replace(" E ", ", ")
    partes = [p.strip() for p in texto_normalizado.split(",") if p.strip()]
    resultado = []
    for parte in partes:
        chave = remover_acentos(parte).upper()
        traduzido = PAISES_PT_EN.get(chave)
        if not traduzido:
            print(f"⚠️ País '{parte}' não está no dicionário de tradução — selecionando "
                  f"'{chave}' como está, confira se bateu com a opção do site.")
            traduzido = chave
        resultado.append(traduzido)
    return resultado


def carregar_dados():
    try:
        with open('dados_cliente.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print("❌ Erro: Arquivo 'dados_cliente.json' não encontrado.")
        return None


def _tem_telefone_real(valor):
    """O rascunho web (ds160-rascunho) usa '0' como convenção pra 'não
    tenho esse telefone' nos campos de segundo telefone/telefone
    comercial (são obrigatórios no formulário deles, então não dá pra
    deixar em branco). Sem esse filtro, o robô preenchia '0' literal no
    campo do CEAC em vez de marcar 'Does Not Apply'."""
    if not valor:
        return False
    digitos = "".join(ch for ch in str(valor) if ch.isdigit())
    return digitos not in ("", "0")


def remover_acentos(texto):
    """O site do DS-160 não aceita acentos/caracteres especiais nos campos
    de texto (ex.: BELÉM -> BELEM, São Paulo -> Sao Paulo)."""
    nfkd = unicodedata.normalize('NFKD', str(texto))
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def limpar_caracteres_nome(texto):
    """O DS-160 só aceita A-Z, 0-9, hífen, apóstrofo, & e espaço simples em
    campos de nome (empresa, escola, pessoa)."""
    texto = remover_acentos(texto).upper()
    texto = re.sub(r"[^A-Z0-9\-'& ]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def limpar_caracteres_endereco(texto):
    """O DS-160 aceita um conjunto mais amplo de caracteres em campos de
    endereço, mas ainda restrito (sem barra, por exemplo)."""
    texto = remover_acentos(texto)
    texto = re.sub(r"[^A-Za-z0-9#$*%&;!@^?><().,'\" -]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


ARQUIVO_LOG_ALERTAS = "log_alertas.txt"

# Pendências da tela atual — acumuladas silenciosamente durante o
# preenchimento (sem pausar campo a campo, que tirava o operador do
# ritmo e causava dessincronia com o navegador) e mostradas de uma vez
# só no final da tela, no mesmo ponto onde o robô já pausava antes.
_pendencias_tela = []


def _registrar_alerta(tipo_campo, seletor, valor_pretendido):
    """Grava no log local e na lista de pendências da tela atual todo
    campo que o robô não conseguiu preencher sozinho. O log serve pra,
    com o tempo, perceber se algum seletor específico vive quebrando
    (sinal de que o CEAC mudou o campo)."""
    print(f"🔴 CAMPO NÃO PREENCHIDO ({tipo_campo}): seletor={seletor} | valor pretendido={valor_pretendido!r}")
    _pendencias_tela.append((tipo_campo, seletor, valor_pretendido))
    try:
        with open(ARQUIVO_LOG_ALERTAS, "a", encoding="utf-8") as f:
            f.write(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {tipo_campo} | seletor={seletor} | "
                f"valor_pretendido={valor_pretendido!r}\n"
            )
    except OSError:
        pass  # log é conveniência, nunca deve travar o robô


def _perguntar(page, mensagem):
    """input() padrão do robô: SEMPRE aceita 'listar' (mostra os campos da
    tela atual e pergunta de novo), em qualquer prompt — antes só alguns
    prompts reconheciam o comando e outros não, o que fazia parecer que
    'listar' às vezes não funcionava."""
    while True:
        resposta = input(mensagem).strip().lower()
        if resposta == "listar":
            listar_campos_da_tela(page)
            continue
        return resposta


def revisar_pendencias_tela(page, nome_tela):
    """Chamado uma vez, no final de cada tela (mesmo ponto onde o robô já
    pausava pedindo pra clicar 'Next'). Se algum campo falhou durante o
    preenchimento dessa tela, mostra a lista completa de uma vez e pausa
    UMA ÚNICA VEZ pedindo confirmação — em vez de interromper o operador
    a cada campo individual."""
    global _pendencias_tela
    if not _pendencias_tela:
        return
    print("\a", end="")  # beep do terminal, chama atenção do operador
    print("\n" + "!" * 60)
    print(f"🔴 {len(_pendencias_tela)} campo(s) NÃO preenchido(s) na tela '{nome_tela}':")
    for tipo_campo, seletor, valor_pretendido in _pendencias_tela:
        print(f"   - [{tipo_campo}] {seletor} (valor pretendido: {valor_pretendido!r})")
    print("!" * 60)
    _perguntar(page, "👉 Preencha esses campos manualmente na tela e aperte ENTER pra continuar (ou 'listar')... ")
    _pendencias_tela = []


def preencher_texto(page, seletor, valor):
    """Preenche um campo de texto. Se o campo não existir/não estiver
    visível, registra a pendência e segue pro próximo campo (a tela
    inteira roda sem pausar; a revisão acontece toda de uma vez no final,
    ver revisar_pendencias_tela). Usa .first porque algumas telas do CEAC
    têm um segundo elemento oculto com id terminando igual (mecanismo de
    'trocar nacionalidade'), o que faria o seletor por sufixo bater em
    dois elementos e travar o Playwright.

    Campos <textarea> (as caixas grandes tipo 'Descreva suas funções' /
    'Explique') usam press_sequentially em vez de fill: alguns desses
    campos no CEAC parecem só validar valor digitado tecla por tecla
    (bloqueiam colar/setar direto), então fill() 'preenchia' sem erro
    mas a página não reconhecia o texto como realmente digitado."""
    if valor is None or valor == "":
        return
    valor_sem_acento = remover_acentos(valor)
    try:
        loc = page.locator(seletor).first
        tipo_elemento = loc.evaluate("e => e.tagName.toLowerCase()")
        if tipo_elemento == "textarea":
            loc.click(timeout=6000)
            loc.press_sequentially(valor_sem_acento, timeout=15000)
        else:
            loc.fill(valor_sem_acento, timeout=6000)
    except Exception:
        _registrar_alerta("texto", seletor, valor_sem_acento)


def selecionar_dropdown(page, seletor, valor):
    """Tenta selecionar uma opção de <select> testando por label e por value,
    com e sem zero à esquerda (o DS-160 varia isso dependendo do campo).
    Se nenhuma variação funcionar, registra a pendência e segue em frente.
    Usa .first pelo mesmo motivo de preencher_texto (elemento duplicado
    oculto em algumas telas)."""
    if not valor:
        return
    loc = page.locator(seletor).first
    valor = str(valor)
    candidatos = [valor, valor.upper(), valor.capitalize()]
    if valor.isdigit():
        candidatos.append(valor.zfill(2))
        candidatos.append(str(int(valor)))
    vistos = []
    for candidato in candidatos:
        if candidato in vistos:
            continue
        vistos.append(candidato)
        for modo in ("label", "value"):
            try:
                loc.select_option(timeout=1200, **{modo: candidato})
                return
            except Exception:
                continue
    _registrar_alerta("dropdown", seletor, valor)


def marcar_checkbox(page, seletor, forcar=False):
    """Clica num checkbox/radio. Se não encontrar, registra a pendência e
    segue em frente (timeout maior que antes — 6s em vez de 3s — porque o
    CEAC às vezes demora pra terminar o postback da pergunta anterior).
    Usa .first pelo mesmo motivo de preencher_texto/selecionar_dropdown."""
    try:
        page.locator(seletor).first.click(force=forcar, timeout=6000)
    except Exception:
        _registrar_alerta("checkbox/radio", seletor, "(clique)")


def marcar_sim_nao(page, id_prefix, valor_sim):
    """Clica no radio Sim (_0) ou Não (_1), seguindo o padrão já usado nas
    telas 1 e 2 (rblOtherNames_0 = Sim, rblOtherNames_1 = Não)."""
    sufixo = "_0" if valor_sim else "_1"
    marcar_checkbox(page, f"input[id$='{id_prefix}{sufixo}']")


def preencher_pagina_1(page, cliente):
    """Preenche a tela Personal Information 1"""
    print("\n▶️ Injetando dados na tela Personal Information 1...")
    page.set_default_timeout(5000)

    preencher_texto(page, "input[id$='tbxAPP_SURNAME']", cliente['sobrenome'])
    preencher_texto(page, "input[id$='tbxAPP_GIVEN_NAME']", cliente['nome'])

    if cliente['nome_nativo_na']:
        marcar_checkbox(page, "input[name*='NATIVE_NA'][type='checkbox']")
    else:
        preencher_texto(page, "input[id$='tbxAPP_FULL_NAME_NATIVE']", cliente['nome_nativo'])

    if cliente['usou_outros_nomes']:
        marcar_checkbox(page, "input[id$='rblOtherNames_0']")
        time.sleep(1)
        preencher_texto(page, "input[id$='DListAlias_ctl00_tbxSURNAME']", cliente['outro_sobrenome'])
        preencher_texto(page, "input[id$='DListAlias_ctl00_tbxGIVEN_NAME']", cliente['outro_nome'])
    else:
        marcar_checkbox(page, "input[id$='rblOtherNames_1']")

    if cliente['tem_telecode']:
        marcar_checkbox(page, "input[id$='rblTelecodeQuestion_0']")
        time.sleep(1)
    else:
        marcar_checkbox(page, "input[id$='rblTelecodeQuestion_1']")

    selecionar_dropdown(page, "select[id$='ddlAPP_GENDER']", cliente['sexo'])
    selecionar_dropdown(page, "select[id$='ddlAPP_MARITAL_STATUS']", cliente['estado_civil'])

    selecionar_dropdown(page, "select[id$='ddlDOBDay']", cliente['dob_dia'])
    selecionar_dropdown(page, "select[id$='ddlDOBMonth']", cliente['dob_mes'])
    preencher_texto(page, "input[id$='tbxDOBYear']", cliente['dob_ano'])

    preencher_texto(page, "input[id$='tbxAPP_POB_CITY']", cliente['cidade_nascimento'])

    if cliente['estado_nascimento_na']:
        marcar_checkbox(page, "input[name*='POB_ST_PROVINCE_NA'][type='checkbox']")
    else:
        preencher_texto(page, "input[id$='tbxAPP_POB_ST_PROVINCE']", cliente['estado_nascimento'])

    selecionar_dropdown(page, "select[id$='ddlAPP_POB_CNTRY']", cliente['pais_nascimento'])

    print("✅ Página 1 preenchida (confira os avisos ⚠️ acima, se houver)!")


def preencher_pagina_2(page, cliente):
    """Preenche a tela Personal Information 2"""
    print("\n▶️ Injetando dados na tela Personal Information 2...")
    page.set_default_timeout(5000)

    pais = cliente.get('pais_nascimento', 'BRAZIL')
    selecionar_dropdown(page, "select[id$='ddlAPP_NATL']", pais)

    marcar_checkbox(page, "input[id$='rblAPP_OTH_NATL_IND_1']")
    marcar_checkbox(page, "input[id$='rblPermResOtherCntryInd_1']")

    cpf = cliente.get('cpf', '')
    if cpf:
        preencher_texto(page, "input[id$='tbxAPP_NATIONAL_ID']", cpf)
    else:
        print("⚠️ CPF não encontrado no JSON. O campo ficará em branco.")

    print("Marcando 'Does Not Apply' no U.S. Social Security Number...")
    marcar_checkbox(page, "input[id$='cbexAPP_SSN_NA']", forcar=True)

    print("Marcando 'Does Not Apply' no U.S. Taxpayer ID Number...")
    marcar_checkbox(page, "input[id$='cbexAPP_TAX_ID_NA']", forcar=True)

    print("✅ Página 2 preenchida (confira os avisos ⚠️ acima, se houver)!")


HOSPEDAGEM_PADRAO = {
    "rua": "HOTEL TO SET",
    "cidade": "ORLANDO",
    "estado": "FLORIDA",
}


def preencher_travel(page, cliente):
    """Preenche a tela Travel Information (datas, hospedagem e quem paga)"""
    print("\n▶️ Injetando dados na tela Travel Information...")
    page.set_default_timeout(5000)

    # Purpose of Trip: fica sempre em B / B1-B2 (padrão para todos os formulários)
    selecionar_dropdown(page, "select[id$='ddlPurposeOfTrip']", "B")
    selecionar_dropdown(page, "select[id$='ddlOtherPurpose']", "B1-B2")

    # Have you made specific travel plans? -> sempre "No"
    marcar_checkbox(page, "input[id$='rblSpecificTravel_1']")

    # Data prevista de chegada
    selecionar_dropdown(page, "select[id$='ddlTRAVEL_DTEDay']", cliente['travel_dia'])
    selecionar_dropdown(page, "select[id$='ddlTRAVEL_DTEMonth']", MESES_ABREV.get(cliente['travel_mes'], "JAN"))
    preencher_texto(page, "input[id$='tbxTRAVEL_DTEYear']", cliente['travel_ano'])

    # Tempo de permanência
    preencher_texto(page, "input[id$='tbxTRAVEL_LOS']", cliente['travel_tempo'])
    selecionar_dropdown(page, "select[id$='ddlTRAVEL_LOS_CD']", "Day(s)")

    # Endereço de hospedagem: sempre o mesmo endereço-padrão (Orlando/FL), independente do cliente
    preencher_texto(page, "input[id$='tbxStreetAddress1']", HOSPEDAGEM_PADRAO["rua"])
    preencher_texto(page, "input[id$='tbxCity']", HOSPEDAGEM_PADRAO["cidade"])
    selecionar_dropdown(page, "select[id$='ddlTravelState']", HOSPEDAGEM_PADRAO["estado"])
    # ZIP Code fica em branco de propósito

    # Quem vai pagar a viagem (S=Self, O=Other Person, P=Present Employer, U=Employer in the U.S., C=Other Company)
    selecionar_dropdown(page, "select[id$='ddlWhoIsPaying']", cliente.get('quem_paga', ''))

    if cliente.get('quem_paga') == 'C':
        print("👉 Preenchendo dados da empresa pagadora:")
        preencher_texto(page, "input[id$='tbxPayingCompany']", cliente.get('pagador_empresa_nome', ''))
        preencher_texto(page, "input[id$='tbxCompanyRelation']", cliente.get('pagador_empresa_relacionamento', ''))
        preencher_texto(page, "input[id$='tbxPayerPhone']", cliente.get('pagador_empresa_telefone', ''))

        # Endereço da empresa pagadora é diferente do endereço do aplicante
        marcar_checkbox(page, "input[id$='rblPayerAddrSameAsInd_1']")
        preencher_texto(page, "input[id$='tbxPayerStreetAddress1']", cliente.get('pagador_empresa_endereco_linha1', ''))

        cidade_uf_cep = cliente.get('pagador_empresa_endereco_cidade_uf_cep', '')
        match_cuc = re.match(r"^(.*),\s*([^,]+?)\s+(\d{5,9})$", cidade_uf_cep.strip())
        if match_cuc:
            preencher_texto(page, "input[id$='tbxPayerCity']", match_cuc.group(1))
            preencher_texto(page, "input[id$='tbxPayerStateProvince']", match_cuc.group(2))
            preencher_texto(page, "input[id$='tbxPayerPostalZIPCode']", match_cuc.group(3))
        else:
            print(f"⚠️ Não consegui separar cidade/UF/CEP de '{cidade_uf_cep}' — preencha manualmente.")

        selecionar_dropdown(page, "select[id$='ddlPayerCountry']", "BRAZIL")

    elif cliente.get('quem_paga') == 'O':
        print("👉 Preenchendo dados da pessoa pagadora:")
        preencher_texto(page, "input[id$='tbxPayerSurname']", cliente.get('pagador_pessoa_sobrenome', ''))
        preencher_texto(page, "input[id$='tbxPayerGivenName']", cliente.get('pagador_pessoa_nome', ''))
        preencher_texto(page, "input[id$='tbxPayerPhone']", cliente.get('pagador_pessoa_telefone', ''))

        email_pagador = cliente.get('pagador_pessoa_email', '')
        if email_pagador:
            preencher_texto(page, "input[id$='tbxPAYER_EMAIL_ADDR']", email_pagador)
        else:
            marcar_checkbox(page, "input[id$='cbxDNAPAYER_EMAIL_ADDR_NA']", forcar=True)

        relacionamento = cliente.get('pagador_pessoa_relacionamento', '')
        if relacionamento:
            selecionar_dropdown(page, "select[id$='ddlPayerRelationship']", relacionamento)

        # Endereço da pessoa pagadora é o mesmo do aplicante? (só sabemos
        # dizer "Não" quando o rascunho web trouxe um endereço diferente)
        endereco_diferente = not cliente.get('pagador_pessoa_endereco_mesmo_aplicante', True)
        marcar_sim_nao(page, "rblPayerAddrSameAsInd", not endereco_diferente)

        if endereco_diferente:
            preencher_texto(page, "input[id$='tbxPayerStreetAddress1']", cliente.get('pagador_pessoa_endereco_linha1', ''))
            cidade_uf_cep = cliente.get('pagador_pessoa_endereco_cidade_uf_cep', '')
            match_cuc = re.match(r"^(.*),\s*([^,]+?)\s+(\d{5,9})$", cidade_uf_cep.strip())
            if match_cuc:
                preencher_texto(page, "input[id$='tbxPayerCity']", match_cuc.group(1))
                preencher_texto(page, "input[id$='tbxPayerStateProvince']", match_cuc.group(2))
                preencher_texto(page, "input[id$='tbxPayerPostalZIPCode']", match_cuc.group(3))
            else:
                print(f"⚠️ Não consegui separar cidade/UF/CEP de '{cidade_uf_cep}' — preencha manualmente.")
            selecionar_dropdown(page, "select[id$='ddlPayerCountry']", "BRAZIL")

    print("✅ Página Travel Information preenchida (confira os avisos ⚠️ acima, se houver)!")


def preencher_travel_companions(page, cliente):
    """Preenche a tela Travel Companions"""
    print("\n▶️ Injetando dados na tela Travel Companions...")
    page.set_default_timeout(5000)

    marcar_sim_nao(page, "rblOtherPersonsTravelingWithYou", cliente.get('viaja_com_alguem', False))

    companheiros = cliente.get('companheiros_viagem', [])
    if cliente.get('viaja_com_alguem'):
        # "Traveling as part of a group or organization?" só existe no site
        # quando já respondeu Sim pra "outras pessoas viajando com você" —
        # confirmado ao vivo em 2026-08-23 (listar_campos_da_tela não
        # mostrava rblGroupTravel nenhuma quando a resposta era "No").
        # Viajando em grupo/organização -> sempre "No" (não temos esse dado)
        marcar_checkbox(page, "input[id$='rblGroupTravel_1']")

    if cliente.get('viaja_com_alguem') and companheiros:
        for indice, comp in enumerate(companheiros):
            if indice > 0:
                # Cada acompanhante extra precisa de uma linha nova no site
                try:
                    page.locator("a[id$='InsertButtonPrincipalPOT']").last.click(timeout=3000)
                    time.sleep(1.5)  # espera o postback que cria a nova linha
                except Exception as e:
                    print(f"⚠️ Não consegui clicar em 'Add Another' para o acompanhante "
                          f"{indice + 1} ({comp.get('nome')}) — adicione a linha manualmente. ({e})")
                    continue

            prefixo_ctl = f"ctl{indice:02d}"
            nome_partes = comp['nome'].split()
            sobrenome = " ".join(nome_partes[1:]) if len(nome_partes) > 1 else comp['nome']
            preencher_texto(page, f"input[id$='dlTravelCompanions_{prefixo_ctl}_tbxSurname']", sobrenome)
            preencher_texto(page, f"input[id$='dlTravelCompanions_{prefixo_ctl}_tbxGivenName']", nome_partes[0])
            relacionamento_en = RELACIONAMENTO_EN.get(comp['parentesco'], comp['parentesco'])
            selecionar_dropdown(
                page, f"select[id$='dlTravelCompanions_{prefixo_ctl}_ddlTCRelationship']", relacionamento_en
            )

    print("✅ Página Travel Companions preenchida (confira os avisos ⚠️ acima, se houver)!")


def preencher_previous_travel(page, cliente):
    """Preenche a tela Previous U.S. Travel"""
    print("\n▶️ Injetando dados na tela Previous U.S. Travel...")
    page.set_default_timeout(5000)

    marcar_sim_nao(page, "rblPREV_US_TRAVEL_IND", cliente.get('ja_esteve_eua', False))

    if cliente.get('ja_esteve_eua'):
        # Carteira de habilitação americana e histórico de visitas só existem
        # no site quando "Have you ever been in the U.S.?" = Yes — confirmado
        # ao vivo em 2026-08-23 (listar_campos_da_tela não mostrava esses
        # campos com a resposta "No").
        marcar_sim_nao(page, "rblPREV_US_DRIVER_LIC_IND", cliente.get('carteira_habilitacao_eua', False))
        if cliente.get('carteira_habilitacao_eua'):
            numero_habilitacao = cliente.get('habilitacao_numero', '')
            if numero_habilitacao and numero_habilitacao != "a confirmar" and "SEI" not in numero_habilitacao.upper():
                preencher_texto(page, "input[id$='dtlUS_DRIVER_LICENSE_ctl00_tbxUS_DRIVER_LICENSE']", numero_habilitacao)
            else:
                marcar_checkbox(page, "input[id$='dtlUS_DRIVER_LICENSE_ctl00_cbxUS_DRIVER_LICENSE_NA']", forcar=True)
            # Estado da habilitação: sempre Florida, independente do cliente
            selecionar_dropdown(page, "select[id$='dtlUS_DRIVER_LICENSE_ctl00_ddlUS_DRIVER_LICENSE_STATE']", "FLORIDA")

        visitas = cliente.get('visitas_anteriores_eua', [])[:5]  # site só aceita até 5
        for indice, visita in enumerate(visitas):
            if indice > 0:
                try:
                    page.locator("a[id$='InsertButtonPREV_US_VISIT']").last.click(timeout=3000)
                    time.sleep(1.5)
                except Exception as e:
                    print(f"⚠️ Não consegui clicar em 'Add Another' para a visita {indice + 1}: {e}")
                    continue
            prefixo_ctl = f"ctl{indice:02d}"
            dia, mes, ano = visita['data_entrada'].split('/')
            selecionar_dropdown(page, f"select[id$='dtlPREV_US_VISIT_{prefixo_ctl}_ddlPREV_US_VISIT_DTEDay']", dia.zfill(2))
            selecionar_dropdown(page, f"select[id$='dtlPREV_US_VISIT_{prefixo_ctl}_ddlPREV_US_VISIT_DTEMonth']", MESES_ABREV[str(int(mes))])
            preencher_texto(page, f"input[id$='dtlPREV_US_VISIT_{prefixo_ctl}_tbxPREV_US_VISIT_DTEYear']", ano)
            preencher_texto(page, f"input[id$='dtlPREV_US_VISIT_{prefixo_ctl}_tbxPREV_US_VISIT_LOS']", visita['duracao'])
            periodo_en = PERIODO_PT_EN.get(visita['periodo'].upper(), "Day(s)")
            selecionar_dropdown(page, f"select[id$='dtlPREV_US_VISIT_{prefixo_ctl}_ddlPREV_US_VISIT_LOS_CD']", periodo_en)

    marcar_sim_nao(page, "rblPREV_VISA_IND", cliente.get('ja_teve_visto_eua', False))
    if cliente.get('ja_teve_visto_eua'):
        if cliente.get('visto_anterior_data_emissao'):
            dia, mes, ano = cliente['visto_anterior_data_emissao'].split('/')
            selecionar_dropdown(page, "select[id$='ddlPREV_VISA_ISSUED_DTEDay']", dia.zfill(2))
            selecionar_dropdown(page, "select[id$='ddlPREV_VISA_ISSUED_DTEMonth']", MESES_ABREV[str(int(mes))])
            preencher_texto(page, "input[id$='tbxPREV_VISA_ISSUED_DTEYear']", ano)

        numero_visto = cliente.get('visto_anterior_numero', '')
        if numero_visto and "SEI" not in numero_visto.upper() and numero_visto != "a confirmar":
            preencher_texto(page, "input[id$='tbxPREV_VISA_FOIL_NUMBER']", numero_visto)
        else:
            marcar_checkbox(page, "input[id$='cbxPREV_VISA_FOIL_NUMBER_NA']", forcar=True)

        marcar_sim_nao(page, "rblPREV_VISA_SAME_TYPE_IND", cliente.get('visto_mesmo_tipo', False))
        marcar_sim_nao(page, "rblPREV_VISA_LOST_IND", cliente.get('visto_perdido_roubado', False))

        # Sempre "Yes" nessas duas, independente do PDF
        marcar_checkbox(page, "input[id$='rblPREV_VISA_SAME_CNTRY_IND_0']")
        marcar_checkbox(page, "input[id$='rblPREV_VISA_TEN_PRINT_IND_0']")

        # "Visto já cancelado/revogado" só existe no site quando já teve
        # visto antes (não dá pra cancelar um visto que nunca existiu) —
        # confirmado ao vivo em 2026-08-23.
        marcar_sim_nao(page, "rblPREV_VISA_CANCELLED_IND", cliente.get('visto_cancelado_revogado', False))

    marcar_sim_nao(page, "rblIV_PETITION_IND", cliente.get('peticao_imigrante', False))

    # Não vem do PDF — sem dado extraído, o padrão é sempre "No"
    marcar_sim_nao(page, "rblPREV_VISA_REFUSED_IND", False)

    print("✅ Página Previous U.S. Travel preenchida (confira os avisos ⚠️ acima, se houver)!")


def preencher_address_phone(page, cliente):
    """Preenche a tela Address and Phone"""
    print("\n▶️ Injetando dados na tela Address and Phone...")
    page.set_default_timeout(5000)

    preencher_texto(page, "input[id$='tbxAPP_ADDR_LN1']", cliente.get('endereco_rua', ''))
    preencher_texto(page, "input[id$='tbxAPP_ADDR_CITY']", cliente.get('endereco_cidade', ''))
    preencher_texto(page, "input[id$='tbxAPP_ADDR_STATE']", cliente.get('endereco_estado', ''))
    if cliente.get('endereco_cep'):
        preencher_texto(page, "input[id$='tbxAPP_ADDR_POSTAL_CD']", cliente['endereco_cep'])
    else:
        marcar_checkbox(page, "input[id$='cbexAPP_ADDR_POSTAL_CD_NA']", forcar=True)
    selecionar_dropdown(page, "select[id$='ddlCountry']", "BRAZIL")

    print("Marcando endereço de correspondência = mesmo endereço residencial...")
    marcar_checkbox(page, "input[id$='rblMailingAddrSame_0']")

    preencher_texto(page, "input[id$='tbxAPP_HOME_TEL']", cliente.get('telefone_principal', ''))
    # O rascunho web usa "0" como convenção pra "não tenho esse telefone"
    # (campo obrigatório no formulário deles) — sem esse tratamento, o
    # robô jogava o "0" literal no campo em vez de marcar "Does Not Apply".
    if _tem_telefone_real(cliente.get('telefone_secundario')):
        preencher_texto(page, "input[id$='tbxAPP_MOBILE_TEL']", cliente['telefone_secundario'])
    else:
        marcar_checkbox(page, "input[id$='cbexAPP_MOBILE_TEL_NA']", forcar=True)
    if _tem_telefone_real(cliente.get('telefone_comercial')):
        preencher_texto(page, "input[id$='tbxAPP_BUS_TEL']", cliente['telefone_comercial'])
    else:
        marcar_checkbox(page, "input[id$='cbexAPP_BUS_TEL_NA']", forcar=True)

    # Não vem do PDF -> padrão "No"
    marcar_sim_nao(page, "rblAddPhone", False)

    preencher_texto(page, "input[id$='tbxAPP_EMAIL_ADDR']", cliente.get('email', ''))

    # Não vem do PDF -> padrão "No"
    marcar_sim_nao(page, "rblAddEmail", False)

    # Mídia social: se o PDF disser "Nenhuma", seleciona NONE; senão usa a(s) plataforma(s)/usuário(s) extraídos
    midias = cliente.get('midias_sociais', [])
    if not cliente.get('tem_midia_social', False) or not midias:
        selecionar_dropdown(page, "select[id$='ddlSocialMedia']", "NONE")
    else:
        for indice, midia in enumerate(midias):
            if indice > 0:
                try:
                    page.get_by_role("link", name="Add Another").last.click(timeout=3000)
                    time.sleep(1.5)
                except Exception as e:
                    print(f"⚠️ Não consegui clicar em 'Add Another' para a mídia social "
                          f"'{midia.get('plataforma')}' — adicione a linha manualmente. ({e})")
                    continue
            prefixo_ctl = f"ctl{indice:02d}"
            selecionar_dropdown(page, f"select[id$='dtlSocial_{prefixo_ctl}_ddlSocialMedia']", midia.get('plataforma', ''))
            time.sleep(1.5)  # ddlSocialMedia dispara postback e libera o campo de usuário
            if midia.get('handle'):
                preencher_texto(page, f"input[id$='dtlSocial_{prefixo_ctl}_tbxSocialMediaIdent']", midia['handle'])
            else:
                print(f"⚠️ Mídia social '{midia.get('plataforma')}' informada mas sem usuário/identificador "
                      f"no PDF — preencha manualmente.")

    # Não vem do PDF -> padrão "No"
    marcar_sim_nao(page, "rblAddSocial", False)

    print("✅ Página Address and Phone preenchida (confira os avisos ⚠️ acima, se houver)!")


def preencher_passport(page, cliente):
    """Preenche a tela Passport Information"""
    print("\n▶️ Injetando dados na tela Passport Information...")
    page.set_default_timeout(5000)

    selecionar_dropdown(page, "select[id$='ddlPPT_TYPE']", "REGULAR")
    time.sleep(1.5)  # ddlPPT_TYPE dispara um postback; espera terminar antes de preencher o resto
    preencher_texto(page, "input[id$='tbxPPT_NUM']", cliente.get('passaporte_numero', ''))
    marcar_checkbox(page, "input[id$='cbexPPT_BOOK_NUM_NA']", forcar=True)
    selecionar_dropdown(page, "select[id$='ddlPPT_ISSUED_CNTRY']", "BRAZIL")
    preencher_texto(page, "input[id$='tbxPPT_ISSUED_IN_CITY']", cliente.get('passaporte_cidade_emissora', ''))
    preencher_texto(page, "input[id$='tbxPPT_ISSUED_IN_STATE']", cliente.get('passaporte_estado_emissor', ''))
    selecionar_dropdown(page, "select[id$='ddlPPT_ISSUED_IN_CNTRY']", "BRAZIL")

    emissao = cliente.get('passaporte_data_emissao', '')
    if emissao:
        dia, mes, ano = emissao.split('/')
        selecionar_dropdown(page, "select[id$='ddlPPT_ISSUED_DTEDay']", dia.zfill(2))
        selecionar_dropdown(page, "select[id$='ddlPPT_ISSUED_DTEMonth']", MESES_ABREV[str(int(mes))])
        preencher_texto(page, "input[id$='tbxPPT_ISSUEDYear']", ano)

    validade = cliente.get('passaporte_data_validade', '')
    if validade:
        dia, mes, ano = validade.split('/')
        selecionar_dropdown(page, "select[id$='ddlPPT_EXPIRE_DTEDay']", dia.zfill(2))
        selecionar_dropdown(page, "select[id$='ddlPPT_EXPIRE_DTEMonth']", MESES_ABREV[str(int(mes))])
        preencher_texto(page, "input[id$='tbxPPT_EXPIREYear']", ano)

    marcar_sim_nao(page, "rblLOST_PPT_IND", cliente.get('passaporte_perdido_roubado', False))

    print("✅ Página Passport Information preenchida (confira os avisos ⚠️ acima, se houver)!")


def preencher_us_contact(page, cliente):
    """Preenche a tela U.S. Point of Contact. Se o cliente informou um contato real
    nos EUA no PDF, usa esses dados; senão usa o esquema padrão (Hotel/Orlando/Florida)
    igual para todo cliente sem contato real."""
    print("\n▶️ Injetando dados na tela U.S. Contact...")
    page.set_default_timeout(5000)

    if cliente.get('tem_contato_eua') and cliente.get('contato_eua_nome'):
        print("👉 Cliente tem contato real nos EUA — preenchendo com dados do PDF:")
        nome_partes = cliente['contato_eua_nome'].split()
        preencher_texto(page, "input[id$='tbxUS_POC_GIVEN_NAME']", nome_partes[0] if nome_partes else '')
        preencher_texto(page, "input[id$='tbxUS_POC_SURNAME']", " ".join(nome_partes[1:]) if len(nome_partes) > 1 else '')
        preencher_texto(page, "input[id$='tbxUS_POC_HOME_TEL']", cliente.get('contato_eua_telefone', ''))
        if cliente.get('contato_eua_email'):
            preencher_texto(page, "input[id$='tbxUS_POC_EMAIL_ADDR']", cliente['contato_eua_email'])
        relacionamento_en = POC_RELACIONAMENTO_EN.get(cliente.get('contato_eua_relacionamento', ''), "OTHER")
        selecionar_dropdown(page, "select[id$='ddlUS_POC_REL_TO_APP']", relacionamento_en)

        # Endereço: Line 1 = rua + complemento; State/City/ZIP separados da linha "cidade, estado cep"
        linha1 = cliente.get('contato_eua_endereco_linha1', '')
        linha2 = cliente.get('contato_eua_endereco_linha2', '')
        rua_completa = f"{linha1}, {linha2}" if linha2 else linha1
        preencher_texto(page, "input[id$='tbxUS_POC_ADDR_LN1']", rua_completa)

        cidade_uf_cep = cliente.get('contato_eua_cidade_uf_cep', '')
        match_cuc = re.match(r"^(.*),\s*([^,]+?)\s+(\d{5,9})$", cidade_uf_cep.strip())
        if match_cuc:
            preencher_texto(page, "input[id$='tbxUS_POC_ADDR_CITY']", match_cuc.group(1))
            estado_pt = re.sub(r"[^A-Za-zÀ-ÿ ]", "", match_cuc.group(2)).strip()
            estado_en = ESTADOS_EUA_PT_EN.get(remover_acentos(estado_pt).upper(), estado_pt)
            selecionar_dropdown(page, "select[id$='ddlUS_POC_ADDR_STATE']", estado_en)
            preencher_texto(page, "input[id$='tbxUS_POC_ADDR_POSTAL_CD']", match_cuc.group(3))
        else:
            preencher_texto(page, "input[id$='tbxUS_POC_ADDR_CITY']", cidade_uf_cep)
            print(f"⚠️ Não consegui separar cidade/estado/CEP do contato de '{cidade_uf_cep}' "
                  f"— confira City/State/ZIP manualmente.")
    else:
        marcar_checkbox(page, "input[id$='cbxUS_POC_NAME_NA']", forcar=True)
        time.sleep(1.5)  # cbxUS_POC_NAME_NA dispara um postback; espera terminar
        preencher_texto(page, "input[id$='tbxUS_POC_ORGANIZATION']", HOSPEDAGEM_PADRAO["rua"])
        selecionar_dropdown(page, "select[id$='ddlUS_POC_REL_TO_APP']", "OTHER")
        preencher_texto(page, "input[id$='tbxUS_POC_ADDR_LN1']", HOSPEDAGEM_PADRAO["rua"])
        preencher_texto(page, "input[id$='tbxUS_POC_ADDR_CITY']", HOSPEDAGEM_PADRAO["cidade"])
        selecionar_dropdown(page, "select[id$='ddlUS_POC_ADDR_STATE']", HOSPEDAGEM_PADRAO["estado"])
        # ZIP Code fica em branco de propósito
        preencher_texto(page, "input[id$='tbxUS_POC_HOME_TEL']", "000000000")
        marcar_checkbox(page, "input[id$='cbexUS_POC_EMAIL_ADDR_NA']", forcar=True)

    print("✅ Página U.S. Contact preenchida (confira os avisos ⚠️ acima, se houver)!")


def preencher_family_relatives(page, cliente):
    """Preenche a tela Family Information: Relatives"""
    print("\n▶️ Injetando dados na tela Family Information: Relatives...")
    page.set_default_timeout(5000)

    nome_pai = cliente.get('pai_nome', '').split()
    preencher_texto(page, "input[id$='tbxFATHER_GIVEN_NAME']", nome_pai[0] if nome_pai else '')
    preencher_texto(page, "input[id$='tbxFATHER_SURNAME']", " ".join(nome_pai[1:]) if len(nome_pai) > 1 else '')

    if cliente.get('pai_data_nascimento'):
        dia, mes, ano = cliente['pai_data_nascimento'].split('/')
        selecionar_dropdown(page, "select[id$='ddlFathersDOBDay']", dia.zfill(2))
        selecionar_dropdown(page, "select[id$='ddlFathersDOBMonth']", MESES_ABREV[str(int(mes))])
        preencher_texto(page, "input[id$='tbxFathersDOBYear']", ano)
    marcar_sim_nao(page, "rblFATHER_LIVE_IN_US_IND", cliente.get('pai_esta_eua', False))
    time.sleep(1.5)  # rblFATHER_LIVE_IN_US_IND dispara um postback; espera terminar

    nome_mae = cliente.get('mae_nome', '').split()
    preencher_texto(page, "input[id$='tbxMOTHER_GIVEN_NAME']", nome_mae[0] if nome_mae else '')
    preencher_texto(page, "input[id$='tbxMOTHER_SURNAME']", " ".join(nome_mae[1:]) if len(nome_mae) > 1 else '')

    if cliente.get('mae_data_nascimento'):
        dia, mes, ano = cliente['mae_data_nascimento'].split('/')
        selecionar_dropdown(page, "select[id$='ddlMothersDOBDay']", dia.zfill(2))
        selecionar_dropdown(page, "select[id$='ddlMothersDOBMonth']", MESES_ABREV[str(int(mes))])
        preencher_texto(page, "input[id$='tbxMothersDOBYear']", ano)
    marcar_sim_nao(page, "rblMOTHER_LIVE_IN_US_IND", cliente.get('mae_esta_eua', False))
    time.sleep(1.5)  # rblMOTHER_LIVE_IN_US_IND também dispara um postback; espera terminar

    marcar_sim_nao(page, "rblUS_IMMED_RELATIVE_IND", cliente.get('parente_1_grau_eua', False))
    if cliente.get('parente_1_grau_eua'):
        nome_parente = cliente.get('parente_1_grau_nome', '').split()
        preencher_texto(page, "input[id$='dlUSRelatives_ctl00_tbxUS_REL_GIVEN_NAME']", nome_parente[0] if nome_parente else '')
        preencher_texto(page, "input[id$='dlUSRelatives_ctl00_tbxUS_REL_SURNAME']", " ".join(nome_parente[1:]) if len(nome_parente) > 1 else '')

        relacionamento_en = PARENTESCO_US_REL_EN.get(cliente.get('parente_1_grau_relacionamento', ''))
        if relacionamento_en:
            selecionar_dropdown(page, "select[id$='dlUSRelatives_ctl00_ddlUS_REL_TYPE']", relacionamento_en)
        else:
            print(f"⚠️ Grau de parentesco '{cliente.get('parente_1_grau_relacionamento')}' não reconhecido "
                  f"— selecione manualmente em 'Relationship to You'.")

        status_en = traduzir_status_parente_eua(cliente.get('parente_1_grau_status', ''))
        selecionar_dropdown(page, "select[id$='dlUSRelatives_ctl00_ddlUS_REL_STATUS']", status_en)

    # HIPÓTESE ainda não 100% confirmada (diferente das outras
    # condicionais já validadas via listar): em 2026-08-23, com
    # parente_1_grau_eua = Sim, o campo rblUS_OTHER_RELATIVE_IND não
    # apareceu na tela (listar_campos_da_tela não mostrou nenhum
    # elemento com esse id) — parece que o CEAC só pergunta "algum outro
    # parente nos EUA" quando a pergunta anterior (parente imediato) foi
    # "Não". Se um cliente com parente_1_grau_eua=Sim aparecer com esse
    # campo alertando de novo, essa hipótese está errada e precisa ser
    # revista.
    if not cliente.get('parente_1_grau_eua'):
        marcar_sim_nao(page, "rblUS_OTHER_RELATIVE_IND", cliente.get('outro_parente_eua', False))

    print("✅ Página Family Information: Relatives preenchida (confira os avisos ⚠️ acima, se houver)!")


def preencher_family_spouse(page, cliente):
    """Preenche a tela Family Information: Spouse (só roda se estado_civil = MARRIED)"""
    print("\n▶️ Injetando dados na tela Family Information: Spouse...")
    page.set_default_timeout(5000)

    if cliente.get('estado_civil') != 'MARRIED' or not cliente.get('conjuge_nome'):
        print("ℹ️ Cliente não é casado ou não há dados de cônjuge no JSON — pulando.")
        return

    nome_conjuge = cliente['conjuge_nome'].split()
    preencher_texto(page, "input[id$='tbxSpouseGivenName']", nome_conjuge[0])
    preencher_texto(page, "input[id$='tbxSpouseSurname']", " ".join(nome_conjuge[1:]))

    if cliente.get('conjuge_data_nascimento'):
        dia, mes, ano = cliente['conjuge_data_nascimento'].split('/')
        selecionar_dropdown(page, "select[id$='ddlDOBDay']", dia.zfill(2))
        selecionar_dropdown(page, "select[id$='ddlDOBMonth']", MESES_ABREV[str(int(mes))])
        preencher_texto(page, "input[id$='tbxDOBYear']", ano)

    nacionalidade = cliente.get('conjuge_nacionalidade', '')
    nacionalidade_en = "BRAZIL" if "BRASIL" in nacionalidade.upper() else nacionalidade
    selecionar_dropdown(page, "select[id$='ddlSpouseNatDropDownList']", nacionalidade_en)

    local_nasc = cliente.get('conjuge_local_nascimento', '')
    cidade = local_nasc.split(',')[0].strip() if local_nasc else ''
    preencher_texto(page, "input[id$='tbxSpousePOBCity']", cidade)
    selecionar_dropdown(page, "select[id$='ddlSpousePOBCountry']", "BRAZIL")

    # Endereço do cônjuge: "Same as Home Address" quando o rascunho diz que é o
    # mesmo do aplicante; senão seleciona "Other" e preenche endereço completo.
    if cliente.get('conjuge_endereco_mesmo_aplicante', True):
        selecionar_dropdown(page, "select[id$='ddlSpouseAddressType']", "Same as Home Address")
    else:
        selecionar_dropdown(page, "select[id$='ddlSpouseAddressType']", "Other (Specify Address)")
        time.sleep(1.5)  # troca de tipo de endereço dispara postback
        preencher_texto(page, "input[id$='tbxSPOUSE_ADDR_LN1']", cliente.get('conjuge_endereco_linha1', ''))
        preencher_texto(page, "input[id$='tbxSPOUSE_ADDR_LN2']", cliente.get('conjuge_endereco_linha2', ''))
        preencher_texto(page, "input[id$='tbxSPOUSE_ADDR_CITY']", cliente.get('conjuge_endereco_cidade', ''))
        if cliente.get('conjuge_endereco_estado'):
            preencher_texto(page, "input[id$='tbxSPOUSE_ADDR_STATE']", cliente['conjuge_endereco_estado'])
        else:
            marcar_checkbox(page, "input[id$='cbexSPOUSE_ADDR_STATE_NA']", forcar=True)
        if cliente.get('conjuge_endereco_cep'):
            preencher_texto(page, "input[id$='tbxSPOUSE_ADDR_POSTAL_CD']", cliente['conjuge_endereco_cep'])
        else:
            marcar_checkbox(page, "input[id$='cbexSPOUSE_ADDR_POSTAL_CD_NA']", forcar=True)
        selecionar_dropdown(page, "select[id$='ddlSPOUSE_ADDR_CNTRY']", cliente.get('conjuge_endereco_pais', 'BRAZIL'))

    print("✅ Página Family Information: Spouse preenchida (confira os avisos ⚠️ acima, se houver)!")


def preencher_work_education_present(page, cliente):
    """Preenche a tela Present Work/Education/Training"""
    print("\n▶️ Injetando dados na tela Present Work/Education/Training...")
    page.set_default_timeout(5000)

    print(f"⚠️ Confira 'Primary Occupation' — PDF diz 'Ocupação Atual': {cliente.get('ocupacao_atual')} "
          f"(selecionando 'BUSINESS' como padrão, ajuste se não fizer sentido)")
    selecionar_dropdown(page, "select[id$='ddlPresentOccupation']", "BUSINESS")
    time.sleep(1.5)  # ddlPresentOccupation dispara postback (ddlPresentOccupationClicked)

    preencher_texto(page, "input[id$='tbxEmpSchName']", limpar_caracteres_nome(cliente.get('trabalho_empresa_nome', '')))
    preencher_texto(page, "input[id$='tbxEmpSchAddr1']", limpar_caracteres_endereco(cliente.get('trabalho_endereco_linha1', '')))

    cidade_uf_cep = cliente.get('trabalho_endereco_cidade_uf_cep', '')
    match_cuc = re.match(r"^(.*),\s*([^,]+?)\s+(\d{5,9})$", cidade_uf_cep.strip())
    if match_cuc:
        preencher_texto(page, "input[id$='tbxEmpSchCity']", match_cuc.group(1))
        preencher_texto(page, "input[id$='tbxWORK_EDUC_ADDR_STATE']", match_cuc.group(2))
        preencher_texto(page, "input[id$='tbxWORK_EDUC_ADDR_POSTAL_CD']", match_cuc.group(3))
    else:
        preencher_texto(page, "input[id$='tbxEmpSchCity']", cidade_uf_cep)
        print(f"⚠️ Não consegui separar cidade/UF/CEP do trabalho de '{cidade_uf_cep}' — confira State/ZIP.")

    selecionar_dropdown(page, "select[id$='ddlEmpSchCountry']", "BRAZIL")
    preencher_texto(page, "input[id$='tbxWORK_EDUC_TEL']", cliente.get('trabalho_telefone', ''))

    if cliente.get('trabalho_data_inicio'):
        dia, mes, ano = cliente['trabalho_data_inicio'].split('/')
        selecionar_dropdown(page, "select[id$='ddlEmpDateFromDay']", dia.zfill(2))
        selecionar_dropdown(page, "select[id$='ddlEmpDateFromMonth']", MESES_ABREV[str(int(mes))])
        preencher_texto(page, "input[id$='tbxEmpDateFromYear']", ano)

    # Sem dado de salário no PDF -> Does Not Apply
    marcar_checkbox(page, "input[id$='cbxCURR_MONTHLY_SALARY_NA']", forcar=True)

    funcoes = cliente.get('trabalho_funcoes', '')
    if not funcoes or funcoes.strip().lower() == "a confirmar":
        funcoes = "A CONFIRMAR"
    else:
        print("⚠️ 'Briefly describe your duties' foi preenchido em português — traduza para inglês se possível.")
    preencher_texto(page, "[id$='tbxDescribeDuties']", funcoes)

    print("✅ Página Present Work/Education/Training preenchida (confira os avisos ⚠️ acima, se houver)!")


def preencher_work_education_previous(page, cliente):
    """Preenche a tela Previous Work/Education/Training"""
    print("\n▶️ Injetando dados na tela Previous Work/Education/Training...")
    page.set_default_timeout(5000)

    marcar_sim_nao(page, "rblPreviouslyEmployed", cliente.get('trabalhou_outra_empresa_5anos', False))
    if cliente.get('trabalhou_outra_empresa_5anos'):
        preencher_texto(page, "input[id$='dtlPrevEmpl_ctl00_tbEmployerName']", limpar_caracteres_nome(cliente.get('trabalho_anterior_empresa_nome', '')))
        preencher_texto(page, "input[id$='dtlPrevEmpl_ctl00_tbEmployerStreetAddress1']", limpar_caracteres_endereco(cliente.get('trabalho_anterior_endereco_linha1', '')))

        cidade_uf_cep = cliente.get('trabalho_anterior_endereco_cidade_uf_cep', '')
        match_cuc = re.match(r"^(.*),\s*([^,]+?)\s+(\d{5,9})$", cidade_uf_cep.strip())
        if match_cuc:
            preencher_texto(page, "input[id$='dtlPrevEmpl_ctl00_tbEmployerCity']", match_cuc.group(1))
            preencher_texto(page, "input[id$='dtlPrevEmpl_ctl00_tbxPREV_EMPL_ADDR_STATE']", match_cuc.group(2))
            preencher_texto(page, "input[id$='dtlPrevEmpl_ctl00_tbxPREV_EMPL_ADDR_POSTAL_CD']", match_cuc.group(3))
        else:
            preencher_texto(page, "input[id$='dtlPrevEmpl_ctl00_tbEmployerCity']", cidade_uf_cep)
            print(f"⚠️ Não consegui separar cidade/estado/CEP do trabalho anterior de '{cidade_uf_cep}' "
                  f"— confira State/ZIP.")

        selecionar_dropdown(page, "select[id$='dtlPrevEmpl_ctl00_DropDownList2']", "BRAZIL")
        preencher_texto(page, "input[id$='dtlPrevEmpl_ctl00_tbEmployerPhone']", cliente.get('trabalho_anterior_telefone', ''))
        preencher_texto(page, "input[id$='dtlPrevEmpl_ctl00_tbJobTitle']", cliente.get('trabalho_anterior_cargo', ''))

        # PDF não traz nome do supervisor -> "Do Not Know" (cada clique dispara um postback)
        marcar_checkbox(page, "input[id$='dtlPrevEmpl_ctl00_cbxSupervisorSurname_NA']", forcar=True)
        time.sleep(1.5)
        marcar_checkbox(page, "input[id$='dtlPrevEmpl_ctl00_cbxSupervisorGivenName_NA']", forcar=True)
        time.sleep(1.5)

        if cliente.get('trabalho_anterior_data_inicio'):
            dia, mes, ano = cliente['trabalho_anterior_data_inicio'].split('/')
            selecionar_dropdown(page, "select[id$='dtlPrevEmpl_ctl00_ddlEmpDateFromDay']", dia.zfill(2))
            selecionar_dropdown(page, "select[id$='dtlPrevEmpl_ctl00_ddlEmpDateFromMonth']", MESES_ABREV[str(int(mes))])
            preencher_texto(page, "input[id$='dtlPrevEmpl_ctl00_tbxEmpDateFromYear']", ano)

        if cliente.get('trabalho_anterior_data_fim'):
            dia, mes, ano = cliente['trabalho_anterior_data_fim'].split('/')
            selecionar_dropdown(page, "select[id$='dtlPrevEmpl_ctl00_ddlEmpDateToDay']", dia.zfill(2))
            selecionar_dropdown(page, "select[id$='dtlPrevEmpl_ctl00_ddlEmpDateToMonth']", MESES_ABREV[str(int(mes))])
            preencher_texto(page, "input[id$='dtlPrevEmpl_ctl00_tbxEmpDateToYear']", ano)

        funcoes_anteriores = cliente.get('trabalho_anterior_funcoes', '')
        if funcoes_anteriores and funcoes_anteriores.strip().lower() != "a confirmar":
            preencher_texto(page, "[id$='dtlPrevEmpl_ctl00_tbDescribeDuties']", funcoes_anteriores)
            print("⚠️ 'Briefly describe your duties' (trabalho anterior) foi preenchido em português "
                  "— traduza para inglês se possível.")
        else:
            preencher_texto(page, "[id$='dtlPrevEmpl_ctl00_tbDescribeDuties']", "A CONFIRMAR")

    marcar_sim_nao(page, "rblOtherEduc", cliente.get('estudou_nivel_medio_superior', False))

    if cliente.get('estudou_nivel_medio_superior'):
        preencher_texto(page, "input[id$='dtlPrevEduc_ctl00_tbxSchoolName']", cliente.get('instituicao_nome', ''))
        preencher_texto(page, "input[id$='dtlPrevEduc_ctl00_tbxSchoolAddr1']", cliente.get('instituicao_endereco_linha1', ''))

        cidade_uf_cep = cliente.get('instituicao_endereco_cidade_uf_cep', '')
        match_cuc = re.match(r"^(.*),\s*([^,]+?)\s+(\d{5,9})$", cidade_uf_cep.strip())
        if match_cuc:
            preencher_texto(page, "input[id$='dtlPrevEduc_ctl00_tbxSchoolCity']", match_cuc.group(1))
            preencher_texto(page, "input[id$='dtlPrevEduc_ctl00_tbxEDUC_INST_ADDR_STATE']", match_cuc.group(2))
            preencher_texto(page, "input[id$='dtlPrevEduc_ctl00_tbxEDUC_INST_POSTAL_CD']", match_cuc.group(3))
        else:
            preencher_texto(page, "input[id$='dtlPrevEduc_ctl00_tbxSchoolCity']", cidade_uf_cep)
            print(f"⚠️ Não consegui separar cidade/UF/CEP da instituição de '{cidade_uf_cep}' — confira State/ZIP.")

        selecionar_dropdown(page, "select[id$='dtlPrevEduc_ctl00_ddlSchoolCountry']", "BRAZIL")
        preencher_texto(page, "input[id$='dtlPrevEduc_ctl00_tbxSchoolCourseOfStudy']", cliente.get('curso_nome', ''))

        if cliente.get('curso_data_inicio'):
            dia, mes, ano = cliente['curso_data_inicio'].split('/')
            selecionar_dropdown(page, "select[id$='dtlPrevEduc_ctl00_ddlSchoolFromDay']", dia.zfill(2))
            selecionar_dropdown(page, "select[id$='dtlPrevEduc_ctl00_ddlSchoolFromMonth']", MESES_ABREV[str(int(mes))])
            preencher_texto(page, "input[id$='dtlPrevEduc_ctl00_tbxSchoolFromYear']", ano)

        if cliente.get('curso_data_termino'):
            dia, mes, ano = cliente['curso_data_termino'].split('/')
            selecionar_dropdown(page, "select[id$='dtlPrevEduc_ctl00_ddlSchoolToDay']", dia.zfill(2))
            selecionar_dropdown(page, "select[id$='dtlPrevEduc_ctl00_ddlSchoolToMonth']", MESES_ABREV[str(int(mes))])
            preencher_texto(page, "input[id$='dtlPrevEduc_ctl00_tbxSchoolToYear']", ano)

    print("✅ Página Previous Work/Education/Training preenchida (confira os avisos ⚠️ acima, se houver)!")


def preencher_work_education_additional(page, cliente):
    """Preenche a tela Additional Work/Education/Training"""
    print("\n▶️ Injetando dados na tela Additional Work/Education/Training...")
    page.set_default_timeout(5000)

    # Clã ou tribo -> sempre "No"
    marcar_checkbox(page, "input[id$='rblCLAN_TRIBE_IND_1']")

    idiomas_texto = cliente.get('idiomas', '')
    idiomas = [p.strip() for p in idiomas_texto.replace(" e ", ", ").split(",") if p.strip()]
    for indice, idioma in enumerate(idiomas):
        if indice > 0:
            try:
                page.locator("a[id$='InsertButtonLANGUAGE']").last.click(timeout=3000)
                time.sleep(1.5)
            except Exception as e:
                print(f"⚠️ Não consegui clicar em 'Add Another' para o idioma '{idioma}': {e}")
                continue
        prefixo_ctl = f"ctl{indice:02d}"
        preencher_texto(page, f"input[id$='dtlLANGUAGES_{prefixo_ctl}_tbxLANGUAGE_NAME']", idioma)

    marcar_sim_nao(page, "rblCOUNTRIES_VISITED_IND", cliente.get('viajou_ultimos_5_anos', False))
    if cliente.get('viajou_ultimos_5_anos'):
        paises = extrair_lista_paises(cliente.get('paises_visitados_5_anos', ''))
        for indice, pais in enumerate(paises):
            if indice > 0:
                try:
                    page.locator("a[id$='InsertButtonCountriesVisited']").last.click(timeout=3000)
                    time.sleep(1.5)
                except Exception as e:
                    print(f"⚠️ Não consegui clicar em 'Add Another' para o país '{pais}': {e}")
                    continue
            prefixo_ctl = f"ctl{indice:02d}"
            selecionar_dropdown(page, f"select[id$='dtlCountriesVisited_{prefixo_ctl}_ddlCOUNTRIES_VISITED']", pais)

    # Organização profissional/social/beneficente -> sem dado no PDF, padrão "No"
    marcar_sim_nao(page, "rblORGANIZATION_IND", False)

    marcar_sim_nao(page, "rblSPECIALIZED_SKILLS_IND", cliente.get('treinamento_arma', False))
    if cliente.get('treinamento_arma') and cliente.get('treinamento_arma_detalhe'):
        time.sleep(0.5)  # ShowHideDiv precisa revelar o campo 'Explain' antes de preencher
        preencher_texto(page, "[id$='tbxSPECIALIZED_SKILLS_EXPL']", cliente['treinamento_arma_detalhe'])

    marcar_sim_nao(page, "rblMILITARY_SERVICE_IND", cliente.get('serviu_exercito', False))
    if cliente.get('serviu_exercito'):
        print("⚠️ Cliente serviu ao exército, mas o PDF não traz os detalhes (país/ramo/posto/datas) "
              "— preencha manualmente os campos 'Provide the following information'.")

    # Grupo paramilitar/insurgente -> sem dado no PDF, padrão "No"
    marcar_sim_nao(page, "rblINSURGENT_ORG_IND", False)

    print("✅ Página Additional Work/Education/Training preenchida (confira os avisos ⚠️ acima, se houver)!")


SEGURANCA_ID_MAP = {
    # chave no JSON -> prefixo do id do radio no site (só perguntas já confirmadas no site real)
    "doenca_transmissivel": "rblDisease",
    "disturbio_mental_fisico": "rblDisorder",
    "usuario_drogas": "rblDruguser",
}


def preencher_seguranca_confirmada(page, cliente, chaves):
    """Marca 'No' nas perguntas de segurança já confirmadas no site (SEGURANCA_ID_MAP)
    e cuja resposta no PDF é 'Não'. Se a resposta for 'Sim', NÃO clica — avisa pra
    marcar manualmente e escrever a explicação (decisão deliberada, ver revisar_seguranca)."""
    seguranca = cliente.get('seguranca', {})
    for chave in chaves:
        prefixo = SEGURANCA_ID_MAP[chave]
        valor = seguranca.get(chave, False)
        if valor:
            print(f"🔴 ATENÇÃO: '{chave}' = SIM no PDF — marque 'Yes' manualmente e escreva a "
                  f"explicação no campo de texto associado a '{prefixo}'.")
        else:
            marcar_sim_nao(page, prefixo, False)


def preencher_seguranca_parte1(page, cliente):
    """Preenche a tela Security and Background: Part 1"""
    print("\n▶️ Injetando dados na tela Security and Background: Part 1...")
    page.set_default_timeout(5000)

    preencher_seguranca_confirmada(
        page, cliente, ["doenca_transmissivel", "disturbio_mental_fisico", "usuario_drogas"]
    )

    print("✅ Página Security and Background: Part 1 preenchida (confira os avisos acima, se houver)!")


def revisar_seguranca(cliente):
    """
    Checklist final das perguntas de Segurança e Histórico. As perguntas já
    mapeadas em SEGURANCA_ID_MAP são preenchidas automaticamente (quando a
    resposta é 'Não') pelas telas preencher_seguranca_parteN; as demais (ainda
    não mapeadas, ou com resposta 'Sim') aparecem aqui pra conferência manual.
    """
    print("\n" + "=" * 60)
    print("🛑 CHECKLIST FINAL — SEGURANÇA E HISTÓRICO")
    print("=" * 60)

    seguranca = cliente.get('seguranca', {})
    pendentes = {
        chave: valor for chave, valor in seguranca.items()
        if valor or chave not in SEGURANCA_ID_MAP
    }
    if pendentes:
        print("Confira estas perguntas manualmente (resposta 'Sim' ou pergunta ainda não")
        print("mapeada nos ids do site):\n")
        for chave, valor in pendentes.items():
            resposta = "SIM ⚠️" if valor else "Não (ainda não automatizado)"
            print(f"  - {chave}: {resposta}")
    else:
        print("Todas as perguntas de segurança já mapeadas foram preenchidas automaticamente.")

    if any(seguranca.values()):
        print("\n🔴 ATENÇÃO: há pelo menos uma resposta 'Sim' na lista acima.")
        print("   Preencha a explicação com cuidado — recomenda-se revisão humana/jurídica.")


def gravar_application_id_no_flow(flow_cliente_id, application_id):
    """Manda o Application ID (número do DS-160) direto pra ficha do
    cliente no Flow, evitando digitação manual depois. Não derruba o
    robô se falhar — só avisa, já que o preenchimento em si já terminou
    nesse ponto."""
    if not ROBO_API_SECRET:
        print("⚠️ ROBO_API_SECRET não configurada — não deu pra gravar automaticamente no Flow. "
              f"Anote manualmente: Application ID = {application_id}")
        return
    try:
        resp = requests.post(
            f"{FLOW_API_URL}/api/robo-integracao/atualizar-ds160",
            headers={"Authorization": f"Bearer {ROBO_API_SECRET}"},
            json={"clienteId": flow_cliente_id, "numeroDs160": application_id},
            timeout=15,
        )
        if resp.status_code == 200:
            print("✅ Application ID gravado no Flow com sucesso.")
        else:
            print(f"⚠️ Flow respondeu {resp.status_code} ao gravar o Application ID: {resp.text}")
            print(f"   Anote manualmente: Application ID = {application_id}")
    except requests.RequestException as e:
        print(f"⚠️ Não consegui falar com o Flow ({e}). Anote manualmente: Application ID = {application_id}")


def capturar_application_id(cliente):
    """Pergunta ao operador o Application ID depois que ELE MESMO enviar o
    DS-160 oficialmente no CEAC (o robô nunca envia sozinho — regra de
    segurança inegociável do projeto). Se o cliente estiver ligado a uma
    ficha no Flow, grava lá automaticamente."""
    print("\n" + "=" * 60)
    print("📋 Depois de você mesmo revisar e ENVIAR o DS-160 no site do CEAC,")
    print("   cole aqui o Application ID que aparece na tela de confirmação.")
    print("=" * 60)
    application_id = input("Application ID (ou ENTER para pular): ").strip()
    if not application_id:
        return

    flow_cliente_id = cliente.get("_flow_cliente_id")
    if flow_cliente_id:
        gravar_application_id_no_flow(flow_cliente_id, application_id)
    else:
        print("⚠️ Esse cliente não está ligado a nenhuma ficha do Flow "
              f"(flowClienteId vazio) — anote manualmente: Application ID = {application_id}")


# Ids que aparecem em TODA tela do CEAC (menu lateral, botões do site,
# mecanismo interno do ASP.NET, resquício de widget de tradução do
# Google) — nunca são campos de dado de verdade, só atrapalham a leitura
# do "listar". Filtrados por prefixo/sufixo de id em vez de nome exato,
# porque o id completo muda (tem o ctl00_SiteContentPlaceHolder... na
# frente).
_RUIDO_ID_SUFIXOS = (
    "Hidden1", "__PREVIOUSPAGE", "ddlLanguage", "HDClearSession", "HiddenPageValid",
    "HiddenSideBarItemClicked", "__LASTFOCUS", "__EVENTTARGET", "__EVENTARGUMENT",
    "__VIEWSTATE", "__VIEWSTATEGENERATOR", "__SCROLLPOSITIONX", "__SCROLLPOSITIONY",
    "__VIEWSTATEENCRYPTED", "UpdateButton1", "UpdateButton2", "UpdateButton3",
    "btnModalHolder", "btnReviewPage", "btnNextPageComplete", "btnWarning",
    "btnRecover", "btnOkWarning", "btnCancelWarning", "btnClientExit",
    "btnCancelExitWarning", "ddlSite", "btnChangeSite", "btnCancel",
)
_RUIDO_LINK_IDS = (
    "lbtnContactUs", "lbtnHelp", "lbtnExit", "COMPLETE", "REVIEW", "ESIGN",
    "GetStarted", "Personal", "Travel", "TravelCompanions", "PreviousUSTravel",
    "AddressPhone", "PptVisa", "USContact", "Family", "WorkEducationMain",
    "SecAndBackMain", "hplCopyright", "hplDisclaimers", "hplPaperworkReduction",
    "hplFbiPrivacyAct",
)


def listar_campos_da_tela(page):
    """Lista todo input/select/textarea/link visível na tela atual — id,
    tipo, name e (pra radio/checkbox) se está marcado; pra links, o texto
    visível (ex.: 'Add Another'). Serve pra mapear uma tela inteira de
    uma vez (digite 'listar' em qualquer prompt) em vez de abrir o F12 e
    inspecionar campo por campo. Se der '0 campos', é porque a página
    estava no meio de um postback nesse instante — digita 'listar' de
    novo que resolve.

    Filtra o "ruído" que aparece em toda tela do CEAC (menu lateral,
    botões do site, campos internos do ASP.NET, resquício de widget de
    tradução do Google) — só mostra o que pode ser um campo de dado de
    verdade dessa tela específica."""
    elementos = page.locator("input, select, textarea").all()
    linhas = []
    for el in elementos:
        try:
            tipo = el.evaluate("e => e.tagName.toLowerCase() === 'select' ? 'select' : (e.tagName.toLowerCase() === 'textarea' ? 'textarea' : e.type)")
            id_attr = el.get_attribute("id") or ""
            name_attr = el.get_attribute("name") or ""
            if not id_attr and not name_attr:
                continue
            if tipo == "hidden" or id_attr.startswith("goog-gt-") or id_attr.endswith(_RUIDO_ID_SUFIXOS):
                continue
            extra = ""
            if tipo in ("radio", "checkbox"):
                extra = " [MARCADO]" if el.is_checked() else ""
            if tipo == "select":
                opcoes = el.evaluate(
                    "e => Array.from(e.options).map(o => o.value + ' | ' + o.text)"
                )
                extra = "\n         opções: " + " ; ".join(opcoes)
            linhas.append(f"   [{tipo}] id={id_attr!r} name={name_attr!r}{extra}")
        except Exception:
            continue
    print(f"\n📋 {len(linhas)} campo(s) encontrados na tela atual:")
    for linha in linhas:
        print(linha)

    links = page.locator("a").all()
    links_com_texto = []
    for el in links:
        try:
            texto_link = (el.inner_text() or "").strip()
            id_attr = el.get_attribute("id") or ""
            if texto_link and id_attr and not id_attr.endswith(_RUIDO_LINK_IDS):
                links_com_texto.append((texto_link, id_attr))
        except Exception:
            continue
    if links_com_texto:
        print(f"🔗 {len(links_com_texto)} link(s) com texto:")
        for texto_link, id_attr in links_com_texto:
            print(f"   [link {texto_link!r}] id={id_attr!r}")


def preencher_ds160():
    cliente = carregar_dados()
    if not cliente:
        return

    print("Iniciando o motor do Playwright...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=100)
        page = browser.new_page()

        page.goto("https://ceac.state.gov/GenNIV/Default.aspx")
        page.locator("select[id$='Location']").select_option(label="BRAZIL, SAO PAULO")

        print("\n" + "=" * 60)
        print("⏸️ PAUSA NO ROBÔ. FAÇA O PROCESSO INICIAL MANUALMENTE.")
        print("=" * 60 + "\n")

        etapas = [
            ("Personal Information 1", preencher_pagina_1),
            ("Personal Information 2", preencher_pagina_2),
            ("Travel Information", preencher_travel),
            ("Travel Companions", preencher_travel_companions),
            ("Previous U.S. Travel", preencher_previous_travel),
            ("Address and Phone", preencher_address_phone),
            ("Passport Information", preencher_passport),
            ("U.S. Contact", preencher_us_contact),
            ("Family Information: Relatives", preencher_family_relatives),
            ("Family Information: Spouse", preencher_family_spouse),
            ("Present Work/Education/Training", preencher_work_education_present),
            ("Previous Work/Education/Training", preencher_work_education_previous),
            ("Additional Work/Education/Training", preencher_work_education_additional),
            ("Security and Background: Part 1", preencher_seguranca_parte1),
        ]

        for nome_tela, funcao in etapas:
            _perguntar(page, f"👉 Quando estiver na tela '{nome_tela}', aperte ENTER aqui (ou 'listar')... ")

            while True:
                try:
                    funcao(page, cliente)
                except Exception as e:
                    print(f"\n❌ ERRO inesperado na tela '{nome_tela}': {e}")
                    print("   Preencha o que faltar manualmente e siga em frente.")
                revisar_pendencias_tela(page, nome_tela)
                resposta = _perguntar(
                    page,
                    "👉 Tela concluída. ENTER pra ir pra próxima, 'repetir' pra rodar essa mesma "
                    "tela de novo, ou 'listar'... ",
                )
                if resposta != "repetir":
                    break
            print("\n👉 Agora clique em 'Next' até chegar na próxima tela do fluxo.")

        revisar_seguranca(cliente)

        input("\n👉 Preenchimento concluído até o momento! Revise tudo, envie o DS-160 você "
              "mesmo no site e aperte ENTER aqui pra registrar o Application ID.")
        capturar_application_id(cliente)

        input("\n👉 Aperte ENTER para encerrar a automação e fechar o navegador.")
        browser.close()


if __name__ == "__main__":
    preencher_ds160()

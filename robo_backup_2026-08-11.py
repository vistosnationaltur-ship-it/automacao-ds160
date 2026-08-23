import json
import re
import time
import unicodedata
from playwright.sync_api import sync_playwright

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

RELACIONAMENTO_EN = {
    "Cônjuge": "SPOUSE",
    "Filho(a)": "CHILD",
    "Pai": "PARENT",
    "Mãe": "PARENT",
    "Irmão(a)": "OTHER RELATIVE",
    "Amigo(a)": "FRIEND",
    "Colega de Trabalho": "BUSINESS ASSOCIATE",
    "Outro Parente": "OTHER RELATIVE",
    "Outro": "OTHER",
}

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


def preencher_texto(page, seletor, valor):
    """Preenche um campo de texto. Se o campo não existir/não estiver
    visível, avisa e segue em frente (não derruba a tela inteira)."""
    if valor is None or valor == "":
        return
    valor_sem_acento = remover_acentos(valor)
    try:
        page.locator(seletor).fill(valor_sem_acento, timeout=3000)
    except Exception as e:
        print(f"⚠️ Não consegui preencher '{seletor}' com '{valor_sem_acento}' — preencha manualmente. ({e})")


def selecionar_dropdown(page, seletor, valor):
    """Tenta selecionar uma opção de <select> testando por label e por value,
    com e sem zero à esquerda (o DS-160 varia isso dependendo do campo)."""
    if not valor:
        return
    loc = page.locator(seletor)
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
    print(f"⚠️ Não consegui selecionar '{valor}' em '{seletor}' — selecione manualmente essa opção.")


def marcar_checkbox(page, seletor, forcar=False):
    """Clica num checkbox/radio. Se não encontrar, avisa e segue em frente."""
    try:
        page.locator(seletor).click(force=forcar, timeout=3000)
    except Exception as e:
        print(f"⚠️ Não consegui clicar em '{seletor}' — marque manualmente. ({e})")


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
        match_cuc = re.match(r"^(.*),\s*([A-Z]{2})\s+(\d+)$", cidade_uf_cep.strip())
        if match_cuc:
            preencher_texto(page, "input[id$='tbxPayerCity']", match_cuc.group(1))
            preencher_texto(page, "input[id$='tbxPayerStateProvince']", match_cuc.group(2))
            preencher_texto(page, "input[id$='tbxPayerPostalZIPCode']", match_cuc.group(3))
        else:
            print(f"⚠️ Não consegui separar cidade/UF/CEP de '{cidade_uf_cep}' — preencha manualmente.")

        selecionar_dropdown(page, "select[id$='ddlPayerCountry']", "BRAZIL")

    elif cliente.get('quem_paga') == 'O':
        print("⚠️ Pagador é 'Other Person', mas o leitor_pdf.py não extrai nome/telefone/relação")
        print("   dessa pessoa — preencha manualmente os campos do pagador.")

    print("✅ Página Travel Information preenchida (confira os avisos ⚠️ acima, se houver)!")


def preencher_travel_companions(page, cliente):
    """Preenche a tela Travel Companions"""
    print("\n▶️ Injetando dados na tela Travel Companions...")
    page.set_default_timeout(5000)

    marcar_sim_nao(page, "rblOtherPersonsTravelingWithYou", cliente.get('viaja_com_alguem', False))

    # Viajando em grupo/organização -> sempre "No" (não temos esse dado no PDF)
    marcar_checkbox(page, "input[id$='rblGroupTravel_1']")

    companheiros = cliente.get('companheiros_viagem', [])
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
    marcar_sim_nao(page, "rblPREV_US_DRIVER_LIC_IND", cliente.get('carteira_habilitacao_eua', False))
    marcar_sim_nao(page, "rblPREV_VISA_IND", cliente.get('ja_teve_visto_eua', False))
    marcar_sim_nao(page, "rblPREV_VISA_CANCELLED_IND", cliente.get('visto_cancelado_revogado', False))
    marcar_sim_nao(page, "rblIV_PETITION_IND", cliente.get('peticao_imigrante', False))

    # Não vem do PDF — sem dado extraído, o padrão é sempre "No"
    marcar_sim_nao(page, "rblPREV_VISA_REFUSED_IND", False)

    if cliente.get('ja_esteve_eua'):
        print("⚠️ Cliente já esteve nos EUA — preencha manualmente a data de chegada e o")
        print("   tempo de permanência da visita anterior (não extraído do PDF).")

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
    if cliente.get('telefone_secundario'):
        preencher_texto(page, "input[id$='tbxAPP_MOBILE_TEL']", cliente['telefone_secundario'])
    else:
        marcar_checkbox(page, "input[id$='cbexAPP_MOBILE_TEL_NA']", forcar=True)
    if cliente.get('telefone_comercial'):
        preencher_texto(page, "input[id$='tbxAPP_BUS_TEL']", cliente['telefone_comercial'])
    else:
        marcar_checkbox(page, "input[id$='cbexAPP_BUS_TEL_NA']", forcar=True)

    # Não vem do PDF -> padrão "No"
    marcar_sim_nao(page, "rblAddPhone", False)

    preencher_texto(page, "input[id$='tbxAPP_EMAIL_ADDR']", cliente.get('email', ''))

    # Não vem do PDF -> padrão "No"
    marcar_sim_nao(page, "rblAddEmail", False)

    # Sem indicação de mídia social no PDF (Nenhuma) -> seleciona "NONE" na plataforma
    if not cliente.get('tem_midia_social', False):
        selecionar_dropdown(page, "select[id$='ddlSocialMedia']", "NONE")
    else:
        print(f"⚠️ Cliente tem mídia social — preencha manualmente (plataforma/identificador), "
              f"o PDF não traz qual rede/usuário.")

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
    """Preenche a tela U.S. Point of Contact — dados padronizados (mesmo esquema
    Hotel/Orlando/Florida usado na Travel Information), iguais para todo cliente."""
    print("\n▶️ Injetando dados na tela U.S. Contact...")
    page.set_default_timeout(5000)

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
        print("⚠️ Cliente tem parente de 1º grau nos EUA, mas o PDF só traz a resposta Sim/Não,")
        print("   não o nome/relação/status — preencha os campos do parente manualmente.")

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

    # Endereço do cônjuge: "Same as Home Address" quando o PDF diz que é o mesmo do aplicante;
    # senão seleciona "Other" e avisa pra preencher manualmente (o PDF não traz endereço estruturado).
    if cliente.get('conjuge_endereco_mesmo_aplicante', True):
        selecionar_dropdown(page, "select[id$='ddlSpouseAddressType']", "Same as Home Address")
    else:
        selecionar_dropdown(page, "select[id$='ddlSpouseAddressType']", "Other (Specify Address)")
        time.sleep(1.5)  # troca de tipo de endereço dispara postback
        print(f"⚠️ Endereço do cônjuge é diferente do aplicante "
              f"('{cliente.get('conjuge_endereco_texto')}') — preencha manualmente "
              f"Street Address/City/State/ZIP/Country.")

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
    match_cuc = re.match(r"^(.*),\s*([A-Z]{2})\s+(\d+)$", cidade_uf_cep.strip())
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

    preencher_texto(page, "input[id$='tbxDescribeDuties']", cliente.get('trabalho_funcoes') or "A CONFIRMAR")
    print("⚠️ 'Briefly describe your duties' foi preenchido em português — traduza para inglês se possível.")

    print("✅ Página Present Work/Education/Training preenchida (confira os avisos ⚠️ acima, se houver)!")


def preencher_work_education_previous(page, cliente):
    """Preenche a tela Previous Work/Education/Training"""
    print("\n▶️ Injetando dados na tela Previous Work/Education/Training...")
    page.set_default_timeout(5000)

    marcar_sim_nao(page, "rblPreviouslyEmployed", cliente.get('trabalhou_outra_empresa_5anos', False))
    if cliente.get('trabalhou_outra_empresa_5anos'):
        print("⚠️ Cliente trabalhou em outra empresa nos últimos 5 anos, mas o PDF não traz os detalhes "
              "(nome/endereço/telefone/cargo/supervisor/datas) — preencha manualmente os campos "
              "'Employer/Employment Information'.")

    marcar_sim_nao(page, "rblOtherEduc", cliente.get('estudou_nivel_medio_superior', False))

    if cliente.get('estudou_nivel_medio_superior'):
        preencher_texto(page, "input[id$='dtlPrevEduc_ctl00_tbxSchoolName']", cliente.get('instituicao_nome', ''))
        preencher_texto(page, "input[id$='dtlPrevEduc_ctl00_tbxSchoolAddr1']", cliente.get('instituicao_endereco_linha1', ''))

        cidade_uf_cep = cliente.get('instituicao_endereco_cidade_uf_cep', '')
        match_cuc = re.match(r"^(.*),\s*([A-Z]{2})\s+(\d+)$", cidade_uf_cep.strip())
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
        preencher_texto(page, "input[id$='tbxSPECIALIZED_SKILLS_EXPL']", cliente['treinamento_arma_detalhe'])

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
            input(f"👉 Quando estiver na tela '{nome_tela}', aperte ENTER aqui... ")
            try:
                funcao(page, cliente)
            except Exception as e:
                print(f"\n❌ ERRO inesperado na tela '{nome_tela}': {e}")
                print("   Preencha o que faltar manualmente e siga em frente.")
            print("\n👉 Agora clique em 'Next' até chegar na próxima tela do fluxo.")

        revisar_seguranca(cliente)

        input("\n👉 Preenchimento concluído até o momento! Aperte ENTER para encerrar a automação e fechar o navegador.")
        browser.close()


if __name__ == "__main__":
    preencher_ds160()

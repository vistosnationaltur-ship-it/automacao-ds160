import pdfplumber
import glob
import json
import os
import re
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

SENHA_PDF = "form2n"


def extrair_texto_pdf(caminho_arquivo):
    if not os.path.exists(caminho_arquivo):
        print(f"❌ Erro: O arquivo '{caminho_arquivo}' não foi encontrado.")
        return None

    texto_completo = ""
    try:
        with pdfplumber.open(caminho_arquivo, password=SENHA_PDF) as pdf:
            for page in pdf.pages:
                texto_completo += page.extract_text() + "\n"
        return texto_completo
    except Exception as e:
        print(f"❌ Erro ao ler PDF: {e}")
        return None

def limpar_valor(valor):
    return valor.strip() if valor else "a confirmar"

def traduzir_mes(mes_numero):
    meses = {
        "01": "JAN", "02": "FEB", "03": "MAR", "04": "APR",
        "05": "MAY", "06": "JUN", "07": "JUL", "08": "AUG",
        "09": "SEP", "10": "OCT", "11": "NOV", "12": "DEC"
    }
    return meses.get(mes_numero, "JAN")

def traduzir_mes_numero(mes_texto):
    meses_num = {
        "JAN": "1", "FEB": "2", "MAR": "3", "APR": "4",
        "MAY": "5", "JUN": "6", "JUL": "7", "AUG": "8",
        "SEP": "9", "OCT": "10", "NOV": "11", "DEC": "12"
    }
    return meses_num.get(mes_texto.upper(), "1")

def traduzir_estado_civil(status_br):
    status = status_br.upper()
    if "CASADO" in status: return "MARRIED"
    if "SOLTEIRO" in status: return "SINGLE"
    if "DIVORCIADO" in status: return "DIVORCED"
    if "VIÚVO" in status or "VIUVO" in status: return "WIDOWED"
    return "OTHER/NON-APPLICABLE"

def mapear_dados_pagina_1(texto):
    dados = {
        "tem_telecode": False,
        "outro_sobrenome": "",
        "outro_nome": "",
        "dob_dia": "1",
        "dob_mes": "JAN",
        "dob_ano": "2000",
        "cidade_nascimento": "a confirmar",
        "estado_nascimento": "a confirmar",
        "estado_nascimento_na": True,
        "pais_nascimento": "BRAZIL",
        "cpf": "",
        "outra_nacionalidade": False,
        "residente_permanente": False,
        "travel_dia": "1",
        "travel_mes": "1",
        "travel_ano": "2026",
        "travel_tempo": "1",
        "quem_paga": "S"
    }

    # 1. Nome Completo (o cabeçalho varia entre versões do rascunho)
    match_nome = re.search(
        r"(?:NOME COMPLETO DE ACORDO COM O PASSAPORTE|NOME E SOBRENOME ATUAIS)\s*\n([^\n]+)",
        texto
    )
    if match_nome:
        nome_limpo = limpar_valor(match_nome.group(1))
        if "VOCÊ" in nome_limpo.upper() or "USOU" in nome_limpo.upper():
            dados["nome"], dados["sobrenome"] = "a confirmar", "a confirmar"
        else:
            nome_completo = nome_limpo.split()
            dados["nome"] = nome_completo[0] if len(nome_completo) > 0 else "a confirmar"
            dados["sobrenome"] = " ".join(nome_completo[1:]) if len(nome_completo) > 1 else "a confirmar"
    else:
        dados["nome"], dados["sobrenome"] = "a confirmar", "a confirmar"

    if dados["nome"] != "a confirmar" and dados["sobrenome"] != "a confirmar":
        dados["nome_nativo_na"] = False
        dados["nome_nativo"] = f"{dados['nome']} {dados['sobrenome']}"
    else:
        dados["nome_nativo_na"] = True

    # 2. Outros Nomes (o cabeçalho varia entre versões do rascunho)
    match_outros = re.search(
        r"(?:VOCÊ JÁ USOU OUTROS NOMES|VOCÊ JÁ TEVE OUTROS NOMES)[\s\S]{1,120}?(Sim|Não|SIM|NÃO|NAO)",
        texto, re.IGNORECASE
    )
    if match_outros and match_outros.group(1).upper() == "SIM":
        dados["usou_outros_nomes"] = True
        match_outro_nome_txt = re.search(
            r"OUTRO NOME E SOBRENOME UTILIZADOS ANTES DO ATUAL\s*\n([^\n]+)", texto
        )
        if match_outro_nome_txt:
            partes_outro = limpar_valor(match_outro_nome_txt.group(1)).split()
            dados["outro_nome"] = partes_outro[0] if partes_outro else "a confirmar"
            dados["outro_sobrenome"] = " ".join(partes_outro[1:]) if len(partes_outro) > 1 else "a confirmar"
        else:
            dados["outro_sobrenome"] = "a confirmar"
            dados["outro_nome"] = "a confirmar"
    else:
        dados["usou_outros_nomes"] = False
        dados["outro_sobrenome"] = ""
        dados["outro_nome"] = ""

    # 3. Sexo
    match_sexo = re.search(r"SEXO[\s\S]{1,60}?(Masculino|Feminino|MASCULINO|FEMININO)", texto, re.IGNORECASE)
    if match_sexo and "MASCULINO" in match_sexo.group(1).upper():
        dados["sexo"] = "MALE"
    else:
        dados["sexo"] = "FEMALE"

    # 4. Estado Civil
    match_civil = re.search(r"ESTADO CIVIL[\s\S]{1,60}?(Casado|Solteiro|Divorciado|Viúvo|Viuvo|CASADO|SOLTEIRO)", texto, re.IGNORECASE)
    if match_civil:
        dados["estado_civil"] = traduzir_estado_civil(limpar_valor(match_civil.group(1)))

    # 5. Data de Nascimento — o PDF junta os cabeçalhos "SEXO ESTADO CIVIL DATA DE
    # NASCIMENTO" numa linha e os valores "Feminino Casado 15/09/1963" na linha
    # seguinte, então a data pode vir depois de outro texto na mesma linha.
    match_data = re.search(r"DATA DE NASCIMENTO\s*\n[^\n]*?(\d{2}/\d{2}/\d{4})", texto)
    if match_data:
        partes_data = match_data.group(1).split('/')
        dados["dob_dia"] = partes_data[0].lstrip('0')
        dados["dob_mes"] = traduzir_mes(partes_data[1])
        dados["dob_ano"] = partes_data[2]

    # 6 e 7. Local de Nascimento — a linha vem como "<CIDADE> <ESTADO (sigla)> <PAÍS>"
    # tudo junto; separar de trás pra frente (país, depois estado) evita pegar uma sigla
    # de 2 letras que apareça no meio do nome da cidade (ex.: "SAO BERNARDO DO CAMPO").
    match_local_nasc = re.search(r"CIDADE DE NASCIMENTO[^\n]*\n([^\n]+)", texto)
    if match_local_nasc:
        linha = limpar_valor(match_local_nasc.group(1))
        if "NACIONALIDADE" not in linha.upper() and "ESTADO" not in linha.upper():
            tokens = linha.split()
            if tokens and tokens[-1].upper() in ("BRASIL", "BRAZIL"):
                resto = tokens[:-1]
                # a sigla do estado às vezes vem como "Sp" (só a 1ª letra maiúscula)
                # em vez de "SP", então comparamos em maiúsculas em vez de usar isupper()
                if resto and len(resto[-1]) == 2 and resto[-1].isalpha():
                    dados["estado_nascimento"] = resto[-1].upper()
                    dados["estado_nascimento_na"] = False
                    dados["cidade_nascimento"] = " ".join(resto[:-1]) if len(resto) > 1 else "a confirmar"
                else:
                    dados["cidade_nascimento"] = " ".join(resto) if resto else "a confirmar"
            else:
                dados["cidade_nascimento"] = linha

    # 8. CPF
    match_cpf = re.search(r"INFORME O NÚMERO DO SEU CPF\s*\n([0-9.\-]+)", texto)
    if match_cpf:
        dados["cpf"] = re.sub(r'[^0-9]', '', match_cpf.group(1))

    # 9. Outra Nacionalidade
    match_outra_nac = re.search(r"POSSUI OUTRA NACIONALIDADE[^\n]*\n([^\n]+)", texto, re.IGNORECASE)
    if match_outra_nac and "SIM" in match_outra_nac.group(1).upper():
        dados["outra_nacionalidade"] = True
        match_outra_nac_pais = re.search(r"INFORME SUA OUTRA NACIONALIDADE\s*\n([^\n]+)", texto)
        dados["outra_nacionalidade_pais"] = limpar_valor(match_outra_nac_pais.group(1)) if match_outra_nac_pais else "a confirmar"

        dados["outra_nacionalidade_tem_passaporte"] = bool(
            extrair_resposta_apos(texto, "POSSUI UM PASSAPORTE PARA ESSA OUTRA NACIONALIDADE")
        )
        if dados["outra_nacionalidade_tem_passaporte"]:
            match_outra_nac_ppt = re.search(r"INFORME O NÚMERO DESSE PASSAPORTE\s*\n([^\n]+)", texto)
            dados["outra_nacionalidade_passaporte_numero"] = (
                limpar_valor(match_outra_nac_ppt.group(1)) if match_outra_nac_ppt else "a confirmar"
            )

    # 10. Residente Permanente
    match_residente = re.search(r"RESIDENTE PERMANENTE[^\n]*\n([^\n]+)", texto, re.IGNORECASE)
    if match_residente and "SIM" in match_residente.group(1).upper():
        dados["residente_permanente"] = True

    # --- 11. DADOS DA TRAVEL INFORMATION ---
    # Data Prevista da Viagem aos EUA
    match_data_viagem = re.search(r"DATA PREVISTA DA VIAGEM AOS EUA\s*\n([0-9/]+)", texto)
    if match_data_viagem:
        partes_viagem = match_data_viagem.group(1).split('/')
        dados["travel_dia"] = partes_viagem[0].lstrip('0')
        mes_abr = traduzir_mes(partes_viagem[1])
        dados["travel_mes"] = traduzir_mes_numero(mes_abr)
        dados["travel_ano"] = partes_viagem[2]

    # Tempo de Permanência nos EUA (Extrai apenas o número antes de "dias")
    match_tempo = re.search(r"TEMPO DE PERMANÊNCIA NOS EUA\s*\n([0-9]+)", texto)
    if match_tempo:
        dados["travel_tempo"] = match_tempo.group(1)

    # Categoria de visto, hospedagem e cidade de destino
    match_visto = re.search(r"CATEGORIA DE VISTO\s*\n([^\n]+)", texto)
    dados["categoria_visto"] = limpar_valor(match_visto.group(1)) if match_visto else "a confirmar"

    match_hospedagem = re.search(r"INFORME ONDE FICARÁ NOS EUA\s*\n([^\n]+)", texto)
    dados["onde_ficara"] = limpar_valor(match_hospedagem.group(1)) if match_hospedagem else "a confirmar"

    match_cidade_destino = re.search(r"QUAL CIDADE FICARÁ NOS ESTADOS UNIDOS[^\n]*\n([^\n]+)", texto)
    dados["cidade_destino_eua"] = limpar_valor(match_cidade_destino.group(1)) if match_cidade_destino else "a confirmar"

    # Quem vai pagar a viagem
    match_pagador = re.search(r"QUEM VAI PAGAR A SUA VIAGEM\?\s*\n([^\n]+)", texto)
    if match_pagador:
        texto_pagador = match_pagador.group(1).strip().upper()
        palavras_self = ["SELF", "PRÓPRIO", "EU MESMO", "MINHAS DESPESAS", "EU IREI PAGAR"]
        if "OUTRA EMPRESA" in texto_pagador or "COMPANY" in texto_pagador:
            dados["quem_paga"] = "C"
        elif any(p in texto_pagador for p in palavras_self):
            dados["quem_paga"] = "S"
        elif "OTHER PERSON" in texto_pagador or "OUTRA PESSOA" in texto_pagador:
            dados["quem_paga"] = "O"
        else:
            print(f"⚠️ Não reconheci quem paga a viagem: '{texto_pagador}' — confira o campo "
                  f"'quem_paga' no JSON manualmente (mantive o valor padrão 'S').")

    # Dados da empresa pagadora (só existem quando quem_paga == "C")
    if dados["quem_paga"] == "C":
        match_emp_nome = re.search(r"NOME DA EMPRESA\s*\n([^\n]+)", texto)
        dados["pagador_empresa_nome"] = limpar_valor(match_emp_nome.group(1)) if match_emp_nome else "a confirmar"

        match_emp_tel = re.search(r"NÚMERO DE TELEFONE DA EMPRESA COM DDD\s*\n([0-9]+)", texto)
        dados["pagador_empresa_telefone"] = match_emp_tel.group(1) if match_emp_tel else ""

        match_emp_rel = re.search(r"QUAL O RELACIONAMENTO DA EMPRESA COM VOCÊ\?\s*\n([^\n]+)", texto)
        dados["pagador_empresa_relacionamento"] = limpar_valor(match_emp_rel.group(1)) if match_emp_rel else "a confirmar"

        match_emp_end = re.search(
            r"ENDEREÇO COMPLETO DA EMPRESA\s*\n([^\n]+)\n([^\n]+)\n([^\n]+)\n([^\n]+)",
            texto
        )
        if match_emp_end:
            dados["pagador_empresa_endereco_linha1"] = limpar_valor(match_emp_end.group(1))
            dados["pagador_empresa_endereco_bairro"] = limpar_valor(match_emp_end.group(2))
            dados["pagador_empresa_endereco_cidade_uf_cep"] = limpar_valor(match_emp_end.group(3))
            dados["pagador_empresa_endereco_pais"] = limpar_valor(match_emp_end.group(4))
        else:
            dados["pagador_empresa_endereco_linha1"] = "a confirmar"
            dados["pagador_empresa_endereco_bairro"] = "a confirmar"
            dados["pagador_empresa_endereco_cidade_uf_cep"] = "a confirmar"
            dados["pagador_empresa_endereco_pais"] = "BRASIL"

    return dados


def extrair_resposta_apos(texto, palavra_chave, janela=300):
    """Procura 'palavra_chave' no texto e retorna True/False olhando o
    primeiro 'Sim' ou 'Não' que aparece logo depois (tolera a pergunta
    quebrada em várias linhas, que é como o pdfplumber costuma extrair)."""
    idx = texto.upper().find(palavra_chave.upper())
    if idx == -1:
        return None
    trecho = texto[idx:idx + janela]
    m = re.search(r"\n\s*(Sim|N[ãa]o)\b", trecho, re.IGNORECASE)
    if not m:
        return None
    return m.group(1).upper().startswith("SIM")


def extrair_resposta_apos_qualquer(texto, palavras_chave, janela=300):
    """Como extrair_resposta_apos, mas tenta várias palavras-chave (versões
    diferentes do rascunho reformulam a mesma pergunta) e usa a primeira que
    encontrar no texto."""
    for palavra in palavras_chave:
        resultado = extrair_resposta_apos(texto, palavra, janela)
        if resultado is not None:
            return resultado
    return None


def extrair_campo(texto, cabecalho, janela=200):
    """Extrai a(s) linha(s) de texto logo após um cabeçalho de campo,
    parando na primeira linha em branco/cabeçalho em CAIXA ALTA seguinte."""
    idx = texto.find(cabecalho)
    if idx == -1:
        return None
    resto = texto[idx + len(cabecalho):idx + len(cabecalho) + janela]
    linhas = [l.strip() for l in resto.split("\n") if l.strip()]
    return linhas[0] if linhas else None


def mapear_dados_pagina_2(texto):
    """Travel Companions + Previous U.S. Travel"""
    dados = {}

    viaja_junto = extrair_resposta_apos(texto, "HÁ OUTRAS PESSOAS VIAJANDO COM VOCÊ")
    dados["viaja_com_alguem"] = bool(viaja_junto)
    if viaja_junto:
        # No PDF cada acompanhante vem como "NOME COMPLETO N  GRAU DE PARENTESCO" seguido de
        # "<nome completo> <parentesco>" na mesma linha (layout em colunas). Pode haver mais
        # de um acompanhante (cônjuge, filhos, outros parentes) no rascunho preenchido.
        relacionamentos_conhecidos = [
            "Cônjuge", "Filho/Filha", "Filho(a)", "Pai", "Mãe", "Irmão(a)",
            "Primo(a)", "Sobrinho(a)", "Tio(a)", "Avô(ó)", "Sogro(a)",
            "Amigo(a)", "Colega de Trabalho", "Outro Parente", "Outro"
        ]
        companheiros = []
        for match_comp in re.finditer(r"NOME COMPLETO \d+\s+GRAU DE PARENTESCO\s*\n([^\n]+)", texto):
            linha = match_comp.group(1).strip().rstrip("*").strip()
            if not linha or linha.upper() == "SELECIONAR":
                continue
            nome_comp, parentesco = linha, "a confirmar"
            for rel in relacionamentos_conhecidos:
                if linha.endswith(rel):
                    nome_comp = linha[:-len(rel)].strip()
                    parentesco = rel
                    break
            else:
                print(f"⚠️ Parentesco de acompanhante não reconhecido em '{linha}' — nome/parentesco "
                      f"podem ter ficado misturados, confira e ajuste no JSON se preciso.")
            companheiros.append({"nome": nome_comp, "parentesco": parentesco})
        dados["companheiros_viagem"] = companheiros if companheiros else [
            {"nome": "a confirmar", "parentesco": "a confirmar"}
        ]
    else:
        dados["companheiros_viagem"] = []

    dados["ja_esteve_eua"] = bool(extrair_resposta_apos(texto, "VOCÊ JÁ ESTEVE NOS ESTADOS UNIDOS"))
    dados["ja_teve_visto_eua"] = bool(extrair_resposta_apos_qualquer(
        texto, ["TEVE VISTO DOS EUA", "TEVE VISTO DOS ESTADOS UNIDOS"]
    ))
    dados["visto_cancelado_revogado"] = bool(extrair_resposta_apos(texto, "SEU VISTO JÁ FOI CANCELADO OU REVOGADO"))
    dados["visto_recusado"] = bool(extrair_resposta_apos(
        texto, "VISTO AMERICANO RECUSADO, OU TEVE A ENTRADA NEGADA"
    ))
    dados["peticao_imigrante"] = bool(extrair_resposta_apos(texto, "PETIÇÃO DE IMIGRANTE"))
    dados["carteira_habilitacao_eua"] = bool(extrair_resposta_apos_qualquer(
        texto, ["CARTEIRA DE HABILITAÇÃO DOS EUA", "TEVE HABILITAÇÃO AMERICANA"]
    ))
    if dados["carteira_habilitacao_eua"]:
        match_habilitacao = re.search(r"DADOS DA SUA HABILITAÇÃO AMERICANA\s*\n([^\n]+)", texto)
        dados["habilitacao_numero"] = limpar_valor(match_habilitacao.group(1)) if match_habilitacao else "a confirmar"

    # Até 5 visitas anteriores aos EUA (Data de Entrada / Duração / Período)
    visitas_anteriores = []
    for m in re.finditer(
        r"DATA DE ENTRADA DURAÇÃO PERÍODO\s*\n(\d{2}/\d{2}/\d{4})\s+(\d+)\s+(\S+)", texto
    ):
        visitas_anteriores.append({
            "data_entrada": m.group(1),
            "duracao": m.group(2),
            "periodo": m.group(3),
        })
    dados["visitas_anteriores_eua"] = visitas_anteriores

    # Detalhes do visto americano anterior (só existem quando ja_teve_visto_eua = Sim)
    match_visto_data = re.search(
        r"(?:DATA EM QUE O ÚLTIMO VISTO FOI EMITIDO|DATA DE EMISSÃO DO ÚLTIMO VISTO)\s*\n(\d{2}/\d{2}/\d{4})",
        texto
    )
    dados["visto_anterior_data_emissao"] = match_visto_data.group(1) if match_visto_data else ""

    match_visto_num = re.search(r"NÚMERO DO VISTO[^\n]*\n([^\n]+)", texto)
    dados["visto_anterior_numero"] = limpar_valor(match_visto_num.group(1)) if match_visto_num else ""

    dados["visto_mesmo_tipo"] = bool(extrair_resposta_apos(texto, "VOCÊ ESTÁ SOLICITANDO O MESMO TIPO DE VISTO"))
    dados["visto_perdido_roubado"] = bool(extrair_resposta_apos(texto, "SEU VISTO AMERICANO JÁ FOI PERDIDO OU ROUBADO"))

    return dados


def mapear_dados_endereco_contato(texto):
    """Endereço residencial, telefones, e-mail, mídia social e passaporte"""
    dados = {}

    match_end = re.search(
        r"RUA,\s*Nº,\s*APTO\s*\n([^\n]+)\nCIDADE\s*\n([^\n]+)\nESTADO\s*\n([^\n]+)\nCEP\s*\n([^\n]+)\nPAÍS\s*\n([^\n]+)",
        texto
    )
    if match_end:
        dados["endereco_rua"] = limpar_valor(match_end.group(1))
        dados["endereco_cidade"] = limpar_valor(match_end.group(2))
        dados["endereco_estado"] = limpar_valor(match_end.group(3))
        dados["endereco_cep"] = re.sub(r"[^0-9]", "", match_end.group(4))
        dados["endereco_pais"] = limpar_valor(match_end.group(5))
    else:
        dados["endereco_rua"] = "a confirmar"
        dados["endereco_cidade"] = "a confirmar"
        dados["endereco_estado"] = "a confirmar"
        dados["endereco_cep"] = "a confirmar"
        dados["endereco_pais"] = "BRASIL"

    match_tel_2 = re.search(r"SEGUNDO TELEFONE[^\n]*\n([0-9]+)", texto)
    match_tel_1 = re.search(r"TELEFONE PRINCIPAL[^\n]*\n([0-9]+)", texto)
    match_tel_com = re.search(r"(?:TELEFONE COMERCIAL|TELEFONE DO TRABALHO)[^\n]*\n([0-9]+)", texto)
    dados["telefone_principal"] = match_tel_1.group(1) if match_tel_1 else ""
    dados["telefone_secundario"] = match_tel_2.group(1) if match_tel_2 else ""
    dados["telefone_comercial"] = match_tel_com.group(1) if match_tel_com else ""

    match_email = re.search(r"INFORME SEU E-MAIL PRINCIPAL\s*\n([^\s\n]+)", texto)
    dados["email"] = match_email.group(1).lower() if match_email else "a confirmar"

    # Versão nova do rascunho: "SUA PRINCIPAL/SEGUNDA/... REDE SOCIAL?" (uma ou mais)
    midias_sociais = []
    for m in re.finditer(
        r"SUA (?:PRINCIPAL|SEGUNDA|TERCEIRA|QUARTA|QUINTA) REDE SOCIAL\?\s*\n([^\n]+)\n"
        r"DESCREVA AQUI ENDEREÇO DA SUA REDE SOCIAL\s*\n([^\n]+)",
        texto
    ):
        midias_sociais.append({"plataforma": limpar_valor(m.group(1)), "handle": limpar_valor(m.group(2))})

    if midias_sociais:
        dados["tem_midia_social"] = True
    else:
        # Versão antiga: "VOCÊ TEM PRESENÇA EM MIDIAS SOCIAIS ?" (uma só, ou "Nenhuma")
        match_midia = re.search(r"VOCÊ TEM PRESENÇA EM MIDIAS SOCIAIS\s*\?\s*\n([^\n]+)", texto, re.IGNORECASE)
        tem_midia = match_midia and "NENHUMA" not in match_midia.group(1).upper()
        dados["tem_midia_social"] = bool(tem_midia)
        if tem_midia:
            match_midia_handle = re.search(r"DESCREVA AQUI ENDEREÇO DA SUA REDE SOCIAL\s*\n([^\n]+)", texto)
            midias_sociais.append({
                "plataforma": limpar_valor(match_midia.group(1)),
                "handle": limpar_valor(match_midia_handle.group(1)) if match_midia_handle else "",
            })

    dados["midias_sociais"] = midias_sociais
    dados["midia_social_plataforma"] = midias_sociais[0]["plataforma"] if midias_sociais else ""
    dados["midia_social_handle"] = midias_sociais[0]["handle"] if midias_sociais else ""

    match_pass_num = re.search(r"NÚMERO DO PASSAPORTE[^\n]*\n([^\n]+)", texto)
    dados["passaporte_numero"] = limpar_valor(match_pass_num.group(1)) if match_pass_num else "a confirmar"

    match_pass_pais = re.search(r"PAÍS QUE EMITIU O PASSAPORTE\s*\n([^\n]+)", texto)
    dados["passaporte_pais_emissor"] = limpar_valor(match_pass_pais.group(1)) if match_pass_pais else "BRASIL"

    match_pass_cidade = re.search(r"CIDADE QUE EMITIU O PASSAPORTE\s*\n([^\n]+)", texto)
    dados["passaporte_cidade_emissora"] = limpar_valor(match_pass_cidade.group(1)) if match_pass_cidade else "a confirmar"

    match_pass_estado = re.search(r"ESTADO QUE EMITIU O PASSAPORTE\s*\n([^\n]+)", texto)
    dados["passaporte_estado_emissor"] = limpar_valor(match_pass_estado.group(1)) if match_pass_estado else "a confirmar"

    # No PDF as duas colunas viram: "DATA DE EMISSÃO DO DATA DE VALIDADE DO PASSAPORTE\nPASSAPORTE\n<validade>\n<emissão>"
    match_pass_datas = re.search(
        r"DATA DE EMISSÃO DO DATA DE VALIDADE DO PASSAPORTE\s*\nPASSAPORTE\s*\n(\d{2}/\d{2}/\d{4})\s*\n(\d{2}/\d{2}/\d{4})",
        texto
    )
    if match_pass_datas:
        dados["passaporte_data_validade"] = match_pass_datas.group(1)
        dados["passaporte_data_emissao"] = match_pass_datas.group(2)
    else:
        dados["passaporte_data_emissao"] = ""
        dados["passaporte_data_validade"] = ""

    dados["passaporte_perdido_roubado"] = bool(extrair_resposta_apos(texto, "JA PERDEU OU ROUBARAM SEU PASSAPORTE"))
    dados["tem_contato_eua"] = bool(extrair_resposta_apos(texto, "VOCÊ TEM CONTATO COM ALGUMA PESSOA OU EMPRESA NOS ESTADOS UNIDOS"))

    if dados["tem_contato_eua"]:
        match_contato_nome = re.search(r"NOME COMPLETO DA PESSOA OU EMPRESA\s*\n([^\n]+)", texto)
        dados["contato_eua_nome"] = limpar_valor(match_contato_nome.group(1)) if match_contato_nome else "a confirmar"

        match_contato_tel = re.search(r"TELEFONE DA PESSOA OU EMPRESA\s*\n([^\n]+)", texto)
        dados["contato_eua_telefone"] = limpar_valor(match_contato_tel.group(1)) if match_contato_tel else ""

        match_contato_email = re.search(r"E-MAIL DA PESSOA OU EMPRESA\s*\n([^\n]+)", texto)
        dados["contato_eua_email"] = limpar_valor(match_contato_email.group(1)).lower() if match_contato_email else ""

        # Endereço em 4 linhas: rua, bairro/linha2, "cidade, estado cep", país
        match_contato_end = re.search(
            r"ENDEREÇO COMPLETO DA PESSOA OU DA EMPRESA\s*\n([^\n]+)\n([^\n]+)\n([^\n]+)\n([^\n]+)",
            texto
        )
        if match_contato_end:
            dados["contato_eua_endereco_linha1"] = limpar_valor(match_contato_end.group(1))
            dados["contato_eua_endereco_linha2"] = limpar_valor(match_contato_end.group(2))
            dados["contato_eua_cidade_uf_cep"] = limpar_valor(match_contato_end.group(3))
            dados["contato_eua_endereco_pais"] = limpar_valor(match_contato_end.group(4))
        else:
            dados["contato_eua_endereco_linha1"] = "a confirmar"
            dados["contato_eua_endereco_linha2"] = ""
            dados["contato_eua_cidade_uf_cep"] = "a confirmar"
            dados["contato_eua_endereco_pais"] = "a confirmar"

        match_contato_rel = re.search(r"QUAL SUA RELAÇÃO COM PESSOA OU EMPRESA CITADA ACIMA\?\s*\n([^\n]+)", texto)
        dados["contato_eua_relacionamento"] = limpar_valor(match_contato_rel.group(1)) if match_contato_rel else "a confirmar"

    return dados


def mapear_dados_familia(texto):
    """Pais e cônjuge/ex-cônjuge"""
    dados = {}

    match_pai = re.search(r"NOME COMPLETO DO PAI\s*\n([^\n]+)", texto)
    dados["pai_nome"] = limpar_valor(match_pai.group(1)) if match_pai else "a confirmar"
    match_pai_nasc = re.search(r"DATA NASCIMENTO PAI[^\n]*\n(\d{2}/\d{2}/\d{4})", texto)
    dados["pai_data_nascimento"] = match_pai_nasc.group(1) if match_pai_nasc else ""
    dados["pai_esta_eua"] = bool(extrair_resposta_apos(texto, "SEU PAI ESTA NO EUA"))

    match_mae = re.search(r"NOME COMPLETO DA MÃE\s*\n([^\n]+)", texto)
    dados["mae_nome"] = limpar_valor(match_mae.group(1)) if match_mae else "a confirmar"
    match_mae_nasc = re.search(r"DATA NASCIMENTO MÃE[^\n]*\n(\d{2}/\d{2}/\d{4})", texto)
    dados["mae_data_nascimento"] = match_mae_nasc.group(1) if match_mae_nasc else ""
    dados["mae_esta_eua"] = bool(extrair_resposta_apos(texto, "SUA MÃE ESTA NO EUA"))

    dados["parente_1_grau_eua"] = bool(extrair_resposta_apos(texto, "PARENTE DE PRIMEIRO GRAU"))
    if dados["parente_1_grau_eua"]:
        match_parente_nome = re.search(r"NOME COMPLETO DO PARENTE IMEDIATO\s*\n([^\n]+)", texto)
        dados["parente_1_grau_nome"] = limpar_valor(match_parente_nome.group(1)) if match_parente_nome else "a confirmar"

        match_parente_rel = re.search(r"INFORME ABAIXO O GRAU DE PARENTESCO\s*\n([^\n]+)", texto)
        dados["parente_1_grau_relacionamento"] = limpar_valor(match_parente_rel.group(1)) if match_parente_rel else "a confirmar"

        match_parente_status = re.search(r"SITUAÇÃO DO PARENTE NOS ESTADOS UNIDOS\s*\n([^\n]+)", texto)
        dados["parente_1_grau_status"] = limpar_valor(match_parente_status.group(1)) if match_parente_status else "a confirmar"

    dados["outro_parente_eua"] = bool(extrair_resposta_apos(texto, "ALGUM OUTRO PARENTE NOS ESTADOS UNIDOS"))

    tem_conjuge = re.search(r"NOME COMPLETO DO CÔNJUGE\s*\n([^\n]+)", texto)
    if tem_conjuge:
        dados["conjuge_nome"] = limpar_valor(tem_conjuge.group(1))
        match_c_nasc = re.search(r"NOME COMPLETO DO CÔNJUGE[^\n]*\n[^\n]+\nDATA NASCIMENTO\s*\n(\d{2}/\d{2}/\d{4})", texto)
        dados["conjuge_data_nascimento"] = match_c_nasc.group(1) if match_c_nasc else ""
        match_c_nac = re.search(r"NACIONALIDADE \(PAIS DE ORIGEM\)\s*\n([^\n]+)", texto)
        dados["conjuge_nacionalidade"] = limpar_valor(match_c_nac.group(1)) if match_c_nac else "BRASIL"
        match_c_local = re.search(r"LOCAL DE NASCIMENTO\s*\n([^\n]+)\n([^\n]+)", texto)
        dados["conjuge_local_nascimento"] = limpar_valor(match_c_local.group(1)) if match_c_local else "a confirmar"

        match_c_end = re.search(r"LOCAL DE NASCIMENTO\s*\n[^\n]+\n[^\n]+\nENDEREÇO\s*\n([^\n]+)", texto)
        texto_end_conjuge = limpar_valor(match_c_end.group(1)) if match_c_end else "a confirmar"
        dados["conjuge_endereco_mesmo_aplicante"] = "MESMO" in texto_end_conjuge.upper()
        dados["conjuge_endereco_texto"] = texto_end_conjuge
    else:
        dados["conjuge_nome"] = ""
        dados["conjuge_data_nascimento"] = ""
        dados["conjuge_nacionalidade"] = ""
        dados["conjuge_local_nascimento"] = ""
        dados["conjuge_endereco_mesmo_aplicante"] = True
        dados["conjuge_endereco_texto"] = ""

    return dados


def mapear_dados_trabalho_educacao(texto):
    """Trabalho atual/anterior, escolaridade, idiomas e viagens anteriores"""
    dados = {}

    match_ocup = re.search(r"(?:OCUPAÇÃO ATUAL|ATIVIDADE ATUAL)\s*\n([^\n]+)", texto)
    dados["ocupacao_atual"] = limpar_valor(match_ocup.group(1)) if match_ocup else "a confirmar"

    match_cargo = re.search(r"DESCREVA SEU CARGO\s*\n([^\n]+)", texto)
    dados["cargo"] = limpar_valor(match_cargo.group(1)) if match_cargo else "a confirmar"

    match_trab_inicio = re.search(r"DATA DE INICIO NO TRABALHO(?: OU ESTUDO)?\s*\n(\d{2}/\d{2}/\d{4})", texto)
    dados["trabalho_data_inicio"] = match_trab_inicio.group(1) if match_trab_inicio else ""

    # A descrição pode ocupar mais de uma linha até o próximo cabeçalho conhecido
    match_funcoes = re.search(
        r"FUNÇÕES DIÁRIAS NO TRABALHO\s*\n([\s\S]+?)\nNOME COMPLETO DA EMPRESA",
        texto
    )
    if match_funcoes:
        dados["trabalho_funcoes"] = " ".join(match_funcoes.group(1).split())
    else:
        dados["trabalho_funcoes"] = "a confirmar"

    match_emp_nome = re.search(
        r"NOME COMPLETO DA EMPRESA ONDE TRABALHA(?: OU INSTITUIÇÃO DE ENSINO)?\s*\n([^\n]+)", texto
    )
    dados["trabalho_empresa_nome"] = limpar_valor(match_emp_nome.group(1)) if match_emp_nome else "a confirmar"

    match_emp_tel = re.search(r"DDD\+TELEFONE\s*\n([0-9]+)", texto)
    dados["trabalho_telefone"] = match_emp_tel.group(1) if match_emp_tel else ""

    match_emp_end = re.search(
        r"DDD\+TELEFONE\s*\n[^\n]+\nENDEREÇO\s*\n([^\n]+)\n([^\n]+)\n([^\n]+)\n([^\n]+)",
        texto
    )
    if match_emp_end:
        dados["trabalho_endereco_linha1"] = limpar_valor(match_emp_end.group(1))
        dados["trabalho_endereco_bairro"] = limpar_valor(match_emp_end.group(2))
        dados["trabalho_endereco_cidade_uf_cep"] = limpar_valor(match_emp_end.group(3))
        dados["trabalho_endereco_pais"] = limpar_valor(match_emp_end.group(4))
    else:
        dados["trabalho_endereco_linha1"] = "a confirmar"
        dados["trabalho_endereco_bairro"] = "a confirmar"
        dados["trabalho_endereco_cidade_uf_cep"] = "a confirmar"
        dados["trabalho_endereco_pais"] = "BRASIL"

    dados["trabalhou_outra_empresa_5anos"] = bool(extrair_resposta_apos(texto, "TRABALHOU EM OUTRA EMPRESA NOS ÚLTIMOS 5 ANOS"))
    if dados["trabalhou_outra_empresa_5anos"]:
        match_prev_emp_nome = re.search(r"NOME COMPLETO DA EMPRESA ANTERIOR\s*\n([^\n]+)", texto)
        dados["trabalho_anterior_empresa_nome"] = limpar_valor(match_prev_emp_nome.group(1)) if match_prev_emp_nome else "a confirmar"

        match_prev_cargo = re.search(r"CARGO ANTERIOR\s*\n([^\n]+)", texto)
        dados["trabalho_anterior_cargo"] = limpar_valor(match_prev_cargo.group(1)) if match_prev_cargo else "a confirmar"

        match_prev_tel = re.search(r"TELEFONE COM DDD[^\n]*\n([0-9]+)", texto)
        dados["trabalho_anterior_telefone"] = match_prev_tel.group(1) if match_prev_tel else ""

        match_prev_end = re.search(
            r"TELEFONE COM DDD[^\n]*\n[0-9]+\nENDEREÇO\s*\n([^\n]+)\n([^\n]+)\n([^\n]+)",
            texto
        )
        if match_prev_end:
            dados["trabalho_anterior_endereco_linha1"] = limpar_valor(match_prev_end.group(1))
            dados["trabalho_anterior_endereco_cidade_uf_cep"] = limpar_valor(match_prev_end.group(2))
            dados["trabalho_anterior_endereco_pais"] = limpar_valor(match_prev_end.group(3))
        else:
            dados["trabalho_anterior_endereco_linha1"] = "a confirmar"
            dados["trabalho_anterior_endereco_cidade_uf_cep"] = "a confirmar"
            dados["trabalho_anterior_endereco_pais"] = "BRASIL"

        match_prev_datas = re.search(
            r"DATA INICIO DATA SAÍDA\s*\n(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})", texto
        )
        if match_prev_datas:
            dados["trabalho_anterior_data_inicio"] = match_prev_datas.group(1)
            dados["trabalho_anterior_data_fim"] = match_prev_datas.group(2)
        else:
            dados["trabalho_anterior_data_inicio"] = ""
            dados["trabalho_anterior_data_fim"] = ""

        match_prev_funcoes = re.search(
            r"DESCREVA BREVEMENTE SUAS FUNÇÕES\s*\n([\s\S]+?)\n(?:Dados da Empresa|Escolaridade)", texto
        )
        dados["trabalho_anterior_funcoes"] = (
            " ".join(match_prev_funcoes.group(1).split()) if match_prev_funcoes else "a confirmar"
        )

    dados["estudou_nivel_medio_superior"] = bool(extrair_resposta_apos(texto, "NÍVEL MÉDIO OU SUPERIOR"))
    match_inst = re.search(r"NOME COMPLETO DA INSTITUIÇÃO\s*\n([^\n]+)", texto)
    dados["instituicao_nome"] = limpar_valor(match_inst.group(1)) if match_inst else ""

    match_inst_end = re.search(
        r"ENDEREÇO DA INSTITUIÇÃO DE ENSINO\s*\n([^\n]+)\n([^\n]+)\n([^\n]+)",
        texto
    )
    if match_inst_end:
        dados["instituicao_endereco_linha1"] = limpar_valor(match_inst_end.group(1))
        dados["instituicao_endereco_cidade_uf_cep"] = limpar_valor(match_inst_end.group(2))
        dados["instituicao_endereco_pais"] = limpar_valor(match_inst_end.group(3))
    else:
        dados["instituicao_endereco_linha1"] = "a confirmar"
        dados["instituicao_endereco_cidade_uf_cep"] = "a confirmar"
        dados["instituicao_endereco_pais"] = "BRASIL"

    match_curso = re.search(r"NOME DO CURSO ACADÊMICO\s*\n([^\n]+)", texto)
    dados["curso_nome"] = limpar_valor(match_curso.group(1)) if match_curso else ""
    match_curso_datas = re.search(
        r"AINDA, COLOCAR DATA PREVISTA DE TERMINO\)\s*\n(\d{2}/\d{2}/\d{4})\s*\n(\d{2}/\d{2}/\d{4})",
        texto
    )
    if match_curso_datas:
        dados["curso_data_inicio"] = match_curso_datas.group(1)
        dados["curso_data_termino"] = match_curso_datas.group(2)
    else:
        dados["curso_data_inicio"] = ""
        dados["curso_data_termino"] = ""

    match_idiomas = re.search(r"SOMENTE SE FOR FLUENTE NO IDIOMA \)\s*\n([^\n]+)", texto)
    dados["idiomas"] = limpar_valor(match_idiomas.group(1)) if match_idiomas else ""

    dados["viajou_ultimos_5_anos"] = bool(extrair_resposta_apos(texto, "VOCÊ JÁ VIAJOU PARA ALGUM PAÍS NOS ÚLTIMOS 5 ANOS"))
    match_paises = re.search(r"DESCREVA TODOS PAÍSES VISITADOS NO ÚLTIMOS 5 ANOS\s*\n([^\n]+)", texto)
    dados["paises_visitados_5_anos"] = limpar_valor(match_paises.group(1)) if match_paises else ""

    dados["treinamento_arma"] = bool(extrair_resposta_apos(texto, "TREINAMENTO ESPECIALIZADO COM ARMA DE FOGO"))
    match_treino_detalhe = re.search(r"EXPLIQUE HABILIDADE OU TREINAMENTO ESPECIALIZADO\s*\n([^\n]+)", texto)
    dados["treinamento_arma_detalhe"] = limpar_valor(match_treino_detalhe.group(1)) if match_treino_detalhe else ""
    dados["serviu_exercito"] = bool(extrair_resposta_apos(texto, "VOCÊ JÁ SERVIU AO EXÉRCITO"))

    return dados


def mapear_dados_seguranca(texto):
    """Segurança e Histórico - Partes I, II e III (todas booleanas: True = 'Sim')"""
    perguntas = {
        "doenca_transmissivel": "DOENÇA TRANSMISSÍVEL DE IMPORTÂNCIA PARA A SAÚDE PÚBLICA",
        "disturbio_mental_fisico": "DISTÚRBIO MENTAL OU FÍSICO QUE REPRESENTE",
        "usuario_drogas": "USUÁRIO DE DROGAS OU VICIADO",
        "preso_condenado": "PRESO OU CONDENADO POR QUALQUER OFENSA OU CRIME",
        "violou_lei_substancias_controladas": "SUBSTÂNCIAS CONTROLADAS",
        "prostituicao": "EXERCER A PROSTITUIÇÃO",
        "lavagem_dinheiro": "LAVAGEM DE",
        "trafico_pessoas": "COMETEU OU CONSPIROU PARA COMETER UM CRIME DE TRÁFICO DE PESSOAS",
        "auxiliou_trafico_pessoas": "AUXILIOU, INSTIGOU, OU CONSPIROU",
        "conjuge_beneficiario_trafico": "CÔNJUGE, FILHO OU FILHA DE UM INDIVÍDUO QUE COMETEU",
        "espionagem_sabotagem": "ESPIONAGEM, SABOTAGEM",
        "atividades_terroristas": "ENVOLVIMENTO EM ATIVIDADES TERRORISTAS",
        "apoio_financeiro_terrorismo": "APOIO A TERRORISTAS",
        "membro_organizacao_terrorista": "MEMBRO OU REPRESENTANTE DE UMA ORGANIZAÇÃO TERRORISTA",
        "genocidio": "GENOCÍDIO",
        "tortura": "TORTURA",
        "execucoes_extrajudiciais": "EXECUÇÕES EXTRAJUDICIAIS",
        "violacoes_liberdade_religiosa": "LIBERDADE RELIGIOSA",
        "audiencia_deportacao": "AUDIÊNCIA DE DEPORTAÇÃO OU REMOÇÃO",
        "fraude_visto": "FRAUDE OU DETURPAÇÃO DELIBERADA",
        "ultrapassou_prazo_visto": "ULTRAPASSOU ILEGALMENTE O PERÍODO DE TEMPO",
        "custodia_crianca_eua": "CUSTÓDIA DE UMA CRIANÇA CIDADÃ E.U.",
        "votou_ilegalmente_eua": "JÁ VOTOU NOS ESTADOS UNIDOS",
        "renunciou_cidadania_evitar_imposto": "RENUNCIOU À CIDADANIA NORTE-AMERICANA",
        "escola_publica_sem_reembolso": "ESCOLA PÚBLICA DE ESTUDANTE (F)",
    }

    dados = {"seguranca": {}}
    for chave, trecho_busca in perguntas.items():
        resposta = extrair_resposta_apos(texto, trecho_busca)
        dados["seguranca"][chave] = bool(resposta)

    return dados


def salvar_json(dados, nome_arquivo="dados_cliente.json"):
    with open(nome_arquivo, 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False, indent=4)
    print(f"✅ Arquivo '{nome_arquivo}' gerado com sucesso!")

PASTA_PDFS = "ds160_preencher"


def escolher_arquivo_pdf():
    """Define qual PDF ler: passado por linha de comando, ou (se só houver um
    .pdf dentro da pasta ds160_preencher) escolhido automaticamente, ou pedido
    interativamente."""
    if len(sys.argv) > 1:
        return sys.argv[1]

    pdfs_na_pasta = glob.glob(os.path.join(PASTA_PDFS, "*.pdf"))
    if len(pdfs_na_pasta) == 1:
        print(f"ℹ️ Usando o único PDF encontrado em '{PASTA_PDFS}': '{pdfs_na_pasta[0]}'")
        return pdfs_na_pasta[0]
    if len(pdfs_na_pasta) > 1:
        print(f"⚠️ Mais de um PDF encontrado em '{PASTA_PDFS}':")
        for i, nome in enumerate(pdfs_na_pasta, start=1):
            print(f"  {i}. {nome}")
        escolha = input("Digite o número do arquivo a usar: ").strip()
        try:
            return pdfs_na_pasta[int(escolha) - 1]
        except (ValueError, IndexError):
            print("❌ Escolha inválida.")
            return None

    return input("Digite o nome do arquivo PDF (com .pdf): ").strip()


if __name__ == "__main__":
    arquivo = escolher_arquivo_pdf()
    if not arquivo:
        sys.exit(1)
    texto_pdf = extrair_texto_pdf(arquivo)
    
    if texto_pdf:
        dados = mapear_dados_pagina_1(texto_pdf)
        dados.update(mapear_dados_pagina_2(texto_pdf))
        dados.update(mapear_dados_endereco_contato(texto_pdf))
        dados.update(mapear_dados_familia(texto_pdf))
        dados.update(mapear_dados_trabalho_educacao(texto_pdf))
        dados.update(mapear_dados_seguranca(texto_pdf))
        salvar_json(dados)
        print("\nResumo dos dados extraídos do cliente:")
        print(json.dumps(dados, indent=2, ensure_ascii=False))
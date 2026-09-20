from __future__ import annotations

import csv
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ_SP = ZoneInfo("America/Sao_Paulo")

DESCARTAR_FORA_DE_SP = True
DESCARTAR_NAO_RESOLVIDOS = False

REGIAO_DESCONHECIDA = "Outra/Desconhecida"
REGIAO_FORA_SP = "Fora de SP"
DISTRITO_NAO_IDENTIFICADO = "Não Identificado"

COLUNAS_SILVER = [
    "ocorrencia_id",
    "evento_nome",
    "tipo_evento",
    "data_evento",
    "dia_semana",
    "hora_inicio",
    "espaco_nome",
    "distrito",
    "regiao_sp",
    "is_liberdade",
    "data_hora_inicio"
]


def normalizar_texto(texto: str) -> str:
    if not texto:
        return ""
    nfkd = unicodedata.normalize('NFD', str(texto))
    sem_acento = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return " ".join(sem_acento.strip().split()).upper()

DISTRITOS_POR_REGIAO = {
    "Centro": [
        "Sé", "República", "Bela Vista", "Consolação", "Liberdade", "Cambuci",
        "Santa Cecília", "Bom Retiro",
    ],
    "ZN": [
        "Santana", "Tucuruvi", "Mandaqui", "Vila Maria", "Vila Guilherme", "Vila Medeiros",
        "Casa Verde", "Cachoeirinha", "Limão", "Freguesia do Ó", "Brasilândia", "Jaçanã",
        "Tremembé", "Perus", "Anhanguera", "Pirituba", "Jaraguá", "São Domingos",
    ],
    "ZS": [
        "Pinheiros", "Alto de Pinheiros", "Itaim Bibi", "Moema", "Vila Mariana", "Santo Amaro",
        "Campo Belo", "Jardim Paulista", "Jabaquara", "Saúde", "Ipiranga", "Cursino", "Sacomã",
        "Morumbi", "Vila Andrade", "Campo Grande", "Campo Limpo", "Capão Redondo",
        "Cidade Ademar", "Pedreira", "Socorro", "Cidade Dutra", "Grajaú", "Parelheiros",
        "Marsilac", "Jardim Ângela", "Jardim São Luís",
    ],
    "ZL": [
        "Mooca", "Tatuapé", "Belém", "Brás", "Pari", "Água Rasa", "Aricanduva", "Carrão",
        "Vila Formosa", "Penha", "Cangaíba", "Vila Matilde", "Artur Alvim",
        "Ermelino Matarazzo", "Ponte Rasa", "São Mateus", "São Rafael", "Iguatemi", "Itaquera",
        "Cidade Líder", "José Bonifácio", "Parque do Carmo", "Cidade Tiradentes", "Guaianases",
        "Lajeado", "São Miguel", "Jardim Helena", "Vila Jacuí", "Vila Curuçá", "Itaim Paulista",
        "Vila Prudente", "São Lucas", "Sapopemba",
    ],
    "ZO": [
        "Lapa", "Barra Funda", "Perdizes", "Vila Leopoldina", "Jaguaré", "Jaguara", "Butantã",
        "Vila Sônia", "Rio Pequeno", "Raposo Tavares",
    ],
}

ALIASES_BAIRROS = {
    "Vila Madalena": "Pinheiros",
    "Pompeia": "Perdizes",
    "Sumaré": "Perdizes",
    "Alto da Lapa": "Lapa",
    "Vila Romana": "Lapa",
    "Água Branca": "Lapa",
    "Vila Olímpia": "Itaim Bibi",
    "Brooklin": "Campo Belo",
    "Chácara Santo Antônio": "Santo Amaro",
    "Higienópolis": "Consolação",
    "Bixiga": "Bela Vista",
    "Luz": "Bom Retiro",
    "Santa Ifigênia": "Santa Cecília",
    "Santa Efigênia": "Santa Cecília",
    "Campos Elíseos": "Santa Cecília",
    "Vila Buarque": "Santa Cecília",
    "Paraíso": "Vila Mariana",
    "Vila Clementino": "Vila Mariana",
    "Indianópolis": "Moema",
    "Ibirapuera": "Moema",
    "Vila Nova Conceição": "Moema",
    "Jardins": "Jardim Paulista",
    "Jardim América": "Jardim Paulista",
    "Jardim Europa": "Jardim Paulista",
    "Interlagos": "Cidade Dutra",
    "Cidade Universitária": "Butantã",
    "Penha de França": "Penha",
    "Vila Carrão": "Carrão",
    "Anália Franco": "Tatuapé",
}

MAPA_LOCAIS: dict[str, tuple[str, str]] = {}
for _regiao, _nomes in DISTRITOS_POR_REGIAO.items():
    for _nome in _nomes:
        MAPA_LOCAIS[normalizar_texto(_nome)] = (_nome, _regiao)
for _alias, _distrito in ALIASES_BAIRROS.items():
    MAPA_LOCAIS[normalizar_texto(_alias)] = MAPA_LOCAIS[normalizar_texto(_distrito)]
MAPA_LOCAIS["CENTRO"] = ("Centro", "Centro")
MAPA_LOCAIS["CENTRO HISTORICO DE SAO PAULO"] = ("Centro", "Centro")

CHAVES_COMPOSTAS = sorted(
    [(k, v) for k, v in MAPA_LOCAIS.items() if " " in k],
    key=lambda kv: -len(kv[0]),
)
TIPOS_LOGRADOURO = {
    "RUA", "R", "AV", "AVENIDA", "ALAMEDA", "AL", "TRAVESSA", "TV",
    "ESTRADA", "EST", "RODOVIA", "ROD", "VIADUTO", "VIA",
}

FAIXAS_CEP = [
    (1000, 1399, "Centro"),
    (1400, 1499, "ZS"),
    (1500, 1599, "Centro"),
    (2000, 2999, "ZN"),
    (3000, 3999, "ZL"),
    (4000, 4999, "ZS"),
    (5000, 5999, "ZO"),
    (8000, 8499, "ZL"),
]

DIAS_SEMANA = {
    0: "Segunda-feira", 1: "Terça-feira", 2: "Quarta-feira",
    3: "Quinta-feira", 4: "Sexta-feira", 5: "Sábado", 6: "Domingo"
}

PADRAO_DISTRITO = re.compile(r"-\s*([^,]+),\s*São Paulo", re.IGNORECASE)
PADRAO_CIDADE = re.compile(r",\s*([^,\-]+?)\s*-\s*([A-Z]{2})\b")
PADRAO_CEP = re.compile(r"\b(\d{5})-?(\d{3})\b")
SEPARADOR_SEGMENTOS = re.compile(r"\s*[,\-–—]\s*")


def parse_iso_sp(dt_str: str) -> datetime | None:
    if not dt_str:
        return None
    dt_clean = str(dt_str).strip()
    try:
        dt_clean = dt_clean.replace("Z", "+00:00")
        if "T" in dt_clean:
            dt = datetime.fromisoformat(dt_clean)
        else:
            dt = datetime.strptime(dt_clean, "%Y-%m-%d %H:%M:%S")
            dt = dt.replace(tzinfo=TZ_SP)
        return dt.astimezone(TZ_SP)
    except Exception:
        return None


def regiao_por_cep(endereco: str) -> str | None:
    m = PADRAO_CEP.search(endereco)
    if not m:
        return None
    n = int(m.group(1))
    if n < 1000:
        return None
    for ini, fim, regiao in FAIXAS_CEP:
        if ini <= n <= fim:
            return regiao
    return REGIAO_FORA_SP


def cidade_diferente_de_sp(endereco: str) -> bool:
    achados = PADRAO_CIDADE.findall(endereco)
    if not achados:
        return False
    cidade = normalizar_texto(achados[-1][0])
    return cidade != "SAO PAULO"


def resolver_local(endereco: str) -> tuple[str, str | None, str, str]:
    """Retorna (distrito, regiao, metodo, dist_raw). regiao None = não resolvido."""
    if not endereco or not endereco.strip():
        return "", None, "sem_endereco", ""

    m = PADRAO_DISTRITO.search(endereco)
    dist_raw = m.group(1).strip() if m else ""

    # 0) outra cidade
    if cidade_diferente_de_sp(endereco):
        return "", REGIAO_FORA_SP, "fora_sp", dist_raw

    # 1) algum trecho do endereço é exatamente um distrito/bairro conhecido
    candidatos = ([dist_raw] if dist_raw else []) + SEPARADOR_SEGMENTOS.split(endereco)
    for c in candidatos:
        achado = MAPA_LOCAIS.get(normalizar_texto(c))
        if achado:
            return achado[0], achado[1], "texto", dist_raw

    # 2) CEP
    r = regiao_por_cep(endereco)
    if r == REGIAO_FORA_SP:
        return "", REGIAO_FORA_SP, "fora_sp", dist_raw
    if r:
        return DISTRITO_NAO_IDENTIFICADO, r, "cep", dist_raw

    # 3) nome composto dentro do endereço, ignorando nomes de rua
    end_norm = normalizar_texto(endereco)
    for chave, (nome, regiao) in CHAVES_COMPOSTAS:
        for mm in re.finditer(rf"\b{re.escape(chave)}\b", end_norm):
            anterior = end_norm[:mm.start()].split()
            if anterior and anterior[-1].strip(".,") in TIPOS_LOGRADOURO:
                continue
            return nome, regiao, "nome_no_endereco", dist_raw

    return "", None, "nenhum", dist_raw


def buscar_arquivo_bronze_mais_recente() -> Path | None:
    pasta_bronze = Path("./dados/bronze/spcultura")
    if not pasta_bronze.exists():
        return None

    arquivos = list(pasta_bronze.glob("*.csv"))
    if not arquivos:
        return None

    return max(arquivos, key=lambda p: p.stat().st_mtime)


def processar_silver(caminho_entrada: Path) -> tuple[list[dict], dict, Counter]:
    stats = {
        "entrada": 0, "descartados_sem_nome": 0, "descartados_sem_data": 0,
        "duplicados": 0, "eventos_liberdade": 0, "saida": 0,
        "res_texto": 0, "res_cep": 0, "res_nome": 0, "res_espaco": 0,
        "fora_sp": 0, "nao_resolvidos": 0,
    }
    relatorio: Counter = Counter()

    with open(caminho_entrada, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        linhas = list(reader)

    stats["entrada"] = len(linhas)
    if not linhas:
        print("[AVISO] O arquivo de entrada está totalmente vazio.")
        return [], stats, relatorio

    registros = []
    vistos = set()

    for row in linhas:
        nome_raw = row.get("evento_nome_raw") or row.get("evento_nome") or ""
        data_ini_raw = row.get("data_hora_inicio_raw") or row.get("data_hora_inicio") or ""
        local_raw = row.get("espaco_nome_raw") or row.get("espaco_nome") or ""
        tipo_raw = row.get("tipo_evento_raw") or row.get("tipo_evento") or row.get("linguagem_principal") or ""
        endereco_raw = row.get("endereco_completo_raw") or row.get("endereco_completo") or row.get("fullAddress") or ""

        nome_limpo = str(nome_raw).strip()
        if not nome_limpo:
            stats["descartados_sem_nome"] += 1
            continue

        dt_inicio = parse_iso_sp(data_ini_raw)
        if not dt_inicio:
            stats["descartados_sem_data"] += 1
            continue

        chave_unica = (normalizar_texto(nome_limpo), dt_inicio.isoformat(), normalizar_texto(local_raw))
        if chave_unica in vistos:
            stats["duplicados"] += 1
            continue
        vistos.add(chave_unica)

        distrito, regiao, metodo, dist_raw = resolver_local(endereco_raw)

        registros.append({
            "ocorrencia_id": row.get("ocorrencia_id") or row.get("evento_id") or "",
            "evento_nome": nome_limpo,
            "tipo_evento": str(tipo_raw).strip().title() or "Outros",
            "data_evento": dt_inicio.strftime("%Y-%m-%d"),
            "dia_semana": DIAS_SEMANA[dt_inicio.weekday()],
            "hora_inicio": dt_inicio.strftime("%H:%M"),
            "espaco_nome": str(local_raw).strip() or "Não Informado",
            "data_hora_inicio": dt_inicio.strftime("%Y-%m-%d %H:%M:%S"),
            "_endereco": endereco_raw,
            "_distrito": distrito,
            "_regiao": regiao,
            "_metodo": metodo,
            "_dist_raw": dist_raw,
        })

    conhecidos: dict[str, Counter] = defaultdict(Counter)
    for r in registros:
        if r["_regiao"] and r["_regiao"] != REGIAO_FORA_SP:
            conhecidos[normalizar_texto(r["espaco_nome"])][(r["_distrito"], r["_regiao"])] += 1

    for r in registros:
        if r["_regiao"] is None and r["espaco_nome"] != "Não Informado":
            escolhas = conhecidos.get(normalizar_texto(r["espaco_nome"]))
            if escolhas:
                (dist, reg), _ = escolhas.most_common(1)[0]
                r["_distrito"], r["_regiao"], r["_metodo"] = dist, reg, "espaco"

    validas = []
    for r in registros:
        metodo, regiao, distrito = r["_metodo"], r["_regiao"], r["_distrito"]

        if regiao == REGIAO_FORA_SP:
            stats["fora_sp"] += 1
            relatorio[("fora_de_sp", r["_endereco"], r["espaco_nome"])] += 1
            if DESCARTAR_FORA_DE_SP:
                continue
        elif regiao is None:
            stats["nao_resolvidos"] += 1
            motivo = "sem_endereco" if metodo == "sem_endereco" else "bairro_nao_mapeado"
            relatorio[(motivo, r["_endereco"], r["espaco_nome"])] += 1
            if DESCARTAR_NAO_RESOLVIDOS:
                continue
            regiao = REGIAO_DESCONHECIDA
            distrito = r["_dist_raw"].title() if r["_dist_raw"] else (
                "Não Informado" if metodo == "sem_endereco" else "Outro"
            )
        else:
            chave_stat = {"texto": "res_texto", "cep": "res_cep",
                          "nome_no_endereco": "res_nome", "espaco": "res_espaco"}.get(metodo)
            if chave_stat:
                stats[chave_stat] += 1

        is_liberdade = 1 if (
            "LIBERDADE" in normalizar_texto(distrito) or "LIBERDADE" in normalizar_texto(r["_endereco"])
        ) else 0
        if is_liberdade:
            stats["eventos_liberdade"] += 1

        validas.append({
            "ocorrencia_id": r["ocorrencia_id"],
            "evento_nome": r["evento_nome"],
            "tipo_evento": r["tipo_evento"],
            "data_evento": r["data_evento"],
            "dia_semana": r["dia_semana"],
            "hora_inicio": r["hora_inicio"],
            "espaco_nome": r["espaco_nome"],
            "distrito": distrito,
            "regiao_sp": regiao,
            "is_liberdade": is_liberdade,
            "data_hora_inicio": r["data_hora_inicio"],
        })

    validas.sort(key=lambda x: x["data_hora_inicio"])
    stats["saida"] = len(validas)
    return validas, stats, relatorio


def gravar_silver(linhas: list[dict], caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUNAS_SILVER, delimiter=",", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(linhas)


def gravar_relatorio(relatorio: Counter, caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=",", quoting=csv.QUOTE_ALL)
        writer.writerow(["motivo", "endereco", "espaco_nome", "ocorrencias"])
        for (motivo, endereco, espaco), qtd in relatorio.most_common():
            writer.writerow([motivo, endereco, espaco, qtd])


def main():
    print("Iniciando tratamento dos dados (Camada Silver Enxuta)...")

    if len(sys.argv) == 3:
        entrada = Path(sys.argv[1])
        saida = Path(sys.argv[2])
    else:
        print("[INFO] Buscando automaticamente o arquivo mais recente da camada Bronze...")
        arquivo_encontrado = buscar_arquivo_bronze_mais_recente()

        if not arquivo_encontrado:
            print("[ERRO] Nenhum CSV encontrado em './dados/bronze/spcultura/'. Execute o script de extração primeiro.")
            return

        entrada = arquivo_encontrado
        saida = Path("./dados/silver/spcultura/spcultura_silver.csv")

    print(f"[INFO] Processando arquivo: {entrada}")

    validas, stats, relatorio = processar_silver(entrada)
    gravar_silver(validas, saida)

    caminho_relatorio = saida.parent / "nao_resolvidos.csv"
    gravar_relatorio(relatorio, caminho_relatorio)

    print("\n=== PIPELINE SILVER CONCLUÍDO COM SUCESSO ===")
    print(f"Registros lidos (Bronze):   {stats['entrada']}")
    print(f"Descartados sem nome:       {stats['descartados_sem_nome']}")
    print(f"Descartados sem data:       {stats['descartados_sem_data']}")
    print(f"Duplicados removidos:       {stats['duplicados']}")
    print(f"Eventos na Liberdade:       {stats['eventos_liberdade']}")
    print(f"Registros salvos (Silver):  {stats['saida']} -> {saida}")
    print("\n--- Resolução de região ---")
    print(f"Por distrito/bairro no texto: {stats['res_texto']}")
    print(f"Por CEP:                      {stats['res_cep']}")
    print(f"Por nome no endereço:         {stats['res_nome']}")
    print(f"Por espaço já conhecido:      {stats['res_espaco']}")
    print(f"Fora de São Paulo:            {stats['fora_sp']}" + (" (descartados)" if DESCARTAR_FORA_DE_SP else ""))
    print(f"Não resolvidos:               {stats['nao_resolvidos']}" + (" (descartados)" if DESCARTAR_NAO_RESOLVIDOS else ""))
    print(f"Relatório de pendências:      {caminho_relatorio}\n")


if __name__ == "__main__":
    main()
from __future__ import annotations

import csv
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ_SP = ZoneInfo("America/Sao_Paulo")

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

MAPA_REGIOES_NORM = {
    # Centro
    "SE": "Centro", "REPUBLICA": "Centro", "BELA VISTA": "Centro", "CONSOLACAO": "Centro",
    "LIBERDADE": "Centro", "CAMBUCI": "Centro", "SANTA CECILIA": "Centro", "BOM RETIRO": "Centro",
    # Zona Norte
    "SANTANA": "ZN", "TUCURUVI": "ZN", "MANDAQUI": "ZN", "VILA MARIA": "ZN", "VILA GUILHERME": "ZN",
    "VILA MEDEIROS": "ZN", "CASA VERDE": "ZN", "CACHOEIRINHA": "ZN", "LIMAO": "ZN", "FREGUESIA DO O": "ZN",
    "BRASILANDIA": "ZN", "JACANA": "ZN", "TREMEMBE": "ZN", "PERUS": "ZN", "ANHANGUERA": "ZN",
    # Zona Sul
    "PINHEIROS": "ZS", "ITAIM BIBI": "ZS", "MOEMA": "ZS", "VILA MARIANA": "ZS", "SANTO AMARO": "ZS",
    "CAMPO BELO": "ZS", "JARDIM PAULISTA": "ZS", "JABAQUARA": "ZS", "SAUDE": "ZS", "IPIRANGA": "ZS",
    "MORUMBI": "ZS", "VILA ANDRADE": "ZS", "CAMPO GRANDE": "ZS", "CAMPO LIMPO": "ZS", "CAPAO REDONDO": "ZS",
    "CIDADE ADEMAR": "ZS", "PEDREIRA": "ZS", "SOCORRO": "ZS", "PARELHEIROS": "ZS", "MARSILAC": "ZS",
    # Zona Leste
    "MOOCA": "ZL", "TATUAPE": "ZL", "BELEM": "ZL", "BRAS": "ZL", "PARI": "ZL", "AGUA RASA": "ZL",
    "ARICANDUVA": "ZL", "CARRAO": "ZL", "VILA FORMOSA": "ZL", "PENHA": "ZL", "CANGAIBA": "ZL",
    "VILA MATILDE": "ZL", "ERMELINO MATARAZZO": "ZL", "PONTE RASA": "ZL", "SAO MATEUS": "ZL",
    "SAO RAFAEL": "ZL", "IGUATEMI": "ZL", "ITAQUERA": "ZL", "CIDADE LIDER": "ZL", "JOSE BONIFACIO": "ZL",
    "PARQUE DO CARMO": "ZL", "GUAIANASES": "ZL", "LAJEADO": "ZL", "SAO MIGUEL": "ZL", "JARDIM HELENA": "ZL",
    "VILA CURUCA": "ZL", "ITAIM PAULISTA": "ZL", "VILA PRUDENTE": "ZL", "SAO LUCAS": "ZL", "SAPOPEMBA": "ZL",
    # Zona Oeste
    "LAPA": "ZO", "BARRA FUNDA": "ZO", "PERDIZES": "ZO", "VILA LEOPOLDINA": "ZO", "JAGUARE": "ZO",
    "JAGUARA": "ZO", "BUTANTA": "ZO", "VILA SONIA": "ZO", "RIO PEQUENO": "ZO", "RAPOSO TAVARES": "ZO"
}

DIAS_SEMANA = {
    0: "Segunda-feira", 1: "Terça-feira", 2: "Quarta-feira", 
    3: "Quinta-feira", 4: "Sexta-feira", 5: "Sábado", 6: "Domingo"
}

PADRAO_DISTRITO = re.compile(r"-\s*([^,]+),\s*São Paulo", re.IGNORECASE)

def normalizar_texto(texto: str) -> str:
    if not texto:
        return ""
    nfkd = unicodedata.normalize('NFD', str(texto))
    sem_acento = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return " ".join(sem_acento.strip().split()).upper()

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

def extrair_distrito_e_regiao(endereco: str) -> tuple[str, str]:
    if not endereco:
        return "Não Informado", "Outra/Desconhecida"
    
    match = PADRAO_DISTRITO.search(endereco)
    dist_raw = match.group(1).strip() if match else ""
    dist_norm = normalizar_texto(dist_raw)

    if dist_norm in MAPA_REGIOES_NORM:
        return dist_raw.title(), MAPA_REGIOES_NORM[dist_norm]

    end_norm = normalizar_texto(endereco)
    for k, regiao in MAPA_REGIOES_NORM.items():
        if k in end_norm:
            return k.title(), regiao

    return dist_raw.title() if dist_raw else "Outro", "Outra/Desconhecida"

def buscar_arquivo_bronze_mais_recente() -> Path | None:
    pasta_bronze = Path("./dados/bronze/spcultura")
    if not pasta_bronze.exists():
        return None
    
    arquivos = list(pasta_bronze.glob("*.csv"))
    if not arquivos:
        return None
    
    return max(arquivos, key=lambda p: p.stat().st_mtime)

def processar_silver(caminho_entrada: Path) -> tuple[list[dict], dict]:
    stats = {
        "entrada": 0, "descartados_sem_nome": 0, "descartados_sem_data": 0,
        "duplicados": 0, "eventos_liberdade": 0, "saida": 0
    }
    
    with open(caminho_entrada, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        linhas = list(reader)

    stats["entrada"] = len(linhas)
    if not linhas:
        print("[AVISO] O arquivo de entrada está totalmente vazio.")
        return [], stats

    validas = []
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

        distrito, regiao = extrair_distrito_e_regiao(endereco_raw)
        is_liberdade = 1 if "LIBERDADE" in normalizar_texto(distrito) or "LIBERDADE" in normalizar_texto(endereco_raw) else 0
        if is_liberdade:
            stats["eventos_liberdade"] += 1

        validas.append({
            "ocorrencia_id": row.get("ocorrencia_id") or row.get("evento_id") or "",
            "evento_nome": nome_limpo,
            "tipo_evento": str(tipo_raw).strip().title() or "Outros",
            "data_evento": dt_inicio.strftime("%Y-%m-%d"),
            "dia_semana": DIAS_SEMANA[dt_inicio.weekday()],
            "hora_inicio": dt_inicio.strftime("%H:%M"),
            "espaco_nome": str(local_raw).strip() or "Não Informado",
            "distrito": distrito,
            "regiao_sp": regiao,
            "is_liberdade": is_liberdade,
            "data_hora_inicio": dt_inicio.strftime("%Y-%m-%d %H:%M:%S")
        })

    validas.sort(key=lambda x: x["data_hora_inicio"])
    stats["saida"] = len(validas)
    return validas, stats

def gravar_silver(linhas: list[dict], caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUNAS_SILVER, delimiter=",", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(linhas)

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

    validas, stats = processar_silver(entrada)
    gravar_silver(validas, saida)

    print("\n=== PIPELINE SILVER CONCLUÍDO COM SUCESSO ===")
    print(f"Registros lidos (Bronze):   {stats['entrada']}")
    print(f"Descartados sem nome:       {stats['descartados_sem_nome']}")
    print(f"Descartados sem data:       {stats['descartados_sem_data']}")
    print(f"Duplicados removidos:       {stats['duplicados']}")
    print(f"Eventos na Liberdade:       {stats['eventos_liberdade']}")
    print(f"Registros salvos (Silver):  {stats['saida']} -> {saida}\n")

if __name__ == "__main__":
    main()
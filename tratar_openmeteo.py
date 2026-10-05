from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PASTA_BRONZE = Path("./dados/bronze/open_meteo")
PASTA_SILVER = Path("./dados/silver/open_meteo")

# Janela de funcionamento da cafeteria: horas H com HORA_ABERTURA <= H < HORA_FECHAMENTO.
HORA_ABERTURA = 9
HORA_FECHAMENTO = 18

# Limites calibrados com os dados de 2026 do ponto da cafeteria (jan-out, horário 9h-18h).
LIMIAR_HORA_CHUVOSA_MM = 0.5   # abaixo disso é garoa do modelo (ERA5 exagera chuva fraca)
LIMIAR_GAROA_MM = 1.0          # chuva no horário comercial
LIMIAR_DIA_CHUVOSO_MM = 5.0    # chuva no horário comercial (~18% dos dias)
LIMIAR_CHUVA_FORTE_MM = 15.0   # chuva no horário comercial (~2% dos dias)
LIMIAR_QUENTE_C = 28.0         # temperatura máxima no horário comercial (~25% dos dias)
LIMIAR_FRIO_C = 19.0           # temperatura máxima no horário comercial (~10% dos dias)
LIMIAR_NUBLADO_PCT = 70.0      # cobertura média de nuvens no horário comercial

# archive = clima observado (reanálise); forecast = previsão. O observado sempre prevalece.
PRIORIDADE_FONTE = {"archive": 0, "forecast": 1}

COLUNAS_HORARIO = [
    "data",
    "hora",
    "data_hora",
    "temperatura_c",
    "sensacao_termica_c",
    "umidade_pct",
    "chuva_mm",
    "nuvens_pct",
    "vento_kmh",
    "codigo_tempo",
    "descricao_tempo",
    "is_horario_comercial",
    "fonte",
]

COLUNAS_DIARIO = [
    "data",
    "temp_min_c",
    "temp_media_c",
    "temp_max_c",
    "sensacao_max_c",
    "umidade_media_pct",
    "nuvens_media_pct",
    "vento_max_kmh",
    "chuva_comercial_mm",
    "chuva_max_hora_mm",
    "horas_com_chuva",
    "chuva_dia_todo_mm",
    "condicao_dia",
    "faixa_temperatura",
    "is_dia_chuvoso",
    "is_chuva_forte",
    "fonte",
    "horas_disponiveis",
]

# Códigos WMO usados pela Open-Meteo.
DESCRICAO_WMO = {
    0: "Céu limpo", 1: "Predominantemente limpo", 2: "Parcialmente nublado", 3: "Nublado",
    45: "Neblina", 48: "Neblina com geada",
    51: "Garoa fraca", 53: "Garoa moderada", 55: "Garoa intensa",
    56: "Garoa congelante fraca", 57: "Garoa congelante intensa",
    61: "Chuva fraca", 63: "Chuva moderada", 65: "Chuva forte",
    66: "Chuva congelante fraca", 67: "Chuva congelante forte",
    71: "Neve fraca", 73: "Neve moderada", 75: "Neve forte", 77: "Grãos de neve",
    80: "Pancadas fracas", 81: "Pancadas moderadas", 82: "Pancadas violentas",
    85: "Pancadas de neve fracas", 86: "Pancadas de neve fortes",
    95: "Trovoada", 96: "Trovoada com granizo fraco", 99: "Trovoada com granizo forte",
}


def para_float(valor: str) -> float | None:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def media(valores: list[float]) -> float | None:
    return round(sum(valores) / len(valores), 1) if valores else None


def carregar_bronze() -> tuple[dict[str, dict], dict]:
    """Junta todos os arquivos bronze: para cada hora fica o dado observado, ou a previsão mais recente."""
    stats = {"arquivos": 0, "linhas": 0, "invalidas": 0}
    por_hora: dict[str, dict] = {}

    for caminho in sorted(PASTA_BRONZE.glob("*.csv")):
        stats["arquivos"] += 1
        with open(caminho, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                stats["linhas"] += 1
                try:
                    datetime.strptime(row["data_hora"], "%Y-%m-%d %H:%M:%S")
                except (KeyError, ValueError):
                    stats["invalidas"] += 1
                    continue
                if para_float(row.get("temperature_2m")) is None:
                    stats["invalidas"] += 1
                    continue

                atual = por_hora.get(row["data_hora"])
                if atual is None:
                    por_hora[row["data_hora"]] = row
                    continue

                prio_nova = PRIORIDADE_FONTE.get(row["fonte"], 9)
                prio_atual = PRIORIDADE_FONTE.get(atual["fonte"], 9)
                mais_recente = row["extraido_em"] > atual["extraido_em"]
                if prio_nova < prio_atual or (prio_nova == prio_atual and mais_recente):
                    por_hora[row["data_hora"]] = row

    return por_hora, stats


def montar_horario(por_hora: dict[str, dict]) -> list[dict]:
    linhas = []
    for data_hora in sorted(por_hora):
        row = por_hora[data_hora]
        dt = datetime.strptime(data_hora, "%Y-%m-%d %H:%M:%S")
        codigo = int(para_float(row["weather_code"]) or 0)
        linhas.append({
            "data": dt.strftime("%Y-%m-%d"),
            "hora": dt.hour,
            "data_hora": data_hora,
            "temperatura_c": para_float(row["temperature_2m"]),
            "sensacao_termica_c": para_float(row["apparent_temperature"]),
            "umidade_pct": para_float(row["relative_humidity_2m"]),
            "chuva_mm": para_float(row["precipitation"]) or 0.0,
            "nuvens_pct": para_float(row["cloud_cover"]),
            "vento_kmh": para_float(row["wind_speed_10m"]),
            "codigo_tempo": codigo,
            "descricao_tempo": DESCRICAO_WMO.get(codigo, "Desconhecido"),
            "is_horario_comercial": int(HORA_ABERTURA <= dt.hour < HORA_FECHAMENTO),
            "fonte": row["fonte"],
        })
    return linhas


def montar_diario(horario: list[dict]) -> list[dict]:
    por_dia: dict[str, list[dict]] = defaultdict(list)
    for h in horario:
        por_dia[h["data"]].append(h)

    horas_esperadas = HORA_FECHAMENTO - HORA_ABERTURA
    diario = []
    for data in sorted(por_dia):
        horas = por_dia[data]
        comercial = [h for h in horas if h["is_horario_comercial"]]
        if not comercial:
            continue

        temps = [h["temperatura_c"] for h in comercial]
        sensacoes = [h["sensacao_termica_c"] for h in comercial if h["sensacao_termica_c"] is not None]
        umidades = [h["umidade_pct"] for h in comercial if h["umidade_pct"] is not None]
        nuvens = [h["nuvens_pct"] for h in comercial if h["nuvens_pct"] is not None]
        ventos = [h["vento_kmh"] for h in comercial if h["vento_kmh"] is not None]
        chuvas = [h["chuva_mm"] for h in comercial]

        chuva_comercial = round(sum(chuvas), 1)
        temp_max = max(temps)

        nuvens_media = media(nuvens)

        # Pela quantidade de chuva, não pelo código WMO: o modelo marca garoa em excesso.
        if any(h["codigo_tempo"] >= 95 for h in comercial):
            condicao = "Tempestade"
        elif chuva_comercial >= LIMIAR_CHUVA_FORTE_MM:
            condicao = "Chuva forte"
        elif chuva_comercial >= LIMIAR_DIA_CHUVOSO_MM:
            condicao = "Chuva"
        elif chuva_comercial >= LIMIAR_GAROA_MM:
            condicao = "Garoa"
        elif nuvens_media is not None and nuvens_media >= LIMIAR_NUBLADO_PCT:
            condicao = "Nublado"
        else:
            condicao = "Sol"

        if temp_max >= LIMIAR_QUENTE_C:
            faixa = "Quente"
        elif temp_max <= LIMIAR_FRIO_C:
            faixa = "Frio"
        else:
            faixa = "Ameno"

        fontes = {h["fonte"] for h in comercial}
        diario.append({
            "data": data,
            "temp_min_c": min(temps),
            "temp_media_c": media(temps),
            "temp_max_c": temp_max,
            "sensacao_max_c": max(sensacoes) if sensacoes else None,
            "umidade_media_pct": media(umidades),
            "nuvens_media_pct": nuvens_media,
            "vento_max_kmh": max(ventos) if ventos else None,
            "chuva_comercial_mm": chuva_comercial,
            "chuva_max_hora_mm": max(chuvas),
            "horas_com_chuva": sum(1 for c in chuvas if c >= LIMIAR_HORA_CHUVOSA_MM),
            "chuva_dia_todo_mm": round(sum(h["chuva_mm"] for h in horas), 1),
            "condicao_dia": condicao,
            "faixa_temperatura": faixa,
            "is_dia_chuvoso": int(chuva_comercial >= LIMIAR_DIA_CHUVOSO_MM),
            "is_chuva_forte": int(chuva_comercial >= LIMIAR_CHUVA_FORTE_MM),
            "fonte": fontes.pop() if len(fontes) == 1 else "misto",
            "horas_disponiveis": f"{len(comercial)}/{horas_esperadas}",
        })

    return diario


def gravar_csv(linhas: list[dict], colunas: list[str], caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=colunas, delimiter=",", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(linhas)


def main():
    print("Iniciando tratamento do clima - Open-Meteo (Camada Silver)...")

    if not PASTA_BRONZE.exists() or not any(PASTA_BRONZE.glob("*.csv")):
        print(f"[ERRO] Nenhum CSV encontrado em '{PASTA_BRONZE}'. Execute extrair_openmeteo.py primeiro.")
        sys.exit(1)

    por_hora, stats = carregar_bronze()
    horario = montar_horario(por_hora)
    diario = montar_diario(horario)
    if not diario:
        print("[ERRO] Nenhum dia com dados no horário comercial.")
        sys.exit(1)

    saida_horario = PASTA_SILVER / "clima_horario_silver.csv"
    saida_diario = PASTA_SILVER / "clima_diario_silver.csv"
    gravar_csv(horario, COLUNAS_HORARIO, saida_horario)
    gravar_csv(diario, COLUNAS_DIARIO, saida_diario)

    por_fonte = {f: sum(1 for d in diario if d["fonte"] == f) for f in ("archive", "forecast", "misto")}
    incompletos = sum(1 for d in diario if d["horas_disponiveis"] != f"{HORA_FECHAMENTO - HORA_ABERTURA}/{HORA_FECHAMENTO - HORA_ABERTURA}")
    print("\n=== PIPELINE SILVER CONCLUÍDO COM SUCESSO ===")
    print(f"Arquivos bronze lidos:      {stats['arquivos']} ({stats['linhas']} linhas, {stats['invalidas']} inválidas)")
    print(f"Horas únicas (silver):      {len(horario)} -> {saida_horario}")
    print(f"Dias (silver):              {len(diario)} -> {saida_diario}")
    print(f"Período:                    {diario[0]['data']} a {diario[-1]['data']}")
    print(f"Dias por fonte:             {por_fonte}")
    print(f"Dias com horas faltando:    {incompletos}")
    print(f"Horário comercial:          {HORA_ABERTURA}h às {HORA_FECHAMENTO}h")
    print(f"Dias chuvosos (>= {LIMIAR_DIA_CHUVOSO_MM} mm):  {sum(d['is_dia_chuvoso'] for d in diario)}")
    print(f"Chuva forte (>= {LIMIAR_CHUVA_FORTE_MM} mm):    {sum(d['is_chuva_forte'] for d in diario)}\n")


if __name__ == "__main__":
    main()
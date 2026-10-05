from __future__ import annotations

import csv
import sys
from datetime import date, timedelta
from pathlib import Path

PASTA_BRONZE = Path("./dados/bronze/brasilapi_feriados")
PASTA_SILVER = Path("./dados/silver/feriados")

ESFERA_API = {"national": "Nacional", "state": "Estadual", "municipal": "Municipal"}
PRIORIDADE_ESFERA = {"Nacional": 0, "Estadual": 1, "Municipal": 2}

COLUNAS_FERIADOS = ["data", "dia_semana", "nome", "esfera", "fonte", "base_legal"]

COLUNAS_CALENDARIO = [
    "data",
    "ano",
    "mes",
    "dia_semana",
    "is_fim_de_semana",
    "is_feriado",
    "feriado_nome",
    "feriado_esfera",
    "is_vespera_feriado",
    "is_ponte",
    "is_dia_util",
]

DIAS_SEMANA = {
    0: "Segunda-feira", 1: "Terça-feira", 2: "Quarta-feira",
    3: "Quinta-feira", 4: "Sexta-feira", 5: "Sábado", 6: "Domingo"
}


def calcular_pascoa(ano: int) -> date:
    """Algoritmo de Meeus/Jones/Butcher (calendário gregoriano)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = (h + l - 7 * m + 114) % 31 + 1
    return date(ano, mes, dia)


def feriados_sp(ano: int) -> list[dict]:
    """Feriados estaduais (SP) e municipais (cidade de SP) que a BrasilAPI não cobre.

    Lei Estadual 9.497/1997: 9 de julho.
    Lei Municipal 14.485/2007: 25 de janeiro, Sexta-feira Santa, Corpus Christi e 20 de novembro
    (Sexta-feira Santa e 20 de novembro já vêm da API como nacionais).
    """
    return [
        {
            "data": date(ano, 1, 25),
            "nome": "Aniversário da Cidade de São Paulo",
            "esfera": "Municipal",
            "base_legal": "Lei Municipal 14.485/2007",
        },
        {
            "data": date(ano, 7, 9),
            "nome": "Revolução Constitucionalista de 1932",
            "esfera": "Estadual",
            "base_legal": "Lei Estadual 9.497/1997",
        },
        {
            # Na esfera federal é ponto facultativo; na capital é feriado municipal.
            "data": calcular_pascoa(ano) + timedelta(days=60),
            "nome": "Corpus Christi",
            "esfera": "Municipal",
            "base_legal": "Lei Municipal 14.485/2007",
        },
    ]


def buscar_arquivo_bronze_mais_recente() -> Path | None:
    if not PASTA_BRONZE.exists():
        return None

    arquivos = list(PASTA_BRONZE.glob("*.csv"))
    if not arquivos:
        return None

    return max(arquivos, key=lambda p: p.stat().st_mtime)


def montar_feriados(caminho_entrada: Path) -> tuple[list[dict], list[int], dict]:
    stats = {"api": 0, "api_sem_data": 0, "locais": 0, "sobrescritos": 0, "duplicados": 0}

    with open(caminho_entrada, encoding="utf-8") as f:
        linhas = list(csv.DictReader(f))
    stats["api"] = len(linhas)

    por_data: dict[date, dict] = {}
    anos = set()

    for row in linhas:
        try:
            dia = date.fromisoformat(row["data_raw"].strip())
        except (KeyError, ValueError):
            stats["api_sem_data"] += 1
            continue

        anos.add(int(row.get("ano_consultado") or dia.year))
        registro = {
            "data": dia,
            "nome": row["nome_raw"].strip(),
            "esfera": ESFERA_API.get(row["tipo_raw"].strip(), "Nacional"),
            "fonte": "brasilapi",
            "base_legal": "",
        }

        atual = por_data.get(dia)
        if atual is not None:
            stats["duplicados"] += 1
            if PRIORIDADE_ESFERA[registro["esfera"]] >= PRIORIDADE_ESFERA[atual["esfera"]]:
                continue
        por_data[dia] = registro

    # A lista local tem a classificação legal correta para SP e por isso prevalece.
    for ano in sorted(anos):
        for local in feriados_sp(ano):
            stats["locais"] += 1
            if local["data"] in por_data:
                stats["sobrescritos"] += 1
            por_data[local["data"]] = {**local, "fonte": "lei_sp"}

    feriados = [por_data[d] for d in sorted(por_data)]
    return feriados, sorted(anos), stats


def montar_calendario(feriados: list[dict], anos: list[int]) -> list[dict]:
    por_data = {f["data"]: f for f in feriados}

    def is_folga(d: date) -> bool:
        return d.weekday() >= 5 or d in por_data

    calendario = []
    dia = date(anos[0], 1, 1)
    fim = date(anos[-1], 12, 31)
    while dia <= fim:
        feriado = por_data.get(dia)
        ontem, amanha = dia - timedelta(days=1), dia + timedelta(days=1)
        fim_de_semana = dia.weekday() >= 5

        # Ponte: dia útil espremido entre um feriado e outra folga (ex.: segunda antes de feriado na terça).
        ponte = (
            not is_folga(dia)
            and is_folga(ontem) and is_folga(amanha)
            and (ontem in por_data or amanha in por_data)
        )

        calendario.append({
            "data": dia.isoformat(),
            "ano": dia.year,
            "mes": dia.month,
            "dia_semana": DIAS_SEMANA[dia.weekday()],
            "is_fim_de_semana": int(fim_de_semana),
            "is_feriado": int(feriado is not None),
            "feriado_nome": feriado["nome"] if feriado else "",
            "feriado_esfera": feriado["esfera"] if feriado else "",
            "is_vespera_feriado": int(feriado is None and amanha in por_data),
            "is_ponte": int(ponte),
            "is_dia_util": int(not is_folga(dia)),
        })
        dia = amanha

    return calendario


def gravar_csv(linhas: list[dict], colunas: list[str], caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=colunas, delimiter=",", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(linhas)


def main():
    print("Iniciando tratamento dos feriados (Camada Silver)...")

    if len(sys.argv) == 2:
        entrada = Path(sys.argv[1])
    else:
        print("[INFO] Buscando automaticamente o arquivo mais recente da camada Bronze...")
        entrada = buscar_arquivo_bronze_mais_recente()
        if not entrada:
            print(f"[ERRO] Nenhum CSV encontrado em '{PASTA_BRONZE}'. Execute extrair_feriados.py primeiro.")
            return

    print(f"[INFO] Processando arquivo: {entrada}")
    feriados, anos, stats = montar_feriados(entrada)
    if not anos:
        print("[ERRO] Nenhum feriado válido no arquivo de entrada.")
        return

    calendario = montar_calendario(feriados, anos)

    saida_feriados = PASTA_SILVER / "feriados_silver.csv"
    saida_calendario = PASTA_SILVER / "calendario_silver.csv"
    gravar_csv(
        [{**f, "data": f["data"].isoformat(), "dia_semana": DIAS_SEMANA[f["data"].weekday()]} for f in feriados],
        COLUNAS_FERIADOS,
        saida_feriados,
    )
    gravar_csv(calendario, COLUNAS_CALENDARIO, saida_calendario)

    por_esfera = {e: sum(1 for f in feriados if f["esfera"] == e) for e in PRIORIDADE_ESFERA}
    print("\n=== PIPELINE SILVER CONCLUÍDO COM SUCESSO ===")
    print(f"Anos processados:              {anos}")
    print(f"Feriados lidos da API:         {stats['api']} (sem data: {stats['api_sem_data']}, duplicados: {stats['duplicados']})")
    print(f"Feriados de SP (lei) aplicados: {stats['locais']} ({stats['sobrescritos']} reclassificaram um feriado da API)")
    print(f"Feriados finais por esfera:    {por_esfera}")
    print(f"Pontes identificadas:          {sum(c['is_ponte'] for c in calendario)}")
    print(f"Feriados -> {saida_feriados}")
    print(f"Calendário diário ({len(calendario)} dias) -> {saida_calendario}\n")


if __name__ == "__main__":
    main()

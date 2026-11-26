"""Monta a base analítica da EDA: um registro por pedido, com calendário e clima.

Junta os pedidos do banco da cafeteria (MySQL) com as bases silver de feriados e de clima
e grava dados/gold/base_eda_pedidos.csv, que é o arquivo lido pelo notebook de EDA.

Conexão com o banco por variáveis de ambiente (os valores abaixo são os padrões):
    KENTO_DB_HOST=localhost  KENTO_DB_PORTA=3306  KENTO_DB_USUARIO=developer  KENTO_DB_NOME=cafeteria
    KENTO_DB_SENHA=<senha>   (se não for definida, o script pergunta)

Dependências: pandas e pymysql (pip install pandas pymysql).
"""
from __future__ import annotations

import os
from getpass import getpass
from pathlib import Path

import pandas as pd
import pymysql

CALENDARIO = Path("./dados/silver/feriados/calendario_silver.csv")
CLIMA_DIARIO = Path("./dados/silver/open_meteo/clima_diario_silver.csv")
CLIMA_HORARIO = Path("./dados/silver/open_meteo/clima_horario_silver.csv")
SAIDA = Path("./dados/gold/base_eda_pedidos.csv")

META_MINUTOS = 10  # mesma meta de tempo da dashboard

CONSULTA = """
    SELECT p.id                               AS id_pedido,
           p.dt_hr_pedido,
           p.dt_hr_pronto,
           p.valor_total,
           p.descricao,
           s.nome                             AS status,
           COALESCE(SUM(i.quantidade), 0)     AS qtd_itens,
           MAX(c.nome = 'Bebidas Frias')      AS tem_bebida_gelada
    FROM pedido p
    JOIN status s           ON s.id = p.status_id
    LEFT JOIN item_pedido i ON i.pedido_id = p.id
    LEFT JOIN produto pr    ON pr.id = i.produto_id
    LEFT JOIN categoria c   ON c.id = pr.categoria_id
    GROUP BY p.id, p.dt_hr_pedido, p.dt_hr_pronto, p.valor_total, p.descricao, s.nome
    ORDER BY p.dt_hr_pedido
"""

# Ordem das colunas no CSV. O notebook usa as primeiras numéricas e categóricas nos gráficos
# automáticos (facetas, Simpson), então as mais importantes para o problema vêm primeiro.
COLUNAS = [
    "id_pedido", "dt_hr_pedido",
    "pedidos_na_hora", "tempo_espera_min", "valor_total", "qtd_itens", "hora",
    "temperatura_hora_c", "chuva_hora_mm", "temp_max_dia_c", "chuva_dia_mm",
    "tipo_dia", "periodo", "dia_semana", "dia_chuvoso", "condicao_dia", "faixa_temperatura",
    "vespera_ou_ponte", "tem_bebida_gelada", "status", "atrasou", "descricao",
]


def buscar_pedidos() -> pd.DataFrame:
    senha = os.getenv("KENTO_DB_SENHA")
    if senha is None:
        senha = getpass("Senha do MySQL: ")
    conexao = pymysql.connect(
        host=os.getenv("KENTO_DB_HOST", "localhost"),
        port=int(os.getenv("KENTO_DB_PORTA", "3306")),
        user=os.getenv("KENTO_DB_USUARIO", "developer"),
        password=senha,
        database=os.getenv("KENTO_DB_NOME", "cafeteria"),
        charset="utf8mb4",
    )
    try:
        with conexao.cursor() as cursor:
            cursor.execute(CONSULTA)
            colunas = [c[0] for c in cursor.description]
            return pd.DataFrame(cursor.fetchall(), columns=colunas)
    finally:
        conexao.close()


def sim_nao(serie: pd.Series) -> pd.Series:
    return serie.astype(bool).map({True: "sim", False: "não"})


def montar(pedidos: pd.DataFrame) -> pd.DataFrame:
    df = pedidos.copy()
    df["dt_hr_pedido"] = pd.to_datetime(df["dt_hr_pedido"])
    df["dt_hr_pronto"] = pd.to_datetime(df["dt_hr_pronto"])
    df["valor_total"] = df["valor_total"].astype(float)
    df["qtd_itens"] = df["qtd_itens"].astype(int)

    # Tempo de atendimento: só existe para pedidos concluídos (cancelados ficam sem valor).
    df["tempo_espera_min"] = ((df["dt_hr_pronto"] - df["dt_hr_pedido"]).dt.total_seconds() / 60).round(1)
    df["atrasou"] = sim_nao(df["tempo_espera_min"] > META_MINUTOS).where(df["tempo_espera_min"].notna())

    df["data"] = df["dt_hr_pedido"].dt.normalize()
    df["hora_cheia"] = df["dt_hr_pedido"].dt.floor("h")
    df["hora"] = df["dt_hr_pedido"].dt.hour
    df["periodo"] = pd.cut(df["hora"], bins=[-1, 11, 13, 23],
                           labels=["manhã (até 12h)", "almoço (12h-14h)", "tarde (após 14h)"]).astype(str)

    # Carga do momento: quantos pedidos foram feitos na mesma hora (inclui cancelados).
    df["pedidos_na_hora"] = df.groupby("hora_cheia")["id_pedido"].transform("size")
    df["tem_bebida_gelada"] = sim_nao(df["tem_bebida_gelada"].fillna(0).astype(int) == 1)

    cal = pd.read_csv(CALENDARIO, encoding="utf-8", parse_dates=["data"])
    cal["tipo_dia"] = "dia útil"
    cal.loc[cal["is_fim_de_semana"] == 1, "tipo_dia"] = "fim de semana"
    cal.loc[cal["is_feriado"] == 1, "tipo_dia"] = "feriado"
    cal["vespera_ou_ponte"] = sim_nao((cal["is_vespera_feriado"] == 1) | (cal["is_ponte"] == 1))
    df = df.merge(cal[["data", "dia_semana", "tipo_dia", "vespera_ou_ponte"]], on="data", how="left")

    dia = pd.read_csv(CLIMA_DIARIO, encoding="utf-8", parse_dates=["data"])
    dia = dia.rename(columns={"temp_max_c": "temp_max_dia_c", "chuva_comercial_mm": "chuva_dia_mm"})
    dia["dia_chuvoso"] = sim_nao(dia["is_dia_chuvoso"] == 1)
    df = df.merge(dia[["data", "temp_max_dia_c", "chuva_dia_mm", "dia_chuvoso",
                       "condicao_dia", "faixa_temperatura"]], on="data", how="left")

    hora = pd.read_csv(CLIMA_HORARIO, encoding="utf-8", parse_dates=["data_hora"])
    hora = hora.rename(columns={"data_hora": "hora_cheia", "temperatura_c": "temperatura_hora_c",
                                "chuva_mm": "chuva_hora_mm"})
    df = df.merge(hora[["hora_cheia", "temperatura_hora_c", "chuva_hora_mm"]], on="hora_cheia", how="left")

    return df[COLUNAS].sort_values("dt_hr_pedido")


def main():
    print("Montando a base da EDA (um registro por pedido)...")
    base = montar(buscar_pedidos())

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    # Data no formato brasileiro: o notebook lê datas com dayfirst=True, que inverte dia e mês
    # no formato ISO (2026-04-01 viraria 4 de janeiro).
    base.to_csv(SAIDA, index=False, encoding="utf-8-sig", date_format="%d/%m/%Y %H:%M:%S")

    sem_clima = base["temp_max_dia_c"].isna().sum()
    print(f"[SUCESSO] {len(base)} pedidos de {base['dt_hr_pedido'].min():%d/%m/%Y} "
          f"a {base['dt_hr_pedido'].max():%d/%m/%Y} -> {SAIDA}")
    if sem_clima:
        print(f"[AVISO] {sem_clima} pedidos sem clima do dia: rode extrair_openmeteo.py e tratar_openmeteo.py.")


if __name__ == "__main__":
    main()

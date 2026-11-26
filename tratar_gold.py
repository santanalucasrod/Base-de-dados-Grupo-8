"""Camada gold do Kento Café: tabelas prontas para análise, dashboards e modelo.

Rode a partir da raiz do repositório (Base-de-dados-Grupo-8), porque os caminhos
dados/silver e dados/gold são relativos a ela.

Junta os pedidos do banco da cafeteria (MySQL) com a camada silver de feriados e clima e
grava quatro tabelas agregadas, uma por arquivo:

    movimento_diario      um dia por linha: pedidos, faturamento, ticket, espera, pico,
                          tipo de dia, feriado e clima. É a base do modelo de previsão.
    movimento_horario     um dia e hora por linha: pedidos, espera, equipe necessária e o
                          clima da hora. Mostra os picos e quando a espera estoura a meta.
    vendas_produto        um dia e produto por linha: unidades e faturamento, com o tipo
                          de dia e a faixa de temperatura. Base do planejamento de estoque.
    consumo_ingrediente   um dia e ingrediente por linha: quantos produtos vendidos levam
                          aquele ingrediente.

Regras gerais:
  * Só entram dias completos: do primeiro pedido até ontem (hoje ainda está acontecendo e
    datas futuras, como dados de demonstração, ficam de fora).
  * Pedidos cancelados não entram em faturamento, ticket nem vendas; aparecem só na
    contagem e na taxa de cancelamento.
  * Espera = dt_hr_pronto - dt_hr_pedido, em minutos, só para pedidos prontos.
  * Horário de funcionamento: 9h às 18h. Meta de espera: 10 minutos. Capacidade de uma
    pessoa: 16 pedidos por hora (os mesmos valores das dashboards).

Uso:
    python pipeline/tratar_gold.py             # silver de dados/silver, gold em dados/gold
    python pipeline/tratar_gold.py --BUCKET_TRUSTED X --BUCKET_REFINED Y
                                               # modo Glue: silver de s3://X/silver/,
                                               # gold em s3://Y/gold/<tabela>/<tabela>.csv

Conexão com o MySQL por variáveis de ambiente (padrões entre parênteses): MYSQL_HOST
(localhost), MYSQL_PORT (3306), MYSQL_USER (kento), MYSQL_PASSWORD (sptech) e
MYSQL_DATABASE (cafeteria).

Dependências: pandas e pymysql (pip install -r modelo_previsao/requirements.txt).
"""

import argparse
import io
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pymysql

PASTA_SILVER = Path("./dados/silver")
PASTA_GOLD = Path("./dados/gold")
PREFIXO_SILVER_S3 = "silver/"
PREFIXO_GOLD_S3 = "gold/"

HORA_ABERTURA = 9
HORA_FECHAMENTO = 18
META_MINUTOS = 10
PEDIDOS_POR_PESSOA_HORA = 16

# caminho de cada CSV silver no PC e no bucket trusted (o que tratar_feriados.py e
# tratar_openmeteo.py gravam em cada modo)
SILVER = {
    "calendario": ("feriados/calendario_silver.csv", "calendario/calendario_silver.csv"),
    "clima_diario": ("open_meteo/clima_diario_silver.csv", "clima_diario/clima_diario_silver.csv"),
    "clima_horario": ("open_meteo/clima_horario_silver.csv", "clima_horario/clima_horario_silver.csv"),
}

DIAS_SEMANA = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]

CONSULTA_PEDIDOS = """
    SELECT p.id AS id_pedido, p.dt_hr_pedido, p.dt_hr_pronto, p.valor_total, s.nome AS status
    FROM pedido p
    JOIN status s ON s.id = p.status_id
    WHERE p.dt_hr_pedido < %s
"""

CONSULTA_ITENS = """
    SELECT ip.pedido_id AS id_pedido, ip.produto_id, pr.nome AS produto, c.nome AS categoria,
           ip.quantidade, ip.preco_unidade
    FROM item_pedido ip
    JOIN pedido p    ON p.id = ip.pedido_id
    JOIN status s    ON s.id = p.status_id
    JOIN produto pr  ON pr.id = ip.produto_id
    JOIN categoria c ON c.id = pr.categoria_id
    WHERE p.dt_hr_pedido < %s AND s.nome <> 'Cancelado'
"""

CONSULTA_RECEITAS = """
    SELECT pi.produto_id, i.nome AS ingrediente
    FROM produto_ingrediente pi
    JOIN ingrediente i ON i.id = pi.ingrediente_id
"""


# ── Leitura ──────────────────────────────────────────────────────────────────

def hoje_sp():
    return (datetime.now(timezone.utc) - timedelta(hours=3)).date()


def conectar():
    return pymysql.connect(
        host=os.environ.get("MYSQL_HOST", "localhost"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USER", "kento"),
        password=os.environ.get("MYSQL_PASSWORD", "sptech"),
        database=os.environ.get("MYSQL_DATABASE", "cafeteria"),
        charset="utf8mb4",
    )


def consultar(con, sql, parametros=()):
    with con.cursor() as cursor:
        cursor.execute(sql, parametros)
        return pd.DataFrame(cursor.fetchall(), columns=[c[0] for c in cursor.description])


def ler_banco(corte):
    con = conectar()
    try:
        pedidos = consultar(con, CONSULTA_PEDIDOS, (corte,))
        itens = consultar(con, CONSULTA_ITENS, (corte,))
        receitas = consultar(con, CONSULTA_RECEITAS)
    finally:
        con.close()
    pedidos["dt_hr_pedido"] = pd.to_datetime(pedidos["dt_hr_pedido"])
    pedidos["dt_hr_pronto"] = pd.to_datetime(pedidos["dt_hr_pronto"])
    pedidos["valor_total"] = pedidos["valor_total"].astype(float)
    itens["quantidade"] = itens["quantidade"].astype(int)
    itens["preco_unidade"] = itens["preco_unidade"].astype(float)
    return pedidos, itens, receitas


def ler_silver(s3=None, bucket=None):
    tabelas = {}
    for nome, (local, chave) in SILVER.items():
        if s3:
            corpo = s3.get_object(Bucket=bucket, Key=PREFIXO_SILVER_S3 + chave)["Body"].read()
            tabelas[nome] = pd.read_csv(io.BytesIO(corpo), encoding="utf-8-sig")
        else:
            tabelas[nome] = pd.read_csv(PASTA_SILVER / local, encoding="utf-8-sig")
    tabelas["calendario"]["data"] = pd.to_datetime(tabelas["calendario"]["data"])
    tabelas["clima_diario"]["data"] = pd.to_datetime(tabelas["clima_diario"]["data"])
    tabelas["clima_horario"]["data_hora"] = pd.to_datetime(tabelas["clima_horario"]["data_hora"])
    return tabelas


# ── Montagem ─────────────────────────────────────────────────────────────────

def mediana(serie):
    serie = serie.dropna()
    return round(float(serie.median()), 1) if len(serie) else None


def no_prazo(serie):
    serie = serie.dropna()
    return round(100 * float((serie <= META_MINUTOS).mean()), 1) if len(serie) else None


def preparar_pedidos(pedidos):
    df = pedidos.copy()
    df["data"] = df["dt_hr_pedido"].dt.normalize()
    df["hora"] = df["dt_hr_pedido"].dt.hour
    df["cancelado"] = df["status"] == "Cancelado"
    df["valido"] = ~df["cancelado"]
    pronto = df["status"] == "Pronto"
    df["espera_min"] = ((df["dt_hr_pronto"] - df["dt_hr_pedido"]).dt.total_seconds() / 60).where(pronto)
    return df


def contexto_do_dia(calendario, clima_diario):
    """Calendário e clima de cada dia, com o tipo de dia na mesma regra das dashboards."""
    cal = calendario.copy()
    cal["tipo_dia"] = "Dia útil"
    cal.loc[cal["is_fim_de_semana"] == 1, "tipo_dia"] = "Fim de semana"
    cal.loc[cal["is_feriado"] == 1, "tipo_dia"] = "Feriado"
    cal = cal[["data", "tipo_dia", "feriado_nome", "is_feriado", "is_vespera_feriado", "is_ponte"]]
    clima = clima_diario[["data", "temp_max_c", "chuva_comercial_mm", "is_dia_chuvoso",
                          "faixa_temperatura", "condicao_dia", "fonte"]].rename(
        columns={"chuva_comercial_mm": "chuva_mm", "fonte": "fonte_clima"})
    return cal.merge(clima, on="data", how="left")


def montar_movimento_diario(ped, itens, contexto):
    validos = ped[ped["valido"]]
    por_dia = ped.groupby("data").agg(pedidos_total=("id_pedido", "size"),
                                      cancelados=("cancelado", "sum"))
    val = validos.groupby("data").agg(pedidos=("id_pedido", "size"),
                                      faturamento=("valor_total", "sum"),
                                      espera_mediana_min=("espera_min", mediana),
                                      pct_no_prazo=("espera_min", no_prazo))
    unidades = itens.merge(validos[["id_pedido", "data"]], on="id_pedido").groupby("data")["quantidade"].sum()
    por_hora = validos.groupby(["data", "hora"]).size().rename("n").reset_index()
    pico = por_hora.sort_values(["data", "n", "hora"], ascending=[True, False, True]).drop_duplicates("data")
    pico = pico.set_index("data").rename(columns={"hora": "hora_pico", "n": "pedidos_no_pico"})

    dia = por_dia.join(val, how="left").join(unidades.rename("itens"), how="left").join(pico, how="left")
    dia = dia.fillna({"pedidos": 0, "faturamento": 0.0, "itens": 0}).reset_index()
    dia["ticket_medio"] = (dia["faturamento"] / dia["pedidos"]).where(dia["pedidos"] > 0).round(2)
    dia["itens_por_pedido"] = (dia["itens"] / dia["pedidos"]).where(dia["pedidos"] > 0).round(2)
    dia["taxa_cancelamento_pct"] = (100 * dia["cancelados"] / dia["pedidos_total"]).round(2)
    dia["equipe_no_pico"] = dia["pedidos_no_pico"].apply(
        lambda n: max(1, math.ceil(n / PEDIDOS_POR_PESSOA_HORA)) if pd.notna(n) else None)
    dia["dia_semana"] = dia["data"].dt.weekday.map(lambda i: DIAS_SEMANA[i])
    dia = dia.merge(contexto, on="data", how="left")
    dia["faturamento"] = dia["faturamento"].round(2)

    return dia[["data", "dia_semana", "tipo_dia", "feriado_nome", "is_vespera_feriado", "is_ponte",
                "pedidos", "cancelados", "taxa_cancelamento_pct", "faturamento", "ticket_medio",
                "itens_por_pedido", "espera_mediana_min", "pct_no_prazo", "hora_pico",
                "pedidos_no_pico", "equipe_no_pico", "temp_max_c", "chuva_mm", "is_dia_chuvoso",
                "faixa_temperatura", "condicao_dia", "fonte_clima"]]


def montar_movimento_horario(ped, contexto, clima_horario):
    abertas = ped[(ped["hora"] >= HORA_ABERTURA) & (ped["hora"] < HORA_FECHAMENTO)]
    validos = abertas[abertas["valido"]]
    hora = abertas.groupby(["data", "hora"]).agg(cancelados=("cancelado", "sum")).join(
        validos.groupby(["data", "hora"]).agg(pedidos=("id_pedido", "size"),
                                              faturamento=("valor_total", "sum"),
                                              espera_mediana_min=("espera_min", mediana),
                                              pct_no_prazo=("espera_min", no_prazo)), how="left")
    hora = hora.fillna({"pedidos": 0, "faturamento": 0.0}).reset_index()
    hora["faturamento"] = hora["faturamento"].round(2)
    hora["equipe_necessaria"] = hora["pedidos"].apply(lambda n: max(1, math.ceil(n / PEDIDOS_POR_PESSOA_HORA)))
    hora["dia_semana"] = hora["data"].dt.weekday.map(lambda i: DIAS_SEMANA[i])
    hora = hora.merge(contexto[["data", "tipo_dia", "is_dia_chuvoso"]], on="data", how="left")

    clima = clima_horario[["data_hora", "temperatura_c", "chuva_mm"]].rename(
        columns={"temperatura_c": "temperatura_hora_c", "chuva_mm": "chuva_hora_mm"})
    hora["data_hora"] = hora["data"] + pd.to_timedelta(hora["hora"], unit="h")
    hora = hora.merge(clima, on="data_hora", how="left")

    return hora[["data", "hora", "dia_semana", "tipo_dia", "pedidos", "cancelados", "faturamento",
                 "espera_mediana_min", "pct_no_prazo", "equipe_necessaria", "temperatura_hora_c",
                 "chuva_hora_mm", "is_dia_chuvoso"]]


def montar_vendas_produto(ped, itens, contexto):
    vendas = itens.merge(ped.loc[ped["valido"], ["id_pedido", "data"]], on="id_pedido")
    vendas["faturamento"] = vendas["quantidade"] * vendas["preco_unidade"]
    vp = vendas.groupby(["data", "produto_id", "produto", "categoria"]).agg(
        unidades=("quantidade", "sum"), faturamento=("faturamento", "sum"),
        pedidos_com_produto=("id_pedido", "nunique")).reset_index()
    vp["faturamento"] = vp["faturamento"].round(2)
    vp = vp.merge(contexto[["data", "tipo_dia", "faixa_temperatura", "is_dia_chuvoso"]], on="data", how="left")
    return vp[["data", "produto_id", "produto", "categoria", "unidades", "faturamento",
               "pedidos_com_produto", "tipo_dia", "faixa_temperatura", "is_dia_chuvoso"]]


def montar_consumo_ingrediente(vendas_produto, receitas):
    consumo = vendas_produto.merge(receitas, on="produto_id")
    ci = consumo.groupby(["data", "ingrediente"]).agg(
        produtos_vendidos=("unidades", "sum"), itens_do_cardapio=("produto_id", "nunique")).reset_index()
    return ci[["data", "ingrediente", "produtos_vendidos", "itens_do_cardapio"]]


# ── Gravação ─────────────────────────────────────────────────────────────────

def em_csv(df):
    saida = df.copy()
    for coluna in saida.select_dtypes(include="datetime").columns:
        saida[coluna] = saida[coluna].dt.strftime("%Y-%m-%d")
    for coluna in ["hora_pico", "pedidos_no_pico", "equipe_no_pico", "is_dia_chuvoso",
                   "is_vespera_feriado", "is_ponte"]:
        if coluna in saida:
            saida[coluna] = saida[coluna].astype("Int64")
    return saida.to_csv(index=False).encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description="Gera a camada gold do Kento Café.")
    parser.add_argument("--BUCKET_TRUSTED", default=os.environ.get("BUCKET_TRUSTED"))
    parser.add_argument("--BUCKET_REFINED", default=os.environ.get("BUCKET_REFINED"))
    args, _ = parser.parse_known_args()
    modo_s3 = bool(args.BUCKET_TRUSTED and args.BUCKET_REFINED)
    s3 = None
    if modo_s3:
        import boto3
        s3 = boto3.client("s3")

    corte = hoje_sp()
    print(f"Montando a camada gold com os dias completos até {corte - timedelta(days=1):%d/%m/%Y}...")
    pedidos, itens, receitas = ler_banco(corte)
    if pedidos.empty:
        raise SystemExit("[ERRO] Nenhum pedido encontrado no banco.")
    silver = ler_silver(s3, args.BUCKET_TRUSTED)

    ped = preparar_pedidos(pedidos)
    contexto = contexto_do_dia(silver["calendario"], silver["clima_diario"])
    vendas = montar_vendas_produto(ped, itens, contexto)
    tabelas = {
        "movimento_diario": montar_movimento_diario(ped, itens, contexto),
        "movimento_horario": montar_movimento_horario(ped, contexto, silver["clima_horario"]),
        "vendas_produto": vendas,
        "consumo_ingrediente": montar_consumo_ingrediente(vendas, receitas),
    }

    for nome, df in tabelas.items():
        corpo = em_csv(df)
        if modo_s3:
            chave = f"{PREFIXO_GOLD_S3}{nome}/{nome}.csv"
            s3.put_object(Bucket=args.BUCKET_REFINED, Key=chave, Body=corpo, ContentType="text/csv; charset=utf-8")
            destino = f"s3://{args.BUCKET_REFINED}/{chave}"
        else:
            PASTA_GOLD.mkdir(parents=True, exist_ok=True)
            destino = PASTA_GOLD / f"{nome}.csv"
            destino.write_bytes(corpo)
        print(f"  {nome}: {len(df)} linhas -> {destino}")

    dias = tabelas["movimento_diario"]
    sem_clima = int(dias["temp_max_c"].isna().sum())
    print(f"[SUCESSO] {len(dias)} dias, de {dias['data'].min():%d/%m/%Y} a {dias['data'].max():%d/%m/%Y}.")
    if sem_clima:
        print(f"[AVISO] {sem_clima} dias sem clima carregado: rode extrair_openmeteo.py e tratar_openmeteo.py.")


if __name__ == "__main__":
    main()

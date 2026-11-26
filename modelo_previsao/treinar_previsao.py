"""Previsão de pedidos por dia do Kento Café com regressão linear.

O que o script faz, em ordem:
  1. Lê do MySQL o histórico de pedidos por dia, já com calendário (feriados) e clima.
  2. Valida 5 candidatos com validação temporal (rolling origin): para cada semana do
     histórico, treina só com os dias ANTERIORES e prevê os 7 dias seguintes, como seria
     na vida real. Mede o erro (MAE, MAPE, RMSE) de cada candidato nos mesmos dias:
       - media_8_semanas            regra antiga: média do mesmo dia da semana (8 semanas)
       - semana_anterior            ingênuo sazonal: repete o mesmo dia da semana passada
       - regressao_calendario       MQO em log(pedidos) ~ dia da semana + feriado/véspera/ponte
       - regressao_calendario_clima + chuva + temperatura máxima
       - regressao_completa         + tendência (semanas desde o início do treino)
  3. Escolhe o candidato de menor erro médio absoluto (MAE) na validação.
  4. Treina o escolhido com todo o histórico e prevê os próximos 35 dias, com faixa
     provável de 80%. Sem previsão do tempo para um dia, usa o clima médio do mês.
  5. Grava tudo no MySQL (modelo_execucao, modelo_validacao, modelo_coeficiente,
     previsao_dia). As dashboards leem a última execução.

Por que log(pedidos): os fatores agem em proporção ("chuva tira ~9% do movimento"),
não em quantidade fixa. No log, cada coeficiente vira um efeito percentual:
(e^coef - 1) x 100. Para voltar para pedidos sem subestimar a média, aplica-se a
correção de Duan (smearing estimator).

Uso (na pasta Base-de-dados-Grupo-8, depois de carregar feriados e clima):
    pip install -r modelo_previsao/requirements.txt
    python modelo_previsao/treinar_previsao.py
    python modelo_previsao/treinar_previsao.py --hoje 2026-12-05     # simula outra data
    python modelo_previsao/treinar_previsao.py --sem-gravar          # só mostra o resultado

Conexão: variáveis de ambiente MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD e
MYSQL_DATABASE (padrão: localhost:3306, usuário kento, senha sptech, banco cafeteria).
"""

import argparse
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pymysql
import statsmodels.api as sm
from statsmodels.stats.diagnostic import het_breuschpagan
from statsmodels.stats.stattools import durbin_watson, jarque_bera

HORIZONTE_DIAS = 35            # quantos dias à frente gravar
JANELA_MEDIA_DIAS = 56         # 8 semanas (regra antiga)
TREINO_MINIMO_DIAS = 56        # a validação só começa com 8 semanas de histórico
PASSO_VALIDACAO_DIAS = 7       # uma origem de validação por semana
DIAS_PREVISTOS_POR_ORIGEM = 7  # cada origem prevê a semana seguinte
NIVEL_FAIXA = 0.80             # faixa provável de 80%
TEMPERATURA_REFERENCIA = 25.0  # a temperatura entra como (máxima - 25 °C)
RELATORIO = Path(__file__).with_name("ultimo_treino.txt")

DIAS_SEMANA = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]
TERMOS_CALENDARIO = ["ter", "qua", "qui", "sex", "sab", "dom", "feriado", "vespera", "ponte"]
TERMOS_CLIMA = ["chuva", "temp"]

CANDIDATOS = {
    "media_8_semanas": ("Média do mesmo dia da semana nas últimas 8 semanas (regra anterior)", None),
    "semana_anterior": ("Repete o mesmo dia da semana anterior", None),
    "regressao_calendario": ("Regressão linear: dia da semana, feriado, véspera e ponte",
                             TERMOS_CALENDARIO),
    "regressao_calendario_clima": ("Regressão linear: calendário + chuva + temperatura máxima",
                                   TERMOS_CALENDARIO + TERMOS_CLIMA),
    "regressao_completa": ("Regressão linear: calendário + clima + tendência semanal",
                           TERMOS_CALENDARIO + TERMOS_CLIMA + ["tendencia"]),
}

DESCRICAO_TERMOS = {
    "const": "Base: segunda-feira comum, sem chuva, máxima de 25 °C",
    "ter": "Terça-feira (comparada à segunda)",
    "qua": "Quarta-feira (comparada à segunda)",
    "qui": "Quinta-feira (comparada à segunda)",
    "sex": "Sexta-feira (comparada à segunda)",
    "sab": "Sábado (comparado à segunda)",
    "dom": "Domingo (comparado à segunda)",
    "feriado": "Feriado em dia útil",
    "vespera": "Véspera de feriado",
    "ponte": "Ponte de feriado",
    "chuva": "Dia chuvoso",
    "temp": "Cada 1 °C a mais na máxima",
    "tendencia": "Cada semana a mais (tendência)",
}


# ── Dados ────────────────────────────────────────────────────────────────────

def conectar():
    return pymysql.connect(
        host=os.environ.get("MYSQL_HOST", "localhost"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USER", "kento"),
        password=os.environ.get("MYSQL_PASSWORD", "sptech"),
        database=os.environ.get("MYSQL_DATABASE", "cafeteria"),
        charset="utf8mb4",
    )


def consultar(con, sql: str, parametros=()) -> pd.DataFrame:
    with con.cursor() as cursor:
        cursor.execute(sql, parametros)
        colunas = [c[0] for c in cursor.description]
        return pd.DataFrame(cursor.fetchall(), columns=colunas)


def carregar_historico(con, hoje: date) -> pd.DataFrame:
    df = consultar(con, """
        SELECT data, pedidos, tipo_dia, is_vespera_feriado, is_ponte, is_dia_chuvoso, temp_max_c
        FROM vw_dia
        WHERE data < %s AND pedidos > 0
        ORDER BY data""", (hoje,))
    return padronizar(df)


def carregar_futuro(con, hoje: date) -> pd.DataFrame:
    df = consultar(con, """
        SELECT c.data,
               CASE WHEN c.is_feriado = 1 THEN 'Feriado'
                    WHEN WEEKDAY(c.data) >= 5 THEN 'Fim de semana' ELSE 'Dia útil' END AS tipo_dia,
               c.is_vespera_feriado, c.is_ponte, cd.is_dia_chuvoso, cd.temp_max_c
        FROM calendario c
        LEFT JOIN clima_diario cd ON cd.data = c.data
        WHERE c.data >= %s AND c.data < %s
        ORDER BY c.data""", (hoje, hoje + timedelta(days=HORIZONTE_DIAS)))
    return padronizar(df)


def padronizar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["data"] = pd.to_datetime(df["data"])
    for coluna in ["pedidos", "is_vespera_feriado", "is_ponte", "is_dia_chuvoso", "temp_max_c"]:
        if coluna in df:
            df[coluna] = pd.to_numeric(df[coluna], errors="coerce").astype(float)
    df["dia_semana"] = df["data"].dt.weekday
    # Feriado só conta em dia útil: no histórico, todo feriado caiu de segunda a sexta, então não
    # há como estimar se um feriado no sábado/domingo enche ainda mais. Num fim de semana ele é
    # tratado como um fim de semana comum (estimativa conservadora).
    df["feriado"] = ((df["tipo_dia"] == "Feriado") & (df["dia_semana"] < 5)).astype(float)
    df["vespera"] = df["is_vespera_feriado"].fillna(0)
    df["ponte"] = df["is_ponte"].fillna(0)
    df["chuva"] = df["is_dia_chuvoso"]
    df["temp"] = df["temp_max_c"] - TEMPERATURA_REFERENCIA
    for i, nome in enumerate(DIAS_SEMANA[1:], start=1):
        df[nome] = (df["dia_semana"] == i).astype(float)
    return df.reset_index(drop=True)


def clima_medio(treino: pd.DataFrame, alvo: pd.DataFrame) -> pd.DataFrame:
    """Preenche chuva e temperatura que faltam com o esperado para o mês (ou o geral).

    Sem previsão do tempo, a chuva vira a probabilidade histórica de chover naquele mês
    e a temperatura, a média das máximas do mês. Num modelo linear, isso dá o efeito
    esperado do clima.
    """
    alvo = alvo.copy()
    com_clima = treino.dropna(subset=["chuva", "temp"])
    alvo["clima_usado"] = np.where(alvo["chuva"].notna() & alvo["temp"].notna(), "previsão", "média histórica")
    for i, linha in alvo.iterrows():
        if linha["clima_usado"] == "previsão":
            continue
        mes = com_clima[com_clima["data"].dt.month == linha["data"].month]
        base = mes if len(mes) >= 15 else com_clima
        alvo.at[i, "chuva"] = base["chuva"].mean()
        alvo.at[i, "temp"] = base["temp"].mean() if len(mes) >= 15 else com_clima["temp"].tail(30).mean()
    return alvo


# ── Modelos ──────────────────────────────────────────────────────────────────

def matriz(df: pd.DataFrame, termos: list[str], inicio_treino: pd.Timestamp) -> pd.DataFrame:
    X = df[[t for t in termos if t != "tendencia"]].copy()
    if "tendencia" in termos:
        X["tendencia"] = (df["data"] - inicio_treino).dt.days / 7
    return sm.add_constant(X, has_constant="add")


class Regressao:
    def __init__(self, termos: list[str]):
        self.termos = termos

    def ajustar(self, treino: pd.DataFrame):
        self.inicio = treino["data"].min()
        X = matriz(treino, self.termos, self.inicio)
        # termos que não variam no treino (ex.: nenhuma ponte ainda) não podem ser estimados
        self.colunas = [c for c in X.columns if c == "const" or X[c].nunique() > 1]
        self.resultado = sm.OLS(np.log(treino["pedidos"]), X[self.colunas]).fit()
        self.smearing = float(np.mean(np.exp(self.resultado.resid)))
        return self

    def prever(self, alvo: pd.DataFrame, nivel: float = NIVEL_FAIXA) -> pd.DataFrame:
        X = matriz(alvo, self.termos, self.inicio)[self.colunas]
        quadro = self.resultado.get_prediction(X).summary_frame(alpha=1 - nivel)
        return pd.DataFrame({
            "previsto": np.exp(quadro["mean"].to_numpy()) * self.smearing,
            "minimo": np.exp(quadro["obs_ci_lower"].to_numpy()),
            "maximo": np.exp(quadro["obs_ci_upper"].to_numpy()),
        }, index=alvo.index)


def prever_media_8_semanas(treino: pd.DataFrame, alvo: pd.DataFrame) -> np.ndarray:
    fim = treino["data"].max()
    recente = treino[(treino["data"] > fim - pd.Timedelta(days=JANELA_MEDIA_DIAS)) & (treino["feriado"] == 0)]
    feriados = treino[treino["feriado"] == 1]
    previsoes = []
    for _, linha in alvo.iterrows():
        if linha["feriado"] == 1 and len(feriados):
            previsoes.append(feriados["pedidos"].mean())
            continue
        mesmo_dia = recente[recente["dia_semana"] == linha["dia_semana"]]
        previsoes.append(mesmo_dia["pedidos"].mean() if len(mesmo_dia) else recente["pedidos"].mean())
    return np.array(previsoes)


def prever_semana_anterior(treino: pd.DataFrame, alvo: pd.DataFrame) -> np.ndarray:
    previsoes = []
    for _, linha in alvo.iterrows():
        mesmo_dia = treino[treino["dia_semana"] == linha["dia_semana"]]
        previsoes.append(mesmo_dia["pedidos"].iloc[-1] if len(mesmo_dia) else treino["pedidos"].mean())
    return np.array(previsoes)


def prever_candidato(nome: str, treino: pd.DataFrame, alvo: pd.DataFrame) -> pd.DataFrame:
    termos = CANDIDATOS[nome][1]
    if termos is None:
        funcao = prever_media_8_semanas if nome == "media_8_semanas" else prever_semana_anterior
        return pd.DataFrame({"previsto": funcao(treino, alvo)}, index=alvo.index)
    return Regressao(termos).ajustar(treino).prever(alvo)


# ── Validação temporal ───────────────────────────────────────────────────────

def validar(historico: pd.DataFrame) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """Rolling origin: treina com o passado de cada origem e prevê a semana seguinte."""
    dados = historico.dropna(subset=["chuva", "temp"]).reset_index(drop=True)
    inicio = dados["data"].min() + pd.Timedelta(days=TREINO_MINIMO_DIAS)
    origens = pd.date_range(inicio, dados["data"].max(), freq=f"{PASSO_VALIDACAO_DIAS}D")
    registros = []
    for origem in origens:
        treino = dados[dados["data"] < origem]
        teste = dados[(dados["data"] >= origem)
                      & (dados["data"] < origem + pd.Timedelta(days=DIAS_PREVISTOS_POR_ORIGEM))]
        if teste.empty:
            continue
        teste_sem_clima = clima_medio(treino, teste.assign(chuva=np.nan, temp=np.nan))
        for nome in CANDIDATOS:
            previsao = prever_candidato(nome, treino, teste)
            sem_clima = (prever_candidato(nome, treino, teste_sem_clima)["previsto"]
                         if CANDIDATOS[nome][1] and "chuva" in CANDIDATOS[nome][1] else previsao["previsto"])
            for i in teste.index:
                real = teste.at[i, "pedidos"]
                registros.append({
                    "modelo": nome, "origem": origem, "data": teste.at[i, "data"], "real": real,
                    "previsto": previsao.at[i, "previsto"], "previsto_sem_clima": sem_clima[i],
                    "dentro_faixa": float(previsao.at[i, "minimo"] <= real <= previsao.at[i, "maximo"])
                    if "minimo" in previsao else np.nan,
                })
    detalhe = pd.DataFrame(registros)
    detalhe["erro"] = detalhe["previsto"] - detalhe["real"]
    detalhe["erro_sem_clima"] = detalhe["previsto_sem_clima"] - detalhe["real"]
    resumo = detalhe.groupby("modelo").apply(lambda g: pd.Series({
        "dias_testados": len(g),
        "mae": g["erro"].abs().mean(),
        "mape": (g["erro"].abs() / g["real"]).mean() * 100,
        "rmse": np.sqrt((g["erro"] ** 2).mean()),
        "mape_sem_clima": (g["erro_sem_clima"].abs() / g["real"]).mean() * 100,
        "cobertura": g["dentro_faixa"].mean() * 100 if g["dentro_faixa"].notna().any() else np.nan,
    }), include_groups=False).reindex(list(CANDIDATOS))
    info = {"origens": len(origens), "inicio": dados["data"].min(), "fim": dados["data"].max()}
    return resumo, info, detalhe


def diebold_mariano(detalhe: pd.DataFrame, modelo_a: str, modelo_b: str) -> float:
    """p-valor do teste de Diebold-Mariano (erro absoluto) entre dois modelos.

    Compara, dia a dia, o erro dos dois modelos nos mesmos dias. Variância com correção
    de Newey-West (HAC), porque erros de dias vizinhos costumam ser parecidos.
    p < 0,05: a diferença de acerto entre os dois é estatisticamente significativa.
    """
    if modelo_a == modelo_b:
        return float("nan")
    a = detalhe[detalhe["modelo"] == modelo_a].set_index("data")["erro"].abs()
    b = detalhe[detalhe["modelo"] == modelo_b].set_index("data")["erro"].abs()
    diferenca = (a - b).dropna()
    teste = sm.OLS(diferenca.to_numpy(), np.ones(len(diferenca))).fit(
        cov_type="HAC", cov_kwds={"maxlags": DIAS_PREVISTOS_POR_ORIGEM - 1})
    return float(teste.pvalues[0])


def escolher_modelo(resumo: pd.DataFrame, detalhe: pd.DataFrame) -> tuple[str, str]:
    """Escolhe a melhor regressão, a não ser que outro candidato seja SIGNIFICATIVAMENTE melhor.

    Entre modelos com erro estatisticamente igual, a regressão é preferida: ela mede o efeito
    de cada fator (com intervalo de confiança) e dá uma faixa de previsão calibrada.
    """
    regressoes = [n for n, (_, termos) in CANDIDATOS.items() if termos is not None]
    melhor_regressao = resumo.loc[regressoes, "mae"].idxmin()
    melhor_geral = resumo["mae"].idxmin()
    if melhor_geral == melhor_regressao:
        return melhor_regressao, "regressão com o menor erro médio absoluto na validação"
    p = diebold_mariano(detalhe, melhor_regressao, melhor_geral)
    if p < 0.05:
        return melhor_geral, (f"'{melhor_geral}' erra significativamente menos que a melhor regressão "
                              f"(Diebold-Mariano p = {p:.3f})")
    return melhor_regressao, (f"menor erro entre as regressões; '{melhor_geral}' errou um pouco menos, "
                              f"mas sem diferença significativa (Diebold-Mariano p = {p:.2f})")


# ── Execução ─────────────────────────────────────────────────────────────────

def diagnosticos(modelo: Regressao) -> str:
    r = modelo.resultado
    jb_p = jarque_bera(r.resid)[1]
    bp_p = het_breuschpagan(r.resid, r.model.exog)[1]
    return (f"R² = {r.rsquared:.3f} | R² ajustado = {r.rsquared_adj:.3f} | "
            f"Durbin-Watson = {durbin_watson(r.resid):.2f} (perto de 2 = resíduos sem autocorrelação) | "
            f"Jarque-Bera p = {jb_p:.3f} (normalidade) | Breusch-Pagan p = {bp_p:.3f} (variância constante)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hoje", type=date.fromisoformat, default=(datetime.now(timezone.utc) - timedelta(hours=3)).date(),
                        help="data de referência (padrão: hoje no horário de Brasília)")
    parser.add_argument("--sem-gravar", action="store_true", help="só mostra o resultado, não grava no banco")
    args = parser.parse_args()

    con = conectar()
    historico = carregar_historico(con, args.hoje)
    if len(historico.dropna(subset=["chuva", "temp"])) < TREINO_MINIMO_DIAS + DIAS_PREVISTOS_POR_ORIGEM:
        raise SystemExit("Histórico curto demais para validar o modelo (precisa de ~9 semanas com clima).")

    resumo, info, detalhe = validar(historico)
    escolhido, motivo = escolher_modelo(resumo, detalhe)
    descricao, termos = CANDIDATOS[escolhido]
    p_vs_anterior = diebold_mariano(detalhe, escolhido, "media_8_semanas")

    linhas = [f"Treino de {datetime.now():%d/%m/%Y %H:%M} | referência {args.hoje:%d/%m/%Y}",
              f"Validação: {info['origens']} semanas, de {info['inicio']:%d/%m/%Y} a {info['fim']:%d/%m/%Y}",
              f"Escolha: {motivo}", "",
              f"{'modelo':28} {'dias':>5} {'MAE':>7} {'MAPE':>7} {'RMSE':>7} {'sem clima':>10} {'faixa 80%':>10}"]
    for nome, l in resumo.iterrows():
        marca = "  <- escolhido" if nome == escolhido else ""
        linhas.append(f"{nome:28} {l.dias_testados:5.0f} {l.mae:7.2f} {l.mape:6.1f}% {l.rmse:7.2f} "
                      f"{l.mape_sem_clima:9.1f}% {l.cobertura:9.1f}%{marca}".replace("nan%", "  - "))
    if not np.isnan(p_vs_anterior):
        linhas.append(f"\nDiebold-Mariano, {escolhido} x media_8_semanas: p = {p_vs_anterior:.3f} "
                      f"({'diferença significativa' if p_vs_anterior < 0.05 else 'diferença NÃO significativa'} a 5%)")

    # treino final com todo o histórico que tem as variáveis do modelo escolhido
    precisa_clima = termos is not None and "chuva" in termos
    treino = historico.dropna(subset=["chuva", "temp"]) if precisa_clima else historico
    futuro = clima_medio(treino, carregar_futuro(con, args.hoje))
    if termos is not None:
        modelo = Regressao(termos).ajustar(treino)
        previsao = modelo.prever(futuro)
        linhas += ["", diagnosticos(modelo), "", str(modelo.resultado.summary())]
    else:
        # regra simples venceu: a faixa de 80% vem dos erros reais dela na validação
        modelo = None
        previsao = prever_candidato(escolhido, treino, futuro)
        d = detalhe[detalhe["modelo"] == escolhido]
        razao = d["real"] / d["previsto"]
        previsao["minimo"] = previsao["previsto"] * razao.quantile((1 - NIVEL_FAIXA) / 2)
        previsao["maximo"] = previsao["previsto"] * razao.quantile((1 + NIVEL_FAIXA) / 2)

    relatorio = "\n".join(linhas)
    print(relatorio)
    RELATORIO.write_text(relatorio, encoding="utf-8")
    if args.sem_gravar:
        return

    l = resumo.loc[escolhido]
    anterior = resumo.loc["media_8_semanas"]
    r = modelo.resultado if modelo else None
    with con.cursor() as cursor:
        cursor.execute("""
            INSERT INTO modelo_execucao (executado_em, data_referencia, modelo, descricao, dias_treino,
                treino_inicio, treino_fim, r2, r2_ajustado, mae_validacao, mape_validacao, mape_sem_clima,
                cobertura_intervalo, mae_regra_anterior, mape_regra_anterior, p_valor_vs_regra_anterior)
            VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (args.hoje, escolhido, descricao, len(treino), treino["data"].min().date(), treino["data"].max().date(),
             r.rsquared if r is not None else None, r.rsquared_adj if r is not None else None,
             round(l.mae, 2), round(l.mape, 2), round(l.mape_sem_clima, 2),
             None if np.isnan(l.cobertura) else round(l.cobertura, 2),
             round(anterior.mae, 2), round(anterior.mape, 2),
             None if np.isnan(p_vs_anterior) else round(p_vs_anterior, 4)))
        execucao = cursor.lastrowid
        cursor.executemany("""
            INSERT INTO modelo_validacao (execucao_id, modelo, descricao, dias_testados, mae, mape, rmse, escolhido)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            [(execucao, nome, CANDIDATOS[nome][0], int(v.dias_testados), round(v.mae, 2), round(v.mape, 2),
              round(v.rmse, 2), int(nome == escolhido)) for nome, v in resumo.iterrows()])
        if r is not None:
            ic = r.conf_int(alpha=0.05)
            cursor.executemany("""
                INSERT INTO modelo_coeficiente (execucao_id, termo, descricao, coeficiente, efeito_pct,
                    efeito_min_pct, efeito_max_pct, p_valor)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                [(execucao, termo, DESCRICAO_TERMOS[termo], float(coef),
                  None if termo == "const" else round((np.exp(coef) - 1) * 100, 2),
                  None if termo == "const" else round((np.exp(ic.loc[termo, 0]) - 1) * 100, 2),
                  None if termo == "const" else round((np.exp(ic.loc[termo, 1]) - 1) * 100, 2),
                  float(r.pvalues[termo])) for termo, coef in r.params.items()])
        cursor.executemany("""
            INSERT INTO previsao_dia (execucao_id, data, pedidos_previstos, pedidos_min, pedidos_max,
                clima_usado, prob_chuva, temp_max_usada)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            [(execucao, futuro.at[i, "data"].date(), round(float(previsao.at[i, "previsto"]), 1),
              round(float(previsao.at[i, "minimo"]), 1), round(float(previsao.at[i, "maximo"]), 1),
              futuro.at[i, "clima_usado"], round(float(futuro.at[i, "chuva"]), 2),
              round(float(futuro.at[i, "temp"]) + TEMPERATURA_REFERENCIA, 1)) for i in futuro.index])
    con.commit()
    con.close()
    print(f"\nExecução {execucao} gravada: modelo '{escolhido}', {len(futuro)} dias previstos.")


if __name__ == "__main__":
    main()

"""Gera o SQL que carrega as bases externas (feriados e clima) no MySQL da cafeteria.

O Grafana lê direto do banco `cafeteria`, então as camadas silver de feriados e de
clima precisam estar lá como tabelas, ao lado dos pedidos. Este script lê os CSVs
silver e escreve um arquivo .sql com INSERT ... ON DUPLICATE KEY UPDATE: rodar de
novo com CSVs mais novos só atualiza as linhas, sem duplicar nada.

Uso (a partir da pasta Base-de-dados-Grupo-8):
    python grafana/carregar_bases_mysql.py                          # CSVs de dados/silver (PC)
    python grafana/carregar_bases_mysql.py --bucket-trusted NOME    # CSVs do bucket trusted (AWS)
    mysql -u kento -p cafeteria < grafana/02_carga_bases_externas.sql

Na AWS, os jobs do Glue gravam uma pasta por tabela no bucket trusted
(silver/calendario/, silver/feriados/, silver/clima_diario/, silver/clima_horario/).
Com --bucket-trusted o script lê esses arquivos com o boto3, usando as credenciais da
máquina (a role da EC2 ou o "aws configure"). Também dá para usar a variável BUCKET_TRUSTED.

As tabelas precisam existir antes: rode uma vez o grafana/01_tabelas_bases_externas.sql.
"""

import argparse
import csv
import io
import os
from datetime import datetime
from pathlib import Path

PASTA_SILVER = Path("./dados/silver")
SAIDA = Path("./grafana/02_carga_bases_externas.sql")
LINHAS_POR_INSERT = 500

# Caminho de cada tabela no bucket trusted (o mesmo que tratar_feriados.py e
# tratar_openmeteo.py gravam no modo Glue).
CHAVES_S3 = {
    "calendario": "silver/calendario/calendario_silver.csv",
    "feriado": "silver/feriados/feriados_silver.csv",
    "clima_diario": "silver/clima_diario/clima_diario_silver.csv",
    "clima_horario": "silver/clima_horario/clima_horario_silver.csv",
}

# tabela -> (arquivo silver no PC, colunas na ordem do CSV)
BASES = {
    "calendario": (
        "feriados/calendario_silver.csv",
        ["data", "ano", "mes", "dia_semana", "is_fim_de_semana", "is_feriado", "feriado_nome",
         "feriado_esfera", "is_vespera_feriado", "is_ponte", "is_dia_util"],
    ),
    "feriado": (
        "feriados/feriados_silver.csv",
        ["data", "dia_semana", "nome", "esfera", "fonte", "base_legal"],
    ),
    "clima_diario": (
        "open_meteo/clima_diario_silver.csv",
        ["data", "temp_min_c", "temp_media_c", "temp_max_c", "sensacao_max_c", "umidade_media_pct",
         "nuvens_media_pct", "vento_max_kmh", "chuva_comercial_mm", "chuva_max_hora_mm",
         "horas_com_chuva", "chuva_dia_todo_mm", "condicao_dia", "faixa_temperatura",
         "is_dia_chuvoso", "is_chuva_forte", "fonte", "horas_disponiveis"],
    ),
    "clima_horario": (
        "open_meteo/clima_horario_silver.csv",
        ["data", "hora", "data_hora", "temperatura_c", "sensacao_termica_c", "umidade_pct",
         "chuva_mm", "nuvens_pct", "vento_kmh", "codigo_tempo", "descricao_tempo",
         "is_horario_comercial", "fonte"],
    ),
}

# Chave de cada tabela: não é atualizada no ON DUPLICATE KEY UPDATE.
CHAVES = {
    "calendario": {"data"},
    "feriado": {"data", "nome"},
    "clima_diario": {"data"},
    "clima_horario": {"data_hora"},
}


def valor_sql(texto: str) -> str:
    if texto is None or texto.strip() == "":
        return "NULL"
    return "'" + texto.replace("\\", "\\\\").replace("'", "''") + "'"


def ler_linhas(texto: str, origem: str, colunas: list[str]) -> list[list[str]]:
    leitor = csv.DictReader(io.StringIO(texto, newline=""))
    faltando = [c for c in colunas if c not in (leitor.fieldnames or [])]
    if faltando:
        raise ValueError(f"{origem}: colunas ausentes {faltando}")
    return [[linha[c] for c in colunas] for linha in leitor]


def ler_csv_local(caminho: Path, colunas: list[str]) -> list[list[str]]:
    return ler_linhas(caminho.read_text(encoding="utf-8-sig"), str(caminho), colunas)


def ler_csv_s3(s3, bucket: str, chave: str, colunas: list[str]) -> list[list[str]]:
    corpo = s3.get_object(Bucket=bucket, Key=chave)["Body"].read().decode("utf-8-sig")
    return ler_linhas(corpo, f"s3://{bucket}/{chave}", colunas)


def montar_inserts(tabela: str, colunas: list[str], linhas: list[list[str]]) -> str:
    nomes = ", ".join(f"`{c}`" for c in colunas)
    atualizar = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in colunas if c not in CHAVES[tabela])
    blocos = []
    for inicio in range(0, len(linhas), LINHAS_POR_INSERT):
        valores = ",\n".join(
            "    (" + ", ".join(valor_sql(v) for v in linha) + ")"
            for linha in linhas[inicio:inicio + LINHAS_POR_INSERT]
        )
        blocos.append(
            f"INSERT INTO `{tabela}` ({nomes}) VALUES\n{valores}\n"
            f"ON DUPLICATE KEY UPDATE {atualizar};\n"
        )
    return "\n".join(blocos)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera o SQL de carga das bases externas.")
    parser.add_argument("--bucket-trusted", default=os.environ.get("BUCKET_TRUSTED"),
                        help="lê a camada silver deste bucket em vez de dados/silver")
    args = parser.parse_args()
    s3 = None
    if args.bucket_trusted:
        import boto3
        s3 = boto3.client("s3")

    partes = [
        "-- Gerado por grafana/carregar_bases_mysql.py em "
        f"{datetime.now():%d/%m/%Y %H:%M}. Não edite à mão: rode o script de novo.\n",
        "USE `cafeteria`;\nSET NAMES utf8mb4;\n",
    ]
    controle = []
    for tabela, (arquivo, colunas) in BASES.items():
        if s3:
            arquivo = f"s3://{args.bucket_trusted}/{CHAVES_S3[tabela]}"
            linhas = ler_csv_s3(s3, args.bucket_trusted, CHAVES_S3[tabela], colunas)
        else:
            linhas = ler_csv_local(PASTA_SILVER / arquivo, colunas)
        partes.append(f"-- {tabela}: {len(linhas)} linha(s) de {arquivo}\n")
        partes.append(montar_inserts(tabela, colunas, linhas))
        controle.append(f"    ('{tabela}', '{arquivo}', {len(linhas)}, NOW())")
        print(f"{tabela}: {len(linhas)} linhas")

    # Registro da carga: a dashboard do Administrador usa para mostrar quando cada base foi atualizada.
    partes.append(
        "INSERT INTO `carga_base_externa` (`tabela`, `arquivo`, `linhas`, `carregado_em`) VALUES\n"
        + ",\n".join(controle) + ";\n"
    )

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text("\n".join(partes), encoding="utf-8")
    print(f"SQL gerado em {SAIDA}")


if __name__ == "__main__":
    main()

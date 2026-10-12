from __future__ import annotations

import csv
import io
import os

import boto3

PREFIXO_FERIADOS = "bronze/feriados"
PREFIXO_CLIMA = "bronze/clima"


def encontrar_bucket_raw(s3=None) -> str:
    """Descobre o bucket da conta cujo nome contém 'raw'.

    - Se a variável de ambiente BUCKET_RAW estiver definida, ela tem prioridade.
    - Precisa da permissão s3:ListAllMyBuckets na role da Lambda.
    - Falha se não houver nenhum candidato ou se houver mais de um (evita gravar no lugar errado).
    """
    forcado = os.environ.get("BUCKET_RAW")
    if forcado:
        return forcado

    s3 = s3 or boto3.client("s3")
    nomes = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
    candidatos = [n for n in nomes if "raw" in n.lower()]

    if len(candidatos) == 1:
        return candidatos[0]
    if not candidatos:
        raise RuntimeError(f"Nenhum bucket com 'raw' no nome foi encontrado. Buckets da conta: {nomes}")
    raise RuntimeError(
        f"Mais de um bucket com 'raw' no nome: {candidatos}. "
        "Defina a variável de ambiente BUCKET_RAW para escolher qual usar."
    )


def csv_em_bytes(linhas: list[dict], colunas: list[str]) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=colunas, delimiter=",", quoting=csv.QUOTE_ALL)
    writer.writeheader()
    writer.writerows(linhas)
    return buf.getvalue().encode("utf-8")


def enviar_csv(s3, bucket: str, key: str, linhas: list[dict], colunas: list[str]) -> None:
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=csv_em_bytes(linhas, colunas),
        ContentType="text/csv; charset=utf-8",
    )

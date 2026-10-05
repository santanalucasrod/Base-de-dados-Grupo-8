from __future__ import annotations

import csv
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://brasilapi.com.br/api/feriados/v1"
TZ_SP = ZoneInfo("America/Sao_Paulo")

# A coleta de 6 meses atravessa a virada do ano, então pegamos os dois anos.
ANOS = [2026, 2027]

REQUEST_TIMEOUT = 20
RETRY_MAX = 3
RETRY_BACKOFF_S = 2.0

OUTPUT_DIR = Path("./dados/bronze/brasilapi_feriados")

COLUNAS_BRONZE = [
    "data_raw",
    "nome_raw",
    "tipo_raw",
    "ano_consultado",
    "extraido_em",
]


def fetch_ano(ano: int) -> list | None:
    last_error = None
    for tentativa in range(1, RETRY_MAX + 1):
        try:
            resp = requests.get(f"{BASE_URL}/{ano}", timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            detalhe = ""
            resp_erro = getattr(e, "response", None)
            if resp_erro is not None:
                detalhe = f" | {resp_erro.text[:200]}"
            last_error = f"{e}{detalhe}"
            print(f"[AVISO] Tentativa {tentativa}/{RETRY_MAX} falhou: {last_error}", file=sys.stderr)
            time.sleep(RETRY_BACKOFF_S * tentativa)

    print(f"[ERRO CRÍTICO] Não foi possível obter os feriados de {ano}: {last_error}", file=sys.stderr)
    return None


def extrair_bronze() -> tuple[list[dict], bool]:
    """Retorna (linhas, ok). ok=False se algum ano falhou (nada deve ser gravado)."""
    extraido_em = datetime.now(tz=TZ_SP).isoformat()
    linhas = []

    for ano in ANOS:
        print(f"[INFO] Buscando feriados nacionais de {ano}...")
        lista = fetch_ano(ano)
        if lista is None:
            return [], False

        for feriado in lista:
            linhas.append({
                "data_raw": feriado.get("date", ""),
                "nome_raw": feriado.get("name", ""),
                "tipo_raw": feriado.get("type", ""),
                "ano_consultado": ano,
                "extraido_em": extraido_em,
            })

    return linhas, True


def gravar_csv(linhas: list[dict], caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUNAS_BRONZE, delimiter=",", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(linhas)


def main():
    agora = datetime.now(tz=TZ_SP)
    print(f"Iniciando extração de feriados - BrasilAPI (Camada Bronze) - anos {ANOS}...")
    linhas, ok = extrair_bronze()

    if not ok:
        print("[ERRO] Extração falhou; nenhum arquivo foi gravado para evitar dados incompletos.", file=sys.stderr)
        sys.exit(1)
    if not linhas:
        print("[ALERTA] Nenhum feriado retornado pela API.")
        return

    caminho = OUTPUT_DIR / f"feriados_raw_{agora.strftime('%Y%m%d_%H%M')}.csv"
    gravar_csv(linhas, caminho)
    print(f"[SUCESSO] {len(linhas)} feriados gravados em: {caminho}")


if __name__ == "__main__":
    main()
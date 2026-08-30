from __future__ import annotations

import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://app-smcca-backend-prod.azurewebsites.net"
TZ_SP = ZoneInfo("America/Sao_Paulo")

PAGE_SIZE = 50
REQUEST_TIMEOUT = 20
RETRY_MAX = 3
RETRY_BACKOFF_S = 2.0

OUTPUT_DIR = Path("./dados/bronze/spcultura")

COLUNAS_BRONZE = [
    "evento_id",
    "ocorrencia_id",
    "evento_nome_raw",
    "tipo_evento_raw",
    "data_hora_inicio_raw",
    "data_hora_fim_raw",
    "espaco_nome_raw",
    "endereco_completo_raw",
    "next_presentation_date",
    "extraido_em",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

def fetch_pagina(reference_iso: str) -> dict:
    if not reference_iso.endswith("Z"):
        reference_iso += "Z"
        
    params = {"reference": reference_iso, "pageSize": PAGE_SIZE}
    last_error = None
    
    for tentativa in range(1, RETRY_MAX + 1):
        try:
            resp = requests.get(
                f"{BASE_URL}/cultural-events/public", 
                params=params,
                headers=HEADERS, 
                timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            last_error = e
            print(f"[AVISO] Tentativa {tentativa}/{RETRY_MAX} falhou: {e}", file=sys.stderr)
            time.sleep(RETRY_BACKOFF_S * tentativa)
            
    print(f"[ERRO CRÍTICO] Não foi possível conectar à API: {last_error}", file=sys.stderr)
    return {}

def extrair_bronze() -> list[dict]:
    agora_utc = datetime.now(tz=timezone.utc)
    extraido_em_iso = agora_utc.astimezone(TZ_SP).isoformat()
    linhas = []
    eventos_vistos = set()
    reference = agora_utc

    paginas_processadas = 0

    while True:
        ref_str = reference.strftime("%Y-%m-%dT%H:%M:%S")
        dados = fetch_pagina(ref_str)
        lista = dados.get("list", [])
        
        if not lista:
            print("[INFO] Nenhuma página adicional retornada ou fim da lista atingido.")
            break

        paginas_processadas += 1
        print(f"[INFO] Processando página {paginas_processadas} ({len(lista)} eventos obtidos)...")

        for evento in lista:
            evento_id = evento.get("id")
            if evento_id in eventos_vistos:
                continue
            eventos_vistos.add(evento_id)

            evento_nome = evento.get("name")
            tipo_evento = evento.get("eventTypeName")

            schedules = evento.get("schedules") or []
            if not schedules:
                linhas.append({
                    "evento_id": evento_id,
                    "ocorrencia_id": "",
                    "evento_nome_raw": evento_nome,
                    "tipo_evento_raw": tipo_evento,
                    "data_hora_inicio_raw": "",
                    "data_hora_fim_raw": "",
                    "espaco_nome_raw": "",
                    "endereco_completo_raw": "",
                    "next_presentation_date": evento.get("nextPresentationDate", ""),
                    "extraido_em": extraido_em_iso,
                })

            for oc in schedules:
                linhas.append({
                    "evento_id": evento_id,
                    "ocorrencia_id": oc.get("id", ""),
                    "evento_nome_raw": evento_nome,
                    "tipo_evento_raw": tipo_evento,
                    "data_hora_inicio_raw": oc.get("startDate", ""),
                    "data_hora_fim_raw": oc.get("endDate", ""),
                    "espaco_nome_raw": oc.get("placeName", ""),
                    "endereco_completo_raw": oc.get("fullAddress", ""),
                    "next_presentation_date": evento.get("nextPresentationDate", ""),
                    "extraido_em": extraido_em_iso,
                })

        ultimo = lista[-1]
        proxima_str = ultimo.get("nextPresentationDate")
        if not proxima_str:
            break

        proxima_ref = datetime.fromisoformat(proxima_str.replace("Z", "+00:00"))
        if proxima_ref <= reference:
            break

        reference = proxima_ref

    return linhas

def gravar_csv(linhas: list[dict], caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUNAS_BRONZE, delimiter=",", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(linhas)

def main():
    agora = datetime.now(tz=TZ_SP)
    print("Iniciando extração de dados brutos (Camada Bronze)...")
    linhas = extrair_bronze()
    
    if not linhas:
        print("[ALERTA] Nenhum registro extraído. Verifique sua conexão à internet ou bloqueios de firewall/proxy.")
        return

    caminho = OUTPUT_DIR / f"spcultura_raw_{agora.strftime('%Y%m%d_%H%M')}.csv"
    gravar_csv(linhas, caminho)
    print(f"[SUCESSO] Extração concluída! {len(linhas)} linhas gravadas em: {caminho}")

if __name__ == "__main__":
    main()
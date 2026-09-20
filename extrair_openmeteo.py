import csv
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

TZ_SP = ZoneInfo("America/Sao_Paulo")

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

LATITUDE = -23.5580
LONGITUDE = -46.6350

DATA_INICIO = date(2026, 1, 1)
DATA_FIM = date(2026, 12, 31)

ARCHIVE_LAG_DIAS = 7
FORECAST_MAX_DIAS = 16

REQUEST_TIMEOUT = 30
RETRY_MAX = 3
RETRY_BACKOFF_S = 2.0

OUTPUT_DIR = Path("./dados/bronze/open_meteo")

VARIAVEIS_HORARIAS = [
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation",
    "rain",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
]

COLUNAS_BRONZE = ["data_hora", *VARIAVEIS_HORARIAS, "fonte", "latitude", "longitude", "extraido_em"]


def fetch_json(url: str, params: dict) -> dict | None:
    last_error = None
    for tentativa in range(1, RETRY_MAX + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
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

    print(f"[ERRO CRÍTICO] Não foi possível obter dados da Open-Meteo: {last_error}", file=sys.stderr)
    return None


def params_base() -> dict:
    return {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "hourly": ",".join(VARIAVEIS_HORARIAS),
        "timezone": "America/Sao_Paulo",
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
    }


def payload_para_linhas(
    payload: dict, fonte: str, dia_min: date, dia_max: date, extraido_em: str
) -> list[dict]:
    hourly = payload.get("hourly") or {}
    tempos = hourly.get("time") or []
    linhas = []
    for i, t in enumerate(tempos):
        dt = datetime.fromisoformat(t)
        if not (dia_min <= dt.date() <= dia_max):
            continue
        linha = {"data_hora": dt.strftime("%Y-%m-%d %H:%M:%S")}
        for var in VARIAVEIS_HORARIAS:
            serie = hourly.get(var) or []
            valor = serie[i] if i < len(serie) else None
            linha[var] = "" if valor is None else valor
        linha.update({
            "fonte": fonte,
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "extraido_em": extraido_em,
        })
        linhas.append(linha)
    return linhas


def extrair_bronze(hoje: date) -> tuple[list[dict], bool]:
    """Retorna (linhas, ok). ok=False se alguma requisição falhou (nada deve ser gravado)."""
    extraido_em = datetime.now(tz=TZ_SP).isoformat()
    linhas: list[dict] = []

    ultimo_archive = min(DATA_FIM, hoje - timedelta(days=ARCHIVE_LAG_DIAS))
    if DATA_INICIO <= ultimo_archive:
        print(f"[INFO] Histórico (archive): {DATA_INICIO} a {ultimo_archive}...")
        params = params_base()
        params.update({"start_date": DATA_INICIO.isoformat(), "end_date": ultimo_archive.isoformat()})
        payload = fetch_json(ARCHIVE_URL, params)
        if payload is None:
            return [], False
        linhas += payload_para_linhas(payload, "archive", DATA_INICIO, ultimo_archive, extraido_em)

    inicio_prev = max(DATA_INICIO, ultimo_archive + timedelta(days=1))
    fim_prev = min(DATA_FIM, hoje + timedelta(days=FORECAST_MAX_DIAS - 1))
    if inicio_prev <= fim_prev:
        print(f"[INFO] Recente/previsão (forecast): {inicio_prev} a {fim_prev}...")
        params = params_base()
        params.update({
            "past_days": max(0, min(92, (hoje - inicio_prev).days)),
            "forecast_days": max(1, min(FORECAST_MAX_DIAS, (fim_prev - hoje).days + 1)),
        })
        payload = fetch_json(FORECAST_URL, params)
        if payload is None:
            return [], False
        linhas += payload_para_linhas(payload, "forecast", inicio_prev, fim_prev, extraido_em)

    if fim_prev < DATA_FIM:
        print(
            f"[AVISO] Sem dados para {fim_prev + timedelta(days=1)} a {DATA_FIM}: "
            f"a previsão só alcança {FORECAST_MAX_DIAS} dias à frente. Rode o script de novo mais tarde."
        )

    linhas.sort(key=lambda l: l["data_hora"])
    return linhas, True


def gravar_csv(linhas: list[dict], caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUNAS_BRONZE, delimiter=",", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(linhas)


def main():
    agora = datetime.now(tz=TZ_SP)
    print(f"Iniciando extração de clima - Open-Meteo (Camada Bronze) - período {DATA_INICIO} a {DATA_FIM}...")
    linhas, ok = extrair_bronze(agora.date())

    if not ok:
        print("[ERRO] Extração falhou; nenhum arquivo foi gravado para evitar dados incompletos.", file=sys.stderr)
        sys.exit(1)
    if not linhas:
        print("[ALERTA] Nenhum registro extraído.")
        return

    caminho = OUTPUT_DIR / f"open_meteo_raw_{agora.strftime('%Y%m%d_%H%M')}.csv"
    gravar_csv(linhas, caminho)

    sem_temp = sum(1 for l in linhas if l["temperature_2m"] == "")
    por_fonte = {f: sum(1 for l in linhas if l["fonte"] == f) for f in ("archive", "forecast")}
    print(f"[SUCESSO] {len(linhas)} linhas horárias gravadas em: {caminho}")
    print(f"[INFO] Período: {linhas[0]['data_hora']} a {linhas[-1]['data_hora']} | por fonte: {por_fonte}")
    if sem_temp:
        print(f"[AVISO] {sem_temp} horas sem valor de temperatura (dados ainda não disponíveis na origem).")


if __name__ == "__main__":
    main()
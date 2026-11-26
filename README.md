# Base de dados do Kento Café

Scripts e dados da parte de análise do projeto: feriados e clima (bronze, silver e gold),
modelo de previsão de movimento e dashboards do Grafana.

## Pastas

| Pasta | O que tem |
| --- | --- |
| pipeline/ | Scripts de extração e tratamento: bronze, silver e gold |
| modelo_previsao/ | Treino da regressão linear que prevê os pedidos por dia |
| grafana/ | SQL das tabelas e visões, carga dos CSVs no MySQL e as duas dashboards |
| notebooks/ | Notebooks da análise exploratória (EDA) |
| dados/bronze/ | Dados brutos das APIs (BrasilAPI e Open-Meteo) |
| dados/silver/ | Feriados, calendário e clima tratados |
| dados/gold/ | Tabelas prontas para análise, juntando pedidos, calendário e clima |
| dados/eda/ | Saídas dos notebooks (base tratada, estatística descritiva, qualidade) |
| docs/ | Dicionário de dados, relatório da EDA e guia de uso |

## Como rodar

Todos os scripts rodam a partir desta pasta (a raiz do repositório), porque os caminhos
dados/... são relativos a ela.

```
pip install -r modelo_previsao/requirements.txt

python pipeline/extrair_feriados.py      # uma vez por ano
python pipeline/tratar_feriados.py
python pipeline/extrair_openmeteo.py     # todo dia, traz a previsão do tempo
python pipeline/tratar_openmeteo.py

python grafana/carregar_bases_mysql.py   # gera grafana/02_carga_bases_externas.sql
python modelo_previsao/treinar_previsao.py
python pipeline/tratar_gold.py
python pipeline/montar_base_eda.py       # base do notebook de EDA
```

Os scripts que leem o MySQL usam o usuário kento e a senha sptech. Para outro usuário, defina
MYSQL_USER e MYSQL_PASSWORD (no montar_base_eda.py, KENTO_DB_USUARIO e KENTO_DB_SENHA).

Os notebooks ficam em notebooks/ e leem e gravam em ../dados, então abra o Jupyter a partir
de qualquer pasta e rode as células normalmente.

A carga no MySQL e as dashboards estão explicadas em grafana/README.md.

## Na AWS

Os extrair_*.py rodam como Lambda (junto com o s3_utils.py) e gravam no bucket raw. Os
tratar_*.py rodam como jobs do Glue (Python shell) com os parâmetros --BUCKET_RAW e
--BUCKET_TRUSTED. O tratar_gold.py usa --BUCKET_TRUSTED e --BUCKET_REFINED.

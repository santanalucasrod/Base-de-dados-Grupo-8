# Dashboards do Kento Café no Grafana

Duas dashboards que leem direto do MySQL `cafeteria`:

| Arquivo | Dashboard | Para quem |
| --- | --- | --- |
| `dashboard_cliente.json` | **Kento Café — Painel da Gerência** | A dona da cafeteria: previsão de movimento, equipe sugerida por hora, estoque e desempenho |
| `dashboard_administrador.json` | **Kento Café — Administração do Sistema** | O time do projeto: fila de pedidos, uso, qualidade dos dados, bases externas, S3 e banco |

## Arquivos desta pasta

- `01_tabelas_bases_externas.sql`: cria as tabelas de feriados e clima, as tabelas do modelo de
  previsão (`modelo_execucao`, `modelo_validacao`, `modelo_coeficiente`, `previsao_dia`), as visões
  usadas pelas dashboards (`vw_pedido`, `vw_dia`, `vw_dia_hora`, `vw_previsao_dia`, `vw_previsao_hora`), o índice
  de data do pedido, o usuário somente leitura `grafana` e a tabela `s3_cleanup_outbox`, que o back
  exige e que não está no `kentocafe_banco_v8.sql`. Pode rodar mais de uma vez.
- `carregar_bases_mysql.py`: lê os CSVs silver de `dados/silver` e gera o `02_carga_bases_externas.sql`.
- `02_carga_bases_externas.sql`: carga com os CSVs silver atuais (feriados de 2026–2027 e clima até 19/10/2026).

## Como subir

1. Crie o banco com o `kentocafe_banco_v8.sql` (repositório do back).
2. Rode `01_tabelas_bases_externas.sql` e depois `02_carga_bases_externas.sql`, no Workbench ou com:
   ```
   mysql -u root -p --default-character-set=utf8mb4 < grafana/01_tabelas_bases_externas.sql
   mysql -u root -p --default-character-set=utf8mb4 < grafana/02_carga_bases_externas.sql
   ```
3. No Grafana: **Connections → Data sources → Add → MySQL**
   - Host: `localhost:3306` · Database: `cafeteria` · User: `grafana` · Password: `grafana@2026`
   - **Session timezone: `-03:00`** (os pedidos são gravados no horário de Brasília; sem isso o
     filtro de datas fica 3 horas deslocado)
4. Treine o modelo de previsão (pasta `modelo_previsao`, na raiz deste repositório):
   ```
   pip install -r modelo_previsao/requirements.txt
   python modelo_previsao/treinar_previsao.py
   ```
5. **Dashboards → New → Import**, envie cada JSON e escolha o data source criado no passo 3.

Troque a senha do usuário `grafana` (no `01_…sql` e no data source) antes de usar fora do PI.

## Manter as bases externas em dia

A previsão da dashboard da gerência usa a **previsão do tempo** (`clima_diario` com
`fonte = 'forecast'`) para ajustar os dias de chuva. Sem previsão para os próximos dias, a coluna
*Tempo* mostra "Sem previsão do tempo" e a previsão de pedidos usa só o calendário. Para atualizar,
rode a partir da pasta `Base-de-dados-Grupo-8`:

```
python extrair_openmeteo.py
python tratar_openmeteo.py
python extrair_feriados.py      # uma vez por ano basta
python tratar_feriados.py
python grafana/carregar_bases_mysql.py
mysql -u root -p --default-character-set=utf8mb4 cafeteria < grafana/02_carga_bases_externas.sql
python modelo_previsao/treinar_previsao.py
```

O ideal é rodar essa sequência **todo dia de manhã** (Agendador de Tarefas do Windows ou uma Lambda).
A previsão gravada vale a partir do dia em que foi gerada; a dashboard do Administrador avisa quando
ela fica velha. **No dia da apresentação, rode o `treinar_previsao.py` de manhã.**

A carga usa `ON DUPLICATE KEY UPDATE`: rodar de novo só atualiza, nunca duplica. Cada carga fica
registrada em `carga_base_externa`, e a dashboard do Administrador mostra há quantos dias o clima
não é atualizado.

## Como a previsão funciona (para explicar na apresentação)

O `modelo_previsao/treinar_previsao.py` faz uma **regressão linear por mínimos quadrados (MQO)** do
logaritmo dos pedidos do dia:

```
log(pedidos) = base + dia da semana + feriado + véspera + ponte + chuva + temperatura máxima
```

- **Por que o logaritmo**: os fatores agem em proporção (chuva tira ~9% do movimento, seja segunda
  ou sábado). No log, cada coeficiente vira um efeito percentual, `(e^coef − 1) × 100`, que é o que
  aparece no painel *O que o modelo aprendeu*. Para voltar a pedidos, usa a correção de Duan
  (smearing), que evita subestimar a média.
- **Validação temporal (rolling origin)**: para cada semana do histórico, o script treina só com os
  dias anteriores e prevê os 7 dias seguintes, como seria na vida real. Assim nenhum candidato é
  avaliado com dados que não teria. São comparados 5 candidatos: a regra anterior (média do dia da
  semana), repetir a semana passada e três regressões (só calendário; calendário + clima; + tendência).
- **Escolha com o teste de Diebold-Mariano**: fica a regressão de menor erro, a não ser que outro
  candidato erre *significativamente* menos (p < 0,05). Entre modelos empatados, a regressão é
  preferida porque mede o efeito de cada fator e dá uma faixa de previsão calibrada.
- **Faixa provável (80%)**: intervalo de predição da regressão. A validação confere se ~80% dos dias
  reais caem dentro dela.
- **Diagnóstico dos resíduos**: Durbin-Watson (autocorrelação), Jarque-Bera (normalidade) e
  Breusch-Pagan (variância constante), no `modelo_previsao/ultimo_treino.txt`.
- **Feriado só conta em dia útil**: todos os feriados do histórico caíram de segunda a sexta, então
  não há dado para dizer se um feriado no fim de semana enche ainda mais; ali ele vale como um fim de
  semana comum.
- **Sem previsão do tempo** para um dia, a chuva entra como a probabilidade histórica de chover
  naquele mês e a temperatura como a média do mês (efeito esperado do clima).

Resultado com os dados atuais (validação de 146 dias, junho a outubro):

| Modelo | Erro médio |
| --- | --- |
| Média do dia da semana (regra anterior) | 13,0% (11,5 pedidos) |
| **Regressão: calendário + clima (escolhida)** | **13,0% (11,6 pedidos)** |
| Regressão: só calendário | 12,9% (11,6 pedidos) |
| Regressão com tendência | 13,5% |
| Repetir a semana passada | 17,9% |

A faixa de 80% acertou 79% dos dias. A regressão e a regra anterior empatam: a diferença **não é
estatisticamente significativa** (Diebold-Mariano, p = 0,92). Repetir a semana passada é bem pior.
Um erro de ~11 pedidos/dia está perto do piso do problema: só a variação aleatória de quem entra na
cafeteria (≈ √100 pedidos num dia de 100) já explica boa parte dele. O ganho da regressão é
outro: mede o efeito de cada fator com intervalo de confiança e p-valor, combina efeitos (feriado
numa segunda chuvosa) e dá uma faixa de incerteza calibrada. Os dados são simulados com essas mesmas
premissas; com dados reais da cafeteria, os números devem ser recalculados (o script faz isso sozinho).

A previsão por hora divide o total do dia pela curva por hora daquele dia da semana nas últimas
8 semanas (`vw_previsao_hora`). A **equipe sugerida** é pedidos previstos na hora ÷ *Pedidos/hora por
pessoa* (padrão 16, tirado do painel *Espera conforme o volume da hora*), arredondado para cima. O
**estoque** usa quanto cada produto saiu por pedido nas últimas 4 semanas × pedidos previstos.

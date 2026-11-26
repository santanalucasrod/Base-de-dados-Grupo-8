-- ============================================================================
-- KENTOCAFE — TABELAS PARA O GRAFANA (bases externas + visões de análise)
-- ============================================================================
-- Rode DEPOIS do kentocafe_banco_v8.sql. Pode rodar mais de uma vez.
--
-- 1. Tabela s3_cleanup_outbox, que o back exige (ddl-auto=validate) e que
--    não está no kentocafe_banco_v8.sql atual.
-- 2. Tabelas das bases externas (camada silver): calendário/feriados e clima.
--    Os dados entram pelo 02_carga_bases_externas.sql, gerado pelo
--    carregar_bases_mysql.py.
-- 3. Tabelas do modelo de previsão (regressão linear) e visões (views) que as
--    dashboards usam, para a regra de negócio ficar num lugar só.
-- 4. Usuário somente leitura para o Grafana.
--
-- Datas e horas dos pedidos estão no horário de Brasília (o back grava
-- LocalDateTime.now() do servidor).
--
-- As colunas de texto calculadas nas visões têm COLLATE explícito. Sem isso, o
-- Grafana (que conecta com utf8mb4_unicode_ci) recusa comparações como
-- tipo_dia = 'Dia útil' com o erro "Illegal mix of collations".
-- ============================================================================

USE `cafeteria`;
SET NAMES utf8mb4;

-- ----------------------------------------------------------------------------
-- 1. Fila de limpeza de imagens no S3 (entidade S3CleanupOutbox do back)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `s3_cleanup_outbox` (
    `id`              BIGINT        NOT NULL AUTO_INCREMENT,
    `object_key`      VARCHAR(1024) NOT NULL,
    `status`          VARCHAR(20)   NOT NULL DEFAULT 'PENDENTE',
    `tentativas`      INT           NOT NULL DEFAULT 0,
    `created_at`      DATETIME(6)   NOT NULL,
    `next_attempt_at` DATETIME(6)   NOT NULL,
    `locked_at`       DATETIME(6)   NULL,
    `last_error`      VARCHAR(1000) NULL,
    PRIMARY KEY (`id`),
    INDEX `idx_s3_cleanup_due` (`status`, `next_attempt_at`),
    INDEX `idx_s3_cleanup_lock` (`status`, `locked_at`)
);

-- ----------------------------------------------------------------------------
-- 2. Bases externas (silver)
-- ----------------------------------------------------------------------------

-- Um dia por linha, com feriados de SP (nacionais, estaduais e municipais)
CREATE TABLE IF NOT EXISTS `calendario` (
    `data`               DATE         NOT NULL,
    `ano`                SMALLINT     NOT NULL,
    `mes`                TINYINT      NOT NULL,
    `dia_semana`         VARCHAR(15)  NOT NULL,
    `is_fim_de_semana`   TINYINT      NOT NULL,
    `is_feriado`         TINYINT      NOT NULL,
    `feriado_nome`       VARCHAR(100) NULL,
    `feriado_esfera`     VARCHAR(20)  NULL,
    `is_vespera_feriado` TINYINT      NOT NULL,
    `is_ponte`           TINYINT      NOT NULL,
    `is_dia_util`        TINYINT      NOT NULL,
    PRIMARY KEY (`data`)
);

CREATE TABLE IF NOT EXISTS `feriado` (
    `data`       DATE         NOT NULL,
    `dia_semana` VARCHAR(15)  NOT NULL,
    `nome`       VARCHAR(100) NOT NULL,
    `esfera`     VARCHAR(20)  NOT NULL,
    `fonte`      VARCHAR(20)  NULL,
    `base_legal` VARCHAR(120) NULL,
    PRIMARY KEY (`data`, `nome`)
);

-- Clima do dia no horário de funcionamento (9h às 18h), Liberdade/SP (Open-Meteo)
-- fonte: 'archive' = observado; 'forecast' = previsão
CREATE TABLE IF NOT EXISTS `clima_diario` (
    `data`               DATE         NOT NULL,
    `temp_min_c`         DECIMAL(4,1) NULL,
    `temp_media_c`       DECIMAL(4,1) NULL,
    `temp_max_c`         DECIMAL(4,1) NULL,
    `sensacao_max_c`     DECIMAL(4,1) NULL,
    `umidade_media_pct`  DECIMAL(4,1) NULL,
    `nuvens_media_pct`   DECIMAL(4,1) NULL,
    `vento_max_kmh`      DECIMAL(5,1) NULL,
    `chuva_comercial_mm` DECIMAL(5,1) NULL,
    `chuva_max_hora_mm`  DECIMAL(5,1) NULL,
    `horas_com_chuva`    TINYINT      NULL,
    `chuva_dia_todo_mm`  DECIMAL(5,1) NULL,
    `condicao_dia`       VARCHAR(30)  NULL,
    `faixa_temperatura`  VARCHAR(15)  NULL,
    `is_dia_chuvoso`     TINYINT      NOT NULL DEFAULT 0,
    `is_chuva_forte`     TINYINT      NOT NULL DEFAULT 0,
    `fonte`              VARCHAR(10)  NOT NULL,
    `horas_disponiveis`  VARCHAR(5)   NULL,
    PRIMARY KEY (`data`)
);

CREATE TABLE IF NOT EXISTS `clima_horario` (
    `data`                 DATE         NOT NULL,
    `hora`                 TINYINT      NOT NULL,
    `data_hora`            DATETIME     NOT NULL,
    `temperatura_c`        DECIMAL(4,1) NULL,
    `sensacao_termica_c`   DECIMAL(4,1) NULL,
    `umidade_pct`          DECIMAL(4,1) NULL,
    `chuva_mm`             DECIMAL(5,1) NULL,
    `nuvens_pct`           DECIMAL(4,1) NULL,
    `vento_kmh`            DECIMAL(5,1) NULL,
    `codigo_tempo`         SMALLINT     NULL,
    `descricao_tempo`      VARCHAR(40)  NULL,
    `is_horario_comercial` TINYINT      NOT NULL,
    `fonte`                VARCHAR(10)  NOT NULL,
    PRIMARY KEY (`data_hora`),
    INDEX `idx_clima_horario_data` (`data`, `hora`)
);

-- Histórico das cargas (a dashboard do Administrador mostra quando cada base foi atualizada)
CREATE TABLE IF NOT EXISTS `carga_base_externa` (
    `id`           BIGINT       NOT NULL AUTO_INCREMENT,
    `tabela`       VARCHAR(30)  NOT NULL,
    `arquivo`      VARCHAR(200) NOT NULL,
    `linhas`       INT          NOT NULL,
    `carregado_em` DATETIME     NOT NULL,
    PRIMARY KEY (`id`),
    INDEX `idx_carga_tabela` (`tabela`, `carregado_em`)
);

-- ----------------------------------------------------------------------------
-- Modelo de previsão (gravado pelo modelo_previsao/treinar_previsao.py)
-- ----------------------------------------------------------------------------
-- Cada execução do treino vira uma linha em modelo_execucao; validação, coeficientes
-- e previsão diária ficam ligados a ela. As dashboards sempre leem a última execução.
CREATE TABLE IF NOT EXISTS `modelo_execucao` (
    `id`                    BIGINT       NOT NULL AUTO_INCREMENT,
    `executado_em`          DATETIME     NOT NULL,
    `data_referencia`       DATE         NOT NULL,   -- "hoje" no momento do treino
    `modelo`                VARCHAR(40)  NOT NULL,   -- modelo escolhido na validação
    `descricao`             VARCHAR(255) NOT NULL,
    `dias_treino`           INT          NOT NULL,
    `treino_inicio`         DATE         NOT NULL,
    `treino_fim`            DATE         NOT NULL,
    `r2`                    DECIMAL(6,4) NULL,
    `r2_ajustado`           DECIMAL(6,4) NULL,
    `mae_validacao`         DECIMAL(6,2) NULL,       -- erro médio (pedidos/dia) na validação
    `mape_validacao`        DECIMAL(5,2) NULL,       -- erro médio (%) na validação
    `mape_sem_clima`        DECIMAL(5,2) NULL,       -- idem, sem previsão do tempo (clima médio)
    `cobertura_intervalo`   DECIMAL(5,2) NULL,       -- % dos dias testados dentro da faixa de 80%
    `mae_regra_anterior`    DECIMAL(6,2) NULL,       -- média do dia da semana (regra antiga)
    `mape_regra_anterior`   DECIMAL(5,2) NULL,
    `p_valor_vs_regra_anterior` DECIMAL(6,4) NULL,   -- teste de Diebold-Mariano
    PRIMARY KEY (`id`)
);

CREATE TABLE IF NOT EXISTS `modelo_validacao` (
    `execucao_id`   BIGINT       NOT NULL,
    `modelo`        VARCHAR(40)  NOT NULL,
    `descricao`     VARCHAR(255) NOT NULL,
    `dias_testados` INT          NOT NULL,
    `mae`           DECIMAL(6,2) NOT NULL,
    `mape`          DECIMAL(5,2) NOT NULL,
    `rmse`          DECIMAL(6,2) NOT NULL,
    `escolhido`     TINYINT      NOT NULL DEFAULT 0,
    PRIMARY KEY (`execucao_id`, `modelo`),
    CONSTRAINT `fk_validacao_execucao` FOREIGN KEY (`execucao_id`) REFERENCES `modelo_execucao`(`id`)
);

CREATE TABLE IF NOT EXISTS `modelo_coeficiente` (
    `execucao_id`    BIGINT       NOT NULL,
    `termo`          VARCHAR(40)  NOT NULL,
    `descricao`      VARCHAR(120) NOT NULL,
    `coeficiente`    DECIMAL(12,6) NOT NULL,
    `efeito_pct`     DECIMAL(7,2) NULL,   -- efeito no movimento: (e^coef - 1) x 100
    `efeito_min_pct` DECIMAL(7,2) NULL,   -- intervalo de confiança de 95%
    `efeito_max_pct` DECIMAL(7,2) NULL,
    `p_valor`        DECIMAL(10,8) NULL,
    PRIMARY KEY (`execucao_id`, `termo`),
    CONSTRAINT `fk_coeficiente_execucao` FOREIGN KEY (`execucao_id`) REFERENCES `modelo_execucao`(`id`)
);

CREATE TABLE IF NOT EXISTS `previsao_dia` (
    `execucao_id`       BIGINT       NOT NULL,
    `data`              DATE         NOT NULL,
    `pedidos_previstos` DECIMAL(6,1) NOT NULL,
    `pedidos_min`       DECIMAL(6,1) NOT NULL,   -- faixa provável (80%)
    `pedidos_max`       DECIMAL(6,1) NOT NULL,
    `clima_usado`       VARCHAR(20)  NOT NULL,   -- 'previsão' ou 'média histórica'
    `prob_chuva`        DECIMAL(4,2) NULL,
    `temp_max_usada`    DECIMAL(4,1) NULL,
    PRIMARY KEY (`execucao_id`, `data`),
    CONSTRAINT `fk_previsao_execucao` FOREIGN KEY (`execucao_id`) REFERENCES `modelo_execucao`(`id`)
);

-- Índice para as consultas por período das dashboards (17 mil+ pedidos)
SET @existe := (SELECT COUNT(*) FROM information_schema.statistics
                WHERE table_schema = 'cafeteria' AND table_name = 'pedido'
                  AND index_name = 'idx_pedido_dt_hr');
SET @sql := IF(@existe = 0,
               'CREATE INDEX `idx_pedido_dt_hr` ON `pedido` (`dt_hr_pedido`)',
               'DO 0');
PREPARE comando FROM @sql; EXECUTE comando; DEALLOCATE PREPARE comando;

-- ----------------------------------------------------------------------------
-- 3. Visões de análise
-- ----------------------------------------------------------------------------

-- Um pedido por linha, já com as informações do dia (calendário e clima).
-- espera_min só existe para pedidos prontos.
CREATE OR REPLACE VIEW `vw_pedido` AS
SELECT
    p.id,
    p.dt_hr_pedido,
    DATE(p.dt_hr_pedido)                                        AS data,
    HOUR(p.dt_hr_pedido)                                        AS hora,
    WEEKDAY(p.dt_hr_pedido)                                     AS dia_semana_num, -- 0 = segunda
    p.valor_total,
    s.nome                                                      AS status,
    (s.nome = 'Cancelado')                                      AS cancelado,
    CASE WHEN s.nome = 'Pronto' AND p.dt_hr_pronto IS NOT NULL
         THEN TIMESTAMPDIFF(SECOND, p.dt_hr_pedido, p.dt_hr_pronto) / 60 END AS espera_min,
    p.funcionario_id,
    COALESCE(c.is_feriado, 0)                                   AS is_feriado,
    c.feriado_nome,
    COALESCE(c.is_vespera_feriado, 0)                           AS is_vespera_feriado,
    COALESCE(c.is_ponte, 0)                                     AS is_ponte,
    cd.is_dia_chuvoso,
    cd.faixa_temperatura,
    cd.temp_max_c
FROM pedido p
JOIN status s             ON s.id = p.status_id
LEFT JOIN calendario c    ON c.data = DATE(p.dt_hr_pedido)
LEFT JOIN clima_diario cd ON cd.data = DATE(p.dt_hr_pedido);

-- Um dia com movimento por linha: totais do dia + calendário + clima.
-- tipo_dia separa os dias que se comportam diferente: feriado, fim de semana e dia útil.
CREATE OR REPLACE VIEW `vw_dia` AS
SELECT
    d.data,
    WEEKDAY(d.data)                                             AS dia_semana_num,
    d.pedidos,
    d.cancelados,
    d.faturamento,
    CASE WHEN COALESCE(c.is_feriado, 0) = 1 THEN 'Feriado'
         WHEN WEEKDAY(d.data) >= 5 THEN 'Fim de semana'
         ELSE 'Dia útil' END COLLATE utf8mb4_0900_ai_ci                  AS tipo_dia,
    c.feriado_nome,
    COALESCE(c.is_vespera_feriado, 0)                           AS is_vespera_feriado,
    COALESCE(c.is_ponte, 0)                                     AS is_ponte,
    cd.is_dia_chuvoso,
    cd.faixa_temperatura,
    cd.temp_max_c,
    cd.chuva_comercial_mm                                       AS chuva_mm
FROM (
    SELECT DATE(p.dt_hr_pedido)                                 AS data,
           SUM(s.nome <> 'Cancelado')                           AS pedidos,
           SUM(s.nome = 'Cancelado')                            AS cancelados,
           SUM(CASE WHEN s.nome <> 'Cancelado' THEN p.valor_total ELSE 0 END) AS faturamento
    FROM pedido p
    JOIN status s ON s.id = p.status_id
    GROUP BY DATE(p.dt_hr_pedido)
) d
LEFT JOIN calendario c    ON c.data = d.data
LEFT JOIN clima_diario cd ON cd.data = d.data;

-- Pedidos (não cancelados) por dia e hora de funcionamento.
CREATE OR REPLACE VIEW `vw_dia_hora` AS
SELECT
    DATE(p.dt_hr_pedido)                                        AS data,
    HOUR(p.dt_hr_pedido)                                        AS hora,
    COUNT(*)                                                    AS pedidos,
    SUM(p.valor_total)                                          AS faturamento
FROM pedido p
JOIN status s ON s.id = p.status_id
WHERE s.nome <> 'Cancelado'
GROUP BY DATE(p.dt_hr_pedido), HOUR(p.dt_hr_pedido);

-- ----------------------------------------------------------------------------
-- Previsão de movimento (usada pela dashboard do Cliente)
-- ----------------------------------------------------------------------------
-- Os pedidos previstos por dia vêm da regressão linear treinada pelo
-- modelo_previsao/treinar_previsao.py (última execução). Esta visão só junta a
-- previsão gravada com o calendário, o clima e o ticket médio recente.
-- "Hoje" é calculado no horário de Brasília (UTC-3, sem horário de verão), para não
-- depender do fuso configurado no MySQL ou no Grafana.
CREATE OR REPLACE VIEW `vw_previsao_dia` AS
WITH
ref AS (
    SELECT DATE(UTC_TIMESTAMP() - INTERVAL 3 HOUR) AS hoje
),
ultima AS (
    -- última execução treinada até hoje (uma execução de teste com --hoje no futuro não vale)
    SELECT e.id
    FROM modelo_execucao e, ref
    WHERE e.data_referencia <= ref.hoje
    ORDER BY e.data_referencia DESC, e.id DESC
    LIMIT 1
),
ticket_semana AS (
    SELECT d.dia_semana_num, SUM(d.faturamento) / SUM(d.pedidos) AS ticket
    FROM vw_dia d, ref
    WHERE d.data < ref.hoje AND d.data >= ref.hoje - INTERVAL 56 DAY AND d.tipo_dia <> 'Feriado'
    GROUP BY d.dia_semana_num
),
ticket_feriado AS (
    SELECT SUM(d.faturamento) / SUM(d.pedidos) AS ticket
    FROM vw_dia d, ref
    WHERE d.data < ref.hoje AND d.tipo_dia = 'Feriado'
)
SELECT
    pv.data,
    DATEDIFF(pv.data, ref.hoje)                                      AS dias_a_frente,
    WEEKDAY(pv.data)                                                 AS dia_semana_num,
    ELT(WEEKDAY(pv.data) + 1, 'Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo') COLLATE utf8mb4_0900_ai_ci AS dia_semana,
    CASE WHEN COALESCE(c.is_feriado, 0) = 1 THEN 'Feriado'
         WHEN WEEKDAY(pv.data) >= 5 THEN 'Fim de semana'
         ELSE 'Dia útil' END COLLATE utf8mb4_0900_ai_ci                      AS tipo_dia,
    c.feriado_nome,
    COALESCE(c.is_vespera_feriado, 0)                                AS is_vespera_feriado,
    COALESCE(c.is_ponte, 0)                                          AS is_ponte,
    CASE WHEN pv.clima_usado = 'previsão' THEN cd.condicao_dia
         ELSE 'Sem previsão (clima médio)' END COLLATE utf8mb4_0900_ai_ci    AS previsao_tempo,
    pv.temp_max_usada                                                AS temp_max_c,   -- previsão do tempo ou média do mês
    pv.prob_chuva,
    pv.clima_usado,
    ROUND(pv.pedidos_previstos)                                      AS pedidos_previstos,
    ROUND(pv.pedidos_min)                                            AS pedidos_min,
    ROUND(pv.pedidos_max)                                            AS pedidos_max,
    ROUND(pv.pedidos_previstos
          * CASE WHEN COALESCE(c.is_feriado, 0) = 1 THEN tf.ticket ELSE ts.ticket END, 2)
                                                                     AS faturamento_previsto,
    pv.execucao_id
FROM previsao_dia pv
JOIN ultima u ON u.id = pv.execucao_id
CROSS JOIN ref
CROSS JOIN ticket_feriado tf
LEFT JOIN calendario c     ON c.data = pv.data
LEFT JOIN clima_diario cd  ON cd.data = pv.data
LEFT JOIN ticket_semana ts ON ts.dia_semana_num = WEEKDAY(pv.data)
WHERE pv.data >= ref.hoje;

-- Previsão por hora: total previsto do dia x participação de cada hora no histórico
-- (mesmo dia da semana nas últimas 8 semanas; feriados usam a curva dos feriados).
CREATE OR REPLACE VIEW `vw_previsao_hora` AS
WITH
ref AS (
    SELECT DATE(UTC_TIMESTAMP() - INTERVAL 3 HOUR) AS hoje
),
hist AS (
    SELECT vh.data, vh.hora, vh.pedidos,
           CASE WHEN COALESCE(c.is_feriado, 0) = 1 THEN 'F'
                ELSE CAST(WEEKDAY(vh.data) AS CHAR) END AS grupo
    FROM vw_dia_hora vh
    JOIN ref ON vh.data < ref.hoje
    LEFT JOIN calendario c ON c.data = vh.data
    WHERE vh.hora BETWEEN 9 AND 17
      AND (COALESCE(c.is_feriado, 0) = 1 OR vh.data >= ref.hoje - INTERVAL 56 DAY)
),
curva AS (
    SELECT grupo, hora,
           SUM(pedidos) / SUM(SUM(pedidos)) OVER (PARTITION BY grupo) AS participacao
    FROM hist
    GROUP BY grupo, hora
)
SELECT
    p.data,
    p.dias_a_frente,
    p.dia_semana,
    p.tipo_dia,
    cv.hora,
    ROUND(p.pedidos_previstos * cv.participacao, 1) AS pedidos_previstos
FROM vw_previsao_dia p
JOIN curva cv
  ON cv.grupo = CASE WHEN p.tipo_dia = 'Feriado' THEN 'F' ELSE CAST(p.dia_semana_num AS CHAR) END;

-- ----------------------------------------------------------------------------
-- 4. Usuário do Grafana (só leitura). Troque a senha antes de usar de verdade.
-- ----------------------------------------------------------------------------
CREATE USER IF NOT EXISTS 'grafana'@'%' IDENTIFIED BY 'grafana@2026';
GRANT SELECT ON `cafeteria`.* TO 'grafana'@'%';
FLUSH PRIVILEGES;

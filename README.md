# notam_schedule

Cruza uma **malha de voos (CSV ou XLSX)** com os **NOTAMs de condição de pista** dos aeroportos de origem e
destino, obtidos da **API oficial AISWEB (DECEA)**, e gera um relatório de impacto por dia e por voo.

## Instalação

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # preencha AISWEB_API_KEY e AISWEB_API_PASS
```

A chave da API é solicitada ao DECEA: <https://aisweb.decea.mil.br/?i=publicacoes&p=api>
(documentação: <https://documenter.getpostman.com/view/7201070/SzKQyg3H>).

## Uso

```bash
.venv/bin/python -m notam_schedule malha.xlsx                       # consulta a API
.venv/bin/python -m notam_schedule malha.xlsx --from 2026-12-01 --to 2026-12-15 -o output/dez.xlsx --csv
.venv/bin/python -m notam_schedule examples/schedule_dated.csv --notam-dir examples/notams   # offline
```

| Opção | Descrição |
|---|---|
| `--timezone auto\|local\|utc` | base dos horários STD/STA. `auto` (padrão) detecta pela coluna de block time; sem ela, assume local |
| `--margin-before/--margin-after MIN` | janela em torno do STD (origem) / STA (destino). Padrão 60/60 |
| `--categories` | restringe: `AERODROME_CLOSED,RUNWAY_CLOSED,LENGTH_REDUCTION,WIDTH_REDUCTION,SURFACE_CONDITION` |
| `--sheet` | aba do `.xlsx` (padrão: a primeira) |
| `--airports arquivo.csv` | tabela IATA→ICAO (padrão: `./airports.csv`, se existir). Formatos: `ident,latitude_deg,longitude_deg,iata_code` (OurAirports) ou `iata,icao,name,tz` |
| `--notam-dir DIR` | modo offline: lê `<ICAO>.xml` e `rotaer_<ICAO>.xml` do diretório |
| `--cache-ttl MIN` | respostas da API ficam em `.cache/` (padrão 60 min) |
| `--no-rotaer` | não consulta o ROTAER (comprimento publicado da pista, usado no % de redução) |
| `--detector rules\|hybrid\|ml` | detector de NOTAMs (ver "Detecção com IA") |

## Tabela de aeroportos

A conversão IATA→ICAO usa, em ordem de prioridade: o `airports.csv` da pasta (ou o passado em `--airports`) e,
para códigos ausentes dele, a tabela interna `notam_schedule/data/airports.csv`. Identificadores que não são ICAO
(ex.: `BR-0569`) são ignorados; se o mesmo IATA aparecer duas vezes, vence o código `SB*`. O fuso horário vem do
arquivo, da tabela interna ou das coordenadas (pacote opcional `timezonefinder`).

## Formato da malha

CSV (`;` ou `,`) ou XLSX. Cabeçalhos em inglês, português ou no padrão de exportação SSIM.
Aeroportos em IATA ou ICAO.

**SSIM / por período** (ex.: `malha.xlsx`) — `Freq`: 1=seg … 7=dom (`1234567`, `.2.4567`)
```
Flt Desg | Eff Date | Dis Date | Freq | Dept Arp | Dept Time | Arvl Arp | Arrv Time | Subfleet | Block Time
```
equivalente a `start,end,frequency,flight,origin,destination,std,sta,aircraft`.

**Voos datados**
```
date;flight;origin;destination;std;sta;aircraft
15/11/2026;AD4001;VCP;SDU;06:00;07:00;E195
```

`std`/`sta` são opcionais (sem horário a análise é feita pelo dia inteiro); `sta` aceita `+1`.
Aeroportos fora do Brasil, ou com IATA desconhecido, aparecem como "fora da cobertura DECEA".

## Como funciona

1. **Malha** → lista de voos (data, STD/STA em UTC).
2. **NOTAMs** de cada aeroporto via `area=notam&icaocode=XXXX`; NOTAMs `C` e os substituídos por `R` são removidos.
3. **Filtro de pista** (texto do item E, em português e inglês):
   `AERODROME_CLOSED`, `RUNWAY_CLOSED` (fechamento parcial vira redução de comprimento), `LENGTH_REDUCTION`
   (TORA/TODA/ASDA/LDA, cabeceira deslocada, comprimento reduzido/disponível), `WIDTH_REDUCTION` e
   `SURFACE_CONDITION` (obras, emborrachamento, contaminação). Ficam de fora luzes, ILS/PAPI, taxiways,
   pátios e obstáculos. Os descartados que citam pista vão para a aba **Revisar - Descartados**.
4. **Vigência**: itens B/C e período D (`DLY 0300-0800`, `MON TIL FRI`, `SEG A SEX`, `NOV 17 18`, `EXC`, `H24`,
   `SR-SS`). Se o D não for interpretado, o NOTAM é considerado ativo em todo o período B–C e marcado.
5. **Impacto**: NOTAM ativo na origem em STD ± margem, ou no destino em STA ± margem.
6. **Severidade**: `CRITICAL` (AD fechado; pista fechada em aeroporto de pista única), `HIGH` (pista fechada
   com outras disponíveis; redução ≥ 10%), `MEDIUM` (largura; redução < 10%), `LOW` (superfície).

## Arquitetura do código

O pacote é dividido em camadas. Cada camada só importa das camadas abaixo dela, e
`tests/test_architecture.py` reprova qualquer import fora dessa regra.

```
cli/          entrada: argumentos e saída no terminal          (python -m notam_schedule, ... .ml)
  │
pipelines/    casos de uso: analyze, collect, train            (liga as peças; um agendador chamaria estes)
  │
  ├── ingestion/   AISWEB (cliente + parser XML), leitores de malha, tabela de aeroportos
  ├── storage/     corpus SQLite e rótulos
  ├── reporting/   Excel, CSV, planilha de clusters
  ├── detection/   regras de detecção de NOTAM de pista
  └── ml/          features, rótulos fracos, K-means, modelo, detector híbrido
  │
domain/       Notam, Flight, Airport, Runway, categorias, expansão do item D, janelas, severidade
              só biblioteca padrão: sem rede, sem arquivo, sem pandas/sklearn/openpyxl
```

| Camada | Pode importar de |
|---|---|
| `domain` | nada além dela mesma |
| `detection` | `domain` |
| `ingestion` | `domain`, `config` |
| `storage`, `reporting` | `domain` |
| `ml` | `domain`, `detection` |
| `pipelines` | todas, menos `cli` |
| `cli` | todas |

As fontes de NOTAM e os detectores entram por interfaces definidas em `domain/ports.py`
(`NotamSource`, `Detector`, `BatchDetector`). Uma fonte nova (por exemplo, NOTAMs de aeroportos fora do
Brasil) só precisa implementar `notams(icao)` e `runways(icao)`, sem mexer no domínio nem nos pipelines.

## Relatório (Excel)

| Aba | Conteúdo |
|---|---|
| **Sumário Executivo** | indicadores, aeroportos e NOTAMs que mais afetam, dias mais críticos |
| **Resumo por Dia** | voos impactados por severidade, decolagens/pousos, NOTAMs e aeroportos do dia |
| **Voos Impactados** | 1 linha por voo: STD/STA local e UTC, pior severidade, impacto na origem e no destino |
| **Resumo por Aeroporto** | operações impactadas (% da malha), severidades, datas, NOTAMs e horários locais |
| **Resumo por NOTAM** | o que é (linguagem simples), quando (hora local), horas ativas, voos afetados |
| **Janelas Ativas** | cada período de vigência (local e UTC) com os voos que caem nele |
| **Impacto por Voo** | 1 linha por voo × NOTAM, com relação à janela ("DURANTE", "35 min antes do início") |
| **Matriz Dia x Aeroporto** | categoria e nº de voos por dia, colorida pela pior severidade |
| **NOTAMs de Pista** / **Revisar - Descartados** / **Info** | texto completo, NOTAMs excluídos que citam pista, parâmetros e erros |

O `--csv` exporta a aba "Impacto por Voo".

## Detecção com IA (semi-supervisionada)

```
             ┌────────────── rótulos fracos ──────────────┐
NOTAMs  ──►  │ humano > Q-code ICAO > regras > K-means    │ ──► Self-training ──► modelo + model card
(SQLite)     └────────────────────────────────────────────┘     (TF-IDF + LogReg)        │
   ▲                                                                                      ▼
   └──── especialista rotula ◄── fila de revisão (discordâncias, incertos, clusters) ◄────┘
```

- **Rótulos fracos**: regras (positivos e negativos claros), Q-code (`QMRLC`, `QMDCH`, `QFALC`...) se vier na
  resposta, e rótulos humanos. Casos ambíguos ficam sem rótulo.
- **K-means** (TF-IDF → SVD): mostra os padrões de redação, propaga rótulos em clusters puros (≥ 95%) e aponta
  clusters a revisar. Duas travas na propagação: nunca aplica `NOT_RUNWAY` a um texto que cita pista, e só
  rotula membros tão próximos do centroide quanto os já rotulados (percentil 90).
- **Texto administrativo** (cabeçalho "NOTAM INICIADOR - SUP AIP …", "RMK: … PRORROGADA POR UM SUP AIP",
  "REF: AIP …") é removido antes de vetorizar, para o agrupamento seguir o conteúdo e não o formato.
- **Self-training**: o classificador aprende também com os NOTAMs não rotulados.
- **Avaliação voltada a segurança**: validação cruzada no gold set (humano), comparando regras, modelo e híbrido.
  O limiar é escolhido para atingir o recall-alvo (padrão 98%).
- **Rastreabilidade**: model card com versão, hash dos dados, seed e métricas (`models/notam_clf_<versão>.json`).
  No relatório, as colunas `Detecção` (rules / ml / rules+ml) e `Prob. ML`.

```bash
M=".venv/bin/python -m notam_schedule.ml"
$M collect                       # baixa NOTAMs para data/notams.sqlite (rodar diariamente)
$M collect --schedule malha.xlsx # só os aeroportos da malha
$M status
$M clusters                      # output/clusters.xlsx
$M train                         # models/current.joblib + model card
$M review -n 200                 # review/review.csv → preencher a coluna human_label
$M label review/review.csv --author "nome"
$M predict "PISTA 10/28 OPERANDO COM 1500 METROS UTEIS"

.venv/bin/python -m notam_schedule malha.xlsx --detector hybrid
```

Demonstração com dados **sintéticos** (não medem desempenho real):
`$M --db data/demo.sqlite synthetic --n 1500 && $M --db data/demo.sqlite train -o models_demo`

**Boas práticas para produção**: monte um **gold set aleatório** (300–500 NOTAMs reais rotulados por 2
especialistas) só para medir desempenho; use `hybrid` (o modelo só acrescenta detecções); compare o model card
novo com o anterior antes de promover `current.joblib`. A extração de pista e distâncias continua por regras.

## Testes

```bash
.venv/bin/python -m pytest -q
```

> A classificação é automática. Ela não substitui a análise de performance/engenharia de operações:
> valide os NOTAMs críticos na fonte oficial.

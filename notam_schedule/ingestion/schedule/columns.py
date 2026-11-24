"""Header recognition: maps English, SSIM-export and Portuguese column names to schedule fields."""
from __future__ import annotations

import re
import unicodedata

# Accepted header names (English, SSIM export and Portuguese) for each field
ALIASES = {
    "date": ["date", "data", "dia", "dt", "flight_date", "data_voo", "dt_voo", "data_partida"],
    "start": ["start", "start_date", "eff_date", "valid_from", "eff_from", "inicio", "data_inicio", "dt_inicio",
              "efetivo_de", "periodo_inicio"],
    "end": ["end", "end_date", "dis_date", "disc_date", "valid_to", "eff_to", "fim", "data_fim", "dt_fim",
            "efetivo_ate", "periodo_fim"],
    "frequency": ["frequency", "freq", "dow", "days", "days_of_operation", "frequencia", "dias", "dias_semana",
                  "operacao"],
    "flight": ["flight", "flt", "flt_desg", "flight_number", "flight_no", "voo", "numero", "nr_voo", "num_voo",
               "voo_numero"],
    "origin": ["origin", "orig", "dep", "adep", "dept_arp", "dep_arp", "from", "from_apt", "dep_apt", "origem",
               "de", "partida", "de_aeroporto", "aeroporto_origem"],
    "destination": ["destination", "dest", "arr", "ades", "arvl_arp", "arr_arp", "to", "to_apt", "arr_apt",
                    "destino", "para", "chegada", "para_aeroporto", "aeroporto_destino"],
    "std": ["std", "etd", "dept_time", "dep_time", "hora_partida", "partida_hora", "horario_partida", "saida",
            "hr_partida", "hora_saida"],
    "sta": ["sta", "eta", "arrv_time", "arr_time", "hora_chegada", "chegada_hora", "horario_chegada", "hr_chegada"],
    "aircraft": ["aircraft", "subfleet", "ac_type", "acft", "equip", "eqp", "equipamento", "aeronave",
                 "tipo_aeronave", "frota"],
    "block": ["block_time", "block", "blk", "tempo_bloco", "tempo_de_bloco"],
}

def _key(name: str) -> str:
    n = unicodedata.normalize("NFKD", str(name or ""))
    n = "".join(ch for ch in n if not unicodedata.combining(ch)).strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", n).strip("_")

def map_columns(header: list[str]) -> dict[str, int]:
    keys = [_key(h) for h in header]
    mapping: dict[str, int] = {}
    for field_name, aliases in ALIASES.items():
        for alias in aliases:
            if alias in keys:
                mapping[field_name] = keys.index(alias)
                break
    return mapping

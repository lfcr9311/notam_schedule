"""Airport table layouts."""
import pytest
from notam_schedule.ingestion.airports import AirportDB


def test_airports_ourairports_layout(tmp_path):
    p = tmp_path / "airports.csv"
    p.write_text(
        "ident,latitude_deg,longitude_deg,iata_code\n"
        "SSGG,-25.388056,-51.521667,GPB\n"        # old code, same IATA
        "SBVC,-14.907778,-40.914722,VDC\n"
        "SBGU,-25.388327,-51.52349,GPB\n"         # current code: SB* wins
        "BR-0569,-14.907885,-40.914804,VDC\n"     # not an ICAO ident: ignored
        "SBSI,-11.885,-55.586,OPS\n"              # overrides the built-in mapping
        "SBUY,-4.85,-65.35,RPU\n"
        "SBXX,-23.0,-47.0,\n",                    # no IATA: still resolvable by ICAO
        encoding="utf-8")
    db = AirportDB.default(p)
    assert db.resolve("GPB").icao == "SBGU"
    assert db.resolve("VDC").icao == "SBVC"
    assert db.resolve("OPS").icao == "SBSI" and db.resolve("OPS").tz == "America/Cuiaba"
    assert db.resolve("RPU").icao == "SBUY"
    assert db.resolve("VCP").icao == "SBKP"    # built-in still used for codes absent from the file
    assert db.resolve("MCO").icao == "KMCO"    # built-in international destinations
    assert db.resolve("JFK").icao == ""        # unknown -> out of coverage

def test_airports_simple_layout(tmp_path):
    p = tmp_path / "extra.csv"
    p.write_text("iata,icao,name,tz\nXYZ,SBZZ,Teste,America/Manaus\n", encoding="utf-8")
    db = AirportDB.default(p)
    assert db.resolve("XYZ") .icao == "SBZZ" and db.resolve("XYZ").tz == "America/Manaus"
    with pytest.raises(FileNotFoundError):
        AirportDB.default(tmp_path / "missing.csv")

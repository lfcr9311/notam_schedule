"""Text preprocessing."""


def test_strip_boilerplate():
    from notam_schedule.ml.features import preprocess, strip_boilerplate
    sup = ("NOTAM INICIADOR - SUP AIP AIRAC N126/26 SUP (E-AIP) 126/26 WEF 11JAN27 TIL 19MAR27 "
           "RWY 16/34 CLSD DEVIDO SER MAINT")
    rmk = "RWY 16/34 CLSD DEVIDO SER MAINT. RMK: ESTA INFORMACAO AERONAUTICA SERA PRORROGADA POR UM SUP AIP"
    assert strip_boilerplate(sup) == "RWY 16/34 CLSD DEVIDO SER MAINT"
    assert preprocess(sup) == preprocess(rmk) == preprocess("RWY 16/34 CLSD DEVIDO SER MAINT")
    assert strip_boilerplate("THR 04 DESLOCADA 289M REF: AIP AD 2.12, AD 2.24 (ADC)") == "THR 04 DESLOCADA 289M"
    assert strip_boilerplate("SUP AIP (E-AIP) 126/26 CNL") == "SUP AIP (E-AIP) 126/26 CNL"  # only boilerplate: kept

# -*- coding: utf-8 -*-
"""Tests de l'entrée GPS NMEA (analyse synchrone via _traiter, sans socket)."""
from simuposition.core.modele import ConfigGPS
from simuposition.core.source_gps import SourceGPS, Fix, _deg, _heure_utc


def source():
    return SourceGPS(ConfigGPS(transport="udp", port_reseau=0))


def test_deg_conversion():
    assert abs(_deg("4807.038", "N") - (48 + 7.038 / 60.0)) < 1e-9
    assert abs(_deg("01131.000", "E") - (11 + 31.0 / 60.0)) < 1e-9
    assert _deg("00000.000", "W") == 0.0
    assert abs(_deg("4807.038", "S") + (48 + 7.038 / 60.0)) < 1e-9
    assert _deg("", "N") is None


def test_heure_utc_avec_date():
    t = _heure_utc("123519", "230394")
    assert (t.year, t.month, t.day, t.hour, t.minute, t.second) == (1994, 3, 23, 12, 35, 19)


def test_rmc_prime_et_remplit_le_fix():
    s = source()
    trame = (b"$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\r\n"
             b"$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A\r\n")
    s._traiter(trame)
    fix, seq = s.dernier_fix()
    assert seq >= 1
    assert fix is not None
    assert abs(fix.lat - (48 + 7.038 / 60.0)) < 1e-6
    assert abs(fix.lon - (11 + 31.0 / 60.0)) < 1e-6
    assert abs(fix.alt_m - 545.4) < 1e-6                     # altitude récupérée de la GGA
    assert abs(fix.sog_ms - 22.4 * 1852.0 / 3600.0) < 1e-3   # vitesse de la RMC (nœuds → m/s)
    assert abs(fix.cog_deg - 84.4) < 1e-6
    assert s._porteuse == "RMC"


def test_gga_seule_publie_si_pas_de_rmc():
    s = source()
    s._traiter(b"$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\r\n")
    fix, seq = s.dernier_fix()
    assert seq == 1 and fix is not None
    assert abs(fix.alt_m - 545.4) < 1e-6
    assert s._porteuse == "GGA"


def test_checksum_faux_ignore():
    s = source()
    s._traiter(b"$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*00\r\n")
    fix, seq = s.dernier_fix()
    assert seq == 0 and fix is None


def test_rmc_invalide_ignore():
    s = source()
    s._traiter(b"$GPRMC,123519,V,,,,,,,230394,,*" + _cs(b"GPRMC,123519,V,,,,,,,230394,,") + b"\r\n")
    fix, seq = s.dernier_fix()
    assert seq == 0 and fix is None


def test_vtg_renseigne_vitesse_cap_pour_gga():
    s = source()
    # flux « GGA + VTG » : la GGA porte, la VTG complète vitesse/cap
    s._traiter(b"$GPVTG,084.4,T,,M,022.4,N,041.5,K,A*" + _cs(b"GPVTG,084.4,T,,M,022.4,N,041.5,K,A") + b"\r\n")
    s._traiter(b"$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\r\n")
    fix, _ = s.dernier_fix()
    assert fix is not None
    assert abs(fix.cog_deg - 84.4) < 1e-6
    assert abs(fix.sog_ms - 22.4 * 1852.0 / 3600.0) < 1e-3


def test_fragmentation_tcp():
    s = source()
    trame = b"$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A\r\n"
    for i in range(0, len(trame), 7):          # octets reçus par petits morceaux
        s._traiter(trame[i:i + 7])
    fix, seq = s.dernier_fix()
    assert seq == 1 and fix is not None
    assert abs(fix.lat - (48 + 7.038 / 60.0)) < 1e-6


def _cs(corps: bytes) -> bytes:
    x = 0
    for b in corps:
        x ^= b
    return f"{x:02X}".encode()

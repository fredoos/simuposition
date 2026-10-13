# -*- coding: utf-8 -*-
"""Tests des trames NMEA et des canaux réseau."""
import socket
import time
from datetime import datetime, timezone

from simuposition.outputs import nmea
from simuposition.outputs.canaux import CanalTCPServeur, CanalUDP, Diffuseur


def solution_test():
    sats = [nmea.Satellite("GPS", 5, 45.2, 120.4, 42), nmea.Satellite("GPS", 12, 10.0, 300.0, 33),
            nmea.Satellite("GPS", 25, 80.0, 10.0, 48), nmea.Satellite("GPS", 29, 30.0, 200.0, 40),
            nmea.Satellite("GPS", 31, 5.0, 90.0, 28, utilise=False),
            nmea.Satellite("GLONASS", 70, 50.0, 60.0, 41), nmea.Satellite("GLONASS", 81, 20.0, 250.0, 36),
            nmea.Satellite("Galileo", 3, 62.0, 180.0, 44), nmea.Satellite("Galileo", 11, 35.0, 330.0, 39),
            nmea.Satellite("BeiDou", 21, 25.0, 140.0, 37)]
    return nmea.Solution(temps=datetime(2026, 10, 13, 8, 30, 15, 250000, tzinfo=timezone.utc),
                         lat=49.0287, lon=1.2199, alt_msl_m=140.2, ondulation_m=45.0,
                         vitesse_ms=46.3, cap=37.5, fix=1, mode_3d=3, pdop=1.9, hdop=1.0, vdop=1.6,
                         satellites=sats)


def test_checksum_connu():
    # exemple classique de la littérature NMEA
    corps = "GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,"
    assert nmea.somme_controle(corps) == "47"
    assert nmea.verifier("$" + corps + "*47")
    assert not nmea.verifier("$" + corps + "*48")


def test_gga_rmc_format():
    g = nmea.GenerateurNMEA("GP")
    sol = solution_test()
    gga = g.gga(sol)
    assert gga.startswith("$GPGGA,083015.25,4901.72200,N,00113.19400,E,1,04,1.0,140.2,M,45.0,M,,*")
    assert gga.endswith("\r\n") and nmea.verifier(gga)
    rmc = g.rmc(sol)
    champs = rmc.split(",")
    assert champs[0] == "$GPRMC" and champs[2] == "A" and champs[7] == "90.00" and champs[9] == "131026"
    assert champs[12].startswith("A*")
    # dialecte GN : talker GN, champ statut de navigation en plus
    gn = nmea.GenerateurNMEA("GN")
    assert gn.rmc(sol).split(",")[0] == "$GNRMC" and gn.rmc(sol).split(",")[13].startswith("V*")
    assert gn.gga(sol).split(",")[7] == "09"      # 9 satellites utilisés toutes constellations


def test_gsa_gsv_par_systeme():
    gn = nmea.GenerateurNMEA("GN")
    sol = solution_test()
    gsa = gn.gsa(sol)
    assert len(gsa) == 4 and all(nmea.verifier(t) for t in gsa)
    assert gsa[0].startswith("$GNGSA,A,3,05,12,25,29,,,,,,,,,1.9,1.0,1.6,1*")
    assert gsa[1].split(",")[-1].startswith("2*") and gsa[3].split(",")[-1].startswith("4*")
    gsv = gn.gsv(sol)
    talkers = [t[1:6] for t in gsv]
    assert talkers == ["GPGSV", "GPGSV", "GLGSV", "GAGSV", "GBGSV"]
    assert gsv[0].startswith("$GPGSV,2,1,05,05,45,120,42,12,10,300,33,25,80,010,48,29,30,200,40,1*")
    assert gsv[1].startswith("$GPGSV,2,2,05,31,05,090,28,1*")
    # dialecte GP : GPS seulement, pas d'identifiant de signal
    gp = nmea.GenerateurNMEA("GP")
    gsv = gp.gsv(sol)
    assert len(gsv) == 2 and gsv[0].startswith("$GPGSV,2,1,05,05,45,120,42,") and gsv[1].endswith("28*" + gsv[1][-4:-2] + "\r\n")
    assert len(gp.gsa(sol)) == 1 and gp.gsa(sol)[0].startswith("$GPGSA,A,3,05,12,25,29,,,,,,,,,1.9,1.0,1.6*")


def test_lot_complet_et_checksums():
    g = nmea.GenerateurNMEA("GN")
    trames = g.trames(solution_test())
    assert [t[3:6] for t in trames][:3] == ["RMC", "VTG", "GGA"]
    assert all(nmea.verifier(t) for t in trames)
    assert any(t.startswith("$GNZDA,083015.25,13,10,2026,00,00*") for t in trames)
    assert any(t.startswith("$GNGLL,4901.72200,N,00113.19400,E,083015.25,A,A*") for t in trames)
    assert any(t.startswith("$GNVTG,37.5,T,,M,90.00,N,166.68,K,A*") for t in trames)


def test_canaux_reseau():
    recu = []
    srv = CanalTCPServeur("127.0.0.1", 0, journal=recu.append)
    assert srv.ouvrir()
    port = srv._srv.getsockname()[1]
    cli = socket.create_connection(("127.0.0.1", port), timeout=2)
    time.sleep(0.3)
    udp_rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_rx.bind(("127.0.0.1", 0))
    udp_rx.settimeout(2)
    udp = CanalUDP("127.0.0.1", udp_rx.getsockname()[1], journal=recu.append)
    d = Diffuseur()
    assert d.ajouter(udp)
    d.canaux.append(srv)
    d.diffuser(["$GPZDA,083015.25,13,10,2026,00,00*5F\r\n"])
    assert cli.recv(100).startswith(b"$GPZDA")
    assert udp_rx.recv(100).startswith(b"$GPZDA")
    assert srv.nb_clients == 1
    cli.close()
    d.tout_fermer()
    udp_rx.close()

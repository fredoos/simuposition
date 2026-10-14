# -*- coding: utf-8 -*-
"""Point d'entrée : python -m simuposition [--reinit]

--reinit : démarre sans restaurer la disposition sauvegardée (taille, panneaux, barres) ; le profil
           (route, réglages) est conservé.

Journal de démarrage : %LOCALAPPDATA%\\skydev\\SimuPosition\\demarrage.log (~/.simuposition ailleurs).
Il trace les étapes du lancement et, en cas de plantage natif (Qt), la pile Python au moment du
plantage (faulthandler) ; les exceptions non rattrapées y sont aussi écrites et affichées."""
import faulthandler
import os
import sys
import time
import traceback

_journal = None


def _chemin_journal() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".simuposition")
    d = os.path.join(base, "skydev", "SimuPosition") if os.environ.get("LOCALAPPDATA") else base
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "demarrage.log")


def etape(msg: str) -> None:
    """Trace une étape du démarrage (horodatée) dans le journal."""
    if _journal is not None:
        _journal.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
        _journal.flush()


def _exception_non_rattrapee(typ, val, tb):
    texte = "".join(traceback.format_exception(typ, val, tb))
    etape("EXCEPTION NON RATTRAPÉE :\n" + texte)
    sys.__stderr__.write(texte)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is not None:
            QMessageBox.critical(None, "SimuPosition — erreur", texte[-3000:])
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    global _journal
    chemin = _chemin_journal()
    try:
        _journal = open(chemin, "w", encoding="utf-8")
        faulthandler.enable(file=_journal, all_threads=True)    # plantage natif → pile Python dans le journal
    except OSError:
        _journal = None
        faulthandler.enable()
    sys.excepthook = _exception_non_rattrapee
    from . import __version__
    print(f"SimuPosition {__version__} — journal de démarrage : {chemin}", flush=True)
    etape(f"SimuPosition {__version__}, Python {sys.version.split()[0]}, {sys.platform}, argv={sys.argv[1:]}")
    if "--reinit" in sys.argv:
        os.environ["SIMUPOSITION_REINIT"] = "1"
        sys.argv.remove("--reinit")
        etape("option --reinit : disposition sauvegardée ignorée")
    # Carte QtLocation : boucle de rendu threaded obligatoire (cf. MapExtract v6 : en « basic »
    # la scène ne se rafraîchit pas à l'interaction).
    os.environ.setdefault("QSG_RENDER_LOOP", "threaded")
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    from PySide6 import __version__ as v_pyside
    from PySide6.QtWidgets import QApplication
    etape(f"PySide6 {v_pyside} importé")
    from .ui.fenetre import Fenetre

    app = QApplication(sys.argv)
    app.setApplicationName("SimuPosition")
    app.setOrganizationName("skydev")
    from .ui import theme
    from PySide6.QtCore import QSettings
    s = QSettings('skydev', 'SimuPosition')
    theme.appliquer(app, str(s.value('theme', 'sombre')), int(s.value('police_pt', theme.TAILLE_DEFAUT)))
    etape("thème appliqué ; construction de la fenêtre…")
    f = Fenetre()
    etape("fenêtre construite")
    if f.size().width() < 800:
        f.resize(1400, 900)
    f.show()
    etape("fenêtre affichée ; boucle d'événements")
    code = app.exec()
    etape(f"fin normale (code {code})")
    return code


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""Point d'entrée : python -m simuposition"""
import os
import sys


def main() -> int:
    # Carte QtLocation : boucle de rendu threaded obligatoire (cf. MapExtract v6 : en « basic »
    # la scène ne se rafraîchit pas à l'interaction).
    os.environ.setdefault("QSG_RENDER_LOOP", "threaded")
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    from PySide6.QtWidgets import QApplication
    from .ui.fenetre import Fenetre

    app = QApplication(sys.argv)
    app.setApplicationName("SimuPosition")
    app.setOrganizationName("EVX")
    f = Fenetre()
    if f.size().width() < 800:
        f.resize(1400, 900)
    f.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

"""
AsturCamín · Avisos meteorológicos oficiales en directo (AEMET a través de MeteoAlarm)

MeteoAlarm publica los avisos de AEMET en formato CAP, gratis y sin clave:
    https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-spain
Nos quedamos con los de las zonas de aviso de Asturias y rellenamos el campo del
contrato alerta_aemet (ninguna | amarilla | naranja | roja) según la zona del aviso.

Zonas de aviso de AEMET en Asturias: litoral occidental, litoral oriental, suroccidental,
central, y cordillera y Picos de Europa. La zona de cada aviso ciudadano se aproxima por
coordenadas y altitud (en producción, con los polígonos oficiales de las zonas).
Los fenómenos costeros se muestran en el mapa pero no cuentan para la carretera.

Si no hay conexión, usa la última copia guardada en contexto/07_tiempo_real/meteoalarm_*.xml
y, si tampoco hay, deja alerta_aemet como 'desconocido'.
"""

import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

URL = "https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-spain"
RAIZ = Path(__file__).resolve().parent.parent
CARPETA_SNAPSHOT = RAIZ / "contexto" / "07_tiempo_real"
REFRESCO_S = 600

ORDEN = ["ninguna", "amarilla", "naranja", "roja"]
SEVERIDAD_CAP = {"minor": "ninguna", "moderate": "amarilla", "severe": "naranja", "extreme": "roja"}
COLOR = {"yellow": "amarilla", "orange": "naranja", "red": "roja", "amarillo": "amarilla", "naranja": "naranja", "rojo": "roja"}
FENOMENOS = [("coastal", "fenómenos costeros"), ("costero", "fenómenos costeros"), ("wind", "viento"), ("viento", "viento"),
             ("rain", "lluvia"), ("lluvia", "lluvia"), ("snow", "nieve"), ("nieve", "nieve"), ("ice", "nieve y hielo"),
             ("thunder", "tormentas"), ("tormenta", "tormentas"), ("fog", "niebla"), ("niebla", "niebla"),
             ("high-temperature", "calor"), ("high temperature", "calor"), ("low-temperature", "frío"),
             ("low temperature", "frío"), ("avalanche", "aludes"), ("aludes", "aludes"), ("fire", "incendios")]
ZONAS = {"litoral_occidental": "Litoral occidental asturiano", "litoral_oriental": "Litoral oriental asturiano",
         "suroccidental": "Suroccidental asturiana", "central": "Central asturiana",
         "cordillera": "Cordillera y Picos de Europa"}


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _fecha(s):
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def zona_de_texto(area):
    a = area.lower()
    if "litoral" in a and "occidental" in a:
        return "litoral_occidental"
    if "litoral" in a and "oriental" in a:
        return "litoral_oriental"
    if "suroccidental" in a:
        return "suroccidental"
    if "central" in a:
        return "central"
    if "cordillera" in a or "picos" in a:
        return "cordillera"
    return None  # zona no reconocida: se aplica a toda Asturias


def zona_de_punto(lat, lon, altitud=None):
    """Aproximación de la zona de aviso de AEMET a partir de coordenadas y altitud."""
    if lat < 43.17 or (altitud or 0) >= 700:
        return "cordillera"
    if lat >= 43.45:
        return "litoral_occidental" if lon < -5.75 else "litoral_oriental"
    if lon < -6.3:
        return "suroccidental"
    return "central"


def parsear(xml_bytes):
    raiz = ET.fromstring(xml_bytes)
    avisos = []
    for entry in raiz.iter():
        if _local(entry.tag) != "entry":
            continue
        campos = {}
        for el in entry.iter():
            n = _local(el.tag)
            t = (el.text or "").strip()
            if t and n not in campos:
                campos[n] = t
        area = campos.get("areaDesc", "")
        codigos = " ".join((el.text or "") for el in entry.iter() if _local(el.tag) == "value")
        if not ("astur" in area.lower() or "picos de europa" in area.lower() or "ES12" in codigos):
            continue
        titulo = campos.get("title", "")
        texto = f"{titulo} {campos.get('event', '')}".lower()
        # Color como palabra completa ("red" no debe casar con "required")
        nivel = next((v for k, v in COLOR.items() if re.search(rf"\b{k}\b", texto)), None) \
            or SEVERIDAD_CAP.get(campos.get("severity", "").lower(), "amarilla")
        if nivel == "ninguna":
            continue
        fenomeno = next((v for k, v in FENOMENOS if re.search(rf"\b{re.escape(k)}", texto)), campos.get("event", "aviso"))
        avisos.append({
            "zona": zona_de_texto(area), "area": area, "nivel": nivel, "fenomeno": fenomeno,
            "costero": fenomeno == "fenómenos costeros",
            "desde": campos.get("onset") or campos.get("effective"), "hasta": campos.get("expires"),
            "titulo": titulo,
        })
    return avisos


class FeedAEMET:
    def __init__(self):
        self.avisos = []
        self.fuente = None
        self.actualizado = None
        self.error = None
        self._hilo = None

    def _descargar(self):
        req = urllib.request.Request(URL, headers={"User-Agent": "AsturCamin/0.1 (hackathon TalentUO)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()

    def refrescar(self):
        try:
            self.avisos = parsear(self._descargar())
            self.fuente, self.error = "MeteoAlarm en directo", None
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            if self.fuente is None:
                for f in sorted(CARPETA_SNAPSHOT.glob("meteoalarm*.xml"), reverse=True):
                    try:
                        self.avisos = parsear(f.read_bytes())
                        self.fuente = f"instantánea {f.stem[-10:]}"
                        break
                    except Exception:
                        continue
        self.actualizado = datetime.now().isoformat(timespec="seconds")

    def arrancar(self):
        def bucle():
            while True:
                self.refrescar()
                print(f"[AsturCamín] Avisos AEMET: {len(self.activos())} activos en Asturias "
                      f"({self.fuente or 'sin datos'}{'; ' + self.error if self.error else ''})")
                time.sleep(REFRESCO_S)
        if self._hilo is None:
            self._hilo = threading.Thread(target=bucle, daemon=True)
            self._hilo.start()

    def activos(self, dt=None):
        momento = (dt or datetime.now()).astimezone()
        res = []
        for a in self.avisos:
            desde, hasta = _fecha(a["desde"] or ""), _fecha(a["hasta"] or "")
            if (desde is None or desde <= momento) and (hasta is None or momento <= hasta):
                res.append(a)
        return res

    def alerta(self, lat, lon, dt, altitud=None):
        """(nivel para el contrato, avisos que aplican). nivel None si no hay datos de AEMET."""
        if self.fuente is None:
            return None, []
        zona = zona_de_punto(lat, lon, altitud)
        aplican = [a for a in self.activos(dt) if not a["costero"] and a["zona"] in (None, zona)]
        nivel = max((a["nivel"] for a in aplican), key=ORDEN.index, default="ninguna")
        return nivel, [{"nivel": a["nivel"], "fenomeno": a["fenomeno"], "zona": ZONAS.get(a["zona"], a["area"]),
                        "hasta": a["hasta"]} for a in aplican]


if __name__ == "__main__":
    # python backend/avisos_aemet.py [--guardar]  -> resume los avisos de Asturias y opcionalmente guarda una copia
    import sys
    feed = FeedAEMET()
    crudo = feed._descargar()
    feed.avisos, feed.fuente = parsear(crudo), "prueba"
    print(f"Avisos de Asturias en el feed: {len(feed.avisos)} · activos ahora: {len(feed.activos())}")
    for a in feed.avisos:
        print(" -", a["nivel"], "|", a["fenomeno"], "|", a["area"], "|", a["desde"], "→", a["hasta"])
    if "--guardar" in sys.argv:
        CARPETA_SNAPSHOT.mkdir(parents=True, exist_ok=True)
        destino = CARPETA_SNAPSHOT / f"meteoalarm_{datetime.now():%Y-%m-%d}.xml"
        destino.write_bytes(crudo)
        print("Guardado en", destino)

"""
AsturCamín · Incidencias oficiales en tiempo real (DGT, DATEX II v3.7)

Descarga el feed público de la DGT (toda España salvo País Vasco y Cataluña, se
actualiza cada minuto), se queda con las incidencias de Asturias y las clasifica
para el mapa: obras, argayos y desprendimientos, cortes, desperfectos del firme,
obstáculos, agua, nieve y hielo, accidentes y retenciones.

También alimenta tres campos del contrato:
  incidencia_oficial_cerca  -> hay una incidencia DGT activa a menos de 1 km
  via_cortada               -> hay un corte a menos de 1 km
  cadenas                   -> hay obligación de cadenas a menos de 5 km

Se refresca en segundo plano cada 2 minutos, así que el triaje nunca espera a la DGT.
Si no hay conexión, usa la instantánea que haya en contexto/07_tiempo_real/dgt_*.xml
(la que guardó el compañero) y, si tampoco hay, deja esos campos como desconocidos.

El parser no depende de los prefijos de los espacios de nombres: busca por nombre local
(situationRecord, latitude, longitude, roadName...), para aguantar cambios menores del perfil.
"""

import math
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

URL = "https://nap.dgt.es/datex2/v3/dgt/SituationPublication/datex2_v37.xml"
RAIZ = Path(__file__).resolve().parent.parent
CARPETA_SNAPSHOT = RAIZ / "contexto" / "07_tiempo_real"
REFRESCO_S = 120
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"

# Caja de Asturias (solo se usa si el registro no dice provincia ni comunidad)
CAJA = (42.85, 43.70, -7.20, -4.45)

CATEGORIAS = {
    "argayo": "Argayo / desprendimiento",
    "corte": "Carretera cortada",
    "obras": "Obras",
    "desperfecto": "Desperfecto en la calzada",
    "agua": "Agua / inundación",
    "nieve": "Nieve o hielo",
    "obstaculo": "Obstáculo en la vía",
    "vehiculo": "Vehículo detenido",
    "accidente": "Accidente",
    "retencion": "Retención",
    "otro": "Otra incidencia",
}

# Palabras (en el tipo, subtipos, causa o comentarios del registro) -> categoría. Se aplican en orden.
REGLAS = [
    ("argayo", ("rockfall", "landslip", "landslide", "mudslide", "subsidence", "avalanche",
                "desprendimiento", "argayo", "derrumbe")),
    ("corte", ("roadclosed", "carriagewayclosures", "closedpermanentlyforthewinter", "cortada", "corte total")),
    ("agua", ("flooding", "flood", "inundaci")),
    ("nieve", ("snow", "ice", "blackice", "frost", "nieve", "hielo", "cadenas", "chains")),
    ("desperfecto", ("surfacedamage", "damagedroadsurface", "roadsurfaceinpoorcondition", "pothole",
                     "infrastructuredamage", "loosechippings", "bache", "socav", "firme")),
    ("obras", ("maintenanceworks", "constructionworks", "roadworks", "roadmaintenance", "resurfacing",
               "obras", "roadworksmanagement")),
    # Vehículos parados en la vía (muchos llegan de balizas V16 conectadas, "DGT 3.0")
    ("vehiculo", ("vehiclestuck", "brokendownvehicle", "vehicleobstruction", "abandonedvehicle")),
    ("accidente", ("accident",)),
    ("obstaculo", ("obstruction", "fallentrees", "objectontheroad", "spillage", "oil", "animal",
                   "brokendownvehicle", "obstaculo")),
    ("retencion", ("abnormaltraffic", "congestion", "queue", "stationarytraffic", "retenci")),
]


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _dist_m(lat1, lon1, lat2, lon2):
    kx = 111320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lon2 - lon1) * kx, (lat2 - lat1) * 110540)


def _texto(el):
    return " ".join(x.strip() for x in el.itertext() if x.strip())


def _registro(rec):
    """Extrae lo útil de un situationRecord, sin depender del espacio de nombres.
    Los textos de DATEX v3 pueden venir como <values><value lang="es">: por eso se usa itertext."""
    tipo = rec.get(XSI_TYPE, "").split(":")[-1]
    coords, textos, comentarios = [], [tipo], []
    campos = {"carretera": None, "provincia": None, "comunidad": None, "municipio": None,
              "inicio": None, "estado": None, "gravedad": None}
    nombres = {"roadName": "carretera", "roadNumber": "carretera", "roadIdentifier": "carretera",
               "province": "provincia", "provincia": "provincia",
               "autonomousCommunity": "comunidad", "comunidadAutonoma": "comunidad",
               "municipality": "municipio", "municipio": "municipio", "town": "municipio",
               "overallStartTime": "inicio", "validityStatus": "estado", "severity": "gravedad"}
    saltar = set()
    lat = None
    for el in rec.iter():
        if id(el) in saltar:
            continue
        n = _local(el.tag)
        tipo_hijo = el.get(XSI_TYPE)
        if tipo_hijo:
            textos.append(tipo_hijo.split(":")[-1])
        if n in nombres or n in ("generalPublicComment", "comment"):
            t = _texto(el)
            saltar.update(id(d) for d in el.iter())
            if n in nombres:
                if t and not campos[nombres[n]]:
                    campos[nombres[n]] = t
            elif t:
                comentarios.append(t)
            continue
        t = (el.text or "").strip()
        if not t:
            continue
        if n == "latitude":
            lat = float(t)
        elif n == "longitude" and lat is not None:
            coords.append((lat, float(t)))
            lat = None
        elif len(t) < 60:
            textos.append(t)
    return dict(id=rec.get("id"), tipo=tipo, coords=coords, textos=textos, comentarios=comentarios, **campos)


def _contiene(texto, clave):
    # Claves cortas como palabra completa ("ice" no debe casar con "police" ni "service")
    return re.search(rf"\b{clave}\b", texto) is not None if len(clave) <= 3 else clave in texto


def categoria_de(r):
    # El tipo y los subtipos vienen en camelCase (blackIce, roadClosed): se comparan en minúsculas, sin "_"
    texto = " ".join(r["textos"] + r["comentarios"]).lower().replace("_", "")
    for cat, claves in REGLAS:
        if any(_contiene(texto, k.lower()) for k in claves):
            return cat
    return "otro"


def es_de_asturias(r):
    admin = " ".join(x for x in (r["provincia"], r["comunidad"]) if x)
    if admin:
        return "asturias" in admin.lower() or "oviedo" in admin.lower()
    if not r["coords"]:
        return False
    la, lo = r["coords"][0]
    return CAJA[0] <= la <= CAJA[1] and CAJA[2] <= lo <= CAJA[3]


def parsear(xml_bytes):
    raiz = ET.fromstring(xml_bytes)
    salida = []
    for rec in raiz.iter():
        if _local(rec.tag) != "situationRecord":
            continue
        r = _registro(rec)
        if not r["coords"] or not es_de_asturias(r):
            continue
        if r["estado"] and r["estado"] not in ("active", "definedByValidityTimeSpec"):
            continue
        cat = categoria_de(r)
        (la, lo), (la2, lo2) = r["coords"][0], r["coords"][-1]
        salida.append({
            "id": r["id"], "categoria": cat, "nombre": CATEGORIAS[cat], "tipo_datex": r["tipo"],
            "carretera": r["carretera"], "municipio": r["municipio"],
            "lat": round(la, 5), "lon": round(lo, 5),
            "lat_fin": round(la2, 5), "lon_fin": round(lo2, 5),
            "inicio": r["inicio"], "gravedad": r["gravedad"],
            "comentario": " ".join(dict.fromkeys(r["comentarios"]))[:300] or None,
        })
    return salida


class FeedDGT:
    def __init__(self):
        self.incidencias = []
        self.fuente = None          # "DGT en directo" | "instantánea ..." | None
        self.actualizado = None
        self.error = None
        self._hilo = None

    def _descargar(self):
        req = urllib.request.Request(URL, headers={"User-Agent": "AsturCamin/0.1 (hackathon TalentUO)"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()

    def refrescar(self):
        try:
            self.incidencias = parsear(self._descargar())
            self.fuente, self.error = "DGT en directo", None
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            if self.fuente is None:  # sin datos todavía: probar la instantánea guardada
                for f in sorted(CARPETA_SNAPSHOT.glob("dgt_*.xml"), reverse=True):
                    try:
                        self.incidencias = parsear(f.read_bytes())
                        self.fuente = f"instantánea {f.stem[-10:]}"
                        break
                    except Exception:
                        continue
        self.actualizado = datetime.now().isoformat(timespec="seconds")

    def arrancar(self):
        """Primera carga y refresco periódico, en un hilo aparte."""
        def bucle():
            while True:
                self.refrescar()
                print(f"[AsturCamín] Incidencias DGT: {len(self.incidencias)} en Asturias "
                      f"({self.fuente or 'sin datos'}{'; ' + self.error if self.error else ''})")
                time.sleep(REFRESCO_S)
        if self._hilo is None:
            self._hilo = threading.Thread(target=bucle, daemon=True)
            self._hilo.start()

    def cerca(self, lat, lon, radio_m):
        res = []
        for i in self.incidencias:
            d = min(_dist_m(lat, lon, i["lat"], i["lon"]), _dist_m(lat, lon, i["lat_fin"], i["lon_fin"]))
            if d <= radio_m:
                res.append({**i, "distancia_m": round(d)})
        return sorted(res, key=lambda x: x["distancia_m"])

    def campos_contrato(self, lat, lon):
        """incidencia_oficial_cerca, via_cortada y cadenas. None si no hay datos de la DGT."""
        if self.fuente is None:
            return {}, []
        a_1km = self.cerca(lat, lon, 1000)
        a_5km = self.cerca(lat, lon, 5000)
        campos = {
            "incidencia_oficial_cerca": bool(a_1km),
            "via_cortada": any(i["categoria"] == "corte" for i in a_1km),
            "cadenas": any("cadena" in (i["comentario"] or "").lower() or "chain" in i["tipo_datex"].lower()
                           for i in a_5km),
        }
        resumen = [{k: i[k] for k in ("nombre", "categoria", "carretera", "distancia_m")} for i in a_1km[:3]]
        return campos, resumen


if __name__ == "__main__":
    # Comprobación rápida desde la raíz del repositorio:
    #   python backend/incidencias_dgt.py            -> descarga y resume las incidencias de Asturias
    #   python backend/incidencias_dgt.py --guardar  -> además guarda el XML en contexto/07_tiempo_real/
    #                                                   (sirve de instantánea si en la demo no hay internet)
    import sys
    from collections import Counter

    feed = FeedDGT()
    t0 = time.perf_counter()
    crudo = feed._descargar()
    print(f"Descargado: {len(crudo) / 1e6:.1f} MB en {time.perf_counter() - t0:.1f} s")
    feed.incidencias = parsear(crudo)
    print(f"Incidencias activas en Asturias: {len(feed.incidencias)}")
    print(dict(Counter(i['nombre'] for i in feed.incidencias)))
    for i in feed.incidencias[:8]:
        print(" -", i["nombre"], "|", i["carretera"], "|", i["tipo_datex"], "|", (i["comentario"] or "")[:80])
    if "--guardar" in sys.argv:
        CARPETA_SNAPSHOT.mkdir(parents=True, exist_ok=True)
        destino = CARPETA_SNAPSHOT / f"dgt_{datetime.now():%Y-%m-%d}.xml"
        destino.write_bytes(crudo)
        print("Guardado en", destino)

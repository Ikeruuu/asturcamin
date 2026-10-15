"""
AsturCamín · Contexto real del aviso (paso 4)

Convierte la ubicación y la hora de un aviso en los campos de contexto del contrato
(titular, vía, tipo de vía, lluvia, temperatura, montaña, fauna, historial, tráfico)
usando los datos reales de contexto/ (tramos de la red de carreteras de Asturias,
accidentes DGT 2019-2024, riesgos geológicos, fauna y clima por concejo).

Se apoya en enriquecer_aviso.py (contexto/08_dataset_unificado) y le añade:
  - ubicación sobre la carretera, no solo sobre el punto kilométrico más cercano
    (distancia al segmento entre dos km consecutivos, con km interpolado);
  - tiempo del momento del aviso con Open-Meteo (gratis, sin clave) cuando el aviso es
    de los últimos 90 días; si no, la serie histórica 2019-2024 del concejo;
  - una categoría por palabras clave, solo para calcular los motivos del prior
    (la explicación); la decisión la sigue tomando el modelo.

Si falta contexto/ o pandas, el servicio sigue funcionando con el contexto 'desconocido'.
"""

import json
import math
import sys
import unicodedata
import urllib.request
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
CARPETA = RAIZ / "contexto" / "08_dataset_unificado"
METEO = RAIZ / "contexto" / "05_meteorologia" / "meteo_diaria_concejos_asturias_2019_2024.csv"

RADIO_CARRETERA_M = 250   # distancia máxima a la carretera para considerar el aviso dentro de la red
ALTITUD_MONTANA_M = 600   # a partir de aquí, "zona de montaña"
HORAS_PUNTA = (7, 8, 9, 13, 14, 18, 19, 20)

TITULAR = {"Principado de Asturias": "Principado", "Estado": "Estado", "Otra/Municipal": "Ayuntamiento"}
TIPO_VIA = {"autopista_autovia": "autovia", "nacional": "nacional", "regional": "regional",
            "comarcal": "comarcal", "local_1_orden": "local", "local_2_orden": "local",
            "otra_enlace": "autovia", "via_urbana_o_municipal": "urbana"}

# Palabras clave -> categoría del prior del compañero (solo para la explicación)
CATEGORIAS = [
    ("desprendimiento", ("desprend", "roca", "piedra", "argayo", "derrumb", "corrimiento", "ladera", "tierra en")),
    ("fauna", ("animal", "jabali", "ciervo", "corzo", "venado", "vaca", "caballo", "oveja", "cabra", "perro",
               "oso", "lobo", "fauna", "atropell")),
    ("agua", ("agua", "balsa", "inund", "charco", "riada", "acuaplan", "desbord")),
    ("hielo", ("hielo", "helad", "nieve", "nevad", "escarcha", "placa")),
    ("infraestructura", ("senal", "semaforo", "arbol", "rama", "farola", "poste", "cable", "quitamiedos",
                         "bionda", "guardarrail", "valla", "panel")),
]


def _sin_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def categoria_de(texto):
    t = _sin_tildes(texto)
    for cat, claves in CATEGORIAS:
        if any(k in t for k in claves):
            return cat
    return "otro"  # baches, socavones, aceite...: el prior solo aporta los motivos de severidad


class ContextoAsturCamin:
    def __init__(self):
        sys.path.insert(0, str(CARPETA))
        from enriquecer_aviso import Contexto  # módulo del compañero

        self.base = Contexto(str(CARPETA), meteo_csvs=[str(METEO)] if METEO.exists() else None)
        t = self.base.t
        self.t = t
        self.lat = t.lat.to_numpy(float)
        self.lon = t.lon.to_numpy(float)
        # Siguiente punto kilométrico de la misma carretera (para formar segmentos)
        orden = t.sort_values(["carretera", "km"]).index.to_numpy()
        sig = np.full(len(t), -1)
        for a, b in zip(orden[:-1], orden[1:]):
            if t.carretera[a] == t.carretera[b] and 0 < t.km[b] - t.km[a] <= 2:
                sig[a] = b
        self.sig = sig
        # La ubicación la decidimos nosotros (segmentos); al módulo base solo le pasamos el punto más cercano
        self.base.radio = float("inf")

    # ------------------------------------------------------------------ ubicación
    def ubicar(self, lat, lon):
        """Tramo de carretera más cercano: distancia al segmento entre km consecutivos y km interpolado."""
        kx = 111320 * math.cos(math.radians(lat))
        ky = 110540
        x = (self.lon - lon) * kx
        y = (self.lat - lat) * ky
        d_punto = np.hypot(x, y)
        mejor = (float(d_punto.min()), int(d_punto.argmin()), 0.0)
        a = np.where(self.sig >= 0)[0]
        b = self.sig[a]
        ax, ay, bx, by = x[a], y[a], x[b], y[b]
        vx, vy = bx - ax, by - ay
        largo2 = vx * vx + vy * vy
        tt = np.clip(-(ax * vx + ay * vy) / np.where(largo2 > 0, largo2, 1), 0, 1)
        d_seg = np.hypot(ax + tt * vx, ay + tt * vy)
        if len(d_seg):
            j = int(d_seg.argmin())
            if d_seg[j] < mejor[0]:
                mejor = (float(d_seg[j]), int(a[j]), float(tt[j]))
        dist, i, frac = mejor
        km = float(self.t.km[i]) + frac * (float(self.t.km[self.sig[i]]) - float(self.t.km[i]) if self.sig[i] >= 0 else 0)
        return i, dist, round(km, 1)

    # ------------------------------------------------------------------ tiempo
    @staticmethod
    @lru_cache(maxsize=256)
    def _open_meteo(lat_r, lon_r, dias):
        url = ("https://api.open-meteo.com/v1/forecast?latitude={}&longitude={}"
               "&hourly=precipitation,temperature_2m&past_days={}&forecast_days=1&timezone=Europe%2FMadrid"
               ).format(lat_r, lon_r, dias)
        with urllib.request.urlopen(url, timeout=3) as r:
            return json.loads(r.read().decode())

    def tiempo(self, lat, lon, dt, meteo_dia):
        """(lluvia de las últimas 24 h en mm, temperatura en °C, fuente)."""
        ahora = datetime.now()
        if ahora - timedelta(days=90) <= dt <= ahora + timedelta(hours=2):
            try:
                dias = min(92, (ahora.date() - dt.date()).days + 2)
                h = self._open_meteo(round(lat, 2), round(lon, 2), dias)["hourly"]
                clave = dt.strftime("%Y-%m-%dT%H:00")
                i = h["time"].index(clave)
                lluvia = sum(v or 0 for v in h["precipitation"][max(0, i - 23):i + 1])
                return round(lluvia, 1), h["temperature_2m"][i], "Open-Meteo"
            except Exception:
                pass
        if meteo_dia:
            return meteo_dia["precipitacion_mm"], meteo_dia["tmin_c"], "histórico del concejo"
        return None, None, None

    # ------------------------------------------------------------------ principal
    def estado(self, texto, lat, lon, dt):
        """Campos de contexto del contrato + información para mostrar al operador."""
        i, dist, km = self.ubicar(lat, lon)
        fila = self.t.iloc[i]
        en_red = dist <= RADIO_CARRETERA_M
        categoria = categoria_de(texto)
        info = self.base.enriquecer(float(fila.lat), float(fila.lon), dt.strftime("%Y-%m-%dT%H:%M"), categoria)
        # El módulo base calcula con el punto kilométrico; si el aviso está fuera de la red, se corrige aquí
        if not en_red:
            info["ubicacion"].update(en_red_catalogada=False, id_tramo=None, carretera=None, km=None,
                                     titularidad="probablemente municipal/urbana",
                                     organismo_conservacion=f"Ayuntamiento de {str(fila.concejo).title()}",
                                     categoria_via="via_urbana_o_municipal")
            info.pop("tramo", None)
            info["prior"] = self.base.prior(categoria, info)
        info["ubicacion"]["distancia_tramo_m"] = round(dist)
        tramo = info.get("tramo") or {}
        mom = info["momento"]

        lluvia, temp, fuente_tiempo = self.tiempo(lat, lon, dt, info.get("meteo_del_dia"))
        tipo_via = TIPO_VIA.get(info["ubicacion"]["categoria_via"], "desconocido")

        if en_red:
            titular = TITULAR.get(fila.titularidad, "desconocido")
            altitud = tramo.get("altitud_m") or 0
            f = (tramo.get("indice_fauna") or 1) * mom["factor_fauna_mes"] * mom["factor_fauna_hora"]
            fauna = "alto" if f >= 2 else ("medio" if f >= 1 else "bajo")
            if (tramo.get("indice_peligrosidad") or 0) >= 3 or tramo.get("nivel_riesgo_base") == "critica":
                historial = "tramo_peligroso"
            elif (tramo.get("n_accidentes") or 0) > 0:
                historial = "algun_accidente"
            else:
                historial = "sin_historial"
            montana = altitud >= ALTITUD_MONTANA_M
        else:
            titular, fauna, historial, montana = "Ayuntamiento", "bajo", "desconocido", False

        # Intensidad de tráfico estimada por tipo de vía y hora (no hay aforos en el dataset)
        punta = dt.hour in HORAS_PUNTA
        if tipo_via in ("autovia", "urbana"):
            trafico = "alta" if punta else "media"
        elif tipo_via == "nacional":
            trafico = "alta" if punta else "media"
        elif tipo_via == "local":
            trafico = "baja"
        elif tipo_via in ("regional", "comarcal"):
            trafico = "media" if punta else "baja"
        else:
            trafico = "desconocido"

        campos = {
            "via": info["ubicacion"]["carretera"],
            "titular": titular,
            "tipo_via": tipo_via,
            "lluvia_24h_mm": lluvia,
            "temperatura_c": temp,
            "zona_montana": montana,
            "riesgo_fauna": fauna,
            "historial_zona": historial,
            "intensidad_trafico": trafico,
        }
        prior = info.get("prior") or {}
        contexto = {
            "en_red": bool(en_red),
            "carretera": info["ubicacion"]["carretera"],
            "km": km if en_red else None,
            "concejo": str(fila.concejo).title(),
            "organismo_conservacion": info["ubicacion"]["organismo_conservacion"],
            "distancia_carretera_m": round(dist),
            "nivel_riesgo_tramo": tramo.get("nivel_riesgo_base"),
            "factores_tramo": tramo.get("factores_riesgo"),
            "accidentes_2019_2024": tramo.get("n_accidentes"),
            "fuente_tiempo": fuente_tiempo,
            "categoria": categoria,
            "veracidad": prior.get("multiplicador_veracidad"),
            "ajuste_severidad_sugerido": prior.get("ajuste_severidad"),
            "motivos": prior.get("motivos", []),
        }
        return campos, contexto

    def tramos_riesgo(self, niveles=("alta", "critica")):
        """Tramos con riesgo base alto o crítico, para pintarlos en el mapa."""
        t = self.t[self.t.nivel_riesgo_base.isin(niveles)]
        return [{"lat": round(r.lat, 5), "lon": round(r.lon, 5), "id": r.id_tramo, "nivel": r.nivel_riesgo_base,
                 "factores": None if not isinstance(r.factores_riesgo, str) else r.factores_riesgo,
                 "accidentes": int(r.n_accidentes)} for r in t.itertuples()]

    def zonas_activas(self):
        """Tramos con procesos activos de ladera (argayos) o de inundación a menos de 200 m
        (Cartografía de Procesos Activos 1:25.000, SITPA-IDEAS)."""
        t = self.t[(self.t.proceso_activo_ladera == 1) | (self.t.proceso_activo_inundacion == 1)]
        return [{"lat": round(r.lat, 5), "lon": round(r.lon, 5), "id": r.id_tramo,
                 "ladera": bool(r.proceso_activo_ladera), "inundacion": bool(r.proceso_activo_inundacion),
                 "susc_argayos": int(r.susc_argayos), "susc_desprendimientos": int(r.susc_desprendimientos)}
                for r in t.itertuples()]

"""
enriquecer_aviso.py — Añade CONTEXTO REAL a un aviso (real o sintético) antes de clasificarlo.

    from enriquecer_aviso import Contexto
    ctx = Contexto("ruta/a/08_dataset_unificado")
    info = ctx.enriquecer(lat=43.30, lon=-4.85, fecha_hora="2026-10-15T22:30", categoria="desprendimiento",
                          es_sintetico=True)

IMPORTANTE
- El contexto (accidentes DGT, riesgos geológicos, fauna, clima, trazado) es REAL.
- Los avisos de entrenamiento son SINTÉTICOS. El contexto actúa como información previa (prior):
  sube o baja la veracidad y la severidad sugerida, pero NUNCA descarta un aviso.
Requisitos: pandas, numpy (astral opcional para día/noche exacto).
"""
import json, math, os
from datetime import datetime
import numpy as np, pandas as pd

try:
    from astral import Observer
    from astral.sun import elevation as _sun_elev
except Exception:  # sin astral -> aproximación horaria
    _sun_elev = None

R_TIERRA = 6371000.0
NIVELES = ["baja", "media", "alta", "critica"]


class Contexto:
    def __init__(self, carpeta, meteo_csvs=None, radio_tramo_m=400):
        self.t = pd.read_csv(os.path.join(carpeta, "contexto_tramos_asturias.csv"))
        with open(os.path.join(carpeta, "parametros_globales.json"), encoding="utf-8") as fh:
            self.p = json.load(fh)
        self.radio = radio_tramo_m
        self._lat = np.radians(self.t.lat.values); self._lon = np.radians(self.t.lon.values)
        self.meteo = None
        if meteo_csvs:
            m = pd.concat([pd.read_csv(f) for f in meteo_csvs], ignore_index=True)
            self.meteo = m.set_index(["codigo_ine", "fecha"])

    # ---------------------------------------------------------------- espacio
    def tramo_cercano(self, lat, lon):
        la, lo = math.radians(lat), math.radians(lon)
        d = 2 * R_TIERRA * np.arcsin(np.sqrt(np.sin((self._lat - la) / 2) ** 2 +
                                             np.cos(la) * np.cos(self._lat) * np.sin((self._lon - lo) / 2) ** 2))
        i = int(np.argmin(d)); return self.t.iloc[i], float(d[i])

    # ---------------------------------------------------------------- tiempo
    @staticmethod
    def franja_luz(lat, lon, dt):
        if _sun_elev is not None:
            from zoneinfo import ZoneInfo
            el = _sun_elev(Observer(latitude=lat, longitude=lon), dt.replace(tzinfo=dt.tzinfo or ZoneInfo("Europe/Madrid")))
            return "dia" if el > 6 else ("amanecer_atardecer" if el > -6 else "noche")
        h = dt.hour + dt.minute / 60
        return "dia" if 8.5 <= h < 19 else ("amanecer_atardecer" if 7 <= h < 8.5 or 19 <= h < 20.5 else "noche")

    @staticmethod
    def estacion(m):
        return {12: "invierno", 1: "invierno", 2: "invierno", 3: "primavera", 4: "primavera", 5: "primavera",
                6: "verano", 7: "verano", 8: "verano"}.get(m, "otono")

    # ---------------------------------------------------------------- principal
    def enriquecer(self, lat, lon, fecha_hora=None, categoria=None, es_sintetico=False, avisos_meteo_activos=None):
        dt = pd.Timestamp(fecha_hora).to_pydatetime() if fecha_hora else datetime.now()
        row, dist = self.tramo_cercano(lat, lon)
        en_red = dist <= self.radio
        franja = self.franja_luz(lat, lon, dt)
        ctx = {
            "es_aviso_sintetico": bool(es_sintetico),
            "origen_contexto": "real",
            "ubicacion": {
                "en_red_catalogada": en_red, "distancia_tramo_m": round(dist),
                "id_tramo": row.id_tramo if en_red else None, "carretera": row.carretera if en_red else None,
                "km": int(row.km) if en_red else None, "concejo": row.concejo,
                "titularidad": row.titularidad if en_red else "probablemente municipal/urbana",
                "organismo_conservacion": row.organismo_conservacion if en_red else f"Ayuntamiento de {str(row.concejo).title()}",
                "categoria_via": row.categoria_via if en_red else "via_urbana_o_municipal",
            },
            "momento": {"fecha_hora": dt.isoformat(timespec="minutes"), "franja_luz": franja, "mes": dt.month,
                        "estacion": self.estacion(dt.month),
                        "factor_fauna_mes": self.p["fauna_factor_mes"].get(str(dt.month), 1.0),
                        "factor_fauna_hora": self.p["fauna_factor_hora"].get(str(dt.hour), 1.0)},
        }
        if en_red:
            ctx["tramo"] = {k: (None if pd.isna(row[k]) else (row[k].item() if hasattr(row[k], "item") else row[k])) for k in [
                "altitud_m", "pendiente_via_pct", "sinuosidad", "susc_desprendimientos", "susc_argayos", "susc_aludes",
                "proceso_activo_ladera", "proceso_activo_inundacion", "terreno_cinegetico", "n_accidentes",
                "indice_peligrosidad", "indice_fauna", "rr_noche", "rr_lluvia", "rr_hielo_nieve",
                "nivel_riesgo_base", "score_riesgo_base", "factores_riesgo"]}
        ctx["clima_concejo"] = {k: (None if pd.isna(row[k]) else float(row[k])) for k in
                                ["precip_media_anual_mm", "pct_dias_lluvia_fuerte", "pct_dias_helada", "pct_dias_nieve"]}
        if self.meteo is not None:
            try:
                d = self.meteo.loc[(int(row.codigo_ine), dt.strftime("%Y-%m-%d"))]
                ctx["meteo_del_dia"] = {"precipitacion_mm": float(d.precipitacion_mm), "nieve_cm": float(d.nieve_cm),
                                        "tmin_c": float(d.tmin_c), "racha_max_kmh": float(d.racha_max_kmh)}
            except Exception:
                ctx["meteo_del_dia"] = None
        if avisos_meteo_activos is not None:
            ctx["avisos_meteo_activos"] = avisos_meteo_activos   # p.ej. desde MeteoAlarm en tiempo real
        if categoria:
            ctx["prior"] = self.prior(categoria, ctx)
        return ctx

    # ---------------------------------------------------------------- prior explicable
    def prior(self, categoria, ctx):
        """Heurística inicial (recalibrar con avisos reales). Devuelve multiplicador de veracidad (0.8-1.6)
        y ajuste de severidad (-1, 0, +1, +2) con sus motivos. Nunca descarta un aviso."""
        t = ctx.get("tramo") or {}; m = ctx["momento"]; mot = []; ver = 1.0; sev = 0
        met = ctx.get("meteo_del_dia") or {}
        if categoria in ("desprendimiento", "roca", "argayo"):
            s = max(t.get("susc_desprendimientos") or 0, t.get("susc_argayos") or 0)
            if s >= 3: ver += 0.3; mot.append("tramo con susceptibilidad alta a desprendimientos/argayos")
            if t.get("proceso_activo_ladera"): ver += 0.2; mot.append("ladera con procesos activos")
            if (met.get("precipitacion_mm") or 0) >= 20: ver += 0.2; mot.append("lluvia fuerte ese día")
        elif categoria in ("fauna", "animal"):
            f = (t.get("indice_fauna") or 1) * m["factor_fauna_mes"] * m["factor_fauna_hora"]
            if f >= 2: ver += 0.3; mot.append(f"zona/momento con {f:.1f}x atropellos de fauna")
            if t.get("terreno_cinegetico"): ver += 0.1; mot.append("terreno cinegético")
        elif categoria in ("agua", "inundacion", "balsa"):
            if t.get("proceso_activo_inundacion"): ver += 0.3; mot.append("zona con procesos de inundación")
            if (met.get("precipitacion_mm") or 0) >= 10: ver += 0.2; mot.append("lluvia ese día")
        elif categoria in ("hielo", "nieve"):
            if (t.get("altitud_m") or 0) >= 800: ver += 0.3; mot.append("tramo de montaña")
            if (met.get("tmin_c") if met.get("tmin_c") is not None else 5) < 1: ver += 0.2; mot.append("temperaturas bajo cero ese día")
        elif categoria in ("infraestructura", "senal", "arbol", "semaforo"):
            if (met.get("racha_max_kmh") or 0) >= 70: ver += 0.3; mot.append(f"temporal confirmado: racha de {met['racha_max_kmh']:.0f} km/h ese día")
            elif (met.get("precipitacion_mm") or 0) >= 30: ver += 0.2; mot.append("lluvia muy fuerte ese día")
        # severidad: el mismo obstáculo es más grave de noche, en curva, en vía rápida o en tramo peligroso
        if m["franja_luz"] == "noche": sev += 1; mot.append("de noche")
        if (t.get("sinuosidad") or 1) >= 1.3: sev += 1; mot.append("trazado con curvas")
        if (t.get("indice_peligrosidad") or 0) >= 3: sev += 1; mot.append("tramo con alta siniestralidad")
        if ctx["ubicacion"]["categoria_via"] in ("autopista_autovia",): sev += 1; mot.append("vía de alta velocidad")
        if (t.get("altitud_m") or 0) >= 800 and categoria in ("desprendimiento", "roca", "argayo", "hielo", "nieve"):
            sev += 1; mot.append("vía de montaña: pocas rutas alternativas y acceso difícil")
        if not ctx["ubicacion"]["en_red_catalogada"]: mot.append("fuera de la red interurbana: sin historial de tramo")
        return {"multiplicador_veracidad": round(min(ver, 1.6), 2), "ajuste_severidad": int(min(sev, 2)),
                "motivos": mot, "nota": "heurística inicial basada en datos reales; el modelo se entrena con avisos sintéticos"}

    @staticmethod
    def aplicar_ajuste(nivel_modelo, ajuste):
        i = NIVELES.index(nivel_modelo); return NIVELES[max(0, min(3, i + ajuste))]

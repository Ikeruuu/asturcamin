"""Enriquece los 5 ejemplos del enunciado del reto con contexto real (ubicaciones ilustrativas)."""
import sys, json, glob, os, pandas as pd
D = sys.argv[1]; O = D + "/08_dataset_unificado"; sys.path.insert(0, O)
from enriquecer_aviso import Contexto
met = glob.glob(D + "/05_meteorologia/meteo_*.csv")
ctx = Contexto(O, meteo_csvs=met); t = ctx.t; m = pd.concat([pd.read_csv(f) for f in met])
row = lambda i: t[t.id_tramo == i].iloc[0]
cn = m[(m.concejo == "CANGAS DEL NARCEA") & (m.fecha >= "2023-01-01")].sort_values("racha_max_kmh").iloc[-1]
as15 = t[(t.carretera == "AS-15") & (t.concejo == "CANGAS DEL NARCEA")].iloc[0]
inu = t[(t.proceso_activo_inundacion == 1) & (t.categoria_via.isin(["comarcal", "local_1_orden", "local_2_orden"])) & (t.clima_disponible == 1)].iloc[0]
lluv = m[m.codigo_ine == inu.codigo_ine].sort_values("precipitacion_mm").iloc[-1]
mont = t[t.carretera == "AS-227"].sort_values("altitud_m").iloc[-1]
ej = [
 ("Hay una roca ocupando parcialmente un carril en la AS-114.", "desprendimiento", row("AS-114_km029"), "2026-10-15T21:30"),
 ("Un semáforo lleva apagado varias horas en una zona urbana.", "infraestructura", pd.Series({"lat": 43.3614, "lon": -5.8494}), "2026-10-15T18:00"),
 ("Existe una señal de tráfico caída tras un temporal.", "infraestructura", as15, cn.fecha + "T16:00"),
 ("Se ha detectado una balsa de agua peligrosa en una carretera secundaria.", "agua", inu, lluv.fecha + "T20:00"),
 ("Hay un desprendimiento próximo a una vía de montaña.", "desprendimiento", mont, "2024-01-20T07:45"),
]
out = []
for texto, cat, r, fh in ej:
    c = ctx.enriquecer(float(r.lat), float(r.lon), fh, categoria=cat, es_sintetico=True)
    out.append({"aviso": {"texto": texto, "categoria_supuesta": cat, "lat": round(float(r.lat), 6), "lon": round(float(r.lon), 6),
                          "fecha_hora": fh, "es_sintetico": True}, "contexto": c})
with open(O + "/ejemplos_reto_enriquecidos.json", "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=2, default=str)
for o in out:
    u = o["contexto"]["ubicacion"]; p = o["contexto"]["prior"]; tt = o["contexto"].get("tramo") or {}
    print("-", o["aviso"]["texto"][:40], "|", u["id_tramo"], u["concejo"], "|", u["organismo_conservacion"], "|", o["contexto"]["momento"]["franja_luz"],
          "| base:", tt.get("nivel_riesgo_base"), "| verac x", p["multiplicador_veracidad"], "sev +", p["ajuste_severidad"], "|", "; ".join(p["motivos"]))

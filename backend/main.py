"""
AsturCamín · Servicio de triaje (esqueleto)

Arrancar (desde la raíz del repositorio):
    pip install -r backend/requirements.txt
    uvicorn backend.main:app --reload --port 8000

Documentación interactiva: http://localhost:8000/docs

Estado actual:
  - construir_estado(): contexto aún sin conectar -> todo 'desconocido' (paso 4)
  - decidir(): RESPUESTA DE PRUEBA, no es el modelo (paso 3: se sustituye por Jev o Laya)
  - aplicar_reglas(): YA ES REAL, usa reglas_fijas y umbrales de contrato.json
"""

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

RAIZ = Path(__file__).resolve().parent.parent
CONTRATO = json.loads((RAIZ / "contrato.json").read_text(encoding="utf-8"))
NIVELES = [n["nombre"] for n in CONTRATO["preguntas"]["severidad"]["niveles"]]
ORGANISMOS = list(CONTRATO["preguntas"]["organismo"]["opciones"])

app = FastAPI(title="AsturCamín · Triaje")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

AVISOS: list = []  # en memoria; más adelante SQLite


class Aviso(BaseModel):
    texto: str
    lat: float
    lon: float
    fecha_hora: Optional[str] = None
    foto: Optional[str] = None  # base64 opcional; el modelo no la usa


def construir_estado(a: Aviso) -> dict:
    """Paso 4: aquí se calcula el contexto (vía, titularidad, AEMET, luz...)."""
    campos = CONTRATO["estado"]["campos"]
    estado = {}
    for nombre, d in campos.items():
        if d["tipo"] == "enum":
            estado[nombre] = "desconocido" if "desconocido" in d["valores"] else d["valores"][0]
        elif d["tipo"] == "integer":
            estado[nombre] = 0
        else:
            estado[nombre] = None
    estado.update(texto=a.texto, lat=a.lat, lon=a.lon,
                  fecha_hora=a.fecha_hora or datetime.now().strftime("%Y-%m-%dT%H:%M"))
    return estado


def decidir(estado: dict) -> dict:
    """RESPUESTA DE PRUEBA. Paso 3: sustituir por la llamada a Jev o Laya con CONTRATO['preguntas'].
    Debe devolver exactamente estas claves."""
    return {
        "organismo_probabilidades": {o: (0.6 if o == ORGANISMOS[0] else 0.1) for o in ORGANISMOS},
        "severidad_probabilidades": {"Baja": 0.1, "Media": 0.5, "Alta": 0.3, "Crítica": 0.1},
        "riesgo_vial": 0.35,
        "urgente": 0.30,
        "inspeccion": 0.50,
        "modelo": "prueba",
    }


def aplicar_reglas(estado: dict, dec: dict) -> dict:
    u = CONTRATO["umbrales"]
    org_p = dec["organismo_probabilidades"]
    organismo = max(org_p, key=org_p.get)
    confianza = org_p[organismo]

    sev_p = dec["severidad_probabilidades"]
    severidad = max(sev_p, key=sev_p.get)
    if sev_p.get("Alta", 0) + sev_p.get("Crítica", 0) >= u["severidad_minima_alta_si_alta_mas_critica_desde"]:
        if NIVELES.index(severidad) < NIVELES.index("Alta"):
            severidad = "Alta"

    revision = confianza < u["confianza_minima_organismo"]
    destinatario = organismo
    regla = CONTRATO["reglas_fijas"]["derivacion_conservacion"]
    if organismo == regla["si_organismo"]:
        destinatario = regla["segun_titular"].get(estado.get("titular"), "revision_humana")
        if destinatario == "revision_humana":
            revision = True

    factores = [k for k in ("luz", "alerta_aemet", "zona_montana", "historial_zona", "intensidad_trafico")
                if estado.get(k) in ("noche", "crepusculo", "naranja", "roja", True, "tramo_peligroso", "alta")]

    return {
        "organismo": organismo,
        "destinatario": destinatario,
        "organismo_probabilidades": org_p,
        "severidad": severidad,
        "severidad_probabilidades": sev_p,
        "riesgo_vial": dec["riesgo_vial"],
        "riesgo_vial_escalado": dec["riesgo_vial"] >= u["riesgo_vial_escala_desde"],
        "urgente": dec["urgente"],
        "urgente_escalado": dec["urgente"] >= u["urgente_escala_desde"],
        "inspeccion": dec["inspeccion"],
        "revision_humana": revision,
        "factores": factores,
        "modelo": dec.get("modelo"),
    }


@app.post("/triaje")
def triaje(aviso: Aviso):
    t0 = time.perf_counter()
    estado = construir_estado(aviso)
    salida = aplicar_reglas(estado, decidir(estado))
    salida.update(
        id_aviso=f"AV-{uuid.uuid4().hex[:8]}",
        id_incidencia=None,  # paso 5: agrupación de duplicados
        latencia_ms=round((time.perf_counter() - t0) * 1000),
        lat=aviso.lat, lon=aviso.lon, texto=aviso.texto, fecha_hora=estado["fecha_hora"],
    )
    AVISOS.append(salida)
    return salida


@app.get("/avisos")
def avisos():
    """Para el mapa y el panel: los más graves primero, y dentro de cada nivel los más recientes."""
    recientes = sorted(AVISOS, key=lambda a: a["fecha_hora"], reverse=True)
    return sorted(recientes, key=lambda a: NIVELES.index(a["severidad"]), reverse=True)


@app.get("/contrato")
def contrato():
    return CONTRATO

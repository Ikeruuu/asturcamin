"""
AsturCamín · Servicio de triaje (esqueleto)

Arrancar (desde la raíz del repositorio):
    pip install -r backend/requirements.txt
    uvicorn backend.main:app --reload --port 8000

Documentación interactiva: http://localhost:8000/docs

Necesita, además de requirements.txt, el paquete laya (ya instalado en el .venv)
y el modelo afinado en modelo/laya_asturcamin/ (no está en GitHub: se comparte aparte).

Estado actual:
  - construir_estado(): luz y estación calculadas; el resto del contexto sigue 'desconocido' (paso 4)
  - decidir(): MODELO REAL (Laya afinado). Si no encuentra el modelo, usa una respuesta de prueba
    y lo indica en el campo "modelo" de la salida
  - aplicar_reglas(): usa reglas_fijas y umbrales de contrato.json
"""

import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "modelo"))
from formato import construir_preguntas, estado_como_texto  # noqa: E402  (mismo formato que en el entrenamiento)

CONTRATO = json.loads((RAIZ / "contrato.json").read_text(encoding="utf-8"))
NIVELES = [n["nombre"] for n in CONTRATO["preguntas"]["severidad"]["niveles"]]
ORGANISMOS = list(CONTRATO["preguntas"]["organismo"]["opciones"])
MODELO_DIR = RAIZ / "modelo" / "laya_asturcamin"
PREGUNTAS, _ = construir_preguntas("nombres")

app = FastAPI(title="AsturCamín · Triaje")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_agente = None
_error_modelo = None


def cargar_agente():
    """Carga el modelo una sola vez. Devuelve None si no está disponible."""
    global _agente, _error_modelo
    if _agente is None and _error_modelo is None:
        if not MODELO_DIR.exists():
            _error_modelo = f"No existe {MODELO_DIR}"
        else:
            try:
                import laya
                _agente = laya.Agent(str(MODELO_DIR), device="cpu")
            except Exception as e:  # el servicio sigue funcionando con la respuesta de prueba
                _error_modelo = f"{type(e).__name__}: {e}"
        if _error_modelo:
            print(f"[AsturCamín] Modelo NO cargado ({_error_modelo}). Se usa la respuesta de prueba.")
    return _agente


@app.on_event("startup")
def precargar_modelo():
    t0 = time.perf_counter()
    if cargar_agente() is not None:
        print(f"[AsturCamín] Modelo laya-asturcamin cargado en {time.perf_counter() - t0:.1f} s")

# Salida y puesta de sol aproximadas en Asturias, hora local (decimal), por mes.
# Suficiente para día/crepúsculo/noche; más adelante se puede calcular con astral por coordenadas.
SOL = {1: (8.9, 18.25), 2: (8.5, 18.8), 3: (8.1, 19.6), 4: (8.2, 20.8), 5: (7.6, 21.4), 6: (7.25, 21.8),
       7: (7.5, 21.8), 8: (8.0, 21.25), 9: (8.6, 20.4), 10: (8.9, 19.4), 11: (8.1, 17.9), 12: (8.7, 17.8)}


def luz_de(dt: datetime) -> str:
    sale, pone = SOL[dt.month]
    h = dt.hour + dt.minute / 60
    if sale - 0.75 <= h < sale + 0.25 or pone - 0.25 <= h < pone + 0.75:
        return "crepusculo"
    return "dia" if sale + 0.25 <= h < pone - 0.25 else "noche"


def estacion_de(mes: int) -> str:
    return {12: "invierno", 1: "invierno", 2: "invierno", 3: "primavera", 4: "primavera", 5: "primavera",
            6: "verano", 7: "verano", 8: "verano"}.get(mes, "otono")

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
    fecha_hora = a.fecha_hora or datetime.now().strftime("%Y-%m-%dT%H:%M")
    dt = datetime.fromisoformat(fecha_hora)
    estado.update(texto=a.texto, lat=a.lat, lon=a.lon, fecha_hora=fecha_hora,
                  luz=luz_de(dt), estacion=estacion_de(dt.month))
    return estado


def decidir(estado: dict) -> dict:
    """Pregunta al modelo de decisión. Devuelve probabilidades, nunca texto generado."""
    agente = cargar_agente()
    if agente is None:
        return respuesta_prueba()
    r = agente.predict(estado_como_texto(estado), PREGUNTAS)["answers"]
    org_p = r["organismo"].get("probabilities") or {r["organismo"]["choice"]: 1.0}
    sev_raw = r["severidad"].get("probabilities") or {str(min(max(round(r["severidad"]["score"]), 0), 3)): 1.0}
    return {
        "organismo_probabilidades": {o: round(float(org_p.get(o, 0.0)), 4) for o in ORGANISMOS},
        "severidad_probabilidades": {NIVELES[i]: round(float(sev_raw.get(str(i), 0.0)), 4) for i in range(len(NIVELES))},
        "riesgo_vial": round(float(r["riesgo_vial"]["noul"]), 4),
        "urgente": round(float(r["urgente"]["noul"]), 4),
        "inspeccion": round(float(r["inspeccion"]["noul"]), 4),
        "modelo": "laya-asturcamin",
    }


def respuesta_prueba() -> dict:
    """Se usa solo si el modelo no está disponible, para que el frontend pueda seguir trabajando."""
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


@app.get("/")
def inicio():
    """Abre la app (formulario, mapa y panel del operador) en http://localhost:8000/"""
    return FileResponse(RAIZ / "frontend" / "index.html")


@app.get("/contrato")
def contrato():
    return CONTRATO


@app.get("/salud")
def salud():
    return {"modelo_cargado": _agente is not None, "modelo": "laya-asturcamin" if _agente else "prueba",
            "error_modelo": _error_modelo, "avisos": len(AVISOS)}

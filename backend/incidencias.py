"""
AsturCamín · Incidencias, revisión del operador y avisos a los organismos

1. Agrupación de avisos repetidos
   Dos avisos son la misma incidencia si hablan de lo mismo (mismo grupo por palabras
   clave), están a menos de 300 m y con menos de 120 min de diferencia (contrato:
   avisos_similares_cerca). El número de avisos parecidos se le pasa al modelo y la
   incidencia se pinta una sola vez en el mapa, con el contador.

2. Revisión del operador (Confirmar / Corregir)
   Cada revisión se guarda en datos/revisiones_operador.jsonl con el mismo formato que
   avisos_train.jsonl (estado + etiquetas), para poder reentrenar con casos reales.

3. Avisos a los organismos (simulados)
   - Crítica: se avisa al organismo en el momento, sin esperar al operador.
   - Resto: se avisa cuando el operador confirma o corrige.
   - Si una corrección cambia el destinatario, se avisa al nuevo y se anula el anterior.
   El texto sale de una plantilla fija (no se genera con un LLM).
"""

import json
import math
import threading
import unicodedata
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FICHERO_REVISIONES = RAIZ / "datos" / "revisiones_operador.jsonl"

RADIO_M = 300
VENTANA_MIN = 120
NIVELES = ["Baja", "Media", "Alta", "Crítica"]

# Grupos para reconocer que dos avisos hablan de lo mismo (más finos que las categorías del contexto)
GRUPOS = [
    ("accidente", ("accidente", "choque", "chocad", "colision", "atropell", "herido", "volcad", "despen", "salido de la carretera", "se ha salido")),
    ("incendio", ("fuego", "incendio", "humo", "llamas", "quema")),
    ("roca", ("roca", "piedra", "desprend", "argayo", "derrumb", "talud", "ladera", "monte abajo")),
    ("arbol", ("arbol", "rama", "poda", "tronco")),
    ("animal", ("animal", "jabali", "ciervo", "corzo", "venado", "vaca", "caballo", "oveja", "cabra", "perro", "zorro", "ganado")),
    ("agua", ("agua", "balsa", "inund", "charc", "riada")),
    ("hielo", ("hielo", "helad", "nieve", "nevad", "escarcha")),
    ("vertido", ("aceite", "gasoil", "vertido", "cisterna", "liquido", "quimico", "mancha")),
    ("vehiculo", ("averiad", "parado", "furgoneta parada", "coche parado", "camion averiado", "triangulos")),
    ("senal", ("senal",)),
    ("semaforo", ("semaforo",)),
    ("farola", ("farola", "alumbrado", "luces apagadas")),
    ("firme", ("bache", "socav", "agujero", "asfalto", "firme", "alcantarilla", "tapa")),
    ("barrera", ("quitamiedos", "bionda", "barrera", "guardarrail")),
    ("carga", ("carga", "neumatico", "objeto", "caido de")),
    ("residuos", ("escombro", "basura", "residuo", "vertedero")),
    ("trafico", ("retencion", "atasco", "cola de coches")),
]

DESTINO_VISIBLE = {
    "Emergencias": "112 Asturias (SEPA)",
    "Tráfico": "Guardia Civil de Tráfico (DGT)",
    "Medio ambiente": "Medio Ambiente del Principado",
}


def _norm(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def grupo_de(texto):
    t = _norm(texto)
    for g, claves in GRUPOS:
        if any(k in t for k in claves):
            return g
    return "otro"


def _dist_m(lat1, lon1, lat2, lon2):
    kx = 111320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lon2 - lon1) * kx, (lat2 - lat1) * 110540)


def _minutos(a, b):
    return abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds()) / 60


class Gestor:
    def __init__(self):
        self.avisos = []          # cada aviso tal y como sale del triaje
        self.estados = {}         # id_aviso -> estado que vio el modelo (para guardar revisiones)
        self.incidencias = {}     # id_incidencia -> {revision, correccion, notificados}
        self.notificaciones = []
        self._n_inc = 0
        self._n_not = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ agrupación
    def parecidos(self, texto, lat, lon, fecha_hora):
        """Avisos anteriores del mismo grupo a menos de 300 m y 120 min."""
        g = grupo_de(texto)
        return [a for a in self.avisos
                if a["grupo"] == g and _dist_m(lat, lon, a["lat"], a["lon"]) <= RADIO_M
                and _minutos(fecha_hora, a["fecha_hora"]) <= VENTANA_MIN]

    def registrar(self, salida, estado, parecidos):
        with self._lock:
            if parecidos:
                id_inc = max(parecidos, key=lambda a: a["fecha_hora"])["id_incidencia"]
            else:
                self._n_inc += 1
                id_inc = f"INC-{self._n_inc:04d}"
                self.incidencias[id_inc] = {"revision": "pendiente", "correccion": None, "notificados": {}}
            salida["id_incidencia"] = id_inc
            salida["grupo"] = grupo_de(salida["texto"])
            self.avisos.append(salida)
            self.estados[salida["id_aviso"]] = estado
            vista = self.vista(id_inc)
            # Crítica: se avisa al organismo sin esperar al operador
            if vista["severidad"] == "Crítica" and not vista["revision_humana"]:
                self._notificar(id_inc, vista, "Aviso inmediato (Crítica)")
            return vista

    # ------------------------------------------------------------------ vista para el mapa y el panel
    def vista(self, id_inc):
        avisos = [a for a in self.avisos if a["id_incidencia"] == id_inc]
        rep = max(avisos, key=lambda a: (NIVELES.index(a["severidad"]), a["fecha_hora"]))
        inc = self.incidencias[id_inc]
        v = dict(rep)
        v.update(n_avisos=len(avisos), textos=[a["texto"] for a in sorted(avisos, key=lambda a: a["fecha_hora"])],
                 primer_aviso=min(a["fecha_hora"] for a in avisos),
                 ultimo_aviso=max(a["fecha_hora"] for a in avisos),
                 revision=inc["revision"], severidad_modelo=rep["severidad"],
                 destinatario_modelo=rep["destinatario"],
                 notificados=[d for d, n in inc["notificados"].items() if not n.get("anulada")])
        if inc["correccion"]:
            v.update(inc["correccion"])
            v["revision_humana"] = False
        return v

    def lista(self):
        vistas = [self.vista(i) for i in self.incidencias if any(a["id_incidencia"] == i for a in self.avisos)]
        vistas.sort(key=lambda v: v["ultimo_aviso"], reverse=True)
        return sorted(vistas, key=lambda v: NIVELES.index(v["severidad"]), reverse=True)

    # ------------------------------------------------------------------ revisión del operador
    def revisar(self, id_inc, accion, derivar, severidad=None, organismo=None, nota=None):
        """accion: confirmar | corregir. derivar(organismo, estado) -> destinatario."""
        with self._lock:
            if id_inc not in self.incidencias:
                raise KeyError(id_inc)
            inc = self.incidencias[id_inc]
            antes = self.vista(id_inc)
            if accion == "corregir":
                organismo = organismo or antes["organismo"]
                severidad = severidad or antes["severidad"]
                estado_rep = self.estados[antes["id_aviso"]]
                inc["correccion"] = {"organismo": organismo, "severidad": severidad,
                                     "destinatario": derivar(organismo, estado_rep, antes.get("contexto")),
                                     "nota": nota}
                inc["revision"] = "corregida"
            else:
                inc["revision"] = "confirmada"
            despues = self.vista(id_inc)
            # Si cambia el destinatario, se anula el aviso anterior
            for d, n in inc["notificados"].items():
                if d != despues["destinatario"] and not n.get("anulada"):
                    n["anulada"] = True
                    self._crear_notificacion(id_inc, despues, d, "Anulación: la incidencia se ha derivado a otro organismo")
            self._notificar(id_inc, despues, "Tras revisión del operador")
            self._guardar_revision(id_inc, despues)
            return self.vista(id_inc)

    def _guardar_revision(self, id_inc, v):
        filas = []
        for a in [a for a in self.avisos if a["id_incidencia"] == id_inc]:
            filas.append({
                "id": a["id_aviso"], "id_incidencia": id_inc, "estado": self.estados[a["id_aviso"]],
                "etiquetas": {"organismo": v["organismo"], "severidad": v["severidad"],
                              "riesgo_vial": bool(a["riesgo_vial_escalado"]), "urgente": bool(a["urgente_escalado"]),
                              "inspeccion": a["inspeccion"] >= 0.5},
                "meta": {"origen": "operador", "revision": v["revision"], "nota": v.get("nota"),
                         "prediccion": {"organismo": a["organismo"], "severidad": a["severidad"]},
                         "fecha_revision": datetime.now().isoformat(timespec="seconds")},
            })
        FICHERO_REVISIONES.parent.mkdir(parents=True, exist_ok=True)
        with open(FICHERO_REVISIONES, "a", encoding="utf-8") as fh:
            for f in filas:
                fh.write(json.dumps(f, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------ avisos a los organismos
    def _notificar(self, id_inc, v, motivo):
        destino = v["destinatario"]
        if destino == "revision_humana":
            return
        previo = self.incidencias[id_inc]["notificados"].get(destino)
        if previo and not previo.get("anulada") and previo["severidad"] == v["severidad"]:
            return  # ya avisado con la misma severidad
        if previo and not previo.get("anulada"):
            motivo = f"Actualización: severidad {previo['severidad']} → {v['severidad']}"
        n = self._crear_notificacion(id_inc, v, destino, motivo)
        self.incidencias[id_inc]["notificados"][destino] = {"severidad": v["severidad"], "id": n["id"]}

    def _crear_notificacion(self, id_inc, v, destino, motivo):
        self._n_not += 1
        c = v.get("contexto") or {}
        if c.get("en_red"):
            lugar = f"{c['carretera']}, km {str(c['km']).replace('.', ',')} ({c['concejo']})"
        elif c.get("concejo"):
            lugar = f"{c['concejo']} (fuera de la red de carreteras)"
        else:
            lugar = f"{v['lat']:.5f}, {v['lon']:.5f}"
        si_no = lambda b: "sí" if b else "no"
        lineas = [
            f"Incidencia {id_inc} · severidad {v['severidad']} · {v['n_avisos']} aviso(s) ciudadano(s)",
            f"Lugar: {lugar} · coordenadas {v['lat']:.5f}, {v['lon']:.5f}",
            f"Aviso: «{v['texto']}»",
            f"Riesgo para la circulación: {si_no(v['riesgo_vial_escalado'])} · Urgente: {si_no(v['urgente_escalado'])} · "
            f"Inspección presencial: {si_no(v['inspeccion'] >= 0.5)}",
        ]
        if c.get("motivos"):
            lineas.append("Contexto: " + "; ".join(m for m in c["motivos"] if not m.startswith("fuera de la red")))
        if c.get("incidencias_oficiales_cerca"):
            lineas.append("DGT cerca: " + "; ".join(f"{i['nombre']} a {i['distancia_m']} m" for i in c["incidencias_oficiales_cerca"]))
        if v.get("nota"):
            lineas.append(f"Nota del operador: {v['nota']}")
        n = {
            "id": f"NOT-{self._n_not:04d}", "hora": datetime.now().isoformat(timespec="seconds"),
            "destinatario": destino, "destinatario_visible": DESTINO_VISIBLE.get(destino, destino),
            "id_incidencia": id_inc, "severidad": v["severidad"], "motivo": motivo,
            "asunto": f"[{v['severidad']}] {v['texto'][:60]}{'…' if len(v['texto']) > 60 else ''} · {lugar}",
            "cuerpo": "\n".join(lineas), "canal": "simulado (en producción: correo o API del organismo)",
        }
        self.notificaciones.append(n)
        return n

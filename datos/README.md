# AsturCamín · Dataset sintético (paso 2)

Generado con `generar_dataset.py` a partir de `contrato.json` v1.0, semilla 42.

## Ficheros

| Fichero | Qué es | Quién lo usa |
| --- | --- | --- |
| `avisos_train.jsonl` | 816 avisos para ajustar instrucciones, umbrales y, si usáis Laya, afinar el modelo | Modelo |
| `avisos_test.jsonl` | 208 avisos apartados. **No se mira ni se toca hasta medir** | Solo para las métricas del paso 7 |
| `semillas_reto.jsonl` | Los 5 ejemplos de la diapositiva, etiquetados a mano | Primera prueba del modelo y demo |
| `revision_muestra.csv` | 100 avisos de train para revisar a mano (se abre en Excel) | Datos |

## Formato de cada línea

```json
{
  "id": "AV-00001",
  "id_incidencia": "INC-0001",
  "estado":    { ...los 20 campos del contrato, texto incluido... },
  "etiquetas": { "organismo": "...", "severidad": "...", "riesgo_vial": true, "urgente": true, "inspeccion": false },
  "meta":      { "tipo": "roca_carril", "ambito": "interurbano", "dificultad": "normal", "agravantes": ["noche"], "origen": "sintetico" }
}
```

`estado` es exactamente lo que se le pasa al modelo. `meta` no se le pasa nunca: sirve para analizar errores (por ejemplo, en qué tipos de incidencia falla).

## Cómo se etiqueta

- **Organismo**: lo fija el tipo de incidencia y el ámbito (la misma señal caída va a Conservación en carretera y a Ayuntamiento en ciudad).
- **Severidad**: nivel base del tipo + 1 si se cumple algún agravante (noche o crepúsculo, lluvia ≥ 20 mm, aviso naranja o rojo, montaña, tráfico alto, tramo peligroso), con tope en Crítica.
- **Riesgo vial**: el tipo pone en peligro a quien circula y la severidad es Alta o Crítica.
- **Urgente**: Crítica siempre; Alta salvo que la reparación se pueda programar (bache, barrera dañada).
- **Inspección**: la fija el tipo (desprendimientos, socavones, barreras… sí; semáforo apagado, vehículo averiado… no).

Incluye 113 avisos duplicados (varias personas avisando de lo mismo) y 22 pares de contraste (mismo hecho con contexto tranquilo y agravado). Train y test se separan por incidencia: los duplicados y los pares nunca quedan a ambos lados.

## Qué hacer ahora (Datos)

1. Abrid `revision_muestra.csv`, marcad cada fila en `correcta_s_n` y escribid la corrección cuando no lo sea.
2. Si un mismo error se repite, se corrige en la regla del generador y se vuelve a generar, no a mano fila a fila.
3. Regenerar: `python generar_dataset.py --n 850 --semilla 42 --contrato contrato.json --salida datos/`. Con la misma semilla sale exactamente el mismo dataset.

## Limitaciones a tener en cuenta

- Las etiquetas salen de reglas, así que el modelo aprende **nuestros** criterios. Si los mentores dicen que el reparto real es otro, se cambia la regla y se regenera.
- Los textos salen de plantillas. Para que suenen más naturales, se pueden reescribir con un LLM (el reto lo permite fuera del sistema) manteniendo las etiquetas.
- Medir solo contra un test sintético da cifras optimistas. Lo ideal es escribir a mano 30–50 avisos reales más como segundo conjunto de prueba.
- La luz (día, crepúsculo, noche) usa horas aproximadas de Oviedo por mes; en producción se calcula con `astral` a partir de las coordenadas.

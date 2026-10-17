# AsturCamín · Dataset sintético (paso 2)

Generado con `generar_dataset.py` a partir de `contrato.json` v1.1, semilla 42. **Versión 3**: reglas ajustadas tras la revisión manual de 100 avisos (revisión v1) y criterio de severidad revisado (revisión v2). Ver el final.

## Ficheros

| Fichero | Qué es | Quién lo usa |
| --- | --- | --- |
| `avisos_train.jsonl` | 1.897 avisos para ajustar instrucciones, umbrales y, si usáis Laya, afinar el modelo | Modelo |
| `avisos_test.jsonl` | 465 avisos apartados. **No se mira ni se toca hasta medir** | Solo para las métricas del paso 7 |
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
- **Severidad**: nivel base del tipo + 1 si se cumple alguno de *sus* agravantes (vía rápida = autovía o nacional, tráfico alto, lluvia ≥ 20 mm, aviso naranja o rojo, noche o crepúsculo, tramo peligroso), con tope en Crítica. Cada tipo tiene sus propios agravantes y algunos tienen nivel fijo (ver las revisiones v1 y v2). Crítica se reserva para personas en peligro ahora mismo o vía cortada.
- **Riesgo vial**: el tipo pone en peligro a quien circula y la severidad es Alta o Crítica.
- **Urgente**: Crítica siempre; Alta salvo que la reparación se pueda programar (bache, barrera dañada).
- **Inspección**: la fija el tipo (desprendimientos, socavones, barreras… sí; semáforo apagado, vehículo averiado… no).

Incluye 306 avisos duplicados (varias personas avisando de lo mismo) y 56 pares de contraste (mismo hecho con contexto tranquilo y agravado). Train y test se separan por incidencia: los duplicados y los pares nunca quedan a ambos lados.

## Qué hacer ahora (Datos)

1. Abrid `revision_muestra.csv`, marcad cada fila en `correcta_s_n` y escribid la corrección cuando no lo sea.
2. Si un mismo error se repite, se corrige en la regla del generador y se vuelve a generar, no a mano fila a fila.
3. Regenerar: `python generar_dataset.py --n 2000 --semilla 42 --contrato contrato.json --salida datos/`. Con la misma semilla sale exactamente el mismo dataset.

## Limitaciones a tener en cuenta

- Las etiquetas salen de reglas, así que el modelo aprende **nuestros** criterios. Si los mentores dicen que el reparto real es otro, se cambia la regla y se regenera.
- Los textos salen de plantillas. Para que suenen más naturales, se pueden reescribir con un LLM (el reto lo permite fuera del sistema) manteniendo las etiquetas.
- Medir solo contra un test sintético da cifras optimistas. Lo ideal es escribir a mano 30–50 avisos reales más como segundo conjunto de prueba.
- La luz (día, crepúsculo, noche) usa horas aproximadas de Oviedo por mes; en producción se calcula con `astral` a partir de las coordenadas.

## Revisión v1 (100 avisos, 81 correctos)

Cambios aplicados a las reglas; con ellos, 99 de las 100 filas revisadas coinciden con el criterio del equipo:

| Tipo | Antes | Ahora |
| --- | --- | --- |
| Vertido peligroso (cisterna, químico) | Crítica | Alta fija, siempre urgente |
| Alcantarilla abierta o sin tapa | Alta, Crítica con agravantes | Alta fija |
| Mancha de aceite o gasoil | Alta, Crítica con agravantes | Alta fija |
| Barrera de seguridad dañada | Media, Alta con agravantes | Alta fija |
| Animal muerto | Un solo tipo | Jabalí o corzo en el carril: Alta fija. Animal sin especificar: Media fija |
| Objeto caído de un vehículo | Alta, Crítica con agravantes | Media fija |
| Bache pequeño o bachecillo | Baja, Media con tráfico | Baja fija |
| Asfalto levantado | Igual que bache pequeño | Carretera: Media. Ciudad: Baja, Media con tráfico alto |
| Tramo peligroso | Agravaba cualquier tipo en calzada | Solo agrava los tipos que admiten agravantes |

Pendiente de la v1 (vehículo averiado en el carril): resuelto en la v2. Alta; Crítica en vía rápida o con tráfico alto.

## Revisión v2 (criterio de severidad)

Problema detectado en las pruebas: una roca en una carretera secundaria de Villaviciosa, de noche, salía Crítica. La noche (y el crepúsculo) era el agravante que más se activaba, y el 28 % del entrenamiento era Crítica.

Criterio nuevo: **Crítica = personas en peligro ahora mismo o vía cortada.** Un obstáculo en el carril es Alta y solo pasa a Crítica en vía rápida (autovía o nacional), con tráfico alto o en tramo peligroso. La noche solo agrava cuando la oscuridad es el problema (bache grande, ganado suelto, aviso vago). La montaña ya no agrava hielo ni argayos: en Asturias es lo normal.

| Tipo | Antes | Ahora |
| --- | --- | --- |
| Roca en el carril | Crítica siempre | Alta; Crítica con vía rápida, tráfico alto, lluvia fuerte o tramo peligroso |
| Señal caída en el carril | Alta; Crítica de noche o con tráfico | Alta; Crítica con vía rápida o tráfico alto |
| Vehículo averiado en el carril | Alta; Crítica de noche, con lluvia o tráfico | Alta; Crítica con vía rápida o tráfico alto |
| Accidente sin heridos | Alta; Crítica de noche o con tráfico | Alta; Crítica con vía rápida o tráfico alto |
| Placas de hielo | Alta; Crítica de noche o en montaña | Alta; Crítica con vía rápida o tráfico alto |
| Nieve en puerto | Alta; Crítica de noche o con aviso | Alta; Crítica solo con aviso naranja o rojo |
| Argayo junto a la vía | Alta; Crítica con lluvia, aviso o montaña | Alta; Crítica con lluvia fuerte o aviso |
| Socavón | Alta; Crítica con lluvia, tráfico o noche | Alta; Crítica con lluvia o tráfico alto |
| Balsa de agua | Alta; Crítica de noche, con aviso o tráfico | Alta; Crítica con aviso o en vía rápida |
| Aviso vago | Alta; Crítica de noche o con lluvia | Media; Alta de noche o con lluvia |
| Asfalto levantado en el arcén (carretera) | Media | Baja |
| Semáforo apagado | Media; Alta con tráfico o de noche | Media; Alta solo con tráfico alto |
| Farolas apagadas | Baja; Media de noche | Baja fija |
| Ramas en el arcén | Baja; Media de noche o con aviso | Baja; Media solo con aviso |

Se mantienen las decisiones de la v1 (vertido peligroso Alta, barrera dañada Alta, objeto caído Media). La semilla 1 del reto (roca en la AS-114, de día, tráfico normal) pasa a Alta. El peso de los tipos de base Crítica baja del 15 % al 10 %: el entrenamiento queda en 26 % Baja, 28 % Media, 27 % Alta y 20 % Crítica. El criterio de cada nivel en `contrato.json` (v1.1) se ha reescrito igual.

**Tamaño (v3.1):** con 850 incidencias el modelo acertaba Baja y Media pero dudaba entre Alta y Crítica (probabilidades casi planas: le faltaban ejemplos por tipo y pasos de entrenamiento). Se sube a 2.000 incidencias (unos 50 ejemplos por tipo) y el afinado a 6 épocas.

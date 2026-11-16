# Fase 4: dashboard y módulos

La aplicación sigue siendo una sola página Flask. Al abrirla aparece el Centro de
control. La barra superior y los controles de simulación permanecen disponibles.
El botón **Menú** abre un panel lateral con seis destinos:

| Vista | Contenido |
| --- | --- |
| Centro de control | Cuatro estadísticas, selección de tripulación, siete indicadores, silueta y resumen de eventos ordenados por relevancia. |
| Historial y análisis | Selector de indicador, evaluación y referencia individual, gráfico Canvas, fechas, procedencia y tabla de registros. |
| Expedientes médicos | Formulario original de antecedentes, observaciones y siete parejas de referencias; guardado explícito en SQLite. |
| Registro manual | Un único formulario para uno o varios indicadores, fecha y observaciones; conserva la validación e idempotencia anteriores. |
| Dispositivos | Estado comunicado por el backend, diferencia entre conexión y telemetría verificada, y explicación de los estados posibles. |
| Historial de eventos | Evaluación completa, orientación, fuentes e historial de episodios del astronauta seleccionado. |

**Centro de control** en la barra superior permite volver desde cualquier módulo.
Desde el dashboard también hay accesos directos al registro manual, dispositivos,
evolución del indicador y eventos.

## Conservación del contexto

Navegar solo cambia la visibilidad de los paneles. Conserva el astronauta, indicador,
filtro de origen, simulación y formularios escritos. No vuelve a cargar el expediente
ni guarda automáticamente. El Canvas vuelve a medir su contenedor cuando se muestra
Historial y análisis, y sigue respondiendo a los cambios de tamaño de la ventana.

Cambiar de astronauta mantiene el comportamiento anterior: carga su expediente y
descarta los borradores del astronauta anterior. Ambos formularios avisan de ello.
Los borradores se conservan al cambiar de **vista**, pero no sobreviven a una recarga
de página. Los datos guardados permanecen en SQLite.

El resumen de eventos no depende del filtro «Solo indicador seleccionado»: las
prioridades no se ocultan por seleccionar otro sensor. Una prioridad que haya emitido
el servidor se conserva en un aviso común a todas las vistas, con astronauta, valor,
fecha y acceso a su evaluación confirmada. Una carga, error, lectura antigua o datos
insuficientes no la dan por resuelta. Se retira cuando una evaluación válida del mismo
astronauta y origen deja de detectar esa condición. Una evaluación de otro origen no
resuelve la prioridad anterior. Este recuerdo de presentación dura la sesión de la
página; los episodios del backend mantienen su persistencia anterior.

No se agregaron criterios médicos, protocolos clínicos ni umbrales. El mecanismo usa
las prioridades que ya entrega la API. Sin protocolos configurados, no se inventa una
prioridad. El Roadtrip continúa **sin integración física verificada**.

## Distribución y teclado

`dashboard.css` amplía el diseño original con Grid y Flexbox. El marco ocupa `100dvh`;
la tripulación y las alertas tienen desplazamiento interno si lo necesitan. En
pantallas grandes hay tres columnas; por debajo de 1180 px se reorganizan y por debajo
de 900 px se apilan. Las vistas largas se pueden recorrer sin perder la barra superior.
El zoom del navegador, textos extensos y alturas pequeñas pueden requerir desplazamiento.

El menú es un diálogo nativo: Tab recorre sus controles, Escape lo cierra y devuelve
el foco al botón Menú. Flechas arriba/abajo, Inicio y Fin recorren los módulos. Enter
abre el módulo y sitúa el foco en su título. Hay enlace para saltar al contenido,
indicación del módulo activo y foco visible. La silueta SVG y sus sensores se conservan.

## Verificación ejecutada

```powershell
python -m unittest discover -s tests -q
node --test tests/test_medical_frontend.cjs
```

- **64 pruebas Python:** API, almacenamiento, expedientes, evaluación, adquisición,
  dispositivos, HTML sin IDs duplicados y presencia de los seis módulos.
- **32 pruebas JavaScript:** incluye las 27 anteriores y cinco casos nuevos para
  navegación, borradores durante navegación/actualización, teclado y foco, Canvas al
  volver a mostrarlo, prioridades persistentes y orden de eventos sin ocultar prioridades.
- **Revisión visual en WebView2 con una base SQLite temporal:** los seis módulos,
  gráfico, menú con teclado, formulario manual parcial guardado, borrador médico
  conservado al navegar y expediente guardado. Roadtrip aparece como integración
  pendiente. Se observaron actualizaciones de la simulación y su pausa.
- **1366×768 de área de navegador:** desbordamiento global horizontal/vertical de
  0/0 px; siete indicadores dentro del área visible y controles superiores accesibles.
- **1920×1080 dentro de un iframe de prueba**, mostrado al 75 % para caber en el
  monitor: desbordamiento global de la aplicación de 0/0 px y siete indicadores visibles.
  No fue una ventana nativa de 1920×1080.
- **520×844 de área de navegador:** distribución vertical, sin desbordamiento horizontal,
  desplazamiento por la tripulación, silueta, indicadores y eventos; barra superior disponible.
- Un monitor de errores añadido exclusivamente al servidor temporal registró **0
  errores JavaScript y 0 rechazos de promesas sin manejar** durante la revisión. El
  monitor y los marcos de prueba no forman parte de la aplicación entregada.

La persistencia de prioridades se comprobó mediante datos sintéticos de pruebas
automatizadas. No se validaron dispositivos físicos, protocolos clínicos ni todos los
navegadores. No se empaquetó ni se publicó un ejecutable.

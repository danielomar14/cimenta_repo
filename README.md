# CIMENTA

**Inteligencia inmobiliaria cuantitativa para la Zona Metropolitana del Valle de México (ZMVM).**

CIMENTA convierte los anuncios de los principales portales inmobiliarios de México en
valuaciones automáticas, pronósticos de plusvalía por colonia y oportunidades de inversión
con evidencia. Responde la pregunta *"Tengo X pesos: ¿dónde pongo mi siguiente proyecto?"*
con un dossier accionable: retorno neto esperado, intervalo de confianza, condición de
fracaso explícita y compatibilidad con la escala de ejecución del cliente.

No es un portal de listados ni un valuador aislado: es el analista cuantitativo que la
constructora mediana no puede contratar de tiempo completo.

## Módulos del sistema

| Módulo | Nombre | Qué hace |
|--------|--------|----------|
| M1 | Ingesta | Recolección diaria multi-portal + deduplicación (geometría, texto, huella visual). |
| M2 | Valuación | AVM con intervalos de confianza; valor estimado independiente del optimismo del vendedor. |
| M3 | Plusvalía | Pronóstico de apreciación por colonia (permisos de obra, comercio, transporte, contagio). |
| M4 | Capa urbana | Uso de suelo oficial (niveles construibles) + conectividad en minutos reales. |
| M5 | Oportunidades | Tres jugadas: subvaluación, plusvalía, redensificación — cada una con evidencia. |
| M6 | Portafolio | Optimizador de cartera dado un capital, con diversificación y retorno esperado. |

## Estructura del repositorio

```
cimenta_repo/
├── app/          # Aplicación / interfaz (frontend, API)
├── data/         # Datos (crudos, intermedios, procesados) — no versionados
├── notebooks/    # Exploración y prototipos en Jupyter
├── src/          # Código fuente de los módulos
├── test/         # Pruebas
├── docs/         # Documentación y contexto del proyecto
├── BITACORA.md   # Bitácora de avance (registro de todo lo que hacemos)
├── requirements.txt
└── README.md
```

## Entorno

Entorno de desarrollo: **Anaconda, Python 3.12**, llamado `cimenta_env`.

```bash
conda create -n cimenta_env python=3.12 -y
conda activate cimenta_env
pip install -r requirements.txt
```

## Flujo de trabajo (ramas)

- `feature/dev` — desarrollo activo.
- `dev` — integración y pruebas.
- `master` — versión estable.

Se desarrolla en `feature/dev`, se prueba en `dev` y, si todo va bien, se promueve a `master`.

## Estado

Proyecto en fase inicial. El desarrollo avanza **etapa por etapa** según el catálogo de
etapas, registrando cada paso en [`BITACORA.md`](BITACORA.md).

---
*Ciudad de México · 2026 · Documento de trabajo.*

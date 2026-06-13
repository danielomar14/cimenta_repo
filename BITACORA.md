# Bitácora de CIMENTA

Registro cronológico de todo lo que hacemos en el proyecto. La entrada más reciente va arriba.

Convención de cada entrada:
- **Fecha** · responsable
- **Qué se hizo**
- **Decisiones / notas**
- **Pendientes / siguiente paso**

---

## 2026-06-12 — Arranque del proyecto (Etapa 0: setup)

**Qué se hizo**
- Se definió la estructura base del repositorio: `app/`, `data/`, `notebooks/`, `test/`,
  `src/`, `docs/`, más `README.md`, `requirements.txt`, `.gitignore` y esta bitácora.
- Carpetas vacías versionadas con `.gitkeep`.
- Se creó el entorno de Anaconda **`cimenta_env`** con **Python 3.12** (vacío, sin
  dependencias todavía).
- Se preparó el repositorio Git con tres ramas: `master`, `dev`, `feature/dev`.
- Remoto configurado a `https://github.com/danielomar14/cimenta_repo.git`.

**Decisiones / notas**
- Flujo de trabajo: desarrollo en `feature/dev` → pruebas en `dev` → estable en `master`.
- El entorno se mantiene vacío a propósito; las dependencias se agregarán etapa por etapa.
- Datos (`data/`) excluidos del control de versiones vía `.gitignore`.

**Bloqueo detectado — Catálogo de Etapas ilegible**
- El archivo `docs/context/CIMENTA-Catalogo-de-Etapas.pdf` (236 págs.) está **corrupto**:
  todos los bytes ≥ 0x80 de sus streams comprimidos fueron sustituidos por el carácter de
  reemplazo Unicode U+FFFD (`ef bf bd`) — 823,069 ocurrencias. Es resultado de un
  round-trip UTF-8 que destruyó la data binaria. **No es recuperable**: ni texto ni render.
- Los demás PDFs de `docs/context/` están sanos: Plan-Maestro-v2 (97p), Plan-Maestro (43p),
  Bitácora-del-Jurado (120p), Propuesta-Inicial (12p), Riesgos-y-Debilidades (17p).
- **Acción requerida:** reexportar / volver a subir una copia limpia del Catálogo de Etapas
  para poder avanzar etapa por etapa.

**Pendientes / siguiente paso**
- [ ] Obtener copia limpia de `CIMENTA-Catalogo-de-Etapas.pdf`.
- [ ] Leer el catálogo y definir la Etapa 1.

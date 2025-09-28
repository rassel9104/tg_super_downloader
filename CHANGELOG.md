
# Changelog

Todas las fechas en **America/New_York**.

## [0.2.0] – 2025-09-27
### Added
- **Logging estructurado** (JSON) con rotación a `./logs/`.
- **Decorador retry/backoff** reutilizable (`tgdl/utils/retry.py`) para I/O/red.
- **Headers en aria2**: `add_uri(url, headers=...)` mapea a RPC `header: ["K: V"]`.
- **MediaFire directo**: resolver con **SSRF guard** y retorno `(url, headers)`.

### Changed
- **Cancelación hardening**: eliminación de parciales y sidecars (`.aria2`, `*.part`, `*.ytdl`) en rutas reportadas por `tellStatus`.
- **yt-dlp wrapper**: mejoras de progreso, límites de playlist, y subcarpetas por playlist/canal.
- **Mensajería UX**: notificaciones de progreso batched y playlist info.

### Fixed
- Import orders (Ruff E402) y formato (Black/Ruff) en varios módulos y tests.
- Problemas intermitentes de permisos en tests Windows (tmp local por script).

### Dev
- Tests unitarios para retry, SSRF y mapeo de headers de aria2.
- Script `scripts/run-tests.ps1` para aislar temporales por ejecución.

---

## [0.1.x] – anterior
- Setup base del bot, cola en SQLite, descargas con aria2/yt-dlp, y panel local inicial.


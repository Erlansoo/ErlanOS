# L3 — Cognitive Runtime

`controller.e` implementa el primer controlador de acciones: `ai_query`,
`ai_execute` y `ai_present` sobre la ABI versionada de `services/system`.
Se enlaza con el proceso `model` en M4; la autoridad para cambiar estado y presentar
el panel vive en el broker L2 aislado.

`/ai` pide al modelo una selección acotada; el controlador valida y transforma su
enum en un mensaje. La respuesta del sistema, no una afirmación del modelo, se
presenta al usuario con su recibo. El chat ordinario nunca ejecuta acciones.

AI Mind persistente, model router, memoria semántica y cognitive scheduler quedan
pendientes. Alcance y pruebas del prototipo: `../docs/M4.md`.

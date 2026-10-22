# Psicología Carlos Santibáñez — rama de desarrollo

Esta carpeta contiene el trabajo de la futura plataforma de psicología. **No corresponde todavía al sitio publicado.**

## Reglas de agenda acordadas
- Modalidad: atención psicológica exclusivamente **online**.
- Valor por sesión particular: **$30.000 CLP**.
- El paciente elige fecha y hora entre cupos disponibles.
- Máximo **3 atenciones confirmadas por día**.
- Al elegir un cupo, se retiene temporalmente (duración provisional a definir).
- Si el pago no se confirma dentro de ese plazo, el cupo se libera automáticamente.
- La reserva se confirma solo tras validación del pago.
- Integraciones previstas (pendientes de implementar y validar): medios de pago, bonos Fonasa y boletas SII.
- Los estados de agenda deben sincronizarse en el servidor para impedir reservas duplicadas.

## Estado técnico
Rama de trabajo creada a partir de `main` del repositorio AVOJA, sin afectar producción. No hay aquí todavía un calendario real ni un sistema de pagos.
Para aislar el proyecto de psicología del de Valhalla a largo plazo, se recomienda migrarlo a un **repositorio propio**, con despliegue y variables de entorno independientes.

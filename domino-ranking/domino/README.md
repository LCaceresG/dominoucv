# Dominó Ranking — ELO, ID de jugador, ligas y torneos

Web + API para llevar el ranking de dominó de una comunidad universitaria: jugadores con ID, ELO, ligas y torneos con cinco modalidades.

## Qué incluye

- **Jugadores** con ID público (`DOM-000001`), categoría (estudiante, egresado, profesor, externo o *sin definir* cuando falta el dato), universidad, carrera/escuela/club, ciudad y país. **Siempre se pueden editar** (nombre, categoría, universidad, carrera…) y eliminar.
- **Perfil** con ELO, puesto general, partidas ganadas, empatadas y perdidas, puntos a favor y en contra, y las ligas y torneos donde jugó (con sede y puesto final).
- **Partidas con cualquier marcador**, incluidos empates, y **fecha de hoy por defecto** (modificable). Todos los resultados se pueden corregir.
- **Autocompletado**: al escribir un nombre o apellido (con o sin tildes) aparecen sugerencias.
- **Ligas y torneos** con sede y fecha, y **Top 3** de cada uno. Modalidades:
  1. **Libre**: tú registras las partidas; tabla por puntos.
  2. **Sistema suizo**: rondas por puntaje sin repetir rival.
  3. **Eliminación directa**, con partido por el tercer puesto.
  4. **Fase de grupos + eliminación directa**.
  5. **Doble eliminación**: llave de ganadores, de perdedores y gran final.
  6. **Suizo + fase final**: rondas suizas y luego semifinales, final y tercer puesto (4 mejores; también 8).
  7. **Suizo + final 1.º vs 2.º**: rondas suizas (desempate por average) y una final entre los dos primeros. Con la casilla *"La final se juega solo si hay empate por el 1.º lugar"*, la final únicamente se juega cuando los dos primeros empatan en puntos; si no hay empate, el 1.º es campeón directo y la final aparece como omitida.
- **ELO** para parejas (2 vs 2) y 1 vs 1, con jugadores nuevos "provisionales". **En los torneos funciona como en FIDE**: durante el torneo todas las partidas se calculan con el ELO que cada jugador tenía al empezar, el cambio se va acumulando (se ve en la columna *ELO ±* de la tabla y como "ELO pendiente" en el ranking y el perfil) y se suma al rating completo recién cuando el torneo termina. Las partidas sueltas y de ligas libres siguen actualizando el ELO al instante.
- **Novedades** (pestaña pública): últimas partidas con ganador y perdedor, torneos en juego o terminados con su podio, y jugadores nuevos.
- **Redes** (pestaña pública): enlace a Instagram **@dominoucv** (https://www.instagram.com/dominoucv/) y un muro con las publicaciones de cada jornada, agrupadas por día, con su imagen y resumen. También aparecen en *Novedades*. Para cargar una: pestaña **Redes → Agregar publicación** (fecha, título, resumen, enlace a la publicación de Instagram y foto; la foto se reduce sola). Se pueden editar y eliminar. Si configuraste `ADMIN_KEY`, escribe la clave de administrador arriba antes de guardar. La web no puede leer Instagram automáticamente (Instagram lo impide sin una cuenta autorizada), por eso las publicaciones se cargan a mano.
- **Colores**: negro, blanco, gris y dorado (sin verde ni rojo).
- **Logo** del Dominó Universitario UCV en la cabecera y en la pestaña del navegador.
- **Universidades**: al crear o editar un jugador se ofrecen las 20 más conocidas de Venezuela (UCV, USB, ULA, LUZ, UC, UCAB, UNIMET, USM, UDO, UNEFA, UPEL, UCLA, UNESR, UBV, UNA, UNEXPO, UJAP, UNET, URBE, UAH) y se puede escribir cualquier otra.

## Paso 1 — Instalar o actualizar en tu computador

Requiere Python 3.11 o superior (en Windows, al instalarlo marca "Add python.exe to PATH").

**Si ya tenías la versión anterior:** cierra el servidor (Ctrl + C) y reemplaza la carpeta `domino` por la nueva, **conservando tu archivo `backend\domino.db`**. Al arrancar, el programa agrega solo las columnas nuevas (carrera, pareja retirada, etc.) sin borrar datos; verás un aviso `[base de datos] columna agregada` en la terminal. Solo si venías de una versión muy antigua (anterior a las categorías) tendrás que borrar `domino.db`.

Abre una terminal (`cmd`) dentro de la carpeta `backend` y ejecuta:

```bash
python -m venv .venv
.venv\Scripts\activate             # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m pytest                   # opcional: corre las pruebas
uvicorn app.main:app --reload
```

Abre http://localhost:8000. Documentación de la API: http://localhost:8000/docs.

Para proteger las escrituras, antes de arrancar define `ADMIN_KEY` (`set ADMIN_KEY=clave` en Windows) y escríbela en el campo "Clave de administrador" de la web.

## Cargar el 1er Torneo de Dominó UCV (ya jugado)

Incluye 34 jugadores (con nombre, apellido, universidad y carrera/escuela cuando se conocen), 17 parejas (una retirada tras la ronda 2) y las 40 partidas de las 5 rondas. La final 1.º vs 2.º también viene cargada: Maira Navas / Ana Liria Zapata 100 – 30 Juan Subero / Heiber González. La pareja retirada (Isael Díaz / Nayeth Brito) sale al final de la tabla con su 1-1.

1. Cierra el servidor (Ctrl + C) y, en la carpeta `backend` con el entorno activado:
   ```bash
   python importar_torneo_ucv.py
   ```
2. Opciones útiles: `--fecha 2026-11-08` (fecha real del torneo), `--sede "Nombre del lugar"`, `--reemplazar` (borra solo ese torneo y lo carga de nuevo; los jugadores se reutilizan, no se duplican).
3. Es seguro: si el torneo ya existe, no importa nada; los jugadores que ya estén registrados (mismo nombre) se reutilizan sin pisar sus datos; si algo falla no se guarda nada.
4. Arranca el servidor y abre la pestaña **Ligas y torneos**. Los datos están en `backend/datos/torneo_ucv_2026.json` (puedes corregirlos y volver a importar con `--reemplazar`).

Si aún no tienes datos propios, también puedes copiar `backend/datos/domino_con_torneo.db` a `backend/domino.db` (con el servidor cerrado).

## Paso 2 — Cómo se usa

1. **Jugadores**: créalos con categoría y universidad (un *externo* no lleva universidad; *sin definir* permite dejarla vacía hasta completarla). Para editar a alguien, búscalo en **Jugadores** y pulsa *Editar*.
2. **Liga libre**: crea la liga, inscribe jugadores y registra partidas en "Registrar partida".
3. **Torneo con formato**: créalo, inscribe parejas (o jugadores), pulsa **Iniciar**. La siembra es automática, de mayor a menor ELO. Registra cada resultado dentro del torneo; las llaves y rondas se arman solas.
4. **Suizo**: cuando terminen todos los resultados de una ronda, pulsa "Generar ronda".
5. El **Top 3** aparece en cada liga o torneo y se confirma al terminar.

## Reglas que conviene conocer

- **Empates**: permitidos en partidas libres, suizo y grupos (ELO 0.5 para cada lado). En eliminación directa y llaves debe haber ganador: ajusta el marcador.
- **Pase libre** (número impar en suizo, o llaves incompletas): cuenta como victoria y no mueve el ELO.
- **Corregir un resultado** recalcula todo el ELO y las estadísticas. En torneos no se puede corregir una partida si ya se jugaron otras que dependen de ella; corrige primero las posteriores.
- **Eliminar un jugador**: si nunca jugó ni fue inscrito, se borra del todo. Si tiene historial, se **archiva** (desaparece del ranking y las búsquedas, pero su historial se conserva y se puede restaurar).
- **Doble eliminación**: la gran final es una sola partida, sin revancha.
- **Desempates suizo**: puntos, average (puntos a favor − en contra) y puntos a favor. **Grupos**: lo mismo.
- **Pareja retirada**: sale al final de la tabla, sin puesto y con la etiqueta "retirada", mostrando lo que jugó antes de irse; esas partidas cuentan para sus rivales.
- **Final 1.º vs 2.º** (modalidad suizo + final): no cambia el average (no suma puntos a favor ni en contra), no cuenta derrota al perdedor y no mueve el ELO. Solo suma una victoria al campeón (por ejemplo 4-1 en el suizo pasa a 5-1, y el subcampeón sigue en 4-1). Se puede corregir el marcador como cualquier otro.

## Cómo funciona el ELO

- Todos empiezan en **1000**. Rating de pareja = promedio de sus dos jugadores.
- Probabilidad esperada: `1 / (1 + 10^((Rb - Ra) / 400))`.
- Cambio = `K × multiplicador × (resultado − esperado)`. K: 40 en las primeras 20 partidas, 28 hasta 60, y 20 después. El multiplicador por marcador va de 1.0 a 1.5.
- **Torneos (estilo FIDE)**: el ELO de cada rival se toma al inicio del torneo y el cambio de cada partida se acumula como pendiente; se suma al rating cuando el torneo termina. En torneos el marcador no cuenta (no hay multiplicador por margen): ganar, empatar o perder, nada más; así, mismo récord = mismo cambio de ELO si los rivales tenían el mismo ELO. El multiplicador por margen (1.0–1.5) solo se usa en partidas sueltas y ligas libres. La final 1.º vs 2.º del formato "suizo + final" no mueve el ELO.
- Se ajusta en `backend/app/elo.py`. Después, `POST /api/admin/recalculate` recalcula todo el historial.

## Estructura

```
backend/
  app/   importer.py (carga de torneos ya jugados) · feed.py (novedades) · universities.py (lista de universidades) · main.py (rutas) · services.py (jugadores, ELO) · competitions.py (ligas y torneos)
         tournament.py (motor de torneos, sin base de datos) · elo.py · models.py · schemas.py · database.py
  datos/ torneo_ucv_2026.json · domino_con_torneo.db
  importar_torneo_ucv.py
  tests/ test_elo.py · test_tournament.py (simulaciones) · test_api.py (flujos completos) · test_importer.py
frontend/ index.html (web sin dependencias; las fuentes vienen de Google Fonts) · logo.png · favicon.png
Dockerfile
```

## Publicar en internet (resumen)

Hosting (Render, Railway o Fly.io) con el `Dockerfile`, base de datos **PostgreSQL** (`DATABASE_URL`), `ADMIN_KEY` y `CORS_ORIGINS`. No uses SQLite en producción. Antes de tener datos reales conviene agregar **Alembic** (migraciones) y copias de seguridad.

## Hoja de ruta

- Cuentas de usuario y roles (administrador, organizador, jugador) en lugar de la clave única.
- Confirmación de resultados por un jugador de cada pareja.
- PWA (instalable en el celular) y, más adelante, app nativa.
- Carnet con código QR por jugador.
- Temporadas, gráfica de evolución del ELO, mejores duplas, deshacer ronda suiza.
- Aviso de privacidad: guardas nombres, universidad y ciudad de personas.

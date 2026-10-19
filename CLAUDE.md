# Grapa

Fanzine web estático para leer reseñas, entrevistas y artículos de los artistas de Encarta, con enlaces a
sus fichas. Es un repo aparte de Encarta, con su propio GitHub Pages. Interfaz y textos en español.

```bash
./main.sh                  # construye site/ (texto completo) y sirve en http://127.0.0.1:8790/
./main.sh build            # solo construir site/
./main.sh gh               # versión pública en gh/ (extractos)
./publish_gh_pages.sh git@github.com:volteret4/grapa.git   # sube gh/ a la rama gh-pages
./main.sh fetch            # buscar artículos nuevos (horas; reanudable)
```

`build.py`: `python3 -I`, solo biblioteca estándar. `fetch.py` necesita requests, lxml y readability-lxml:
usa el venv de `~/Scripts/python_venv` (`VENV_PY`). `site/`, `gh/`, `articles.db` y `fetch.log` no van al repo.

## Dos versiones

| | `site/` (local) | `gh/` (GitHub Pages, `--publico`) |
|---|---|---|
| Texto | completo, páginas `articulo/<id>.html` | solo extractos; las tarjetas enlazan al original |
| Enlaces a Encarta | `http://127.0.0.1:8765/` (todas las fichas) | `https://volteret4.github.io/encarta/`, solo fichas con weight ≥ 10 (las que exporta `build_static.py`) |

No hay versión en pepecono. La URL de Encarta se cambia con `ENCARTA_URL` y el umbral con `--encarta-min`.
Las reseñas son de sus medios: el texto completo es para uso en casa, la versión publicada solo lleva extractos.

**Conexión con Encarta**: el build escribe `data/artistas.json` (`artists`: id de Encarta → nº por tipo;
`albums`: id de disco → [artista, nº]). `encarta/web/app.js` lo pide a `GRAPA_URL`
(`https://volteret4.github.io/grapa/`) en todas sus versiones y añade «En la prensa» a las fichas de artista y
disco y el enlace de «Artículos» en la portada. Si cambia la URL de publicación, cambiar `GRAPA_URL` allí.
Los discos llevan ancla `#d<id>` en la página del artista.

## Fuentes

| Variable | Por defecto | Qué aporta |
|---|---|---|
| `MUSIC_FUZZY_DB` | `../music-fuzzy/db/sqlite/musica_local.sqlite` | `feeds` (reseñas, `entity_type='album'`) → `albums` → `artists` |
| `ARTICLES_DB` | `articles.db` | lo que encuentra `fetch.py`, ya con ids de Encarta |
| `ENCARTA_DB` | `../encarta/encarta.db` | `entity`: artistas y discos, con portada, foto, etiquetas y notas AOTY/Metacritic |

El cruce con music-fuzzy usa las mismas claves que Encarta (`artist_norm`, `artista|título`), copiadas en
`build.py`: si cambian allí, hay que cambiarlas aquí. Lo de artistas que no están en Encarta no sale.
Los resúmenes de puntuación de AOTY y Metacritic no son artículos: sus notas ya vienen en la ficha del disco.
Si una URL está en las dos bases, gana music-fuzzy.

## fetch.py: artículos para los artistas que music-fuzzy no tiene

Por defecto, artistas de Encarta con weight ≥ 25 sin reseñas en music-fuzzy, de más a menos peso
(`--min-weight`, `--limit`, `--artist "Nombre"`, `--todos` para incluir los que ya tienen, `--solo adm|wp`,
`--paralelo 4`). La tabla `searched` apunta qué artista se buscó en qué fuente: relanzar continúa donde iba;
si una fuente no responde, ese artista se reintenta la vez siguiente (`--rehacer` lo busca todo de nuevo).

- **adm**: AnyDecentMusic (la web que agrega reseñas de otras; de ahí sale la mayoría de lo de music-fuzzy).
  Busca el artista, entra en cada disco y descarga cada «Read Review» con readability. Guarda nota (0-100) y la
  frase destacada aunque no se pueda extraer el texto. Cubre sobre todo discos de 2008 en adelante.
- **wp**: el buscador público de WordPress (`/wp-json/wp/v2/posts?search=`) de las revistas de `WP_SITES`
  (cinco españolas). Trae el texto completo; de ahí salen las entrevistas.

Pertinencia: el título tiene que nombrar al artista (palabras enteras, sin «the»); los nombres de una palabra
corta («Low», «Swans») además necesitan que se nombre un disco suyo. Se descartan los recopilatorios
(«Efemérides…», títulos con muchos nombres, `ROUNDUP`), las noticias de menos de 1.500 caracteres y el texto
que no nombra al artista (dominios caducados que ahora sirven spam). Tipo (`kind`: reseña, entrevista,
artículo, noticia) por la URL y el título (`KIND_RULES`). `HOST_INTERVAL` separa las peticiones a cada web.

El venv trae urllib3-future, que se bloquea si varios hilos comparten una `requests.Session`: `Net` usa una
por hilo.

## Limpieza del texto de music-fuzzy

El `content` de `feeds` es texto raspado: párrafos partidos en trozos donde había un enlace o una cursiva,
menús al principio, pies de página al final. `clean_text` recose los trozos (si el anterior no acaba en punto
o el siguiente empieza en minúscula), empieza en el primer párrafo de verdad (≥120 caracteres acabado en
puntuación) y quita lo corto o con pinta de boilerplate (`JUNK`). Con menos de `MIN_CHARS` el artículo sale
como «solo enlace» si tiene nota o título. Los títulos genéricos («NME Review (Score: 80)») pasan a
«Artista: Disco»; los nombres bonitos de cabecera están en `OUTLETS`. Lo de `articles.db` ya viene limpio
(párrafos separados por línea en blanco; `## ` marca un subtítulo).

## Páginas

`index.html` (artículo destacado y «En este número», al azar con la fecha como semilla; índice A–Z filtrable;
cabeceras), `artista/<id Encarta>.html` (entrevistas y artículos, luego discos del más nuevo al más viejo,
noticias al final), `articulo/<id>.html` (solo local; `<id>` es el de `feeds` o `n<id>` el de `articles.db`),
`cabecera/<dominio>.html`, `azar.html` y `data/artistas.json`.
Estilo en `web/style.css` (se copia al build); portadas y fotos se enlazan a su origen, en blanco y negro.

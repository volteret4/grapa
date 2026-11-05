# Grapa

Un fanzine web que reúne reseñas, entrevistas y artículos de los artistas de [Encarta](https://github.com/volteret4/encarta), con enlaces a sus fichas.

## Para qué sirve

Para leer lo que la prensa ha escrito sobre un artista o un disco, organizado en un solo sitio.

[Versión pública de prueba](https://volteret4.github.io/grapa/). Solo lleva extractos que enlazan al medio original: las reseñas son de sus medios.

## Instalación individual

Solo necesita Python 3 para construirse. Se alimenta de tus bases `MUSIC_FUZZY_DB`, `ARTICLES_DB` y `ENCARTA_DB`, que no van en el repo; sus rutas se cambian por entorno.

```bash
git clone https://github.com/volteret4/grapa.git
```

```bash
cd grapa && ./main.sh
```

Construye `site/` y lo sirve en <http://127.0.0.1:8790/>. Buscar artículos nuevos con `./main.sh fetch` necesita además `requests`, `lxml` y `readability-lxml`.

## REPEAT-ONE

Este proyecto se enlaza desde el índice de [REPEAT-ONE](https://volteret4.github.io/repeat-one-web/), pero no se instala con él. Para los módulos que sí se instalan juntos, [mira su instalación](https://github.com/volteret4/repeat-one-web#instalación).

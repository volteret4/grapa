#!/usr/bin/env python3
"""Grapa: fanzine estático con las reseñas y entrevistas de los artistas que están en Encarta.

    python3 -I build.py                       # escribe site/ (texto completo: local y pepecono)
    python3 -I build.py --publico --out gh    # versión pública (GitHub Pages): extractos que enlazan al
                                              # original, sin páginas de lectura, y solo enlaces a las
                                              # fichas que exporta la Encarta estática (--encarta-min, 10)

Fuentes (solo lectura; sobreescribibles por entorno):
  MUSIC_FUZZY_DB  tabla `feeds` (reseñas) → `albums` → `artists`
  ARTICLES_DB     articles.db de fetch.py: lo encontrado para los artistas de Encarta (ya con sus ids)
  ENCARTA_DB      `entity`: los artistas y discos con los que se cruzan, con sus mismas claves
  ENCARTA_URL     web de Encarta a la que enlazan las fichas (#/e/<id>): la local, o con --publico la de
                  GitHub Pages
"""
import datetime
import html
import json
import os
import random
import re
import shutil
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
POLLO = HERE.parent
MUSIC_FUZZY_DB = os.environ.get('MUSIC_FUZZY_DB', str(POLLO / 'music-fuzzy/db/sqlite/musica_local.sqlite'))
ENCARTA_DB = os.environ.get('ENCARTA_DB', str(POLLO / 'encarta/encarta.db'))
ARTICLES_DB = os.environ.get('ARTICLES_DB', str(HERE / 'articles.db'))

def _arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


OUT = Path(_arg('--out', HERE / 'site')).resolve()
PUBLICO = '--publico' in sys.argv
ENCARTA_URL = os.environ.get('ENCARTA_URL', 'https://volteret4.github.io/encarta/' if PUBLICO else 'http://127.0.0.1:8765/')
# La Encarta estática solo exporta las fichas con weight >= 10 (build_static.py --min): las demás no se enlazan.
ENCARTA_MIN = int(_arg('--encarta-min', 10 if PUBLICO else 0))

MIN_CHARS = 600        # menos texto que esto no se lee aquí: solo se enlaza al original
EXCERPT = 420          # extracto en tarjetas y en la versión pública
FEATURED = 9           # artículos en portada

MONTHS = 'enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre'.split()

# Nombres de cabecera para los dominios más habituales; el resto sale como dominio.
OUTLETS = {
    'theguardian.com': 'The Guardian', 'pitchfork.com': 'Pitchfork', 'clashmusic.com': 'Clash',
    'thelineofbestfit.com': 'The Line of Best Fit', 'spectrumculture.com': 'Spectrum Culture', 'nme.com': 'NME',
    'popmatters.com': 'PopMatters', 'exclaim.ca': 'Exclaim!', 'undertheradarmag.com': 'Under the Radar',
    'theskinny.co.uk': 'The Skinny', 'thequietus.com': 'The Quietus', 'diymag.com': 'DIY',
    'drownedinsound.com': 'Drowned in Sound', 'theartsdesk.com': 'The Arts Desk', 'slantmagazine.com': 'Slant',
    'rollingstone.com': 'Rolling Stone', 'independent.co.uk': 'The Independent', 'consequence.net': 'Consequence',
    'pastemagazine.com': 'Paste', 'loudandquiet.com': 'Loud and Quiet', 'beatsperminute.com': 'Beats Per Minute',
    'noripcord.com': 'No Ripcord', 'no-ripcord.github.io': 'No Ripcord', 'irishtimes.com': 'The Irish Times',
    'godisinthetvzine.co.uk': 'God Is in the TV', 'ft.com': 'Financial Times', 'themusic.com.au': 'The Music',
    'crackmagazine.net': 'Crack', 'standard.co.uk': 'Evening Standard', 'spinmagazine.com': 'Spin', 'spin.com': 'Spin',
    'tinymixtapes.com': 'Tiny Mix Tapes', 'ra.co': 'Resident Advisor', 'bbc.co.uk': 'BBC', 'faroutmagazine.co.uk': 'Far Out',
    'xsnoize.com': 'XS Noize', 'readdork.com': 'Dork', 'allmusic.com': 'AllMusic', 'gigwise.com': 'Gigwise',
    'albumoftheyear.org': 'Album of the Year', 'metacritic.com': 'Metacritic', 'mojo4music.com': 'Mojo',
    'uncut.co.uk': 'Uncut', 'musicomh.com': 'musicOMH', 'stereogum.com': 'Stereogum', 'thewire.co.uk': 'The Wire',
    'kerrang.com': 'Kerrang!', 'louder.com': 'Louder', 'loudersound.com': 'Louder', 'telegraph.co.uk': 'The Telegraph',
    'avclub.com': 'The A.V. Club', 'thetimes.co.uk': 'The Times', 'mixmag.net': 'Mixmag',
    'recordcollectormag.com': 'Record Collector', 'latimes.com': 'Los Angeles Times', 'nytimes.com': 'The New York Times',
    'thefourohfive.com': 'The 405', 'sputnikmusic.com': 'Sputnikmusic', 'treblezine.com': 'Treble',
    'musicomh.com': 'musicOMH', 'hotpress.com': 'Hot Press', 'thestatesman.com': 'The Statesman',
}

# Títulos que no dicen nada: se sustituyen por «Artista — Disco».
GENERIC_TITLE = re.compile(r'(review \((score|via)|^full review|^client challenge$|^subscribe to read$|^review on |^just a moment|^access denied)', re.I)
JUNK = re.compile(r'(enable javascript|cookie|newsletter|sign up|subscribe|all rights reserved|©|advertisement|'
                  r'click to expand|privacy policy|terms of (use|service)|share this|follow us)', re.I)
SUMMARY_ORIGINS = {'review_aoty', 'review_metacritic'}

# Lo que no va con un disco se agrupa por tipo, en este orden, antes (o después) de la discografía.
KIND_GROUPS = {'entrevista': ('Entrevistas', 0), 'artículo': ('Artículos', 1), 'reseña': ('Otras reseñas', 2),
               'noticia': ('Noticias', 99)}
KIND_LABEL = {'entrevista': 'Entrevista', 'artículo': 'Artículo', 'noticia': 'Noticia'}


# ---------- Claves de Encarta (copiadas de encarta/build.py para cruzar igual) ----------

def norm(s):
    if not s:
        return ''
    s = unicodedata.normalize('NFKD', str(s))
    s = ''.join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace('&', ' and ').replace('’', "'")
    s = re.sub(r"[^\w\s]", ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def artist_norm(s):
    s = norm(s)
    return s[4:] if s.startswith('the ') else s


title_norm = artist_norm


def slugify(s):
    return re.sub(r'\s+', '-', norm(s)) or 'x'


# ---------- Limpieza del texto raspado ----------

TERMINAL = tuple('.!?…:;"”’»)]')


def clean_text(raw):
    """Recose los párrafos que el raspado partió en trozos y quita menús y pies de página."""
    raw = (raw or '').replace('\r', '').replace('\xa0', ' ').replace('\t', ' ')
    raw = re.sub(r'^[\w .&()\'!-]{2,40} Review:\s*\n', '', raw)   # «NME Review:» de cabecera
    chunks = re.split(r'\n\s*\n|\n', raw)
    paras = []
    prev_raw_end_space = False
    for c in chunks:
        text = re.sub(r'\s+', ' ', c).strip()
        if not text:
            continue
        if paras:
            prev = paras[-1]
            if len(prev) == 1 and prev.isupper() and text[0].islower():     # capitular suelta: «F» + «ew bands»
                paras[-1] = prev + text
                continue
            glued = c[:1].isspace() or prev_raw_end_space or text[0].islower() or text[0] in ',.;:)!?’”»'
            if glued and not prev.endswith(TERMINAL[:4]):
                sep = '' if text[0] in ',.;:)!?’”»' else ' '
                paras[-1] = prev + sep + text
                prev_raw_end_space = c[-1:].isspace()
                continue
        paras.append(text)
        prev_raw_end_space = c[-1:].isspace()
    # Lo anterior al primer párrafo de verdad son menús, migas y fichas técnicas.
    start = next((i for i, p in enumerate(paras) if len(p) >= 120 and p.endswith(TERMINAL)), None)
    if start is None:
        return []
    out = []
    for p in paras[start:]:
        if JUNK.search(p) and len(p) < 300:
            continue
        if len(p) < 40 and not p.endswith(TERMINAL):
            continue
        out.append(p)
    # Pie de página: se cortan los párrafos cortos del final.
    while out and len(out[-1]) < 80:
        out.pop()
    return out


def excerpt(paras, n=EXCERPT):
    text = ' '.join(paras)
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(' ', 1)[0]
    return cut.rstrip(',;:—-') + '…'


def clean_title(title, outlet_domain):
    t = re.sub(r'\s+', ' ', title or '').strip()
    if not t or GENERIC_TITLE.search(t):
        return None
    # Quitar « | Medio» y « - Medio» del final; « - X» solo si X es el medio, que a veces es el disco.
    t = re.sub(r'(\s+(\||//)\s+[^|/]{2,40})+$', '', t)
    t = re.sub(r'^(album )?review:\s*', '', t, flags=re.I)
    words = set(norm(outlet_name(outlet_domain)).split()) | set(norm(outlet_domain.split('.')[0]).split())
    m = re.search(r'\s+[–—-]\s+([^–—-]{2,40})$', t)
    if m and set(norm(m.group(1)).split()) & (words - {'the', 'music', 'magazine', 'review', 'com'}):
        t = t[:m.start()]
    return t or None


def canon(url):
    u = re.sub(r'^https?://(www\.)?', '', (url or '').lower())
    return u.split('?')[0].split('#')[0].rstrip('/')


def outlet_name(domain):
    d = re.sub(r'^www\.', '', domain or '')
    return OUTLETS.get(d, d)


# ---------- Datos ----------

def load():
    en = sqlite3.connect(f'file:{ENCARTA_DB}?mode=ro', uri=True)
    ents = {}
    for eid, t, key, name, year, info, weight in en.execute(
            "SELECT id, type, key, name, year, info, weight FROM entity WHERE type IN ('artist','album')"):
        ents[(t, key)] = (eid, name, year, info)
        if weight >= ENCARTA_MIN:
            LINKABLE.add(eid)
    mf = sqlite3.connect(f'file:{MUSIC_FUZZY_DB}?mode=ro', uri=True)
    rows = mf.execute("""
        SELECT f.id, f.feed_name, f.post_title, f.post_url, f.content, f.origen,
               ar.name, al.name, al.year
        FROM feeds f JOIN albums al ON al.id = f.entity_id JOIN artists ar ON ar.id = al.artist_id
        WHERE f.entity_type = 'album'""").fetchall()

    by_id = {v[0]: (k, v) for k, v in ents.items()}
    artists, articles = {}, []

    def artist_rec(aid, aname, ainfo):
        if aid not in artists:
            info = json.loads(ainfo or '{}')
            artists[aid] = {'id': aid, 'name': aname, 'img': info.get('img'), 'tags': (info.get('tags') or [])[:5],
                            'origin': info.get('origin') or info.get('country'), 'albums': {}}
        return artists[aid]

    def album_rec(a, b, name=None, year=None):
        """b: fila de Encarta (id, nombre, año, info) o None si el disco no está allí."""
        akey = b[0] if b else f'mf{slugify(name)}'
        if akey not in a['albums']:
            binfo = json.loads(b[3] or '{}') if b else {}
            a['albums'][akey] = {
                'key': akey, 'eid': b[0] if b else None, 'name': b[1] if b else name, 'year': (b[2] if b else None) or year,
                'cover': binfo.get('cover'), 'label': binfo.get('label'), 'aoty': binfo.get('aoty'),
                'metacritic': binfo.get('metacritic'), 'articles': [], 'order': 50}
        return a['albums'][akey]

    def kind_rec(a, kind):
        name, order = KIND_GROUPS.get(kind, KIND_GROUPS['artículo'])
        akey = f'k:{kind}'
        if akey not in a['albums']:
            a['albums'][akey] = {'key': akey, 'eid': None, 'name': name, 'year': None, 'cover': None, 'label': None,
                                 'articles': [], 'order': order, 'group': True}
        return a['albums'][akey]

    for fid, domain, title, url, content, origen, art, alb, alb_year in rows:
        a = ents.get(('artist', artist_norm(art)))
        if not a:
            continue
        a = artist_rec(a[0], a[1], a[3])
        b = ents.get(('album', f'{artist_norm(art)}|{title_norm(alb)}'))
        album = album_rec(a, b, alb, int(alb_year[:4]) if alb_year and alb_year[:4].isdigit() else None)
        score = re.search(r'\(Score: (\d+)\)', title or '')
        if origen in SUMMARY_ORIGINS and domain in ('albumoftheyear.org', 'metacritic.com'):
            continue    # resúmenes de puntuaciones: ya están en la ficha del disco (aoty / metacritic)
        paras = clean_text(content)
        readable = sum(map(len, paras)) >= MIN_CHARS
        if not readable and not score and not clean_title(title, domain):
            continue    # ni texto, ni nota, ni título: no aporta nada
        art_rec = {
            'id': str(fid), 'outlet': outlet_name(domain), 'domain': re.sub(r'^www\.', '', domain or ''), 'url': url,
            'title': clean_title(title, domain), 'score': int(score.group(1)) if score else None, 'kind': 'reseña',
            'summary': None, 'lang': 'en', 'paras': paras if readable else [], 'artist': a['id'], 'album': album['key']}
        album['articles'].append(art_rec)
        articles.append(art_rec)

    # Lo que fetch.py encontró para los artistas de Encarta: ya viene con sus ids y el texto limpio.
    if os.path.exists(ARTICLES_DB):
        ar = sqlite3.connect(f'file:{ARTICLES_DB}?mode=ro', uri=True)
        seen = {canon(x['url']) for x in articles}
        for (nid, url, domain, outlet, lang, kind, title, score, summary, text, aid, bid) in ar.execute(
                'SELECT id, url, domain, outlet, lang, kind, title, score, summary, text, artist, album FROM article'):
            if canon(url) in seen or aid not in by_id:
                continue
            seen.add(canon(url))
            (_, _), (_, aname, _, ainfo) = by_id[aid]
            a = artist_rec(aid, aname, ainfo)
            if bid in by_id:
                album = album_rec(a, by_id[bid][1])
            else:
                album = kind_rec(a, kind)
            paras = [p for p in (text or '').split('\n\n') if p.strip()]
            if not paras and not summary and score is None:
                continue
            rec = {'id': f'n{nid}', 'outlet': outlet, 'domain': domain, 'url': url,
                   'title': clean_title(title, domain) or title, 'score': score, 'kind': kind, 'summary': summary,
                   'lang': lang, 'paras': paras, 'artist': aid, 'album': album['key']}
            album['articles'].append(rec)
            articles.append(rec)

    for a in artists.values():
        a['albums'] = {k: v for k, v in a['albums'].items() if v['articles']}
    artists = {k: v for k, v in artists.items() if v['albums']}
    articles = [x for x in articles if x['artist'] in artists]
    return artists, articles


# ---------- HTML ----------

def e(s):
    return html.escape(str(s or ''), quote=True)


def encarta(eid):
    return f'{ENCARTA_URL}#/e/{eid}'


def has_encarta(eid):
    return eid is not None and eid in LINKABLE


def href_of(x, up):
    """Página de lectura, o el original si no hay texto o es la versión pública."""
    return x['url'] if PUBLICO or not x['paras'] else f'{up}articulo/{x["id"]}.html'


def ext_attrs(x):
    return ' class="ext" target="_blank" rel="noopener"' if PUBLICO or not x['paras'] else ''


def page(title, body, depth=0, cls=''):
    up = '../' * depth
    return f'''<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Special+Elite&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{up}style.css"></head>
<body class="{cls}">
<header class="strip"><a class="mini-mast" href="{up}index.html">GRAPA</a>
<nav><a href="{up}index.html#artistas">Artistas</a><a href="{up}index.html#cabeceras">Cabeceras</a><a href="{up}azar.html">Al azar</a>
<a class="ext" href="{e(ENCARTA_URL)}">Encarta</a></nav></header>
{body}
<footer class="colophon">Grapa · recortes de prensa musical de music-fuzzy y de la búsqueda propia, cruzados con Encarta · {e(BUILT)}</footer>
</body></html>
'''


def score_badges(album):
    out = []
    if album.get('aoty'):
        out.append(f'<span class="stamp" title="Album of the Year (crítica)">AOTY {album["aoty"]}</span>')
    if album.get('metacritic'):
        out.append(f'<span class="stamp" title="Metacritic">MC {album["metacritic"]}</span>')
    return ''.join(out)


def cover(album, cls='cover'):
    """Portada sobre un hueco con la inicial, que queda a la vista si la imagen falta o ya no existe."""
    img = (f'<img src="{e(album["cover"])}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">'
           if album.get('cover') else '')
    return f'<div class="{cls} frame"><span>{e(album["name"][:1])}</span>{img}</div>'


def card(x, artists, depth):
    up = '../' * depth
    a = artists[x['artist']]
    b = a['albums'][x['album']]
    href, ext = href_of(x, up), ext_attrs(x)
    title = x['title'] or f'{a["name"]}: {b["name"]}'
    score = f'<span class="score">{x["score"]}</span>' if x['score'] is not None else ''
    dek = excerpt(x['paras'], 220) if x['paras'] else x['summary']
    return f'''<article class="card">
  <a href="{e(href)}"{ext}>{cover(b if not b.get('group') else {'name': a['name'], 'cover': a['img']}, 'thumb')}</a>
  <div><p class="kicker">{kicker(x)} {score}</p>
  <h3><a href="{e(href)}"{ext}>{e(title)}</a></h3>
  <p class="byline"><a href="{up}artista/{a["id"]}.html">{e(a["name"])}</a>{album_byline(b)}</p>
  {f'<p class="dek">{e(dek)}</p>' if dek else '<p class="dek muted">Solo nota y enlace al original.</p>'}</div>
</article>'''


def kicker(x):
    kind = KIND_LABEL.get(x['kind'])
    return ' · '.join(filter(None, [e(kind) if kind else '', e(x['outlet']), 'ES' if x['lang'] == 'es' else '']))


def album_byline(b):
    if b.get('group'):
        return ''
    return f' · <i>{e(b["name"])}</i>' + (f' ({b["year"]})' if b['year'] else '')


def build_index(artists, articles):
    readable = [x for x in articles if x['paras']]
    rnd = random.Random(BUILT)
    featured = rnd.sample(readable, min(FEATURED, len(readable)))
    lead, rest = featured[0], featured[1:]
    la = artists[lead['artist']]
    lb = la['albums'][lead['album']]
    if lb.get('group'):
        lb = {'name': la['name'], 'cover': la['img']}

    by_letter = defaultdict(list)
    for a in sorted(artists.values(), key=lambda a: artist_norm(a['name'])):
        k = artist_norm(a['name'])[:1].upper()
        by_letter[k if k.isalpha() else '#'].append(a)
    letters = ''.join(
        f'<section class="letter"><h3>{L}</h3><ul>' + ''.join(
            f'<li data-n="{e(norm(a["name"]))}"><a href="artista/{a["id"]}.html">{e(a["name"])}</a>'
            f' <small>{sum(len(b["articles"]) for b in a["albums"].values())}</small></li>' for a in lst)
        + '</ul></section>' for L, lst in sorted(by_letter.items(), key=lambda kv: (kv[0] == '#', kv[0])))

    outlets = defaultdict(int)
    for x in articles:
        outlets[(x['outlet'], x['domain'])] += 1
    outlet_list = ''.join(f'<li><a href="cabecera/{slugify(d)}.html">{e(n)}</a> <small>{c}</small></li>'
                          for (n, d), c in sorted(outlets.items(), key=lambda kv: -kv[1]))

    now = datetime.date.today()
    body = f'''<main class="home">
<div class="masthead">
  <h1 class="ransom" aria-label="Grapa"><span>G</span><span>R</span><span>A</span><span>P</span><span>A</span></h1>
  <p class="tagline">fanzine de recortes · reseñas y entrevistas de los artistas de Encarta</p>
  <p class="issue">Nº {now.strftime("%y%m")} · {MONTHS[now.month - 1]} de {now.year} · {len(readable):,} artículos para leer · {len(artists):,} artistas</p>
</div>

<section class="lead">
  <a href="{e(href_of(lead, ''))}"{ext_attrs(lead)}>{cover(lb, "lead-cover")}</a>
  <div><p class="kicker">Portada · {kicker(lead)}</p>
  <h2><a href="{e(href_of(lead, ''))}"{ext_attrs(lead)}>{e(lead["title"] or f'{la["name"]}: {lb["name"]}')}</a></h2>
  <p class="byline"><a href="artista/{la["id"]}.html">{e(la["name"])}</a>{album_byline(la['albums'][lead['album']])}</p>
  <p class="dek">{e(excerpt(lead["paras"], 520))}</p>
  <p><a class="more" href="{e(href_of(lead, ''))}"{ext_attrs(lead)}>{"Leer en " + e(lead["outlet"]) if PUBLICO else "Seguir leyendo"} →</a></p></div>
</section>

<h2 class="section-tape">En este número</h2>
<div class="cards">{"".join(card(x, artists, 0) for x in rest)}</div>

<h2 class="section-tape" id="artistas">Artistas</h2>
<input class="filter" type="search" placeholder="Filtrar artistas…" autocomplete="off">
<div class="az">{letters}</div>

<h2 class="section-tape" id="cabeceras">Cabeceras</h2>
<ul class="outlets">{outlet_list}</ul>
</main>
<script>
const f = document.querySelector('.filter');
const norm = (s) => s.normalize('NFKD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase();
f.addEventListener('input', () => {{
  const q = norm(f.value.trim());
  for (const li of document.querySelectorAll('.az li')) li.hidden = q && !li.dataset.n.includes(q);
  for (const s of document.querySelectorAll('.az .letter')) s.hidden = !s.querySelector('li:not([hidden])');
}});
</script>'''
    (OUT / 'index.html').write_text(page('Grapa — fanzine', body, 0, 'paper'))

    # Al azar: un artículo; en la versión pública, que no tiene páginas de lectura, un artista.
    targets = [f'artista/{a}.html' for a in artists] if PUBLICO else [f'articulo/{x["id"]}.html' for x in readable]
    (OUT / 'azar.html').write_text(f'''<!doctype html><meta charset="utf-8"><title>Grapa — al azar</title>
<script>const t = {json.dumps(targets)}; location.replace(t[Math.floor(Math.random() * t.length)]);</script>''')


def ordered_albums(a):
    """Entrevistas y artículos primero, luego los discos del más nuevo al más viejo y al final las noticias."""
    return sorted(a['albums'].values(), key=lambda b: (b['order'], -(b['year'] or 0)))


def build_artist(a):
    albums = ordered_albums(a)
    n = sum(len(b['articles']) for b in albums)
    blocks = []
    for b in albums:
        items = []
        for x in sorted(b['articles'], key=lambda x: (not x['paras'], x['outlet'])):
            score = f' <span class="score">{x["score"]}</span>' if x['score'] is not None else ''
            label = kicker(x)
            if x['paras'] and not PUBLICO:
                items.append(f'<li><a href="../articulo/{x["id"]}.html"><b>{label}</b>{score}'
                             f'<span class="t">{e(x["title"] or "Reseña")}</span></a>'
                             f'<p class="dek">{e(excerpt(x["paras"], 200))}</p></li>')
            else:
                dek = x['summary'] or (excerpt(x['paras'], 300) if x['paras'] else '')
                dek = f'<p class="dek">{e(dek)}</p>' if dek else ''
                items.append(f'<li class="link-only"><a class="ext" href="{e(x["url"])}" target="_blank" rel="noopener">'
                             f'<b>{label}</b>{score}<span class="t">{e(x["title"] or "Reseña en el original")}</span></a>{dek}</li>')
        if b.get('group'):
            blocks.append(f'''<section class="album group" id="{e(b["key"])}">
  <div class="group-mark">{e(b["name"][:1])}</div>
  <div><h2>{e(b["name"])}</h2><ul class="clips">{"".join(items)}</ul></div>
</section>''')
            continue
        name = f'<a class="ext" href="{e(encarta(b["eid"]))}">{e(b["name"])}</a>' if has_encarta(b['eid']) else e(b['name'])
        blocks.append(f'''<section class="album" id="d{e(b["key"])}">
  {cover(b)}
  <div><h2>{name}</h2>
  <p class="meta">{b["year"] or "s. f."}{f" · {e(b['label'])}" if b["label"] else ""} {score_badges(b)}</p>
  <ul class="clips">{"".join(items)}</ul></div>
</section>''')
    img = (f'<div class="portrait frame"><span>{e(a["name"][:1])}</span><img src="{e(a["img"])}" alt="" '
           f'referrerpolicy="no-referrer" onerror="this.parentNode.remove()"></div>') if a['img'] else ''
    tags = ''.join(f'<span class="tag">{e(t)}</span>' for t in a['tags'])
    body = f'''<main class="artist">
<div class="artist-head">{img}<div>
  <p class="kicker">Expediente · {n} recorte{"s" if n != 1 else ""}</p>
  <h1>{e(a["name"])}</h1>
  <p class="meta">{e(a["origin"] or "")} {tags}</p>
  {f'<p><a class="ext encarta" href="{e(encarta(a["id"]))}">Ficha en Encarta</a></p>' if has_encarta(a["id"]) else ''}</div></div>
{"".join(blocks)}
</main>'''
    (OUT / 'artista' / f'{a["id"]}.html').write_text(page(f'{a["name"]} — Grapa', body, 1, 'paper'))


def build_article(x, artists, siblings):
    a = artists[x['artist']]
    b = a['albums'][x['album']]
    paras = x['paras']
    if PUBLICO:
        paras = [excerpt(paras)]
    text = ''.join(f'<h3>{e(p[3:])}</h3>' if p.startswith('## ') else f'<p>{e(p)}</p>' for p in paras)
    i = siblings.index(x)
    prev_, next_ = siblings[i - 1] if i else None, siblings[i + 1] if i + 1 < len(siblings) else None

    def step(y, before, after):
        if not y:
            return '<span></span>'
        yb = a['albums'][y['album']]
        about = f' sobre <i>{e(yb["name"])}</i>' if not yb.get('group') else f' · {e(y["title"] or "")[:60]}'
        return f'<a href="{y["id"]}.html">{before}{e(y["outlet"])}{about}{after}</a>'
    nav = step(prev_, '← ', '') + step(next_, '', ' →')
    score = f'<span class="score big">{x["score"]}</span>' if x['score'] is not None else ''
    if b.get('group'):
        art = {'name': a['name'], 'cover': a['img']}
        byline = ''
    else:
        art = b
        album_link = f'<a class="ext" href="{e(encarta(b["eid"]))}">{e(b["name"])}</a>' if b['eid'] else e(b['name'])
        byline = f' · <i>{album_link}</i>{f" ({b['year']})" if b["year"] else ""} {score_badges(b)}'
    body = f'''<main class="read">
<header class="read-head">
  {cover(art, "read-cover")}
  <div><p class="kicker">{kicker(x)} {score}</p>
  <h1>{e(x["title"] or f'{a["name"]}: {b["name"]}')}</h1>
  <p class="byline"><a href="../artista/{a["id"]}.html">{e(a["name"])}</a>{byline}</p></div>
</header>
<div class="text">{text}</div>
<p class="source">{"Extracto. " if PUBLICO else ""}Texto original en <a class="ext" href="{e(x["url"])}" target="_blank" rel="noopener">{e(x["outlet"])}</a>
 · <a class="ext" href="{e(encarta(a["id"]))}">{e(a["name"])} en Encarta</a></p>
<nav class="pager">{nav}</nav>
</main>'''
    (OUT / 'articulo' / f'{x["id"]}.html').write_text(page(f'{x["title"] or a["name"]} ({x["outlet"]}) — Grapa', body, 1, 'paper'))


def build_outlets(artists, articles):
    groups = defaultdict(list)
    for x in articles:
        groups[x['domain']].append(x)
    for d, xs in groups.items():
        xs.sort(key=lambda x: (not x['paras'], artist_norm(artists[x['artist']]['name'])))
        body = f'''<main class="outlet"><p class="kicker">Cabecera · {len(xs)} recortes</p><h1>{e(xs[0]["outlet"])}</h1>
<div class="cards">{"".join(card(x, artists, 1) for x in xs)}</div></main>'''
        (OUT / 'cabecera' / f'{slugify(d)}.html').write_text(page(f'{xs[0]["outlet"]} — Grapa', body, 1, 'paper'))


def build_json(artists, articles):
    """data/artistas.json: lo que Encarta necesita para enlazar aquí desde sus fichas y su portada."""
    out = {'built': BUILT, 'articles': sum(1 for x in articles if x['paras']), 'artists': {}, 'albums': {}}
    for a in artists.values():
        kinds = defaultdict(int)
        for b in a['albums'].values():
            for x in b['articles']:
                kinds[x['kind']] += 1
            if b['eid']:
                out['albums'][b['eid']] = [a['id'], len(b['articles'])]
        out['artists'][a['id']] = dict(kinds)
    (OUT / 'data').mkdir(exist_ok=True)
    (OUT / 'data' / 'artistas.json').write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')))


def main():
    global BUILT
    BUILT = datetime.date.today().isoformat()
    artists, articles = load()
    if OUT.exists():
        shutil.rmtree(OUT)
    for d in ('artista', 'cabecera') if PUBLICO else ('artista', 'articulo', 'cabecera'):
        (OUT / d).mkdir(parents=True)
    shutil.copy(HERE / 'web' / 'style.css', OUT / 'style.css')
    build_index(artists, articles)
    for a in artists.values():
        build_artist(a)
        if PUBLICO:
            continue
        siblings = [x for b in ordered_albums(a) for x in b['articles'] if x['paras']]
        for x in siblings:
            build_article(x, artists, siblings)
    build_outlets(artists, articles)
    build_json(artists, articles)
    readable = sum(1 for x in articles if x['paras'])
    print(f'{len(artists)} artistas · {readable} artículos legibles · {len(articles) - readable} solo enlace → {OUT}')


BUILT = ''
LINKABLE = set()    # ids de Encarta con ficha enlazable (todas, salvo con --encarta-min)

if __name__ == '__main__':
    main()

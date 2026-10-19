#!/usr/bin/env python3
"""Busca reseñas, entrevistas y artículos de los artistas de Encarta y los guarda en articles.db.

    ~/Scripts/python_venv/bin/python -I fetch.py                  # artistas sin artículos en music-fuzzy, weight >= 25
    ~/Scripts/python_venv/bin/python -I fetch.py --min-weight 10 --limit 200
    ~/Scripts/python_venv/bin/python -I fetch.py --artist "Swans" --todos
    ~/Scripts/python_venv/bin/python -I fetch.py --solo adm       # o wp

Fuentes:
  adm  AnyDecentMusic: busca el artista, entra en cada disco y descarga las reseñas enlazadas («Read Review»).
  wp   el buscador de WordPress (/wp-json/wp/v2/posts) de las revistas de WP_SITES: texto completo, entrevistas incluidas.

Es reanudable: `searched` apunta qué artista se buscó en qué fuente; --rehacer lo ignora.
Necesita requests, lxml y readability-lxml (el venv de ~/Scripts).
"""
import argparse
import html
import json
import re
import sqlite3
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import requests
from lxml import html as lhtml
from readability import Document

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build import ENCARTA_DB, MUSIC_FUZZY_DB, OUTLETS, artist_norm, norm, title_norm  # noqa: E402

DB = HERE / 'articles.db'
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
MIN_CHARS = 600
HOST_INTERVAL = 1.5     # segundos entre peticiones a una misma web

# dominio → (cabecera, idioma)
WP_SITES = {
    'jenesaispop.com': ('JENESAISPOP', 'es'), 'muzikalia.com': ('Muzikalia', 'es'),
    'mondosonoro.com': ('Mondo Sonoro', 'es'), 'binaural.es': ('Binaural', 'es'), 'mariskalrock.com': ('Mariskal Rock', 'es'),
    'consequence.net': ('Consequence', 'en'), 'thequietus.com': ('The Quietus', 'en'),
    'beatsperminute.com': ('Beats Per Minute', 'en'), 'spectrumculture.com': ('Spectrum Culture', 'en'),
    'xsnoize.com': ('XS Noize', 'en'), 'godisinthetvzine.co.uk': ('God Is in the TV', 'en'),
    'louderthanwar.com': ('Louder Than War', 'en'), 'crackmagazine.net': ('Crack', 'en'),
    'faroutmagazine.co.uk': ('Far Out', 'en'), 'treblezine.com': ('Treble', 'en'),
    'aquariumdrunkard.com': ('Aquarium Drunkard', 'en'), 'www.brooklynvegan.com': ('BrooklynVegan', 'en'),
    'www.popmatters.com': ('PopMatters', 'en'), 'www.clashmusic.com': ('Clash', 'en'),
    'uncut.co.uk': ('Uncut', 'en'), 'musicomh.com': ('musicOMH', 'en'),
}

KIND_RULES = [
    ('entrevista', r'interview|entrevista|in conversation|q ?& ?a|\bq&a\b|habla(mos)? con|talks? to|speaks? to|meet the'),
    ('reseña', r'review|reseña|critica|crítica|/discos?/|album-of-the-week|disco de la semana|track by track'),
    ('noticia', r'/news/|/noticias?/|/actualidad/|announce|anuncia|shares?\b|comparte|estrena|premieres?|tour dates|gira|new (song|single|video)|nuevo (single|video|vídeo|disco)'),
]

# Títulos de recopilatorio («Efemérides de la semana: AC/DC, Kiss, Ramones…»): el artista es uno de muchos.
ROUNDUP = re.compile(r'efem[eé]rides|bolsa de noticias|programaci[oó]n|nuestra radio|(?:[,–-][^,–-]+){4,}', re.I)
MIN_MENTIONS = 3    # veces que el texto tiene que nombrar al artista: menos es una mención de pasada

SCHEMA = '''
CREATE TABLE IF NOT EXISTS article (
    id        INTEGER PRIMARY KEY,
    url       TEXT UNIQUE NOT NULL,
    domain    TEXT NOT NULL,
    outlet    TEXT NOT NULL,
    lang      TEXT,
    kind      TEXT NOT NULL,             -- reseña | entrevista | artículo | noticia
    title     TEXT,
    published TEXT,
    score     INTEGER,                   -- 0-100 si la fuente la da
    summary   TEXT,                      -- frase destacada (AnyDecentMusic)
    text      TEXT,                      -- párrafos separados por línea en blanco; vacío si no se pudo extraer
    artist    INTEGER NOT NULL,          -- id de Encarta
    album     INTEGER,                   -- id de Encarta, si se reconoce
    source    TEXT NOT NULL,             -- adm | wp
    fetched   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_article_artist ON article(artist);
CREATE TABLE IF NOT EXISTS searched (
    artist INTEGER NOT NULL,
    source TEXT NOT NULL,
    found  INTEGER NOT NULL,
    at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (artist, source)
);
'''


# ---------- Red ----------

class Net:
    """Intervalo mínimo entre peticiones a cada web. Una sesión por hilo: el urllib3-future del venv se
    bloquea (traffic_police) si varios hilos comparten la misma."""

    def __init__(self):
        self.local = threading.local()
        self.last = {}
        self.locks = {}
        self.guard = threading.Lock()

    def get(self, url, timeout=20, **kw):
        host = urlparse(url).netloc
        with self.guard:
            lock = self.locks.setdefault(host, threading.Lock())
        with lock:
            wait = self.last.get(host, 0) + HOST_INTERVAL - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            if not hasattr(self.local, 's'):
                self.local.s = requests.Session()
                self.local.s.headers['User-Agent'] = UA
            try:
                return self.local.s.get(url, timeout=timeout, **kw)
            except requests.RequestException:
                return None
            finally:
                self.last[host] = time.monotonic()


# ---------- Texto ----------

def paragraphs(fragment_html):
    """Párrafos legibles de un fragmento HTML (contenido de WordPress o salida de readability)."""
    if not fragment_html or not fragment_html.strip():
        return []
    try:
        root = lhtml.fromstring(fragment_html)
    except Exception:
        return []
    for bad in root.xpath('//script|//style|//figure|//figcaption|//iframe|//form|//aside|//nav'):
        bad.drop_tree()
    out = []
    for el in root.xpath('//p|//blockquote|//h2|//h3|//li[not(ancestor::p)]'):
        if el.tag == 'blockquote' and el.xpath('.//p'):
            continue    # sus <p> ya salen solos
        t = re.sub(r'\s+', ' ', el.text_content()).strip()
        if len(t) < 2 or (out and out[-1] == t):
            continue
        if el.tag in ('h2', 'h3'):
            t = f'## {t}'
        elif el.tag == 'li':
            if len(t) < 60:
                continue    # listas de enlaces, menús
        out.append(t)
    # Firmas, «Comparte», «Más noticias»…: se cortan los cortos del final.
    while out and (len(out[-1]) < 60 or out[-1].startswith('## ')):
        out.pop()
    return out


def extract(page_html, url):
    try:
        doc = Document(page_html, url=url)
        return doc.short_title(), paragraphs(doc.summary(html_partial=True))
    except Exception:
        return None, []


def kind_of(url, title):
    hay = f'{urlparse(url).path} {title or ""}'.lower()
    for kind, rx in KIND_RULES:
        if re.search(rx, hay):
            return kind
    return 'artículo'


def mentions(text, key):
    return len(re.findall(rf'(?:^| ){re.escape(key)}(?= |$)', artist_norm(text))) if key else 0


def contains(haystack_norm, needle_norm):
    return bool(needle_norm) and re.search(rf'(^| ){re.escape(needle_norm)}( |$)', haystack_norm) is not None


# ---------- Encarta ----------

class Artist:
    def __init__(self, eid, name, key, weight, albums):
        self.id, self.name, self.key, self.weight = eid, name, key, weight
        self.albums = albums                      # [(id, nombre, title_norm)]
        # Nombre de una sola palabra corta («Low», «Air», «Swans»): hace falta otra pista para fiarse.
        self.ambiguous = len(key.split()) == 1 and len(key) <= 6

    def album_in(self, text_norm):
        best = None
        for aid, name, tn in self.albums:
            if len(tn) >= 3 and tn != self.key and contains(text_norm, tn) and (not best or len(tn) > len(best[2])):
                best = (aid, name, tn)
        return best

    def matches(self, title, body=''):
        t = artist_norm(title)
        if not contains(t, self.key):
            return False
        if not self.ambiguous:
            return True
        return bool(self.album_in(norm(title) + ' ' + norm(body[:4000])))


def load_artists(min_weight, only_name, todos):
    en = sqlite3.connect(f'file:{ENCARTA_DB}?mode=ro', uri=True)
    albums = {}
    for aid, bid, bname in en.execute("""
            SELECT r.a, b.id, b.name FROM rel r JOIN entity b ON b.id = r.b
            WHERE r.kind = 'by' AND b.type = 'album'"""):
        albums.setdefault(aid, {})[bid] = (bid, bname, title_norm(bname))
    if only_name:
        rows = en.execute("SELECT id, name, key, weight FROM entity WHERE type='artist' AND key = ?",
                          (artist_norm(only_name),)).fetchall()
    else:
        rows = en.execute("SELECT id, name, key, weight FROM entity WHERE type='artist' AND weight >= ? ORDER BY weight DESC",
                          (min_weight,)).fetchall()
    if not todos and not only_name:
        mf = sqlite3.connect(f'file:{MUSIC_FUZZY_DB}?mode=ro', uri=True)
        covered = {artist_norm(n) for (n,) in mf.execute("""
            SELECT DISTINCT ar.name FROM feeds f JOIN albums al ON al.id = f.entity_id JOIN artists ar ON ar.id = al.artist_id
            WHERE f.entity_type = 'album' AND length(f.content) > 1500""")}
        rows = [r for r in rows if r[2] not in covered]
    return [Artist(i, n, k, w, list(albums.get(i, {}).values())) for i, n, k, w in rows]


def known_urls(db):
    mf = sqlite3.connect(f'file:{MUSIC_FUZZY_DB}?mode=ro', uri=True)
    urls = {canon(u) for (u,) in mf.execute('SELECT post_url FROM feeds')}
    urls |= {canon(u) for (u,) in db.execute('SELECT url FROM article')}
    return urls


def canon(url):
    p = urlparse(url or '')
    return (p.netloc.removeprefix('www.') + p.path.rstrip('/')).lower()


# ---------- AnyDecentMusic ----------

ADM = 'http://www.anydecentmusic.com'


def adm_search(net, artist):
    r = net.get(f'{ADM}/search-results.aspx?search={quote(artist.name)}')
    if not r or r.status_code != 200:
        return None
    root = lhtml.fromstring(r.text)
    found = {}
    for li in root.xpath('//li[contains(@class,"search_result_album")]'):
        who = ' '.join(li.xpath('.//h2//text()')).strip()
        what = ' '.join(li.xpath('.//h3//text()')).strip()
        href = (li.xpath('.//a/@href') or [''])[0]
        if artist_norm(who) == artist.key and '/review/' in href:
            found[urljoin(ADM, href)] = what
    return found


def adm_album(net, url):
    r = net.get(url)
    if not r or r.status_code != 200:
        return []
    root = lhtml.fromstring(r.text)
    out = []
    for li in root.xpath('//ol[@id="album_reviews_list"]/li'):
        link = li.xpath('.//a[contains(text(),"Read Review")]/@href')
        if not link:
            continue
        rating = (li.xpath('.//span[@class="data_rating"]/text()') or [''])[0].strip()
        outlet = ' '.join(li.xpath('.//h4/span//text()')).strip()
        p = li.xpath('.//p')[0] if li.xpath('.//p') else None
        blurb = re.sub(r'\s+', ' ', (p.text or '') if p is not None else '').strip()
        out.append({'url': link[0].strip(), 'outlet': outlet,
                    'score': round(float(rating) * 10) if re.fullmatch(r'\d+(\.\d+)?', rating) else None,
                    'summary': blurb or None})
    return out


def run_adm(net, artist, seen, pool):
    albums = adm_search(net, artist)
    if albums is None:
        return None
    reviews = []
    for url, album_name in albums.items():
        for rv in adm_album(net, url):
            if canon(rv['url']) in seen:
                continue
            seen.add(canon(rv['url']))
            alb = artist.album_in(title_norm(album_name))
            rv['album'] = alb[0] if alb else None
            rv['album_name'] = album_name
            reviews.append(rv)

    def fetch(rv):
        r = net.get(rv['url'])
        title, paras = (None, [])
        if r is not None and r.status_code == 200 and 'html' in r.headers.get('content-type', ''):
            title, paras = extract(r.text, r.url)
        # Dominios caducados que ya sirven otra cosa (spam de casinos…): el texto tiene que nombrar al artista.
        if sum(map(len, paras)) < MIN_CHARS or not contains(artist_norm(' '.join(paras)), artist.key):
            paras = []
        domain = urlparse(rv['url']).netloc.removeprefix('www.')
        return {
            'url': rv['url'], 'domain': domain, 'outlet': rv['outlet'] or OUTLETS.get(domain, domain), 'lang': 'en',
            'kind': 'reseña', 'title': title if title and len(title) < 200 else f'{artist.name}: {rv["album_name"]}',
            'published': None, 'score': rv['score'], 'summary': rv['summary'], 'text': '\n\n'.join(paras),
            'artist': artist.id, 'album': rv['album'], 'source': 'adm'}

    return list(pool.map(fetch, reviews))


# ---------- WordPress ----------

def wp_site(net, domain, artist, seen):
    outlet, lang = WP_SITES[domain]
    base = f'https://{domain}/wp-json/wp/v2/posts'
    r = net.get(f'{base}?search={quote(artist.name)}&per_page=30&_fields=id,link,title,date')
    if not r or r.status_code != 200:
        return None
    try:
        hits = r.json()
    except ValueError:
        return None
    if not isinstance(hits, list):
        return None
    cand = []
    for h in hits:
        title = html.unescape(re.sub('<[^>]+>', '', (h.get('title') or {}).get('rendered', '')))
        link = h.get('link', '')
        # El título lo nombra; «Swans» y similares además necesitan un disco suyo (se mira con el texto).
        if canon(link) in seen or not contains(artist_norm(title), artist.key) or ROUNDUP.search(title):
            continue
        cand.append((h['id'], link, title, h.get('date')))
    if not cand:
        return []
    ids = ','.join(str(c[0]) for c in cand)
    r = net.get(f'{base}?include={ids}&per_page={len(cand)}&_fields=id,content')
    bodies = {}
    if r and r.status_code == 200:
        try:
            bodies = {p['id']: (p.get('content') or {}).get('rendered', '') for p in r.json()}
        except (ValueError, TypeError, KeyError):
            pass
    out = []
    for pid, link, title, date in cand:
        body = bodies.get(pid, '')
        paras = paragraphs(body)
        text = '\n\n'.join(paras)
        if not artist.matches(title, text) or (text and mentions(text, artist.key) < MIN_MENTIONS):
            continue
        kind = kind_of(link, title)
        if kind == 'noticia' and len(text) < 1500:
            continue    # anuncios de gira y estrenos: no son lectura
        alb = artist.album_in(norm(title))
        seen.add(canon(link))
        out.append({
            'url': link, 'domain': domain.removeprefix('www.'), 'outlet': outlet, 'lang': lang, 'kind': kind,
            'title': title, 'published': (date or '')[:10] or None, 'score': None, 'summary': None,
            'text': text if len(text) >= MIN_CHARS else '', 'artist': artist.id, 'album': alb[0] if alb else None,
            'source': 'wp'})
    return out


def run_wp(net, artist, seen, pool):
    results = list(pool.map(lambda d: wp_site(net, d, artist, seen), WP_SITES))
    if all(r is None for r in results):
        return None
    return [a for r in results if r for a in r]


# ---------- Principal ----------

def save(db, rows):
    cols = ['url', 'domain', 'outlet', 'lang', 'kind', 'title', 'published', 'score', 'summary', 'text', 'artist', 'album', 'source']
    db.executemany(f'INSERT OR IGNORE INTO article ({",".join(cols)}) VALUES ({",".join("?" * len(cols))})',
                   [[r[c] for c in cols] for r in rows])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--min-weight', type=int, default=25)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--artist')
    ap.add_argument('--todos', action='store_true', help='incluir artistas que ya tienen reseñas en music-fuzzy')
    ap.add_argument('--solo', choices=['adm', 'wp'])
    ap.add_argument('--rehacer', action='store_true', help='volver a buscar aunque ya se hiciera')
    ap.add_argument('--paralelo', type=int, default=4, help='artistas a la vez (default 4)')
    args = ap.parse_args()

    db = sqlite3.connect(DB)
    db.executescript(SCHEMA)
    sources = [args.solo] if args.solo else ['adm', 'wp']
    runners = {'adm': run_adm, 'wp': run_wp}
    artists = load_artists(args.min_weight, args.artist, args.todos)
    if not args.rehacer:
        done = set(db.execute('SELECT artist, source FROM searched'))
        artists = [a for a in artists if any((a.id, s) not in done for s in sources)]
    else:
        done = set()
    if args.limit:
        artists = artists[:args.limit]
    print(f'{len(artists)} artistas por buscar ({", ".join(sources)})', flush=True)

    net = Net()
    seen = known_urls(db)
    total = 0

    def work(a):
        # Varios artistas a la vez: el límite por web (Net) sigue valiendo, y la base solo la toca el hilo principal.
        return a, {s: runners[s](net, a, seen, pool) for s in sources if (a.id, s) not in done}

    with ThreadPoolExecutor(max_workers=len(WP_SITES) * args.paralelo) as pool, \
            ThreadPoolExecutor(max_workers=args.paralelo) as artists_pool:
        for i, (a, results) in enumerate(artists_pool.map(work, artists), 1):
            got = {}
            for s, rows in results.items():
                if rows is None:
                    got[s] = 'error'
                    continue        # la fuente no respondió: se reintenta la próxima vez
                save(db, rows)
                db.execute('INSERT OR REPLACE INTO searched (artist, source, found) VALUES (?,?,?)', (a.id, s, len(rows)))
                db.commit()
                readable = sum(1 for r in rows if r['text'])
                got[s] = f'{readable}/{len(rows)}'
                total += readable
            print(f'[{i}/{len(artists)}] {a.name} (w{a.weight}): '
                  + ' · '.join(f'{s} {v}' for s, v in got.items()) + f'  — total legibles {total}', flush=True)


if __name__ == '__main__':
    main()

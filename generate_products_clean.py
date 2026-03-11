import csv
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

ODS_PATH = Path('lista_de_precios.ods')
IMAGES_ROOT = Path('productos')
CSV_OUT = Path('products_clean.csv')
REPORT_OUT = Path('products_clean_report.md')

ns = {
    'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
    'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
}


def strip_accents(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))


def slugify(name: str) -> str:
    s = strip_accents(name).lower()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'\s+', '-', s.strip())
    s = re.sub(r'-+', '-', s)
    return s


def normalize_name(name: str) -> str:
    s = name.strip()
    s = re.sub(r'\s+', ' ', s)
    s = s.replace('LT', 'L').replace('Lt', 'L').replace('lt', 'L')
    s = re.sub(r'(\d)\s*(ML|Ml|mL)', r'\1ml', s)
    s = re.sub(r'(\d)\s*(CC|Cc|cc)', r'\1ml', s)
    s = re.sub(r'(\d)\s*L\b', r'\1L', s)
    s = re.sub(r'(\d)\s*(GR|gr|Gr|G)\b', r'\1g', s)
    s = re.sub(r'(\d)\s*(KG|Kg|kg)\b', r'\1kg', s)
    words = []
    for w in s.split(' '):
        if re.fullmatch(r'[0-9]+([.,][0-9]+)?(ml|l|g|kg)', w.lower()):
            unit = w.lower().replace(',', '.')
            unit = unit.replace('.0', '')
            words.append(unit)
        elif re.fullmatch(r'[A-Z0-9]{2,}', w):
            words.append(w.title())
        elif w.isupper():
            words.append(w.title())
        else:
            words.append(w[0].upper() + w[1:] if w else w)
    out = ' '.join(words)
    fixes = {
        'Branca': 'Branca',
        'Brahma': 'Brahma',
        'Smirnoff': 'Smirnoff',
        'Gancia': 'Gancia',
        'Johnnie': 'Johnnie',
        'Jagermeister': 'Jägermeister',
    }
    for k, v in fixes.items():
        out = re.sub(rf'\b{k}\b', v, out, flags=re.I)
    out = re.sub(r'\s+', ' ', out).strip()
    return out


def parse_price(value: str):
    s = (value or '').strip()
    if not s:
        return None
    s = s.replace('$', '').replace(' ', '')
    s = s.replace('.', '').replace(',', '.') if s.count(',') == 1 and s.count('.') >= 1 else s
    s = s.replace(',', '.')
    m = re.search(r'\d+(?:\.\d+)?', s)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def is_barcode(s: str) -> bool:
    return bool(re.fullmatch(r'\d{5,14}', (s or '').strip()))


def extract_rows():
    root = ET.fromstring(zipfile.ZipFile(ODS_PATH).read('content.xml'))
    rows = []
    for r in root.findall('.//table:table-row', ns):
        vals = []
        for c in r.findall('table:table-cell', ns):
            rep = int(c.attrib.get('{urn:oasis:names:tc:opendocument:xmlns:table:1.0}number-columns-repeated', '1'))
            txt = ' '.join((t.text or '').strip() for t in c.findall('text:p', ns)).strip()
            vals.extend([txt] * rep)
        if not vals:
            continue
        vals += [''] * (8 - len(vals))
        if vals[0].strip().lower() == 'nombre':
            continue

        if is_barcode(vals[0]) and vals[1].strip():
            name = vals[1].strip()
            category = vals[2].strip()
            price = None
            for token in vals[3:]:
                p = parse_price(token)
                if p is not None:
                    price = p
                    break
        else:
            name = vals[0].strip()
            category = vals[2].strip() if len(vals) > 2 else ''
            price = parse_price(vals[1])
            if price is None:
                for token in vals[3:]:
                    p = parse_price(token)
                    if p is not None:
                        price = p
                        break

        if not name or re.fullmatch(r'\d+', name):
            continue
        rows.append({'name_raw': name, 'price': price, 'category_raw': category})
    return rows




def has_token(text: str, token: str) -> bool:
    return bool(re.search(rf'\b{re.escape(token)}\b', text))

def normalize_category(raw: str, name: str) -> str:
    c = strip_accents((raw or '').strip().lower())
    n = strip_accents(name.lower())

    mapping = {
        'gaseosas': 'Gaseosas',
        'agua': 'Aguas',
        'aguas': 'Aguas',
        'vinos': 'Vinos Tintos',
        'espumantes': 'Vinos Espumantes',
        'cervezas': 'Cervezas',
        'gin': 'Gin',
        'whiskey': 'Whisky',
        'whisky': 'Whisky',
        'bebidas alcholicas': 'Aperitivos',
        'bebidas alcoholicas': 'Aperitivos',
        'energizantes': 'Energizantes',
        'snacks': 'Snacks',
        'snaks': 'Snacks',
        'chocolates': 'Golosinas',
        'alfajores': 'Golosinas',
        'golosinas': 'Golosinas',
        'hielo': 'Hielo',
        'promos': 'Promos',
        'jugos': 'Jugos',
        'almacen': 'Snacks',
        'sandwichs': 'Snacks',
        'postres': 'Golosinas',
        'sin identificar': 'Sin identificar',
        'categoria': 'Sin identificar',
    }
    cat = mapping.get(c, 'Sin identificar')

    # Inferencia por nombre
    if 'fernet' in n:
        return 'Fernet'
    if any(x in n for x in ['malbec', 'cabernet', 'merlot', 'syrah', 'blend tinto', 'tinto']):
        return 'Vinos Tintos'
    if any(x in n for x in ['chardonnay', 'sauvignon', 'blanco', 'torrontes', 'chenin']):
        return 'Vinos Blancos'
    if any(x in n for x in ['espumante', 'champagne', 'extra brut', 'brut nature', 'prosecco', 'sidra']):
        return 'Vinos Espumantes'
    if any(has_token(n, x) for x in ['ipa', 'lager', 'stout', 'pilsen', 'beer', 'cerveza', 'heineken', 'stella', 'corona', 'budweiser', 'quilmes', 'brahma']):
        return 'Cervezas'
    if 'gin' in n:
        return 'Gin'
    if any(x in n for x in ['vodka', 'smirnoff', 'absolut']):
        return 'Vodka'
    if any(x in n for x in ['whisky', 'whiskey', 'jack daniels', 'johnnie walker', 'jb']):
        return 'Whisky'
    if any(x in n for x in ['coca', 'pepsi', 'sprite', 'fanta', 'seven up', 'manaos', 'mirinda', 'pomelo', 'tonica', 'gaseosa']):
        return 'Gaseosas'
    if any(x in n for x in ['agua', 'bonaqua', 'rumipal']):
        return 'Aguas'
    if any(x in n for x in ['jugo', 'aquarius', 'cepita', 'ades']):
        return 'Jugos'
    if any(x in n for x in ['monster', 'red bull', 'speed', 'energ']):
        return 'Energizantes'
    if any(x in n for x in ['mani', 'papas', 'doritos', 'palitos', 'sandwich', 'galleta', 'lays']):
        return 'Snacks'
    if any(x in n for x in ['alfajor', 'chocolate', 'mogul', 'bon o bon', 'caramelo', 'gomita', 'chicle']):
        return 'Golosinas'
    if 'hielo' in n:
        return 'Hielo'
    if 'promo' in n:
        return 'Promos'

    if cat == 'Aperitivos':
        if 'fernet' in n:
            return 'Fernet'
        if 'gin' in n:
            return 'Gin'
        if any(x in n for x in ['campari', 'aperol', 'cynar', 'vermouth', 'gancia']):
            return 'Aperitivos'
        if 'vodka' in n:
            return 'Vodka'
        if any(x in n for x in ['whisky', 'whiskey']):
            return 'Whisky'
    return cat


def canonical_key(name: str) -> str:
    s = strip_accents(name.lower())
    s = re.sub(r'\b(ml|l|g|kg)\b', '', s)
    s = re.sub(r'[^a-z0-9]', '', s)
    return s


def choose_best(records):
    # prefer record with price, specific category, and longest normalized name
    def score(r):
        return (
            1 if r['price'] is not None else 0,
            1 if r['category'] != 'Sin identificar' else 0,
            len(r['name']),
        )
    chosen = max(records, key=score)
    prices = [r['price'] for r in records if r['price'] is not None]
    if prices:
        freq = Counter(prices)
        chosen['price'] = freq.most_common(1)[0][0]
    return chosen


def load_image_slugs():
    slugs = set()
    for p in IMAGES_ROOT.rglob('*'):
        if p.is_file():
            stem = p.stem.replace('_', ' ')
            slugs.add(slugify(stem))
    return slugs


def main():
    rows = extract_rows()
    image_slugs = load_image_slugs()

    prepped = []
    for r in rows:
        name = normalize_name(r['name_raw'])
        category = normalize_category(r['category_raw'], name)
        prepped.append({
            'name': name,
            'price': r['price'],
            'category': category,
            'name_raw': r['name_raw'],
        })

    groups = defaultdict(list)
    for r in prepped:
        groups[canonical_key(r['name'])].append(r)

    duplicates = []
    deduped = []
    for key, recs in groups.items():
        if len(recs) > 1:
            duplicates.append(recs)
        deduped.append(choose_best(recs))

    for r in deduped:
        r['slug'] = slugify(r['name'])
        exists = r['slug'] in image_slugs
        r['image_url'] = f"https://cdn.jsdelivr.net/gh/USER/toma-images/images/{r['slug']}.jpg" if exists else ''

    deduped.sort(key=lambda x: x['name'].lower())

    with CSV_OUT.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['name', 'price', 'category', 'slug', 'image_url'])
        w.writeheader()
        for r in deduped:
            w.writerow({
                'name': r['name'],
                'price': '' if r['price'] is None else f"{r['price']:.2f}",
                'category': r['category'],
                'slug': r['slug'],
                'image_url': r['image_url'],
            })

    counts = Counter(r['category'] for r in deduped)
    large_categories = {k: v for k, v in counts.items() if v >= 120}

    suggested_subdivisions = {}
    if 'Cervezas' in large_categories:
        suggested_subdivisions['Cervezas'] = ['Cervezas Industriales', 'Cervezas Importadas', 'Cervezas Artesanales']
    if any(c.startswith('Vinos') for c in counts):
        suggested_subdivisions['Vinos'] = ['Vinos Tintos', 'Vinos Blancos', 'Vinos Espumantes']
    if 'Golosinas' in large_categories:
        suggested_subdivisions['Golosinas'] = ['Alfajores', 'Chocolates', 'Caramelos y Gomitas']

    missing_images = [r['name'] for r in deduped if not r['image_url']]

    with REPORT_OUT.open('w', encoding='utf-8') as f:
        f.write('# Reporte de limpieza de productos\n\n')
        f.write(f'- Total productos originales: {len(rows)}\n')
        f.write(f'- Total productos finales (sin duplicados): {len(deduped)}\n')
        f.write(f'- Duplicados detectados: {sum(len(g)-1 for g in duplicates)}\n\n')

        f.write('## Productos duplicados encontrados\n')
        if duplicates:
            for g in sorted(duplicates, key=lambda x: x[0]['name']):
                f.write('- ' + ' | '.join(sorted({r['name'] for r in g})) + '\n')
        else:
            f.write('- No se detectaron duplicados.\n')

        f.write('\n## Productos sin imagen\n')
        for name in missing_images[:250]:
            f.write(f'- {name}\n')
        if len(missing_images) > 250:
            f.write(f'- ... y {len(missing_images)-250} más\n')

        f.write('\n## Categorías con demasiados productos\n')
        if large_categories:
            for cat, n in sorted(large_categories.items(), key=lambda x: -x[1]):
                f.write(f'- {cat}: {n}\n')
        else:
            f.write('- No se detectaron categorías sobredimensionadas según el umbral usado (>=120).\n')

        f.write('\n## Categorías sugeridas nuevas\n')
        if suggested_subdivisions:
            for cat, subs in suggested_subdivisions.items():
                f.write(f'- {cat}: ' + ' / '.join(subs) + '\n')
        else:
            f.write('- Sin sugerencias adicionales.\n')


if __name__ == '__main__':
    main()

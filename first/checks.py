"""
Server-side validation of the player's HTML/CSS.

Player code is *parsed*, never executed. The HTML is walked with the
stdlib HTMLParser and the CSS is read with a deliberately small
tolerant parser -- enough to answer "did they fix this declaration?"
without pulling in a full CSS engine.
"""

import re
from html.parser import HTMLParser

VOID_TAGS = {
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
    'link', 'meta', 'param', 'source', 'track', 'wbr',
}

# Enough named colours for the palette this challenge uses.
NAMED_COLORS = {
    'white': '#ffffff', 'black': '#000000', 'red': '#ff0000',
    'lime': '#00ff00', 'blue': '#0000ff', 'transparent': 'transparent',
}

AUTO_COLUMNS = -1  # grid-template-columns using auto-fit / auto-fill


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

class Node:
    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.parent = parent
        self.children = []
        self.text = ''

    @property
    def classes(self):
        return set(self.attrs.get('class', '').split())

    def has_class(self, name):
        return name in self.classes

    def walk(self):
        for child in self.children:
            yield child
            yield from child.walk()

    def find_all(self, tag=None, cls=None):
        return [
            n for n in self.walk()
            if (tag is None or n.tag == tag) and (cls is None or n.has_class(cls))
        ]

    def find(self, tag=None, cls=None, node_id=None):
        for n in self.walk():
            if tag is not None and n.tag != tag:
                continue
            if cls is not None and not n.has_class(cls):
                continue
            if node_id is not None and n.attrs.get('id') != node_id:
                continue
            return n
        return None

    def all_text(self):
        return (self.text + ''.join(c.all_text() for c in self.children)).strip()


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node('#root')
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or '') for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v or '') for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        # Tolerant close: unwind to the nearest matching open tag, ignore strays.
        for depth in range(len(self.stack) - 1, 0, -1):
            if self.stack[depth].tag == tag:
                del self.stack[depth:]
                return

    def handle_data(self, data):
        self.stack[-1].text += data


def parse_html(source):
    builder = _TreeBuilder()
    builder.feed(source or '')
    builder.close()
    return builder.root


# --------------------------------------------------------------------------
# CSS
# --------------------------------------------------------------------------

class Rule:
    def __init__(self, selector, declarations, media=None):
        self.selector = selector
        self.declarations = declarations
        self.media = media


def _strip_comments(css):
    return re.sub(r'/\*.*?\*/', ' ', css or '', flags=re.S)


def _split_top_level(text, separator):
    """Split on `separator`, ignoring anything inside (), [] or quotes."""
    parts, buf, depth, quote = [], '', 0, None
    for ch in text:
        if quote:
            buf += ch
            if ch == quote:
                quote = None
            continue
        if ch in '"\'':
            quote = ch
        elif ch in '([':
            depth += 1
        elif ch in ')]':
            depth = max(0, depth - 1)
        elif ch == separator and depth == 0:
            parts.append(buf)
            buf = ''
            continue
        buf += ch
    parts.append(buf)
    return parts


def _parse_declarations(body):
    declarations = []
    for chunk in _split_top_level(body, ';'):
        if ':' not in chunk:
            continue
        prop, _, value = chunk.partition(':')
        prop = prop.strip().lower()
        value = re.sub(r'!\s*important', '', value, flags=re.I).strip()
        if prop and value:
            declarations.append((prop, value))
    return declarations


def _parse_rules(text, media, out):
    i, prelude = 0, ''
    while i < len(text):
        ch = text[i]
        if ch == '{':
            depth, j = 1, i + 1
            while j < len(text) and depth:
                if text[j] == '{':
                    depth += 1
                elif text[j] == '}':
                    depth -= 1
                j += 1
            body = text[i + 1:j - 1]
            head = prelude.strip()
            if head.startswith('@'):
                if head.lower().startswith('@media'):
                    _parse_rules(body, head, out)
                # @keyframes / @font-face carry no declarations we validate
            elif head:
                declarations = _parse_declarations(body)
                for selector in _split_top_level(head, ','):
                    out.append(Rule(selector.strip(), declarations, media))
            prelude = ''
            i = j
            continue
        if ch == '}':
            prelude = ''
            i += 1
            continue
        prelude += ch
        i += 1


def parse_css(source):
    rules = []
    _parse_rules(_strip_comments(source), None, rules)
    return rules


def _targets(selector, target):
    """True when `selector`'s subject is exactly `target` (e.g. `.card-grid`).

    Ancestors are allowed (`.section .card-grid`) but pseudo-classes are not
    (`.btn:hover` must not answer questions asked about `.btn`).
    """
    subject = re.split(r'[\s>+~]+', selector.strip())[-1]
    return subject == target


def declared(rules, target, prop, media='desktop'):
    """Last declared value of `prop` for `target`, or None.

    media='desktop' -> rules outside any @media
    media='narrow'  -> rules inside a max-width @media block
    media='any'     -> everywhere
    """
    found = None
    for rule in rules:
        if not _targets(rule.selector, target):
            continue
        if media == 'desktop' and rule.media:
            continue
        if media == 'narrow' and not (rule.media and 'max-width' in rule.media.lower()):
            continue
        for name, value in rule.declarations:
            if name == prop:
                found = value
    return found


def _normalize_color(value):
    if not value:
        return None
    token = re.sub(r'!\s*important', '', value, flags=re.I).strip().lower()
    token = NAMED_COLORS.get(token, token)
    if re.fullmatch(r'#[0-9a-f]{3}', token):
        return '#' + ''.join(ch * 2 for ch in token[1:])
    return token


def _color_from_background(value):
    if not value:
        return None
    if 'gradient' in value.lower():
        return None
    for token in value.split():
        if token.startswith('#') or token.startswith('rgb') or token in NAMED_COLORS:
            return _normalize_color(token)
    return None


def _to_px(value):
    """Largest length in `value`, in px. Handles px/rem/em/pt and clamp()."""
    best = None
    for number, unit in re.findall(r'(\d*\.?\d+)\s*(px|rem|em|pt)', value or '', re.I):
        size = float(number)
        unit = unit.lower()
        if unit in ('rem', 'em'):
            size *= 16
        elif unit == 'pt':
            size *= 4 / 3
        best = size if best is None else max(best, size)
    return best


def _count_columns(value):
    if not value:
        return None
    lowered = value.lower()
    if 'auto-fit' in lowered or 'auto-fill' in lowered:
        return AUTO_COLUMNS
    repeat = re.search(r'repeat\(\s*(\d+)\s*,', lowered)
    if repeat:
        return int(repeat.group(1))
    return len([t for t in _split_top_level(lowered, ' ') if t.strip()])


# --------------------------------------------------------------------------
# The objectives
# --------------------------------------------------------------------------

def _check_h1(dom, rules):
    return len(dom.find_all(tag='h1')) == 1


def _check_nav_links(dom, rules):
    nav = dom.find(cls='site-nav')
    return bool(nav) and len(nav.find_all(tag='a')) >= 4


def _check_img_alt(dom, rules):
    images = dom.find_all(tag='img')
    return bool(images) and all(node.attrs.get('alt', '').strip() for node in images)


def _check_feature_cards(dom, rules):
    features = dom.find(node_id='features')
    if not features:
        return False
    grid = features.find(cls='card-grid')
    return bool(grid) and len(grid.find_all(cls='card')) >= 3


def _check_card_titles(dom, rules):
    cards = dom.find_all(cls='card')
    return bool(cards) and all(card.find_all(tag='h3') for card in cards)


def _check_real_button(dom, rules):
    return any(node.has_class('btn') for node in dom.find_all(tag='button'))


def _check_footer(dom, rules):
    footer = dom.find(tag='footer')
    return bool(footer) and footer.has_class('site-footer') and bool(footer.all_text())


def _check_body_font(dom, rules):
    return bool(declared(rules, 'body', 'font-family', media='any'))


def _check_nav_flex(dom, rules):
    value = (declared(rules, '.site-nav', 'display') or '').lower()
    return value in ('flex', 'inline-flex')


def _check_hero_center(dom, rules):
    value = (declared(rules, '.hero', 'text-align') or '').lower()
    return value == 'center'


def _check_hero_title_size(dom, rules):
    size = _to_px(declared(rules, '.hero-title', 'font-size', media='any'))
    return size is not None and size >= 32


def _check_grid_columns(dom, rules):
    columns = _count_columns(declared(rules, '.card-grid', 'grid-template-columns'))
    return columns in (3, AUTO_COLUMNS)


def _check_button_contrast(dom, rules):
    text = _normalize_color(declared(rules, '.btn', 'color'))
    if not text:
        return False
    background = _normalize_color(declared(rules, '.btn', 'background-color'))
    if background is None:
        background = _color_from_background(declared(rules, '.btn', 'background'))
    return background is None or background != text


def _check_responsive(dom, rules):
    narrow = _count_columns(declared(rules, '.card-grid', 'grid-template-columns', media='narrow'))
    if narrow == 1:
        return True
    # An auto-fit desktop grid is already responsive without a media query.
    return _count_columns(declared(rules, '.card-grid', 'grid-template-columns')) == AUTO_COLUMNS


# id, group, title, hint, function
_DEFINITIONS = [
    ('h1', 'html', 'Page has exactly one <h1>',
     'The hero headline is the main title of the page, but it is marked up as an <h2>.',
     _check_h1),
    ('nav-links', 'html', 'Navigation has 4 links',
     'A "Contact" link is missing from <nav class="site-nav">.',
     _check_nav_links),
    ('img-alt', 'html', 'Every image has alt text',
     'The hero image has no alt attribute, so screen readers announce nothing.',
     _check_img_alt),
    ('feature-cards', 'html', 'Features section has 3 cards',
     'One article in #features never got its class="card", so it loses the card styling.',
     _check_feature_cards),
    ('card-titles', 'html', 'Every card has an <h3> title',
     'One feature card lost its <h3 class="card-title"> heading.',
     _check_card_titles),
    ('real-button', 'html', 'The call to action is a real <button>',
     'The hero action is a <div class="btn ...">. Buttons must be <button> elements.',
     _check_real_button),
    ('footer', 'html', 'Page ends with a <footer class="site-footer">',
     'The page has no footer at all. The CSS already styles .site-footer -- add the markup.',
     _check_footer),
    ('body-font', 'css', 'body sets a font-family',
     'Nothing declares a font-family, so the page falls back to Times New Roman.',
     _check_body_font),
    ('nav-flex', 'css', '.site-nav lays out in a row',
     '.site-nav uses display: block, which stacks the links. It already has a gap set.',
     _check_nav_flex),
    ('hero-center', 'css', '.hero text is centered',
     '.hero has a text-align value the browser does not understand.',
     _check_hero_center),
    ('hero-title', 'css', '.hero-title is at least 32px',
     'The headline font-size is far too small for a hero section.',
     _check_hero_title_size),
    ('grid-columns', 'css', '.card-grid shows 3 columns on desktop',
     '.card-grid is a grid, but grid-template-columns only defines one column.',
     _check_grid_columns),
    ('btn-contrast', 'css', '.btn text is readable',
     '.btn uses the same colour for text and background, so the label is invisible.',
     _check_button_contrast),
    ('responsive', 'css', '.card-grid collapses to 1 column on narrow screens',
     'The @media (max-width: 720px) block forces three columns -- it has it backwards.',
     _check_responsive),
]

TOTAL_CHECKS = len(_DEFINITIONS)


def run_checks(html, css):
    """Return one result dict per objective. Never raises on bad input."""
    dom = parse_html(html)
    rules = parse_css(css)

    results = []
    for check_id, group, title, hint, function in _DEFINITIONS:
        try:
            passed = bool(function(dom, rules))
        except Exception:  # a malformed submission must not 500 the game
            passed = False
        results.append({
            'id': check_id,
            'group': group,
            'title': title,
            'hint': hint,
            'passed': passed,
        })
    return results

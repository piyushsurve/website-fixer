"""
Server-side validation of the player's HTML/CSS.

Player code is *parsed*, never executed. The HTML is walked with the
stdlib HTMLParser and the CSS is read with a deliberately small
tolerant parser -- enough to answer "did they fix this declaration?"
without pulling in a full CSS engine.

Checks are outcome-based on purpose: they ask "is the navigation laid out
in a row?", not "does line 74 say `display: flex`". Whitespace, property
order, comments and equivalent values (flex vs inline-flex, 3rem vs 48px,
repeat(3, 1fr) vs auto-fit) are all accepted.
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


def _targets(selector, target, exact=False):
    """True when `selector`'s subject is exactly `target` (e.g. `.feature-grid`).

    Ancestors are normally allowed (`.section .feature-grid`) but pseudo-classes
    never are (`.btn-primary:hover` must not answer questions asked about
    `.btn-primary`).

    `exact=True` additionally rejects descendant selectors, so a contextual
    override like `.cta .btn-primary` cannot stand in for the base rule.
    """
    selector = selector.strip()
    parts = re.split(r'[\s>+~]+', selector)
    if exact and len(parts) > 1:
        return False
    return parts[-1] == target


def declared(rules, target, prop, media='desktop', exact=False):
    """Last declared value of `prop` for `target`, or None.

    media='desktop' -> rules outside any @media
    media='narrow'  -> rules inside a max-width @media block
    media='any'     -> everywhere
    """
    found = None
    for rule in rules:
        if not _targets(rule.selector, target, exact=exact):
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


def _length_px(token):
    """One CSS length in px, or None. Understands px/rem/em/pt and bare 0."""
    if token is None:
        return None
    token = token.strip().lower()
    if re.fullmatch(r'0+(\.0+)?', token):
        return 0.0
    match = re.fullmatch(r'(\d*\.?\d+)\s*(px|rem|em|pt)', token)
    if not match:
        return None
    size, unit = float(match.group(1)), match.group(2)
    if unit in ('rem', 'em'):
        return size * 16
    if unit == 'pt':
        return size * 4 / 3
    return size


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


def _lengths(value):
    return [t for t in _split_top_level(value or '', ' ') if t.strip()]


def _padding_top(rules, target):
    """Effective top padding in px from `padding` / `padding-block` / `padding-top`."""
    top = None
    shorthand = declared(rules, target, 'padding')
    if shorthand:
        parts = _lengths(shorthand)
        top = _length_px(parts[0]) if parts else None
    block = declared(rules, target, 'padding-block')
    if block:
        parts = _lengths(block)
        top = _length_px(parts[0]) if parts else top
    explicit = declared(rules, target, 'padding-top')
    if explicit is not None:
        top = _length_px(explicit)
    return top


def _smallest_gap(rules, target):
    """Smallest gap in px across gap / row-gap / column-gap, or None."""
    sizes = []
    for prop in ('gap', 'row-gap', 'column-gap'):
        value = declared(rules, target, prop)
        if not value:
            continue
        for token in _lengths(value):
            size = _length_px(token)
            if size is not None:
                sizes.append(size)
    return min(sizes) if sizes else None


def _max_rotation(value):
    """Largest absolute rotation/skew in degrees inside a transform value."""
    if not value or value.strip().lower() in ('none', 'initial', 'unset'):
        return 0.0
    angles = [
        abs(float(degrees))
        for args in re.findall(r'(?:rotate|rotatez|skew|skewx|skewy)[^(]*\(([^)]*)\)', value, re.I)
        for degrees in re.findall(r'(-?\d*\.?\d+)\s*deg', args, re.I)
    ]
    return max(angles) if angles else 0.0


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


def _unitless(value):
    """A bare number such as a line-height, or a length in px."""
    if not value:
        return None
    match = re.fullmatch(r'(\d*\.?\d+)', value.strip())
    return float(match.group(1)) if match else _to_px(value)


def _scale_factor(value):
    """The largest scale() factor in a transform value; 1.0 when absent."""
    if not value:
        return 1.0
    factors = [
        float(n)
        for args in re.findall(r'scale[xy3d]*\s*\(([^)]*)\)', value, re.I)
        for n in re.findall(r'-?\d*\.?\d+', args)
    ]
    return max(factors) if factors else 1.0


# --------------------------------------------------------------------------
# The objectives
#
# Round 01 is CSS only: the markup is read-only and is never submitted, so
# every objective below asks a question about the stylesheet. `style.css`
# ships 37 deliberate defects and these 14 are the graded ones. Each check
# asks about an outcome, so any equivalent repair passes.
# --------------------------------------------------------------------------

def _check_line_height(dom, rules):
    return (_unitless(declared(rules, 'body', 'line-height')) or 0) >= 1.3


def _check_navbar_row(dom, rules):
    value = (declared(rules, '.navbar', 'display') or '').lower()
    return value in ('flex', 'inline-flex', 'grid', 'inline-grid')


def _check_nav_spacing(dom, rules):
    gap = _smallest_gap(rules, '.navbar__menu')
    aligned = (declared(rules, '.navbar__menu', 'justify-content') or '').lower()
    return gap is not None and gap >= 16 and aligned == 'center'


def _check_hero_split(dom, rules):
    columns = _count_columns(declared(rules, '.hero__container', 'grid-template-columns'))
    return columns in (2, AUTO_COLUMNS)


def _check_hero_title(dom, rules):
    size = _to_px(declared(rules, '.hero__title', 'font-size'))
    return size is not None and size >= 32


def _check_hero_gap(dom, rules):
    gap = _smallest_gap(rules, '.hero__actions')
    return gap is not None and 0 < gap <= 48


def _check_console_upright(dom, rules):
    return _max_rotation(declared(rules, '.console', 'transform', exact=True)) <= 3


def _check_stats_band(dom, rules):
    columns = _count_columns(declared(rules, '.stats__grid', 'grid-template-columns'))
    aligned = (declared(rules, '.stat-card', 'text-align') or '').lower()
    return columns in (4, AUTO_COLUMNS) and aligned == 'center'


def _check_features_grid(dom, rules):
    columns = _count_columns(declared(rules, '.features__grid', 'grid-template-columns'))
    return columns in (3, AUTO_COLUMNS)


def _check_feature_box(dom, rules):
    padding = _padding_top(rules, '.feature-card')
    radius = (declared(rules, '.feature-card', 'border-radius') or '0').strip().lower()
    rounded = radius not in ('0', '0px', '0%', 'none')
    return padding is not None and padding >= 16 and rounded


def _check_feature_icon(dom, rules):
    width = _to_px(declared(rules, '.feature-card__icon', 'width'))
    height = _to_px(declared(rules, '.feature-card__icon', 'height'))
    if width is None or height is None:
        return False
    # A square-ish tile, not a stretched bar.
    return width <= height * 1.5


def _check_steps(dom, rules):
    columns = _count_columns(declared(rules, '.steps', 'grid-template-columns'))
    padding = _padding_top(rules, '.step')
    return columns in (3, AUTO_COLUMNS) and padding is not None and padding >= 16


def _check_pricing(dom, rules):
    gap = _smallest_gap(rules, '.pricing__grid')
    scale = _scale_factor(declared(rules, '.pricing-card--featured', 'transform', exact=True))
    return gap is not None and gap >= 12 and scale >= 1


def _check_responsive(dom, rules):
    # The 860px block must be a max-width query or the phone layout never runs.
    return _count_columns(
        declared(rules, '.hero__container', 'grid-template-columns', media='narrow')
    ) == 1


# id, group, title, description, (hint 1, hint 2, hint 3), function
_DEFINITIONS = [
    ('css-line-height', 'css', 'Body text has readable line spacing',
     'Paragraph lines should not be touching each other.',
     ('Every paragraph on the page is set solid — the lines are jammed together '
      'with no breathing room.',
      'One declaration in the body rule controls the space between lines of text.',
      'Look at line-height in the body rule. A value of 1 means "no leading at '
      'all"; body copy usually wants about 1.6.'),
     _check_line_height),

    ('css-navbar-row', 'css', 'The header lays out in a row',
     'The logo, the menu and the buttons should sit on one line across the top.',
     ('The header is three stacked blocks — brand, then menu, then buttons — '
      'instead of one bar.',
      'The .navbar rule already sets justify-content and gap, and those only do '
      'anything in one layout mode.',
      'Check the display property on .navbar.'),
     _check_navbar_row),

    ('css-nav-spacing', 'css', 'Nav links are spaced and centered',
     'The menu links belong in the middle of the header with room between them.',
     ('The nav links are crammed together and shoved over to one side.',
      'Two properties in the same rule are wrong: one controls the space between '
      'the links, the other controls where the group sits.',
      'In .navbar__menu, fix gap (2px is far too tight) and justify-content.'),
     _check_nav_spacing),

    ('css-hero-split', 'css', 'The hero is a two-column layout',
     'The hero copy and the deploy console should sit side by side on desktop.',
     ('The hero text and the deploy console are stacked in a single column '
      'instead of sitting next to each other.',
      '.hero__container is already a grid — it has just been told how many '
      'columns to create. (The phone breakpoint overrides this one, so you may '
      'not see it change until that is fixed too.)',
      'Set grid-template-columns on .hero__container to two columns.'),
     _check_hero_split),

    ('css-hero-title', 'css', 'The hero headline is headline-sized',
     'The main headline should be the largest text on the page.',
     ('The headline is no bigger than the paragraph underneath it.',
      'Something set the headline to roughly body-copy size.',
      'Fix font-size in .hero__title — the design uses a clamp() that tops out '
      'around 3.6rem.'),
     _check_hero_title),

    ('css-hero-gap', 'css', 'The hero buttons sit together',
     'The two call-to-action buttons should be next to each other, not far apart.',
     ('There is a huge empty gulf between "Start deploying free" and "Watch a 90s '
      'demo".',
      'Flex containers control the space between their children with one property.',
      'The gap on .hero__actions is far too large — the rest of the design uses '
      '16px.'),
     _check_hero_gap),

    ('css-console', 'css', 'The deploy console sits straight',
     'The dark console panel in the hero should be almost level.',
     ('The console panel is tipped over at a wild angle and swamps the top of '
      'the page.',
      'Something is rotating that one element. The design does tilt it, but only '
      'very slightly.',
      'Fix the transform on .console — it should be about 1 degree, not 45.'),
     _check_console_upright),

    ('css-stats-band', 'css', 'The statistics band is a centered 4-column row',
     'The four headline figures should sit in one centered row.',
     ('The four statistics are in a 2x2 block and every number hugs the left edge '
      'of its card.',
      'Two rules are involved: one decides how many columns the band has, the '
      'other how the text sits inside each card.',
      'Set grid-template-columns on .stats__grid to four columns, and text-align '
      'on .stat-card to center.'),
     _check_stats_band),

    ('css-features', 'css', 'Feature cards form a 3-column grid',
     'The six feature cards should sit three across on a desktop screen.',
     ('The six feature cards are stacked in one very long column.',
      'The container is already a grid — check how many columns it asks for.',
      'Set grid-template-columns on .features__grid to three columns.'),
     _check_features_grid),

    ('css-feature-box', 'css', 'Feature cards look like cards',
     'Each feature card needs inner spacing and rounded corners.',
     ('The feature cards have square corners and their text is pressed right up '
      'against the border.',
      'Two properties in the same rule: one controls the space inside the card, '
      'the other its corners.',
      'In .feature-card, fix padding (4px is far too tight) and border-radius — '
      'the design system has a --radius-md token for exactly this.'),
     _check_feature_box),

    ('css-feature-icon', 'css', 'Feature icons are square tiles',
     'Each feature icon should sit in a small square, not a stretched bar.',
     ('The little coloured icon tiles are stretched into wide bars across the '
      'top of every feature card.',
      'The tile is meant to be as wide as it is tall. One of those two numbers '
      'is wrong.',
      'The height on .feature-card__icon is 48px. Make the width match it.'),
     _check_feature_icon),

    ('css-steps', 'css', 'The three steps sit in a padded 3-column row',
     'The workflow section has three steps and they need room to breathe.',
     ('The three step cards are squeezed into three quarters of the row, and '
      'their text is jammed against the card edges.',
      'Two rules: one sets how many columns the row has (there are three steps, '
      'not four), the other the space inside each card.',
      'Set grid-template-columns on .steps to three columns, and give .step its '
      'padding back — the other cards use about 32px.'),
     _check_steps),

    ('css-pricing', 'css', 'Pricing cards are spaced and the featured one stands out',
     'The three plans need space between them, and "Growth" should be the biggest.',
     ('The three pricing cards are glued together, and the highlighted "Most '
      'popular" plan is smaller than the two beside it.',
      'One property controls the space between the cards; another is scaling the '
      'featured card the wrong way.',
      'Give .pricing__grid a gap (the other rows use 28px) and make the scale() '
      'on .pricing-card--featured larger than 1, not smaller.'),
     _check_pricing),

    ('css-responsive', 'css', 'Mobile styles only apply to mobile',
     'The phone layout should take over below 860px, not above it.',
     ('On this full-size preview the nav links have vanished and a hamburger '
      'button has appeared. That is the phone menu, on a desktop screen.',
      'One @media block near the bottom of the stylesheet holds the entire phone '
      'layout — the stacked hero, the slide-down menu, the hamburger — and it is '
      'matching the wrong screens.',
      'The @media query at 860px asks for min-width, so it applies to everything '
      'wider than a phone. It should be max-width.'),
     _check_responsive),
]

TOTAL_CHECKS = len(_DEFINITIONS)
CSS_CHECKS = sum(1 for d in _DEFINITIONS if d[1] == 'css')
HTML_CHECKS = sum(1 for d in _DEFINITIONS if d[1] == 'html')


def objectives():
    """The objective list without any grading -- for the home page."""
    return [
        {'id': cid, 'group': group, 'title': title, 'description': description}
        for cid, group, title, description, _hints, _fn in _DEFINITIONS
    ]


def run_checks(html, css):
    """Return one result dict per objective. Never raises on bad input.

    `html` is the fixed challenge markup, supplied by the server -- it is not
    editable, but the checks receive it so a future round can grade markup
    without changing this signature.
    """
    dom = parse_html(html)
    rules = parse_css(css)

    results = []
    for check_id, group, title, description, hints, function in _DEFINITIONS:
        try:
            passed = bool(function(dom, rules))
        except Exception:  # a malformed submission must not 500 the game
            passed = False
        results.append({
            'id': check_id,
            'group': group,
            'title': title,
            'description': description,
            'hints': list(hints),
            'passed': passed,
        })
    return results

"""
Single source of truth for the Website Fixer challenge.

Everything the game needs to know about "what the player is fixing" and
"how long they get" lives here, so views, checks and templates never
hardcode duplicate copies of it.
"""

# Hard 30 minute session.
GAME_DURATION_SECONDS = 30 * 60

# Below this many seconds the UI switches the timer into its "danger" state.
TIMER_DANGER_SECONDS = 5 * 60
TIMER_WARNING_SECONDS = 10 * 60

# How often the browser re-syncs its countdown with the server.
TIMER_SYNC_INTERVAL_SECONDS = 20


# The broken page the player has to repair. This is a <body> fragment --
# the preview iframe supplies the document shell around it.
STARTER_HTML = """<header class="site-header">
  <div class="brand">
    <span class="brand-mark">PP</span>
    <span class="brand-name">Pixel Perk</span>
  </div>

  <nav class="site-nav">
    <a href="#features">Why us</a>
    <a href="#menu">Menu</a>
    <a href="#plans">Plans</a>
  </nav>
</header>

<!-- HERO -->
<section class="hero">
  <h2 class="hero-title">Coffee that compiles.</h2>
  <p class="hero-text">
    Freshly roasted beans delivered to developers who would rather ship
    features than queue at a counter. Pick a plan, skip the line.
  </p>

  <div class="btn btn-primary">Order a bag</div>

  <img class="hero-shot" src="/static/img/hero-cup.svg">
</section>

<!-- FEATURES -->
<section class="section" id="features">
  <h2 class="section-title">Why Pixel Perk</h2>
  <p class="section-sub">Three reasons our beans end up on every desk.</p>

  <div class="card-grid">
    <article class="card">
      <span class="card-icon">🔥</span>
      <h3 class="card-title">Roasted weekly</h3>
      <p class="card-text">Every batch leaves the roaster on a Monday and reaches you before Friday.</p>
    </article>

    <article class="card">
      <span class="card-icon">🚚</span>
      <p class="card-text">Free delivery on every recurring order, anywhere in the city.</p>
    </article>

    <article>
      <span class="card-icon">♻️</span>
      <h3 class="card-title">Refillable tins</h3>
      <p class="card-text">Send the tin back, we clean it, you get a discount on the next bag.</p>
    </article>
  </div>
</section>

<!-- MENU -->
<section class="section section-alt" id="menu">
  <h2 class="section-title">On the menu</h2>

  <div class="card-grid">
    <article class="card">
      <h3 class="card-title">House Blend</h3>
      <p class="card-text">Chocolate, hazelnut, very forgiving.</p>
      <p class="card-price">$14</p>
    </article>

    <article class="card">
      <h3 class="card-title">Late Night</h3>
      <p class="card-text">Dark roast for the last hour of a deadline.</p>
      <p class="card-price">$16</p>
    </article>

    <article class="card">
      <h3 class="card-title">Sunrise</h3>
      <p class="card-text">Bright, citrusy, dangerously easy to drink.</p>
      <p class="card-price">$15</p>
    </article>
  </div>
</section>

<!-- PLANS -->
<section class="section" id="plans">
  <h2 class="section-title">Simple plans</h2>

  <div class="plan-row">
    <div class="plan">
      <p class="plan-name">Solo</p>
      <p class="plan-price">$14<span>/mo</span></p>
      <p class="card-text">One bag a month.</p>
    </div>
    <div class="plan plan-featured">
      <p class="plan-name">Team</p>
      <p class="plan-price">$38<span>/mo</span></p>
      <p class="card-text">Three bags a month.</p>
    </div>
    <div class="plan">
      <p class="plan-name">Office</p>
      <p class="plan-price">$95<span>/mo</span></p>
      <p class="card-text">Eight bags a month.</p>
    </div>
  </div>
</section>
"""


# The stylesheet that ships with the broken page. Most of it is fine --
# the player has to find the handful of declarations that are wrong.
STARTER_CSS = """/* ---------- base ---------- */
body {
  margin: 0;
  color: #292524;
  background: #fdfbf7;
  line-height: 1.5;
}

h2, h3 { margin: 0 0 10px; }
p { margin: 0 0 12px; }

/* ---------- header ---------- */
.site-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 32px;
  background: #ffffff;
  border-bottom: 1px solid #ece3d7;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 700;
  font-size: 18px;
}

.brand-mark {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: #b45309;
  color: #ffffff;
  display: grid;
  place-items: center;
  font-size: 13px;
}

.site-nav {
  display: block;
  gap: 24px;
}

.site-nav a {
  color: #57534e;
  text-decoration: none;
  font-size: 15px;
}

.site-nav a:hover { color: #b45309; }

/* ---------- hero ---------- */
.hero {
  padding: 72px 24px 56px;
  text-align: centre;
  background: linear-gradient(180deg, #fff8ef, #fdfbf7);
}

.hero-title {
  font-size: 12px;
  margin: 0 0 14px;
  letter-spacing: -0.5px;
}

.hero-text {
  max-width: 540px;
  margin: 0 auto 26px;
  color: #57534e;
}

.hero-shot {
  width: 100%;
  max-width: 360px;
  margin-top: 32px;
}

/* ---------- buttons ---------- */
.btn {
  display: inline-block;
  padding: 13px 28px;
  border: none;
  border-radius: 999px;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  background-color: #b45309;
  color: #b45309;
}

.btn:hover { background-color: #92400e; }

/* ---------- sections ---------- */
.section {
  padding: 56px 32px;
  max-width: 1040px;
  margin: 0 auto;
}

.section-alt { background: #fffaf3; max-width: none; }

.section-title { font-size: 28px; }
.section-sub { color: #78716c; margin-bottom: 28px; }

/* ---------- cards ---------- */
.card-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 20px;
}

.card {
  background: #ffffff;
  border: 1px solid #efe6d9;
  border-radius: 16px;
  padding: 22px;
}

.card-icon { font-size: 24px; }
.card-title { font-size: 18px; }
.card-text { color: #78716c; margin: 0; }
.card-price { font-weight: 700; margin-top: 12px; }

/* ---------- plans ---------- */
.plan-row {
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
}

.plan {
  flex: 1 1 220px;
  border: 1px solid #efe6d9;
  border-radius: 16px;
  padding: 22px;
  background: #ffffff;
}

.plan-featured { border-color: #b45309; }
.plan-name { text-transform: uppercase; letter-spacing: 1px; font-size: 12px; color: #a8a29e; }
.plan-price { font-size: 30px; font-weight: 700; margin: 0 0 10px; }
.plan-price span { font-size: 14px; font-weight: 400; color: #a8a29e; }

/* ---------- footer ---------- */
.site-footer {
  padding: 28px 32px;
  text-align: center;
  color: #78716c;
  border-top: 1px solid #ece3d7;
}

/* ---------- responsive ---------- */
@media (max-width: 720px) {
  .card-grid { grid-template-columns: repeat(3, 1fr); }
  .site-header { flex-direction: column; gap: 12px; }
  .hero { padding: 48px 18px; }
}
"""

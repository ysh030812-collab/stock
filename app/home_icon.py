"""홈 화면 아이콘 · 앱 이름 주입.

Streamlit 은 <head> 를 직접 수정할 수 없으므로, 높이 0 iframe 의 스크립트로
상위 문서(head)에 apple-touch-icon(iOS/iPadOS), icon·manifest(안드로이드) 태그를 넣는다.
아이콘 파일은 app/static/ 에 있고 .streamlit/config.toml 의 enableStaticServing 으로 서빙된다.
아이콘 재생성: python tools/make_icon.py
"""
from __future__ import annotations

import json

import streamlit as st

ICON_VERSION = "1"  # 아이콘을 바꾸면 올려서 캐시 무효화

_SCRIPT = """
<script>
(function () {
  var cfg = %(cfg)s;
  var appWin = window.parent;
  var base = appWin.location.origin + appWin.location.pathname;
  base = base.substring(0, base.lastIndexOf('/') + 1);
  function url(name) { return base + 'app/static/' + name + '?v=' + cfg.version; }

  var docs = [];
  [window.parent, window.top].forEach(function (w) {
    try { if (w && w.document && docs.indexOf(w.document) < 0) docs.push(w.document); } catch (e) {}
  });

  docs.forEach(function (doc) {
    var head = doc.head;
    if (!head || head.querySelector('link[data-levtrack]')) return;
    head.querySelectorAll('link[rel="apple-touch-icon"], link[rel="apple-touch-icon-precomposed"], link[rel="manifest"]')
        .forEach(function (e) { e.remove(); });
    function link(rel, href, sizes, type) {
      var l = doc.createElement('link');
      l.rel = rel; l.href = href; l.setAttribute('data-levtrack', '1');
      if (sizes) l.sizes = sizes;
      if (type) l.type = type;
      head.appendChild(l);
    }
    function meta(name, content) {
      var m = head.querySelector('meta[name="' + name + '"]') || doc.createElement('meta');
      m.name = name; m.content = content; head.appendChild(m);
    }
    link('apple-touch-icon', url('icon-180.png'), '180x180');
    link('icon', url('icon-192.png'), '192x192', 'image/png');
    link('icon', url('icon-512.png'), '512x512', 'image/png');
    meta('apple-mobile-web-app-title', cfg.name);
    meta('application-name', cfg.name);
    meta('theme-color', cfg.theme);

    var win = doc.defaultView;
    var manifest = {
      name: cfg.name, short_name: cfg.name, display: 'browser',
      start_url: win.location.origin + win.location.pathname,
      background_color: cfg.theme, theme_color: cfg.theme,
      icons: [
        { src: url('icon-192.png'), sizes: '192x192', type: 'image/png', purpose: 'any' },
        { src: url('icon-512.png'), sizes: '512x512', type: 'image/png', purpose: 'any' }
      ]
    };
    var blob = new Blob([JSON.stringify(manifest)], { type: 'application/manifest+json' });
    link('manifest', win.URL.createObjectURL(blob));
  });
})();
</script>
"""


def inject_home_icon(name: str = "LevTrack", theme: str = "#0b1226") -> None:
    cfg = json.dumps({"name": name, "theme": theme, "version": ICON_VERSION})
    html = _SCRIPT % {"cfg": cfg}
    if hasattr(st, "iframe"):  # 같은 출처 iframe (스크립트 실행 가능). 화면 공간은 CSS 로 숨김
        with st.container(key="lt_icon"):
            st.iframe(html, height=1)
    else:  # 구버전
        import streamlit.components.v1 as components

        components.html(html, height=0)

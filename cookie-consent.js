(function () {
  if (localStorage.getItem('cs_cookie_consent')) return;

  const banner = document.createElement('div');
  banner.id = 'cs-cookie-banner';
  banner.setAttribute('role', 'dialog');
  banner.setAttribute('aria-label', 'Cookie consent');
  banner.innerHTML = `
    <div id="cs-cookie-inner">
      <p id="cs-cookie-text">
        We use essential cookies to keep you signed in and analytics cookies to improve the product.
        <a href="/cookies.html">Cookie policy</a>
      </p>
      <div id="cs-cookie-btns">
        <button id="cs-cookie-reject">Essential only</button>
        <button id="cs-cookie-accept">Accept all</button>
      </div>
    </div>
  `;

  const style = document.createElement('style');
  style.textContent = `
    #cs-cookie-banner {
      position: fixed;
      bottom: 0;
      left: 0;
      right: 0;
      z-index: 9999;
      background: #030507;
      border-top: 1px solid #8a6d3b;
      padding: 16px 24px;
      box-shadow: 0 -4px 32px rgba(0,0,0,0.6), 0 -1px 0 rgba(201,164,101,0.15);
      animation: cs-slide-up 0.3s ease;
    }
    @keyframes cs-slide-up {
      from { transform: translateY(100%); opacity: 0; }
      to   { transform: translateY(0);   opacity: 1; }
    }
    #cs-cookie-inner {
      max-width: 1100px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      gap: 24px;
      flex-wrap: wrap;
    }
    #cs-cookie-text {
      flex: 1;
      font-family: 'Plus Jakarta Sans', sans-serif;
      font-size: 14px;
      color: #8a8578;
      line-height: 1.5;
      min-width: 240px;
    }
    #cs-cookie-text a {
      color: #C9A465;
      text-decoration: none;
    }
    #cs-cookie-text a:hover { text-decoration: underline; color: #e0bc82; }
    #cs-cookie-btns {
      display: flex;
      gap: 10px;
      flex-shrink: 0;
    }
    #cs-cookie-btns button {
      font-family: 'Plus Jakarta Sans', sans-serif;
      font-size: 13px;
      font-weight: 600;
      padding: 8px 18px;
      border-radius: 6px;
      border: none;
      cursor: pointer;
      transition: opacity 0.15s, transform 0.1s;
    }
    #cs-cookie-btns button:hover { opacity: 0.85; }
    #cs-cookie-btns button:active { transform: scale(0.97); }
    #cs-cookie-reject {
      background: transparent;
      color: #8a8578;
      border: 1px solid #2a2f38 !important;
    }
    #cs-cookie-reject:hover { border-color: #8a6d3b !important; color: #C9A465; }
    #cs-cookie-accept {
      background: linear-gradient(135deg, #C9A465, #a07a3a);
      color: #030507;
    }
    @media (max-width: 600px) {
      #cs-cookie-inner { flex-direction: column; align-items: flex-start; }
      #cs-cookie-btns { width: 100%; }
      #cs-cookie-btns button { flex: 1; }
    }
  `;

  document.head.appendChild(style);
  document.body.appendChild(banner);

  function dismiss(choice) {
    localStorage.setItem('cs_cookie_consent', choice);
    banner.style.animation = 'none';
    banner.style.transition = 'opacity 0.25s, transform 0.25s';
    banner.style.opacity = '0';
    banner.style.transform = 'translateY(100%)';
    setTimeout(() => banner.remove(), 280);
    if (choice === 'all' && window._cs_plausible_deferred) {
      window._cs_plausible_deferred();
    }
  }

  document.getElementById('cs-cookie-accept').addEventListener('click', () => dismiss('all'));
  document.getElementById('cs-cookie-reject').addEventListener('click', () => dismiss('essential'));
})();
